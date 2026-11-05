"""Pure snapshot mapping. Persistent-reference resolution belongs to the SW adapter."""
from copy import deepcopy


def map_candidates(components, mappings, *, target, snapshot, current):
    """Return all CAD candidates, refusing stale contexts; never authorize a write."""
    required = ("source_revision", "configuration", "generation")
    for context in (snapshot, current):
        if any(not isinstance(context.get(k), str) or not context[k].strip() for k in required[:2]) or type(context.get("generation")) is not int or context["generation"] < 0:
            raise ValueError("INVALID_SNAPSHOT_CONTEXT")
    if any(snapshot.get(k) is None or snapshot.get(k) != current.get(k) for k in required):
        raise ValueError("STALE_OR_UNKNOWN_SNAPSHOT")
    by_id = {}
    for component in components:
        cid = component.get("id")
        if not isinstance(cid, str) or not cid or cid in by_id:
            raise ValueError("INVALID_OR_DUPLICATE_COMPONENT_ID")
        by_id[cid] = component
    candidates = set()
    for mapping in mappings:
        value = mapping.get("target")
        valid = isinstance(value, str) and bool(value.strip())
        if isinstance(value, dict):
            valid = set(value) == {"kind", "id"} and all(isinstance(value[k], str) and value[k].strip() for k in value)
        if not valid:
            raise ValueError("INVALID_MAPPING_TARGET")
        ids = mapping.get("component_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in by_id for i in ids):
            raise ValueError("INVALID_MAPPING_COMPONENTS")
        if mapping.get("target") == target:
            candidates.update(ids)
    return [deepcopy(by_id[cid]) for cid in sorted(candidates)]


def component_targets(components, mappings, *, component_id, snapshot, current):
    """Reverse lookup in the same validated snapshot; merged bodies remain many-to-one."""
    map_candidates(components, mappings, target=None, snapshot=snapshot, current=current)
    if component_id not in {c["id"] for c in components}:
        raise ValueError("UNKNOWN_COMPONENT")
    return [deepcopy(m["target"]) for m in mappings if component_id in m["component_ids"]]


def classify_motion(*, translation_dof, rotation_dof, solver_status, complete):
    """Classify an adapter's solved relative DOFs, never infer them from mate names.

    Counts alone do not establish axes or global mechanism mobility.
    """
    if complete is not True or solver_status != "solved":
        raise ValueError("UNRESOLVED_OR_OVERCONSTRAINED_MATES")
    if any(type(n) is not int or not 0 <= n <= 3 for n in (translation_dof, rotation_dof)):
        raise ValueError("INVALID_DOF")
    kinds = {(0, 0): "fixed", (0, 1): "revolute", (1, 0): "prismatic"}
    try:
        return kinds[translation_dof, rotation_dof]
    except KeyError:
        raise ValueError("UNSUPPORTED_MOBILITY") from None


def validate_tree(links, joints):
    """Reject loops, multiple parents and disconnected links; never cut edges."""
    if not links or any(not isinstance(x, str) or not x for x in links) or len(set(links)) != len(links):
        raise ValueError("INVALID_LINKS")
    parents, names = {}, set()
    for joint in joints:
        name, parent, child = (joint.get(k) for k in ("id", "parent", "child"))
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("INVALID_JOINT_ID")
        names.add(name)
        if parent not in links or child not in links or parent == child:
            raise ValueError("INVALID_JOINT_ENDPOINT")
        if child in parents:
            raise ValueError("MULTIPLE_PARENTS_OR_CLOSED_LOOP")
        parents[child] = parent
    for link in links:
        seen = set()
        while link in parents:
            if link in seen:
                raise ValueError("CLOSED_LOOP_UNSUPPORTED")
            seen.add(link)
            link = parents[link]
    roots = set(links) - parents.keys()
    if len(roots) != 1:
        raise ValueError("DISCONNECTED_ASSEMBLY")
    return next(iter(roots))


def validate_joint(joint):
    """Validate explicit SI joint geometry; no defaults for unknown capture data."""
    from math import isfinite, sqrt
    kind = joint.get("type")
    if kind not in ("fixed", "revolute", "prismatic"):
        raise ValueError("UNSUPPORTED_JOINT")
    if joint.get("units") != "SI" or joint.get("axis_frame") != "joint":
        raise ValueError("UNKNOWN_JOINT_CONVENTION")
    def vector(key):
        value = joint.get(key)
        if not isinstance(value, (list, tuple)) or len(value) != 3 or any(type(n) not in (int, float) or not isfinite(n) for n in value):
            raise ValueError("INVALID_" + key.upper())
        return value
    vector("origin_xyz_m")
    vector("origin_rpy_rad")
    if kind != "fixed":
        axis = vector("axis")
        if abs(sqrt(sum(n*n for n in axis)) - 1) > 1e-9:
            raise ValueError("AXIS_NOT_UNIT")
        limits = joint.get("limits", {})
        for key in ("lower", "upper", "effort", "velocity"):
            value = limits.get(key)
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError("INVALID_JOINT_LIMIT")
        if limits["lower"] > limits["upper"] or limits["effort"] <= 0 or limits["velocity"] <= 0:
            raise ValueError("INVALID_JOINT_LIMIT")
    return deepcopy(joint)


def resolved_component(component, *, resolution, snapshot, current):
    """Check adapter evidence for an exact occurrence; does not invoke SW or allow edits.

    resolution must be freshly produced by a trusted SW adapter, not model text.
    Opaque references are not compared across rebuilds: the adapter must resolve them.
    """
    map_candidates([component], [], target=None, snapshot=snapshot, current=current)
    required = ("owner_document", "instance_path", "configuration", "persistent_reference")
    if any(not isinstance(component.get(k), str) or not component[k].strip() for k in required):
        raise ValueError("MISSING_PERSISTENT_OCCURRENCE_IDENTITY")
    if resolution.get("method") != "IModelDocExtension.GetObjectByPersistReference3" or resolution.get("status") != "resolved" or resolution.get("object_type") != "IComponent2":
        raise ValueError("UNRESOLVED_COMPONENT_REFERENCE")
    if resolution.get("snapshot") != current or any(resolution.get(k) != component[k] for k in required):
        raise ValueError("REFERENCE_CONTEXT_OR_OCCURRENCE_MISMATCH")
    if resolution.get("component_id") != component["id"]:
        raise ValueError("REFERENCE_COMPONENT_MISMATCH")
    return deepcopy(component)


def capture_reference(extension, obj):
    """Capture opaque bytes with the owning document's native extension."""
    import base64
    raw = extension.GetPersistReference3(obj)
    if raw is None or isinstance(raw, (str, int, bool)):
        raise ValueError("PERSISTENT_REFERENCE_UNAVAILABLE")
    try:
        raw = bytes(raw)
    except (TypeError, ValueError):
        raise ValueError("INVALID_PERSISTENT_REFERENCE_BYTES") from None
    if not raw:
        raise ValueError("PERSISTENT_REFERENCE_UNAVAILABLE")
    return base64.b64encode(raw).decode("ascii")


def resolve_reference(extension, token, *, invoke=None):
    """Resolve without attaching/launching SW. Optional invoke supports offline fakes.

    Production path uses explicit pywin32 SAFEARRAY/UI1 and BYREF/I4; must be
    calibrated against the installed SW typelib before enabling automatic writes.
    """
    import base64
    if not isinstance(token, str) or not token:
        raise ValueError("INVALID_PERSISTENT_REFERENCE")
    raw = base64.b64decode(token, validate=True)
    if not raw:
        raise ValueError("INVALID_PERSISTENT_REFERENCE")
    if invoke is None:
        import pythoncom
        from win32com.client import VARIANT
        reference = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_UI1, list(raw))
        error = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        obj = extension.GetObjectByPersistReference3(reference, error)
        status = error.value
    else:
        obj, status = invoke(extension, raw)
    if type(status) is not int or status != 0 or obj is None:
        raise ValueError("PERSISTENT_REFERENCE_NOT_RESOLVED")
    return obj


def capture_component_mates(component, extension):
    """Read raw native mates without inventing a DOF solution or omitting failures."""
    result = []
    for mate in component.GetMates() or ():
        record = {"status": "unknown", "persistent_reference": None, "entities": []}
        try:
            record["persistent_reference"] = capture_reference(extension, mate)
            record["mate_type_raw"] = int(mate.Type)
            count = mate.GetMateEntityCount()
            if type(count) is not int or not 1 <= count <= 64:
                raise ValueError("INVALID_MATE_ENTITY_COUNT")
            for i in range(count):
                entity = mate.MateEntity(i)
                referenced = entity.ReferenceComponent
                record["entities"].append({
                    "component_reference": capture_reference(extension, referenced) if referenced is not None else None,
                    "reference_type_raw": int(entity.ReferenceType2),
                    "entity_params_raw": list(entity.EntityParams) if entity.EntityParams is not None else None,
                })
            record["status"] = "captured-not-solved"
        except Exception as error:
            # Preserve the mate and failure; in-place/unsupported mates are not dropped.
            record["error"] = type(error).__name__ + ": " + str(error)
        result.append(record)
    return result


def solve_axial_bundle(constraints, *, complete, frame, tolerance=1e-8):
    """Solve a narrow exact coaxial bundle from normalized captured geometry.

    Supported primitives: coaxial (4 constraints), axial_position (1),
    rotation_lock (1). No raw enum guessing, general rank solver or loop cutting.
    Adapter must establish full active bundle and one common owning assembly frame.
    """
    from math import isfinite, sqrt
    if complete is not True or not isinstance(frame, str) or not frame.strip():
        raise ValueError("INCOMPLETE_CONSTRAINT_BUNDLE")
    if type(tolerance) not in (int, float) or not isfinite(tolerance) or not 0 < tolerance < 0.01:
        raise ValueError("INVALID_GEOMETRIC_TOLERANCE")
    groups = {}
    for item in constraints:
        kind = item.get("kind")
        if kind not in ("coaxial", "axial_position", "rotation_lock") or kind in groups:
            raise ValueError("UNSUPPORTED_OR_REDUNDANT_CONSTRAINT")
        if item.get("frame") != frame or item.get("status") != "active-satisfied":
            raise ValueError("UNVERIFIED_CONSTRAINT_CONTEXT")
        groups[kind] = item
    if "coaxial" not in groups:
        raise ValueError("MISSING_COAXIAL_CONSTRAINT")
    def vec(value):
        if not isinstance(value, (tuple, list)) or len(value) != 3 or any(type(x) not in (int, float) or not isfinite(x) for x in value):
            raise ValueError("INVALID_CONSTRAINT_GEOMETRY")
        return value
    def axis(value):
        value = vec(value)
        if abs(sqrt(sum(x*x for x in value))-1) > tolerance:
            raise ValueError("INVALID_CONSTRAINT_AXIS")
        return value
    item = groups["coaxial"]
    a, b = axis(item.get("parent_axis")), axis(item.get("child_axis"))
    p, q = vec(item.get("parent_point_m")), vec(item.get("child_point_m"))
    dot = sum(x*y for x,y in zip(a,b))
    if abs(abs(dot)-1) > tolerance:
        raise ValueError("AXES_NOT_PARALLEL")
    d = [q[i]-p[i] for i in range(3)]
    along = sum(d[i]*a[i] for i in range(3))
    if sqrt(sum((d[i]-along*a[i])**2 for i in range(3))) > tolerance:
        raise ValueError("AXES_NOT_COINCIDENT")
    if "axial_position" in groups:
        lock = groups["axial_position"]
        normal = axis(lock.get("normal"))
        if abs(abs(sum(a[i]*normal[i] for i in range(3)))-1) > tolerance:
            raise ValueError("POSITION_CONSTRAINT_NOT_AXIAL")
        residual = lock.get("residual_m")
        if type(residual) not in (int, float) or not isfinite(residual) or abs(residual) > tolerance:
            raise ValueError("UNSATISFIED_AXIAL_POSITION")
    if "rotation_lock" in groups and groups["rotation_lock"].get("locked") is not True:
        raise ValueError("UNVERIFIED_ROTATION_LOCK")
    t, r = int("axial_position" not in groups), int("rotation_lock" not in groups)
    kind = classify_motion(translation_dof=t, rotation_dof=r, solver_status="solved", complete=True)
    return {"type": kind, "translation_dof": t, "rotation_dof": r, "axis": list(a), "point_m": list(p), "frame": frame, "provenance": "static-derived", "physical_validation": "not-run"}


def solve_captured_mates(records, *, parent_reference, child_reference, frame, complete, tolerance=1e-8):
    """Native enum/EntityParams -> narrow axial solution, with explicit capture context.

    Each record additionally needs owning_frame, active=True and lock_rotation
    (for concentric), obtained by the trusted capture caller from feature data.
    """
    from math import isfinite
    if not isinstance(parent_reference, str) or not parent_reference or not isinstance(child_reference, str) or not child_reference or parent_reference == child_reference:
        raise ValueError("INVALID_OCCURRENCE_PAIR")
    if type(tolerance) not in (int,float) or not isfinite(tolerance) or not 0 < tolerance < 0.01:
        raise ValueError("INVALID_GEOMETRIC_TOLERANCE")
    constraints = []
    for record in records:
        if record.get("status") != "captured-not-solved" or record.get("owning_frame") != frame or record.get("active") is not True:
            raise ValueError("UNKNOWN_MATE_STATE_OR_FRAME")
        entities = record.get("entities", [])
        if len(entities) != 2:
            raise ValueError("UNSUPPORTED_ENTITY_COUNT")
        by_ref = {e.get("component_reference"): e for e in entities}
        if set(by_ref) != {parent_reference, child_reference}:
            raise ValueError("MATE_OCCURRENCE_MISMATCH")
        a, b = by_ref[parent_reference], by_ref[child_reference]
        vectors = []
        for entity in (a,b):
            values = entity.get("entity_params_raw")
            if not isinstance(values, list) or len(values) < 6 or any(type(v) not in (int,float) or not isfinite(v) for v in values):
                raise ValueError("INVALID_MATE_PARAMETERS")
            if abs(sum(v*v for v in values[3:6])-1) > tolerance:
                raise ValueError("NONUNIT_NATIVE_ENTITY_DIRECTION")
            vectors.append(values)
        p, q = vectors
        common = {"frame": frame, "status": "active-satisfied"}
        kind = record.get("mate_type_raw")
        if type(kind) is not int:
            raise ValueError("INVALID_NATIVE_MATE_TYPE")
        if kind == 1 and a.get("reference_type_raw") == b.get("reference_type_raw") == 4:
            constraints.append(dict(common, kind="coaxial", parent_axis=p[3:6], child_axis=q[3:6], parent_point_m=p[:3], child_point_m=q[:3]))
            locked = record.get("lock_rotation")
            if type(locked) is not bool:
                raise ValueError("UNKNOWN_CONCENTRIC_ROTATION_LOCK")
            if locked:
                constraints.append(dict(common, kind="rotation_lock", locked=True))
        elif kind == 0 and a.get("reference_type_raw") == b.get("reference_type_raw") == 3:
            if abs(abs(sum(p[i]*q[i] for i in range(3,6)))-1) > tolerance:
                raise ValueError("COINCIDENT_PLANES_NOT_PARALLEL")
            constraints.append(dict(common, kind="axial_position", normal=p[3:6], residual_m=sum((q[i]-p[i])*p[i+3] for i in range(3))))
        else:
            raise ValueError("UNSUPPORTED_NATIVE_MATE")
    return solve_axial_bundle(constraints, complete=complete, frame=frame, tolerance=tolerance)


def capture_mate_features(model, *, owning_frame, configuration, sw_this_configuration=1):
    """Read this document's MateGroup features; never recurse into subassemblies.

    Owning frame is caller's source-snapshot ID for this exact document instance.
    Does not establish global completeness across flexible/nested assemblies.
    """
    from types import SimpleNamespace
    if not isinstance(owning_frame, str) or not owning_frame.strip():
        raise ValueError("OWNING_FRAME_REQUIRED")
    if model.ConfigurationManager.ActiveConfiguration.Name != configuration:
        raise ValueError("CONFIGURATION_MISMATCH")
    extension = model.Extension
    result = []
    feature = model.FirstFeature()
    visited = set()
    while feature is not None:
        token = capture_reference(extension, feature)
        if token in visited:
            raise ValueError("CYCLIC_FEATURE_TRAVERSAL")
        visited.add(token)
        if feature.GetTypeName2() == "MateGroup":
            sub = feature.GetFirstSubFeature()
            sub_seen = set()
            while sub is not None:
                sub_token = capture_reference(extension, sub)
                if sub_token in sub_seen:
                    raise ValueError("CYCLIC_MATE_TRAVERSAL")
                sub_seen.add(sub_token)
                record = {"status": "unknown", "feature_reference": sub_token, "owning_frame": owning_frame, "configuration": configuration, "active": None}
                try:
                    states = sub.IsSuppressed2(sw_this_configuration, None)
                    if not isinstance(states, (tuple,list)) or len(states) != 1 or type(states[0]) is not bool:
                        raise ValueError("UNKNOWN_SUPPRESSION_STATE")
                    record["active"] = not states[0]
                    mate = sub.GetSpecificFeature2()
                    raw = capture_component_mates(SimpleNamespace(GetMates=lambda: [mate]), extension)[0]
                    record.update(raw)
                    if record.get("mate_type_raw") == 1:
                        locked = sub.GetDefinition().LockRotation
                        if type(locked) is not bool:
                            raise ValueError("UNKNOWN_LOCK_ROTATION")
                        record["lock_rotation"] = locked
                except Exception as error:
                    record["status"] = "unknown"
                    record["error"] = type(error).__name__ + ": " + str(error)
                result.append(record)
                sub = sub.GetNextSubFeature()
        feature = feature.GetNextFeature()
    if model.ConfigurationManager.ActiveConfiguration.Name != configuration:
        raise ValueError("CONFIGURATION_CHANGED_DURING_CAPTURE")
    return result


def joint_from_solution(solution, *, joint_id, parent, child, parent_rotation, parent_translation_m, owning_frame, limits=None):
    """Express an assembly-frame axial solution in a parent-aligned joint frame.

    parent_rotation/translation map parent link -> solution owning frame. Child
    link geometry must subsequently be expressed in this selected joint frame.
    """
    from math import isfinite
    if not isinstance(owning_frame, str) or not owning_frame.strip() or solution.get("frame") != owning_frame:
        raise ValueError("SOLUTION_PARENT_FRAME_MISMATCH")
    if any(not isinstance(v, str) or not v.strip() for v in (joint_id,parent,child)) or parent == child:
        raise ValueError("INVALID_JOINT_ENDPOINT")
    r = parent_rotation
    if not isinstance(r,(list,tuple)) or len(r)!=3 or any(not isinstance(row,(list,tuple)) or len(row)!=3 for row in r):
        raise ValueError("INVALID_PARENT_ROTATION")
    values = [x for row in r for x in row] + list(parent_translation_m)
    if len(parent_translation_m)!=3 or any(type(x) not in (int,float) or not isfinite(x) for x in values):
        raise ValueError("INVALID_PARENT_TRANSFORM")
    det = r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])-r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])+r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0])
    if abs(det-1)>1e-9 or any(abs(sum(r[k][i]*r[k][j] for k in range(3))-(i==j))>1e-9 for i in range(3) for j in range(3)):
        raise ValueError("INVALID_PARENT_ROTATION")
    axis, point = solution["axis"], solution["point_m"]
    if any(not isinstance(v,(list,tuple)) or len(v)!=3 or any(type(x) not in (int,float) or not isfinite(x) for x in v) for v in (axis,point)):
        raise ValueError("INVALID_SOLUTION_GEOMETRY")
    local_axis = [sum(r[k][i]*axis[k] for k in range(3)) for i in range(3)]
    local_point = [sum(r[k][i]*(point[k]-parent_translation_m[k]) for k in range(3)) for i in range(3)]
    joint = dict(id=joint_id,parent=parent,child=child,type=solution["type"],units="SI",axis_frame="joint",axis=local_axis,origin_xyz_m=local_point,origin_rpy_rad=[0,0,0])
    if limits is not None:
        joint["limits"] = deepcopy(limits)
    return validate_joint(joint)
