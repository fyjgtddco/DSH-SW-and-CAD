"""Fake object contract tests; never import or connect to SW COM."""
from pathlib import Path
import hashlib
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from webots_mcp.sw_export import (capture_part, part_bundle, rigid_group_bundle,
                                  capture_assembly, capture_context_component)


class ExportChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)/'part.SLDPRT'; path.write_bytes(b'fixture')
        self.source = {'path': str(path), 'sha256': hashlib.sha256(b'fixture').hexdigest(), 'configuration': 'Default'}
        mass = NS(UseSystemUnits=True, Mass=2., CenterOfMass=(.01,.02,.03),
                  GetMomentOfInertia=lambda where: (5,.2,.3,.2,6,.4,.3,.4,7), UserAssigned=False)
        self.model = NS(GetType=lambda: 1, GetPathName=lambda: str(path), GetSaveFlag=lambda: False,
                        ConfigurationManager=NS(ActiveConfiguration=NS(Name='Default')),
                        SelectionManager=NS(GetSelectedObjectCount2=lambda mark: 0),
                        Extension=NS(NeedsRebuild2=False, CreateMassProperty=lambda: mass),
                        GetBodies2=lambda kind, visible: [object()] if kind == 0 else [],
                        GetTessTriangleCount=lambda: 1,
                        GetTessTriangles=lambda no_conversion: (0,0,0,.1,0,0,0,.2,0))

    def test_capture_and_bundle_no_second_transform(self):
        snap = capture_part(self.model, expected_source=self.source, sw_version='fake', provenance='fixture')
        result = part_bundle(snap)
        root = ET.fromstring(result['robot.urdf'])
        self.assertEqual(root.find('link/inertial/origin').get('xyz'), '0.01 0.02 0.029999999999999999')
        self.assertEqual(float(root.find('link/inertial/inertia').get('ixy')), -.2)
        self.assertEqual(root.find('link/visual/geometry/mesh').get('scale'), '1 1 1')
        self.assertIsNone(root.find('link/collision'))
        self.assertIn('v 0.10000000000000001 0 0', result['visual.obj'])
        self.assertEqual(result['conversion_data']['physical_validation'], 'not_run')
        self.assertEqual(snap['provenance'], 'fixture')

    def test_refuse_dirty_selected_configuration_and_drift(self):
        for field, value in [('GetSaveFlag', lambda: True), ('GetType', lambda: 2)]:
            old = getattr(self.model, field); setattr(self.model, field, value)
            with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')
            setattr(self.model, field, old)
        self.model.SelectionManager.GetSelectedObjectCount2 = lambda mark: 1
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')
        self.model.SelectionManager.GetSelectedObjectCount2 = lambda mark: 0
        self.model.ConfigurationManager.ActiveConfiguration.Name = 'Other'
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')
        self.model.ConfigurationManager.ActiveConfiguration.Name = 'Default'
        Path(self.source['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')

    def test_mesh_missing_hidden_surface_and_budget_rejected(self):
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake', max_triangles=0)
        self.model.GetTessTriangles = lambda flag: ()
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')
        self.model.GetBodies2 = lambda kind, visible: [object()] if not visible else []
        with self.assertRaises(ValueError): capture_part(self.model, expected_source=self.source, sw_version='fake')

    def test_unknown_part_body_queries_rejected(self):
        for kind in ('surface','solid','visible'):
            for unknown in (None,False,0):
                def bodies(body_kind, visible, kind=kind, unknown=unknown):
                    key = 'surface' if body_kind == 1 else ('visible' if visible else 'solid')
                    return unknown if key == kind else ([] if body_kind == 1 else [object()])
                self.model.GetBodies2 = bodies
                with self.assertRaisesRegex(ValueError,'UNKNOWN_PART_BODY_QUERY'):
                    capture_part(self.model,expected_source=self.source,sw_version='fixture')

    def test_rigid_group_applies_instance_pose_once(self):
        snap = capture_part(self.model, expected_source=self.source, sw_version='fake', provenance='fixture')
        r = [[0,-1,0],[1,0,0],[0,0,1]]
        members = [{'id':'one', 'capture':snap, 'rotation':r, 'translation_m':[1,2,3]},
                   {'id':'two', 'capture':snap, 'rotation':r, 'translation_m':[-1,-2,-3]}]
        result = rigid_group_bundle(members, frame='assembly-root')
        body = result['manifest_data']['rigid_bodies'][0]
        self.assertEqual(body['component_ids'], ['one', 'two'])
        self.assertEqual(body['mass_kg'], 4)
        for a,b in zip(body['com_m'], [-.02,.01,.03]): self.assertAlmostEqual(a,b)
        self.assertIn('v 1 2 3', result['visual.obj'])
        self.assertEqual(len(result['manifest_data']['components']), 2)
        self.assertEqual(result['conversion_data']['mappings'][0]['component_ids'], ['one','two'])
        with self.assertRaises(ValueError): rigid_group_bundle(members+members, frame='assembly-root')

    def test_assembly_occurrences_not_source_file_deduplicated(self):
        root = NS(**vars(self.model))
        root.GetType = lambda: 2
        transform = [0,1,0,-1,0,0,0,0,1,1,2,3,1,0,0,0]
        component = lambda name: NS(Name2=name, ReferencedConfiguration='Default',
            GetPathName=lambda:self.source['path'], GetSuppression2=lambda:2,
            GetModelDoc2=lambda:self.model, Transform2=NS(ArrayData=transform))
        first, second = component('part-1'), component('part-2')
        root.GetComponents = lambda top: [first,second]
        capture = capture_assembly(root, expected_source=self.source, sw_version='fake', provenance='fixture')
        self.assertEqual(len(capture['components']), 2)
        self.assertNotEqual(capture['components'][0]['id'], capture['components'][1]['id'])
        self.assertEqual(capture['components'][0]['translation_m'], [1,2,3])
        self.assertFalse(capture['ready_for_physics'])
        self.assertIsNone(capture['components'][0]['persistent_reference'])
        second.GetSuppression2 = lambda: 1
        capture = capture_assembly(root, expected_source=self.source, sw_version='fake', provenance='fixture')
        self.assertEqual(capture['components'][1]['status'], 'unsupported')
        self.assertIn('COMPONENT_NOT_RESOLVED', capture['components'][1]['error'])

    def test_context_bodies_mass_is_already_assembly_frame(self):
        root = NS(**vars(self.model)); root.GetType = lambda: 2
        mp = root.Extension.CreateMassProperty()
        seen=[]; mp.AddBodies=lambda bodies: seen.extend(bodies) or True
        mp.CenterOfMass=(10,20,30)
        body=NS(GetFaces=lambda:[NS(GetTessTriangles=lambda flag:(0,0,0,.1,0,0,0,.2,0))])
        native=NS(Name2='part-1',ReferencedConfiguration='Default',GetPathName=lambda:self.source['path'],
                  GetSuppression2=lambda:2,GetModelDoc2=lambda:self.model,
                  Transform2=NS(ArrayData=[1,0,0,0,1,0,0,0,1,1,2,3,1,0,0,0]))
        invoke=lambda comp,kind: ([body],[1]) if kind==0 else ([],[])
        snap=capture_context_component(root,native,expected_source=self.source,
            expected_component_source=self.source,sw_version='fake',provenance='fixture',invoke_bodies=invoke)
        self.assertEqual(snap['physics']['com_m'],[10,20,30])
        self.assertEqual(snap['physics']['frame'],'root-assembly')
        self.assertEqual(snap['mesh_frame'],'unknown')
        self.assertEqual(seen,[body])
        self.assertFalse(snap['ready_for_physics'])
        with self.assertRaises(ValueError):
            capture_context_component(root,native,expected_source=self.source,
                expected_component_source=self.source,sw_version='fake',invoke_bodies=lambda comp,kind:([body],[1]))
        for unknown in (None, False, 0):
            with self.assertRaises(ValueError):
                capture_context_component(root,native,expected_source=self.source,
                    expected_component_source=self.source,sw_version='fake',
                    invoke_bodies=lambda comp,kind:([body],[1]) if kind==0 else (unknown,[]))
        self.model.GetSaveFlag=lambda:True
        with self.assertRaises(ValueError):
            capture_context_component(root,native,expected_source=self.source,
                expected_component_source=self.source,sw_version='fake',invoke_bodies=invoke)

    def test_unknown_native_dirty_or_rebuild_status_is_not_clean(self):
        self.model.GetSaveFlag=lambda:None
        with self.assertRaises(ValueError): capture_part(self.model,expected_source=self.source,sw_version='fake')
        self.model.GetSaveFlag=lambda:False
        self.model.Extension.NeedsRebuild2=None
        with self.assertRaises(ValueError): capture_part(self.model,expected_source=self.source,sw_version='fake')


if __name__ == '__main__': unittest.main()
