"""Unwired, bounded part-export adapter. Importing this module never opens COM.

Caller must supply an already authorized, exclusively owned SW part document.
No application discovery, locking, configuration switching, saving or deployment.
Production MCP wiring remains outside this adapter; captures never authorize physics.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .physics import (mass_properties, sw_inertia_tensor, sw_transform, transform_mass,
                      merge_rigid_bodies, validate_rotation, _checked, _numbers)


def _require_clean(model):
    rebuild = model.Extension.NeedsRebuild2
    if model.GetSaveFlag() is not False or type(rebuild) not in (int, bool) or rebuild != 0:
        raise ValueError('DIRTY_REBUILD_REQUIRED_OR_UNKNOWN')


def _source_state(model, expected, doc_type=1):
    if model.GetType() != doc_type:
        raise ValueError('DOCUMENT_TYPE_MISMATCH')
    path = Path(model.GetPathName())
    if not path.is_absolute() or not path.is_file() or path.resolve() != Path(expected['path']).resolve():
        raise ValueError('SOURCE_PATH_MISMATCH')
    _require_clean(model)
    config = model.ConfigurationManager.ActiveConfiguration.Name
    if not config or config != expected['configuration']:
        raise ValueError('CONFIGURATION_MISMATCH')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected['sha256']:
        raise ValueError('SOURCE_HASH_MISMATCH')
    return {'path': str(path.resolve()), 'configuration': config, 'sha256': digest}


def _component_state(component):
    return {'instance_path': component.Name2, 'configuration': component.ReferencedConfiguration,
            'source_file': component.GetPathName(), 'suppression_raw': component.GetSuppression2(),
            'transform_raw': list(component.Transform2.ArrayData)}


def _mass_readout(mp, frame):
    if mp is None: raise ValueError('MASS_PROPERTIES_UNAVAILABLE')
    mp.UseSystemUnits = True
    if mp.UseSystemUnits is not True: raise ValueError('SYSTEM_UNITS_NOT_CONFIRMED')
    raw = {'mass': mp.Mass, 'center_of_mass': list(mp.CenterOfMass),
           'moment': list(mp.GetMomentOfInertia(0)), 'user_assigned': mp.UserAssigned}
    body = mass_properties(raw['mass'], raw['center_of_mass'], sw_inertia_tensor(raw['moment']),
                           length_unit='m', mass_unit='kg', frame=frame)
    return raw, body


def capture_context_component(model, component, *, expected_source, expected_component_source,
                              sw_version, provenance='unknown', max_triangles=100000,
                              invoke_bodies=None):
    """Read actual assembly-context solids (including assembly-cut user bodies).

    AddBodies on the assembly mass object already accounts for component poses.
    Face mesh coordinates are retained raw with UNKNOWN frame until real native
    calibration; they cannot be fed to the part-local export path by accident.
    invoke_bodies is the offline seam for GetBodies3's COM out argument only.
    """
    if type(max_triangles) is not int or not 1 <= max_triangles <= 100000:
        raise ValueError('triangle budget must be 1..100000')
    if provenance not in ('unknown', 'fixture', 'real-sw-export') or not isinstance(sw_version, str) or not sw_version.strip():
        raise ValueError('explicit version/provenance required')
    source = _source_state(model, expected_source, 2)
    before = _component_state(component)
    if type(before['suppression_raw']) is not int or before['suppression_raw'] not in (2,3):
        raise ValueError('COMPONENT_NOT_RESOLVED')
    sw_transform(before['transform_raw'])
    if model.SelectionManager.GetSelectedObjectCount2(-1) != 0:
        raise ValueError('SELECTION_MUST_BE_EMPTY')
    path = Path(before['source_file'])
    if not path.is_absolute() or path.resolve() != Path(expected_component_source['path']).resolve() or before['configuration'] != expected_component_source['configuration']:
        raise ValueError('COMPONENT_SOURCE_MISMATCH')
    child_doc = component.GetModelDoc2()
    if child_doc is None or child_doc.GetType() != 1 or Path(child_doc.GetPathName()).resolve() != path.resolve():
        raise ValueError('COMPONENT_DOCUMENT_UNAVAILABLE_OR_MISMATCH')
    # Referenced assembly configuration may differ from the part's active one;
    # do not switch it, but never attribute unsaved geometry to a saved file hash.
    _require_clean(child_doc)
    def check_child_file():
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_component_source['sha256']:
                raise ValueError('COMPONENT_SOURCE_HASH_MISMATCH')
    check_child_file()
    if invoke_bodies is None:
        def invoke_bodies(comp, kind):
            import pythoncom
            from win32com.client import VARIANT
            info = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
            bodies = comp.GetBodies3(kind, info)
            return bodies, info.value
    solids, info = invoke_bodies(component, 0)
    surfaces, _ = invoke_bodies(component, 1)
    if not isinstance(surfaces, (list, tuple)):
        raise ValueError('UNKNOWN_CONTEXT_SURFACE_QUERY')
    if surfaces or not isinstance(solids, (list,tuple)) or not 1 <= len(solids) <= 10000:
        raise ValueError('MISSING_SURFACE_OR_OVER_BUDGET_CONTEXT_BODIES')
    if not isinstance(info, (list,tuple)) or len(info) != len(solids) or any(type(x) is not int for x in info):
        raise ValueError('UNKNOWN_CONTEXT_BODY_INFO')
    mp = model.Extension.CreateMassProperty()
    if mp is None or mp.AddBodies(tuple(solids)) is not True:
        raise ValueError('CONTEXT_BODIES_NOT_ACCEPTED')
    raw, physics = _mass_readout(mp, 'root-assembly')
    vertices, face_count = [], 0
    for body in solids:
        faces = body.GetFaces()
        if not isinstance(faces, (list,tuple)) or not faces:
            raise ValueError('MISSING_CONTEXT_FACES')
        face_count += len(faces)
        if face_count > 10000: raise ValueError('CONTEXT_FACE_BUDGET')
        for face in faces:
            points = _triangles(face.GetTessTriangles(True), max_triangles)
            if len(vertices)+len(points) > 9*max_triangles:
                raise ValueError('CONTEXT_TESSELLATION_BUDGET')
            vertices.extend(points)
    check_child_file()
    _require_clean(child_doc)
    if _component_state(component) != before or _source_state(model, expected_source, 2) != source:
        raise ValueError('CONTEXT_CHANGED_DURING_CAPTURE')
    return {'source':source, 'component_source':deepcopy(expected_component_source), 'component':before,
            'source_revision':source['sha256'], 'provenance':provenance, 'sw_version':sw_version,
            'physics':physics, 'raw_mass_properties':raw, 'body_info_raw':list(info),
            'vertices_raw_system_units':vertices, 'mesh_frame':'unknown', 'ready_for_physics':False,
            'methods':{'bodies':'IComponent2.GetBodies3', 'mass':'IMassProperty.AddBodies/GetMomentOfInertia(0)',
                       'mesh':'IFace2.GetTessTriangles(True)', 'component_transform_applied_to_mass':False},
            'missing_information':['installed COM marshaling and user-body density calibration',
                                   'context face mesh frame and geometric tolerance', 'collision coverage']}


def capture_assembly(model, *, expected_source, sw_version, provenance='unknown',
                     max_components=256, max_triangles=100000, generation=None,
                     capture_mode='standalone', invoke_bodies=None):
    """Capture occurrence poses and either standalone or assembly-context evidence.

    All occurrences, including failed/unsupported ones, are retained. Containers
    carry no mass to avoid double counting. Source-file deduplication is forbidden.
    Context mode binds AddBodies mass to the same native occurrence and A02
    reference. Its mesh frame remains unknown; standalone equivalence is unproved.
    Neither capture mode can authorize a physics run or CAD write.
    """
    if type(max_components) is not int or not 1 <= max_components <= 1000:
        raise ValueError('component budget must be 1..1000')
    if type(max_triangles) is not int or not 1 <= max_triangles <= 100000:
        raise ValueError('triangle budget must be 1..100000')
    if provenance not in ('unknown', 'fixture', 'real-sw-export') or not isinstance(sw_version, str) or not sw_version.strip():
        raise ValueError('explicit version/provenance required')
    if capture_mode not in ('standalone', 'context'):
        raise ValueError('INVALID_CAPTURE_MODE')
    if capture_mode == 'context' and generation is None:
        raise ValueError('CONTEXT_CAPTURE_REQUIRES_GENERATION')
    source = _source_state(model, expected_source, 2)
    snapshot_context = {'source_revision':source['sha256'], 'configuration':source['configuration'],
                        'generation':generation}
    native = model.GetComponents(False)
    if not isinstance(native, (list, tuple)) or not 1 <= len(native) <= max_components:
        raise ValueError('MISSING_OR_OVER_BUDGET_COMPONENTS')
    references, root_mates = None, None
    if generation is not None:
        if type(generation) is not int or generation < 0:
            raise ValueError('INVALID_GENERATION')
        from .assembly import capture_reference, capture_mate_features
        references = [capture_reference(model.Extension, component) for component in native]
        if len(set(references)) != len(references):
            raise ValueError('DUPLICATE_COMPONENT_REFERENCE')
        root_mates = capture_mate_features(model, owning_frame='root-assembly',
                                          configuration=source['configuration'])
    records, captured_docs, captured_components, total = [], [], [], 0
    for index, component in enumerate(native):
        record = {'id': f'occurrence-{index}', 'identity_status': 'snapshot-local-only',
                  'persistent_reference': None, 'status': 'unknown'}
        if references is not None:
            record.update(persistent_reference=references[index], identity_status='native-reference-unresolved',
                          owner_document=source['path'], owner_configuration=source['configuration'],
                          snapshot_context=deepcopy(snapshot_context))
        try:
            record.update(instance_path=component.Name2, configuration=component.ReferencedConfiguration,
                          source_file=component.GetPathName(), suppression_raw=component.GetSuppression2())
            if type(record['suppression_raw']) is not int or record['suppression_raw'] not in (2, 3):
                raise ValueError('COMPONENT_NOT_RESOLVED')
            raw_transform = list(component.Transform2.ArrayData)
            r, t = sw_transform(raw_transform)
            record.update(rotation=r, translation_m=t, transform_raw=raw_transform,
                          transform_frame='root-assembly', transform_method='IComponent2.Transform2')
            captured_components.append((component, {key:deepcopy(record[key]) for key in
                ('instance_path','configuration','source_file','suppression_raw','transform_raw')}))
            doc = component.GetModelDoc2()
            if doc is None: raise ValueError('COMPONENT_DOCUMENT_UNAVAILABLE')
            if doc.GetType() == 2:
                record['status'] = 'container-no-mass'
            elif doc.GetType() == 1:
                child_path = Path(record['source_file'])
                if not child_path.is_absolute(): raise ValueError('UNSAVED_OR_VIRTUAL_COMPONENT')
                with child_path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                expected = {'path': str(child_path), 'sha256': digest, 'configuration': record['configuration']}
                if capture_mode == 'context':
                    # Referenced configuration belongs to the occurrence, not the active part document.
                    active_expected = dict(expected, configuration=doc.ConfigurationManager.ActiveConfiguration.Name)
                    _source_state(doc, active_expected)
                    snap = capture_context_component(model, component, expected_source=expected_source,
                        expected_component_source=expected, sw_version=sw_version, provenance=provenance,
                        max_triangles=max_triangles, invoke_bodies=invoke_bodies)
                    total += len(snap['vertices_raw_system_units'])//9
                    record.update(capture=snap, status='captured-context-mesh-frame-unknown')
                    captured_docs.append((doc, active_expected))
                else:
                    snap = capture_part(doc, expected_source=expected, sw_version=sw_version,
                                        provenance=provenance, max_triangles=max_triangles)
                    total += len(snap['vertices_m'])//9
                    record.update(capture=snap, status='captured-standalone-part-not-context-validated')
                    captured_docs.append((doc, expected))
                if total > max_triangles: raise ValueError('ASSEMBLY_TESSELLATION_BUDGET')
            else:
                raise ValueError('UNSUPPORTED_COMPONENT_DOCUMENT_TYPE')
        except Exception as error:
            record['status'] = 'unsupported'
            record['error'] = type(error).__name__ + ': ' + str(error)
        records.append(record)
        if total > max_triangles:
            raise ValueError('ASSEMBLY_TESSELLATION_BUDGET')
    for doc, expected in captured_docs:
        _source_state(doc, expected)
    for component, expected in captured_components:
        if _component_state(component) != expected:
            raise ValueError('COMPONENT_CHANGED_DURING_CAPTURE')
    if references is not None:
        # Re-enumerate: checking only the original objects misses inserted/removed occurrences.
        after = model.GetComponents(False)
        if not isinstance(after, (list, tuple)) or len(after) != len(native):
            raise ValueError('COMPONENT_SET_CHANGED')
        after_refs = [capture_reference(model.Extension, component) for component in after]
        if len(set(after_refs)) != len(after_refs) or set(after_refs) != set(references):
            raise ValueError('COMPONENT_SET_CHANGED')
        if [capture_reference(model.Extension, component) for component in native] != references:
            raise ValueError('COMPONENT_REFERENCE_CHANGED')
        if capture_mate_features(model, owning_frame='root-assembly', configuration=source['configuration']) != root_mates:
            raise ValueError('MATES_CHANGED_DURING_CAPTURE')
    if _source_state(model, expected_source, 2) != source:
        raise ValueError('ASSEMBLY_CHANGED_DURING_CAPTURE')
    return {'source': source, 'source_revision': source['sha256'], 'sw_version': sw_version,
            'provenance': provenance, 'components': records, 'ready_for_physics': False,
            'snapshot_context':snapshot_context, 'capture_mode':capture_mode,
            'root_mates':root_mates, 'mates_complete':False,
            'missing_information': ['A02 persistent occurrence identity and complete mate solution',
                                    'assembly-context geometry equivalence', 'validated collisions',
                                    'real SW and Webots calibration']}


def _triangles(raw, max_triangles):
    if not isinstance(raw, (list, tuple)) or not raw or len(raw) % 9 or len(raw)//9 > max_triangles:
        raise ValueError('MISSING_INVALID_OR_OVER_BUDGET_TESSELLATION')
    vertices = _numbers(raw, len(raw))
    for i in range(0, len(vertices), 9):
        a, b, c = (vertices[i+k:i+k+3] for k in (0,3,6))
        u, v = ([b[k]-a[k] for k in range(3)], [c[k]-a[k] for k in range(3)])
        normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        if not all(math.isfinite(x) for x in normal) or not any(normal):
            raise ValueError('DEGENERATE_TESSELLATION')
    return vertices


def capture_part(model, *, expected_source, sw_version, provenance='unknown', max_triangles=100000):
    """Capture current saved part via named APIs, with source checks on both sides.

    Tests use fake objects. Real SW call signatures/geometry must be calibrated
    before this adapter is connected to a live host. No fallback to old arrays.
    """
    if type(max_triangles) is not int or not 1 <= max_triangles <= 100000:
        raise ValueError('triangle budget must be 1..100000')
    if provenance not in ('unknown', 'fixture', 'real-sw-export') or not isinstance(sw_version, str) or not sw_version.strip():
        raise ValueError('explicit version/provenance required')
    source = _source_state(model, expected_source)
    if model.SelectionManager.GetSelectedObjectCount2(-1) != 0:
        raise ValueError('SELECTION_MUST_BE_EMPTY: will not clear user selection')
    solids = model.GetBodies2(0, False)
    visible = model.GetBodies2(0, True)
    surfaces = model.GetBodies2(1, False)
    if any(not isinstance(value, (list, tuple)) for value in (solids, visible, surfaces)):
        raise ValueError('UNKNOWN_PART_BODY_QUERY')
    if not solids or len(solids) != len(visible) or surfaces:
        raise ValueError('HIDDEN_SURFACE_OR_MISSING_BODIES_UNSUPPORTED')
    count = model.GetTessTriangleCount()
    if type(count) is not int or not 1 <= count <= max_triangles:
        raise ValueError('TESSELLATION_BUDGET')
    mp = model.Extension.CreateMassProperty()
    raw, body = _mass_readout(mp, 'part-local')
    vertices = _triangles(model.GetTessTriangles(True), max_triangles)
    if len(vertices)//9 != count: raise ValueError('TESSELLATION_COUNT_CHANGED')
    if _source_state(model, expected_source) != source:
        raise ValueError('SOURCE_CHANGED_DURING_CAPTURE')
    return {'source': source, 'source_revision': source['sha256'], 'sw_version': sw_version,
            'provenance': provenance, 'physics': body, 'vertices_m': vertices,
            'raw_mass_properties': raw,
            'methods': {'mass': 'IModelDocExtension.CreateMassProperty/IMassProperty',
                        'inertia': 'IMassProperty.GetMomentOfInertia(0)',
                        'mesh': 'IPartDoc.GetTessTriangles(True)', 'UseSystemUnits': True}}


def part_bundle(snapshot):
    """Return URDF + local-metre OBJ and S04 data payloads, without writing files.

    Visual tessellation is never silently promoted to a validated collision body.
    This bundle alone is therefore NOT ready for physical simulation.
    """
    body = _checked(snapshot['physics'], snapshot['physics']['frame'])
    vertices = _triangles(snapshot['vertices_m'], 100000)
    number = lambda x: format(x, '.17g')
    xyz = lambda v: ' '.join(map(number, v))
    root = ET.Element('robot', name='sw_part')
    link = ET.SubElement(root, 'link', name='part')
    inertial = ET.SubElement(link, 'inertial')
    ET.SubElement(inertial, 'origin', xyz=xyz(body['com_m']), rpy='0 0 0')
    ET.SubElement(inertial, 'mass', value=number(body['mass_kg']))
    a = body['inertia_kg_m2']
    ET.SubElement(inertial, 'inertia', **{name: number(a[i][j]) for name,i,j in
                  [('ixx',0,0),('ixy',0,1),('ixz',0,2),('iyy',1,1),('iyz',1,2),('izz',2,2)]})
    visual = ET.SubElement(link, 'visual')
    ET.SubElement(visual, 'origin', xyz='0 0 0', rpy='0 0 0')
    ET.SubElement(ET.SubElement(visual, 'geometry'), 'mesh', filename='visual.obj', scale='1 1 1')
    obj = ['# SW display tessellation, metres, part-local; collision unvalidated']
    obj += ['v '+xyz(vertices[i:i+3]) for i in range(0,len(vertices),3)]
    obj += [f'f {i+1} {i+2} {i+3}' for i in range(0,len(vertices)//3,3)]
    missing = ['real SW API calibration', 'validated collision geometry and coverage',
               'urdf2webots conversion and precision audit', 'real Webots physics']
    return {'robot.urdf': ET.tostring(root, encoding='unicode'), 'visual.obj': '\n'.join(obj)+'\n',
            'vertices_m': vertices,
            'manifest_data': {'source': deepcopy(snapshot['source']),
                              'capture_provenance': snapshot.get('provenance', 'unknown'),
                              'capture_methods': deepcopy(snapshot.get('methods', {})),
                              'raw_mass_properties': deepcopy(snapshot['raw_mass_properties']),
                              'components': [{'id': 'part', 'persistent_reference': None,
                                              'identity_status': 'unknown'}],
                              'rigid_bodies': [body], 'joints': []},
            'conversion_data': {'inputs': [deepcopy(snapshot['source'])],
                                'tools': [{'name': 'SW', 'version': snapshot['sw_version']}],
                                'mappings': [{'target': {'kind': 'urdf_link', 'id': 'part'}, 'component_ids': ['part']}],
                                'approximations': ['display tessellation; error not calibrated'],
                                'omissions': ['collision geometry'], 'support': 'approximate',
                                'physical_validation': 'not_run', 'missing_information': missing}}


def rigid_group_bundle(components, *, frame, source_frame='part-local'):
    """Export one explicitly fixed group; input captures are component-local.

    Each member: id, capture, rotation, translation_m (source frame to output link).
    root-assembly mode requires independently established mesh_frame evidence;
    native context captures currently emit unknown and are rejected.
    Caller must establish fixed membership and assembly-context geometry
    equivalence. This pure function does not infer either from part filenames.
    """
    if source_frame not in ('part-local', 'root-assembly'):
        raise ValueError('UNSUPPORTED_GROUP_SOURCE_FRAME')
    members = list(components)
    bodies, vertices, sources = [], [], []
    ids = [m['id'] for m in members]
    if not ids or any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique component ids required')
    for member in members:
        snap = member['capture']
        if member.get('transform_frame', frame) != frame:
            raise ValueError('COMPONENT_OUTPUT_FRAME_MISMATCH')
        r, t = validate_rotation(member['rotation']), _numbers(member['translation_m'], 3)
        body = transform_mass(snap['physics'], r, t, source_frame=source_frame, target_frame=frame)
        if source_frame == 'root-assembly':
            if snap.get('mesh_frame') != 'root-assembly':
                raise ValueError('CONTEXT_MESH_FRAME_UNPROVEN')
            raw = _triangles(snap['vertices_raw_system_units'], 100000)
        else:
            raw = _triangles(snap['vertices_m'], 100000)
        bodies.append((member['id'], body))
        if len(vertices) + len(raw) > 900000:
            raise ValueError('GROUP_TESSELLATION_BUDGET')
        for offset in range(0, len(raw), 3):
            vertices.extend(sum(r[i][j]*raw[offset+j] for j in range(3))+t[i] for i in range(3))
        sources.append(deepcopy(snap['source']))
    merged = merge_rigid_bodies(bodies, frame=frame)
    combined = {'physics': merged, 'vertices_m': vertices, 'source': {'members': sources},
                'raw_mass_properties': [deepcopy(m['capture']['raw_mass_properties']) for m in members],
                'sw_version': members[0]['capture']['sw_version']}
    bundle = part_bundle(combined)
    bundle['manifest_data']['rigid_bodies'] = [merged]
    bundle['manifest_data']['components'] = [
        {'id': m['id'], 'source': deepcopy(m['capture']['source']),
         'provenance': m['capture']['provenance'], 'methods': deepcopy(m['capture']['methods']),
         'rotation': deepcopy(m['rotation']), 'translation_m': deepcopy(m['translation_m']),
         'source_transform': deepcopy(m.get('source_transform')),
         'persistent_reference': None, 'identity_status': 'unknown'} for m in members]
    for exported, member in zip(bundle['manifest_data']['components'], members):
        for key in ('persistent_reference', 'identity_status', 'owner_document', 'owner_configuration',
                    'instance_path', 'configuration', 'snapshot_context'):
            if key in member:
                exported[key] = deepcopy(member[key])
    bundle['conversion_data']['inputs'] = sources
    bundle['conversion_data']['tools'] = [{'name': 'SW', 'version': v} for v in
                                          sorted({m['capture']['sw_version'] for m in members})]
    bundle['conversion_data']['mappings'] = [{'target': {'kind': 'urdf_link', 'id': 'part'},
                                            'component_ids': ids}]
    bundle['conversion_data']['missing_information'].append('fixed-group proof and assembly-context geometry equivalence')
    return bundle


def reframe_component(component, *, link_rotation, link_translation_m, owning_frame, link_frame):
    """Root-relative component and link poses -> component pose in link frame.

    Both input poses must refer to the same captured assembly-root frame. Mesh
    and physics remain untouched until rigid_group_bundle applies this pose once.
    """
    if (not isinstance(owning_frame, str) or not owning_frame.strip() or
        component.get('transform_frame') != owning_frame or
        not isinstance(link_frame, str) or not link_frame.startswith('link:') or len(link_frame) == 5):
        raise ValueError('COMPONENT_LINK_FRAME_MISMATCH')
    rc = validate_rotation(component['rotation'])
    tc = _numbers(component['translation_m'], 3)
    rl = validate_rotation(link_rotation)
    tl = _numbers(link_translation_m, 3)
    result = dict(component)
    result['source_transform'] = {'frame': owning_frame, 'rotation': deepcopy(rc), 'translation_m': list(tc),
                                  'raw': deepcopy(result.pop('transform_raw', None))}
    result['transform_frame'] = link_frame
    result['rotation'] = [[sum(rl[k][i]*rc[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    result['translation_m'] = [sum(rl[k][i]*(tc[k]-tl[k]) for k in range(3)) for i in range(3)]
    return result


def jointed_bundle(links, joints):
    """Serialize A02-validated joints and link-local rigid-group bundles.

    A02 remains the single tree/joint validator. Before I01 its module must be
    supplied from that worktree for source-only seam tests. Never reads external
    XML/OBJ: regenerates mesh and inertial XML from the validated numeric payload.
    """
    from .assembly import validate_joint, validate_tree
    if not isinstance(links, (list, tuple)) or not 1 <= len(links) <= 256:
        raise ValueError('LINK_BUDGET')
    ids = [link['id'] for link in links]
    checked_joints = [validate_joint(j) for j in joints]
    root_id = validate_tree(ids, checked_joints)
    robot = ET.Element('robot', name='sw_assembly')
    files, components, bodies, mappings, inputs, raw_mass, seen = {}, [], [], [], [], [], set()
    audit = {key: [] for key in ('tools', 'omissions', 'approximations', 'missing_information')}
    vertex_count = 0
    for index, link in enumerate(links):
        name, bundle = link['id'], link['bundle']
        data = bundle['manifest_data']
        if len(data['rigid_bodies']) != 1:
            raise ValueError('ONE_RIGID_BODY_PER_LINK_REQUIRED')
        original = data['rigid_bodies'][0]
        body = _checked(original, 'link:'+name)
        member_ids = original.get('component_ids')
        member_records = data['components']
        if (not isinstance(member_ids, list) or not member_ids or
            any(not isinstance(cid, str) or not cid.strip() for cid in member_ids) or
            len(set(member_ids)) != len(member_ids) or set(member_ids) & seen or
            [c['id'] for c in member_records] != member_ids):
            raise ValueError('INVALID_OR_REUSED_RIGID_MEMBERS')
        seen.update(member_ids)
        body['component_ids'] = list(member_ids)
        body['id'] = name
        vertices = _triangles(bundle['vertices_m'], 100000)
        vertex_count += len(vertices)
        if vertex_count > 900000:
            raise ValueError('ASSEMBLY_TESSELLATION_BUDGET')
        fresh = part_bundle({'physics':body, 'vertices_m':vertices, 'source':data['source'],
                             'raw_mass_properties':data['raw_mass_properties'], 'sw_version':'see-inputs'})
        element = ET.fromstring(fresh['robot.urdf']).find('link')
        element.set('name', name)
        mesh_path = f'meshes/link_{index}.obj'
        element.find('visual/geometry/mesh').set('filename', mesh_path)
        robot.append(element)
        files[mesh_path] = fresh['visual.obj']
        components.extend(deepcopy(member_records)); bodies.append(body)
        inputs.extend(deepcopy(bundle['conversion_data']['inputs']))
        for key, values in audit.items():
            # ponytail: bounded 256-link audit lists; use canonical keys if this budget grows.
            for value in bundle['conversion_data'].get(key, []):
                if value not in values: values.append(deepcopy(value))
        raw_mass.append(deepcopy(data['raw_mass_properties']))
        mappings.append({'target':{'kind':'urdf_link','id':name}, 'component_ids':list(member_ids)})
    number = lambda x: format(x, '.17g')
    xyz = lambda v: ' '.join(map(number, v))
    for j in checked_joints:
        element = ET.SubElement(robot, 'joint', name=j['id'], type=j['type'])
        ET.SubElement(element, 'parent', link=j['parent'])
        ET.SubElement(element, 'child', link=j['child'])
        ET.SubElement(element, 'origin', xyz=xyz(j['origin_xyz_m']), rpy=xyz(j['origin_rpy_rad']))
        if j['type'] != 'fixed':
            ET.SubElement(element, 'axis', xyz=xyz(j['axis']))
            ET.SubElement(element, 'limit', **{k:number(j['limits'][k]) for k in ('lower','upper','effort','velocity')})
    files['robot.urdf'] = ET.tostring(robot, encoding='unicode')
    files['manifest_data'] = {'source':{'inputs':inputs}, 'components':components,
                              'rigid_bodies':bodies, 'joints':checked_joints, 'raw_mass_properties':raw_mass}
    files['conversion_data'] = {'inputs':inputs, 'root_link':root_id, 'mappings':mappings,
        'support':'approximate', 'approximations':['display tessellation; error not calibrated'],
        'omissions':['collision geometry'], 'physical_validation':'not_run',
        'missing_information':['trusted source snapshot and full mate completeness',
                               'assembly-context geometry calibration', 'collision coverage',
                               'converter precision and real SW/Webots validation']}
    for key, values in audit.items():
        target = files['conversion_data'].setdefault(key, [])
        for value in values:
            if value not in target: target.append(value)
    return files
