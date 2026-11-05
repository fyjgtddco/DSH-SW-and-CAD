"""A01/A02 source-only seam. Before I01 set WEBOTS_A02_TOOLS to A02/tools.

Only Python package search is extended; neither checkout is copied or merged.
"""
import copy
import os
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import webots_mcp
if os.environ.get('WEBOTS_A02_TOOLS'):
    webots_mcp.__path__.append(str(Path(os.environ['WEBOTS_A02_TOOLS'])/'webots_mcp'))
from webots_mcp.assembly import joint_from_solution, map_candidates
from webots_mcp.physics import mass_properties, IDENTITY
from webots_mcp.sw_export import rigid_group_bundle, jointed_bundle, reframe_component, capture_assembly


class JointedChecks(unittest.TestCase):
    def capture(self):
        return {'physics':mass_properties(1,[.01,.02,.03],[[2,0,0],[0,3,0],[0,0,4]],
                    length_unit='m',mass_unit='kg',frame='part-local'),
                'vertices_m':[0,0,0,.1,0,0,0,.2,0], 'source':{'sha256':'fixture'},
                'sw_version':'fixture','provenance':'fixture','raw_mass_properties':{},'methods':{}}

    def links(self):
        members=[]
        for name, position in [('base',[0,0,0]),('moving',[1,2,3])]:
            component={'id':name+'-occ','capture':self.capture(),'rotation':IDENTITY,'translation_m':position,'transform_frame':'assembly-root'}
            local=reframe_component(component, link_rotation=IDENTITY, link_translation_m=position,owning_frame='assembly-root',link_frame='link:'+name)
            members.append({'id':name,'bundle':rigid_group_bundle([local],frame='link:'+name)})
        return members

    def joint(self, kind='revolute'):
        solution={'type':kind,'axis':[0,0,1],'point_m':[1,2,3],'frame':'assembly-root'}
        return joint_from_solution(solution,joint_id='j',parent='base',child='moving',
            parent_rotation=IDENTITY,parent_translation_m=[0,0,0],owning_frame='assembly-root',
            limits={'lower':-1,'upper':2,'effort':3,'velocity':4})

    def test_native_snapshot_references_share_capture_and_reject_changed_set(self):
        from types import SimpleNamespace as NS
        from test_sw_export import ExportChecks
        fixture = ExportChecks(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        root = NS(**vars(fixture.model)); root.GetType = lambda: 2
        transform = [1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0]
        def comp(token):
            return NS(token=token, Name2='same-display-name', ReferencedConfiguration='Default',
                GetPathName=lambda:fixture.source['path'], GetSuppression2=lambda:2,
                GetModelDoc2=lambda:fixture.model, Transform2=NS(ArrayData=transform))
        first, second = comp(b'first'), comp(b'second')
        root.Extension = NS(**vars(root.Extension))
        root.Extension.GetPersistReference3 = lambda obj: obj.token
        root.FirstFeature = lambda: None
        root.GetComponents = lambda top: [first,second]
        args = dict(expected_source=fixture.source, sw_version='fixture', generation=7)
        captured = capture_assembly(root, **args)
        self.assertEqual(captured['snapshot_context']['generation'],7)
        self.assertEqual(captured['snapshot_context']['configuration'],'Default')
        refs = [c['persistent_reference'] for c in captured['components']]
        self.assertEqual(refs,['Zmlyc3Q=','c2Vjb25k'])
        self.assertEqual(captured['components'][0]['owner_document'],fixture.source['path'])
        self.assertEqual(captured['components'][0]['owner_configuration'],'Default')
        self.assertFalse(captured['mates_complete'])
        self.assertFalse(captured['ready_for_physics'])
        self.assertEqual(captured['root_mates'],[])
        bundle=rigid_group_bundle(captured['components'],frame='root-assembly')
        preserved=bundle['manifest_data']['components'][0]
        self.assertEqual(preserved['persistent_reference'],refs[0])
        self.assertEqual(preserved['owner_document'],fixture.source['path'])
        self.assertEqual(preserved['snapshot_context'],captured['snapshot_context'])
        self.assertEqual(preserved['identity_status'],'native-reference-unresolved')
        calls = []
        def changed(top):
            calls.append(top)
            return [first,second] if len(calls)==1 else [first]
        root.GetComponents = changed
        with self.assertRaisesRegex(ValueError,'COMPONENT_SET_CHANGED'):
            capture_assembly(root, **args)
        root.GetComponents = lambda top: [first,second]
        sub = NS(token=b'mate-feature', IsSuppressed2=lambda option, configs:[False],
                 GetSpecificFeature2=lambda:None, GetNextSubFeature=lambda:None)
        group = NS(token=b'mate-group', GetTypeName2=lambda:'MateGroup',
                   GetFirstSubFeature=lambda:sub, GetNextFeature=lambda:None)
        root.FirstFeature = lambda:group
        unknown = capture_assembly(root, **args)
        self.assertEqual(unknown['root_mates'][0]['status'],'unknown')
        self.assertFalse(unknown['mates_complete'])
        calls.clear()
        def changed_mates():
            calls.append(True)
            return group if len(calls)==1 else None
        root.FirstFeature = changed_mates
        with self.assertRaisesRegex(ValueError,'MATES_CHANGED_DURING_CAPTURE'):
            capture_assembly(root, **args)
        root.FirstFeature = lambda:None
        root.GetComponents = lambda top: [first,first]
        with self.assertRaisesRegex(ValueError,'DUPLICATE_COMPONENT_REFERENCE'):
            capture_assembly(root, **args)
        with self.assertRaisesRegex(ValueError,'INVALID_GENERATION'):
            capture_assembly(root, **dict(args,generation=True))

    def test_context_mass_bound_to_same_occurrence_reference(self):
        from types import SimpleNamespace as NS
        from test_sw_export import ExportChecks
        fixture = ExportChecks(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        root = NS(**vars(fixture.model)); root.GetType = lambda:2
        root.Extension = NS(**vars(root.Extension))
        root.Extension.GetPersistReference3 = lambda obj:obj.token
        root.FirstFeature = lambda:None
        part = fixture.model
        # Assembly uses Default even if the standalone part has another active configuration.
        part.ConfigurationManager = NS(ActiveConfiguration=NS(Name='Other'))
        part.GetTessTriangles = lambda flag: (_ for _ in ()).throw(AssertionError('standalone fallback'))
        native = NS(token=b'occ', Name2='part-1',ReferencedConfiguration='Default',
            GetPathName=lambda:fixture.source['path'], GetSuppression2=lambda:2,
            GetModelDoc2=lambda:part,
            Transform2=NS(ArrayData=[1,0,0,0,1,0,0,0,1,1,2,3,1,0,0,0]))
        root.GetComponents = lambda top:[native]
        body = NS(GetFaces=lambda:[NS(GetTessTriangles=lambda flag:(0,0,0,.1,0,0,0,.2,0))])
        mp = root.Extension.CreateMassProperty(); mp.CenterOfMass=(10,20,30)
        mp.AddBodies = lambda bodies:bodies == (body,)
        calls=[]
        def invoke(comp, kind):
            self.assertIs(comp,native); calls.append(kind)
            return ([body],[1]) if kind==0 else ([],[])
        args=dict(expected_source=fixture.source,sw_version='fixture',generation=1,
                  capture_mode='context',invoke_bodies=invoke)
        captured=capture_assembly(root,**args)
        record=captured['components'][0]
        self.assertEqual(record['status'],'captured-context-mesh-frame-unknown')
        self.assertEqual(record['persistent_reference'],'b2Nj')
        self.assertEqual(record['configuration'],'Default')
        self.assertEqual(record['capture']['physics']['com_m'],[10,20,30])
        self.assertEqual(record['capture']['physics']['frame'],'root-assembly')
        self.assertNotIn('vertices_m',record['capture'])
        self.assertEqual(calls,[0,1])
        failed=capture_assembly(root,**dict(args,invoke_bodies=lambda comp,kind: (None,None)))
        self.assertEqual(failed['components'][0]['status'],'unsupported')
        self.assertEqual(failed['components'][0]['persistent_reference'],'b2Nj')
        self.assertNotIn('capture',failed['components'][0])
        self.assertFalse(captured['ready_for_physics'])
        with self.assertRaises(ValueError):
            rigid_group_bundle([record],frame='root-assembly')
        with self.assertRaisesRegex(ValueError,'CONTEXT_CAPTURE_REQUIRES_GENERATION'):
            capture_assembly(root,**dict(args,generation=None))

    def test_root_context_geometry_and_mass_reexpressed_once(self):
        root_capture=self.capture()
        root_capture['physics']=mass_properties(2,[1,2,3],[[2,0,0],[0,3,0],[0,0,4]],
            length_unit='m',mass_unit='kg',frame='root-assembly')
        root_capture['vertices_raw_system_units']=[1,2,3,1,3,3,0,2,3]
        del root_capture['vertices_m']
        root_capture['mesh_frame']='root-assembly'
        # Link->root is Rz(90 degrees), t=(1,2,3); supplied inverse maps root->link.
        member={'id':'context','capture':root_capture,'rotation':[[0,1,0],[-1,0,0],[0,0,1]],
                'translation_m':[-2,1,-3],'transform_frame':'link:moving'}
        bundle=rigid_group_bundle([member],frame='link:moving',source_frame='root-assembly')
        body=bundle['manifest_data']['rigid_bodies'][0]
        self.assertEqual(body['com_m'],[0,0,0])
        self.assertEqual(body['inertia_kg_m2'],[[3,0,0],[0,2,0],[0,0,4]])
        self.assertEqual(bundle['vertices_m'],[0,0,0,1,0,0,0,1,0])
        for unknown in ('unknown','part-local'):
            root_capture['mesh_frame']=unknown
            with self.assertRaisesRegex(ValueError,'CONTEXT_MESH_FRAME_UNPROVEN'):
                rigid_group_bundle([member],frame='link:moving',source_frame='root-assembly')

    def test_actual_a02_joints_and_mapping_round_trip(self):
        for kind in ('revolute','prismatic'):
            bundle=jointed_bundle(self.links(),[self.joint(kind)])
            root=ET.fromstring(bundle['robot.urdf'])
            self.assertEqual(root.find('joint').get('type'),kind)
            self.assertEqual(root.find('joint/origin').get('xyz'),'1 2 3')
            self.assertEqual(root.find('joint/axis').get('xyz'),'0 0 1')
            self.assertEqual(len(root.findall('link')),2)
            self.assertIn('meshes/link_1.obj',bundle)
            self.assertIn('v 0 0 0',bundle['meshes/link_1.obj'])
            manifest=bundle['manifest_data']; snapshot={'source_revision':'fixture','configuration':'Default','generation':1}
            found=map_candidates(manifest['components'],bundle['conversion_data']['mappings'],
                target={'kind':'urdf_link','id':'moving'},snapshot=snapshot,current=snapshot)
            self.assertEqual([c['id'] for c in found],['moving-occ'])
            self.assertEqual(bundle['conversion_data']['physical_validation'],'not_run')
            self.assertIn({'name':'SW','version':'fixture'},bundle['conversion_data']['tools'])

    def test_frame_duplicate_members_and_cycle_fail_closed(self):
        links=self.links()
        links[1]['bundle']['manifest_data']['rigid_bodies'][0]['frame']='assembly-root'
        with self.assertRaises(ValueError): jointed_bundle(links,[self.joint()])
        links=self.links(); links[1]['bundle']=copy.deepcopy(links[0]['bundle'])
        links[1]['bundle']['manifest_data']['rigid_bodies'][0]['frame']='link:moving'
        with self.assertRaises(ValueError): jointed_bundle(links,[self.joint()])
        with self.assertRaises(ValueError): jointed_bundle(self.links(),[self.joint(),dict(self.joint(),id='back',parent='moving',child='base')])

    def test_reframe_rotated_parent_not_second_scale(self):
        r=[[0,-1,0],[1,0,0],[0,0,1]]
        component={'id':'x','capture':self.capture(),'rotation':r,'translation_m':[1,2,3],'transform_frame':'assembly-root'}
        local=reframe_component(component,link_rotation=r,link_translation_m=[1,2,3],owning_frame='assembly-root',link_frame='link:x')
        self.assertEqual(local['rotation'],[list(row) for row in IDENTITY])
        self.assertEqual(local['translation_m'],[0,0,0])
        self.assertEqual(local['transform_frame'],'link:x')
        self.assertEqual(local['source_transform']['frame'],'assembly-root')
        with self.assertRaises(ValueError):
            reframe_component(local,link_rotation=r,link_translation_m=[1,2,3],owning_frame='assembly-root',link_frame='link:x')


if __name__=='__main__': unittest.main()
