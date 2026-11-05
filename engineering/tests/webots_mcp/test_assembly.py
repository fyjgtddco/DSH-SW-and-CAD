import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from webots_mcp.assembly import map_candidates, component_targets

class SnapshotMappingTests(unittest.TestCase):
    def test_repeated_and_merged_instances_and_stale_snapshot(self):
        components = [{"id": "a", "source_file": "same", "persistent_reference": None}, {"id": "b", "source_file": "same", "persistent_reference": None}]
        mappings = [{"target": "body", "component_ids": ["a", "b"]}]
        context = {"source_revision": "synthetic", "configuration": "default", "generation": 1}
        args = dict(snapshot=context, current=context)
        self.assertEqual([c["id"] for c in map_candidates(components, mappings, target="body", **args)], ["a", "b"])
        self.assertEqual(component_targets(components, mappings, component_id="b", **args), ["body"])
        target = {"kind": "urdf_link", "id": "part"}
        native_mapping = [{"target": target, "component_ids": ["a", "b"]}]
        self.assertEqual(len(map_candidates(components, native_mapping, target=target, **args)), 2)
        self.assertEqual(component_targets(components, native_mapping, component_id="a", **args), [target])
        for field in context:
            with self.assertRaises(ValueError):
                map_candidates(components, mappings, target="body", snapshot=context, current={**context, field: None})
        with self.assertRaises(ValueError):
            map_candidates(components + [components[0]], mappings, target="body", **args)


class MotionBoundaryTests(unittest.TestCase):
    def test_motion_and_loop_boundaries(self):
        from webots_mcp.assembly import classify_motion, validate_tree
        for dofs, kind in [((0, 0), "fixed"), ((0, 1), "revolute"), ((1, 0), "prismatic")]:
            self.assertEqual(classify_motion(translation_dof=dofs[0], rotation_dof=dofs[1], solver_status="solved", complete=True), kind)
        for t, r, status, complete in [(1, 1, "solved", True), (0, 1, "overconstrained", True), (0, 1, "solved", False)]:
            with self.assertRaises(ValueError):
                classify_motion(translation_dof=t, rotation_dof=r, solver_status=status, complete=complete)
        edge = {"id": "j", "parent": "a", "child": "b"}
        self.assertEqual(validate_tree(["a", "b"], [edge]), "a")
        for links, joints in [(["a", "b", "c"], [edge]), (["a", "b"], [edge, {"id": "k", "parent": "b", "child": "a"}]), (["a", "b"], [edge, {**edge, "id": "k"}])]:
            with self.assertRaises(ValueError):
                validate_tree(links, joints)

class JointFixtureTests(unittest.TestCase):
    def test_existing_joint_fixtures_and_bad_axis(self):
        import xml.etree.ElementTree as ET
        from webots_mcp.assembly import validate_joint
        for name in ("single_revolute", "single_prismatic"):
            element = ET.parse(Path(__file__).parent / "fixtures" / "valid" / (name + ".urdf")).getroot().find("joint")
            joint = {"type": element.get("type"), "units": "SI", "axis_frame": "joint", "axis": list(map(float, element.find("axis").get("xyz").split())), "origin_xyz_m": list(map(float, element.find("origin").get("xyz").split())), "origin_rpy_rad": list(map(float, element.find("origin").get("rpy").split())), "limits": {k: float(v) for k, v in element.find("limit").attrib.items()}}
            self.assertEqual(validate_joint(joint), joint)
            for bad in ([0, 0, 0], [float("nan"), 0, 1], [0, 0, 2]):
                with self.assertRaises(ValueError):
                    validate_joint({**joint, "axis": bad})

class IdentityGuardTests(unittest.TestCase):
    def test_context_and_resolution_guards(self):
        from webots_mcp.assembly import resolved_component
        context = dict(source_revision="r", configuration="c", generation=1)
        c = dict(id="a", owner_document="assembly", instance_path="a/part-1", configuration="c", persistent_reference="opaque-captured-token")
        resolution = dict(c, component_id="a", snapshot=context, method="IModelDocExtension.GetObjectByPersistReference3", status="resolved", object_type="IComponent2")
        self.assertEqual(resolved_component(c, resolution=resolution, snapshot=context, current=context), c)
        for key, value in [("source_revision", ""), ("configuration", " "), ("generation", True)]:
            with self.assertRaises(ValueError):
                map_candidates([c], [], target=None, snapshot={**context, key: value}, current=context)
        with self.assertRaises(ValueError):
            map_candidates([c], [{"component_ids": ["a"]}], target=None, snapshot=context, current=context)
        for key, value in [("status", "deleted"), ("instance_path", "a/part-2"), ("owner_document", "other"), ("component_id", "b")]:
            with self.assertRaises(ValueError):
                resolved_component(c, resolution={**resolution, key: value}, snapshot=context, current=context)

class NativeAdapterTests(unittest.TestCase):
    def test_reference_capture_resolve_and_mate_failure_retention(self):
        from types import SimpleNamespace
        from webots_mcp.assembly import capture_reference, resolve_reference, capture_component_mates
        obj = object()
        extension = SimpleNamespace(GetPersistReference3=lambda value: bytes([0, 255, 2]))
        token = capture_reference(extension, obj)
        self.assertIs(resolve_reference(extension, token, invoke=lambda ext, raw: (obj, 0)), obj)
        for status in (1, 2, 4, 6, True):
            with self.assertRaises(ValueError):
                resolve_reference(extension, token, invoke=lambda ext, raw: (obj, status))
        entity = SimpleNamespace(ReferenceComponent=obj, ReferenceType2=1, EntityParams=[0, 0, 0])
        mate = SimpleNamespace(Type=0, GetMateEntityCount=lambda: 1, MateEntity=lambda i: entity)
        component = SimpleNamespace(GetMates=lambda: [mate, object()])
        records = capture_component_mates(component, extension)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["status"], "captured-not-solved")
        self.assertIn("error", records[1])

class AxialSolveTests(unittest.TestCase):
    def test_supported_bundles_and_geometric_counterexamples(self):
        from webots_mcp.assembly import solve_axial_bundle
        base = dict(kind="coaxial", frame="assembly", status="active-satisfied", parent_axis=[0,0,1], child_axis=[0,0,-1], parent_point_m=[1,2,0], child_point_m=[1,2,3])
        position = dict(kind="axial_position", frame="assembly", status="active-satisfied", normal=[0,0,1], residual_m=0)
        lock = dict(kind="rotation_lock", frame="assembly", status="active-satisfied", locked=True)
        for bundle, kind in [([base,position], "revolute"), ([base,lock], "prismatic"), ([base,position,lock], "fixed")]:
            self.assertEqual(solve_axial_bundle(bundle, complete=True, frame="assembly")["type"], kind)
        for bundle in ([base], [base,base], [{**base,"child_point_m":[2,2,3]},position], [base,{**position,"normal":[1,0,0]}], [base,{**lock,"locked":False}]):
            with self.assertRaises(ValueError):
                solve_axial_bundle(bundle, complete=True, frame="assembly")

class RawMateSolveTests(unittest.TestCase):
    def test_native_enum_geometry_to_joint(self):
        from webots_mcp.assembly import solve_captured_mates
        def record(kind, entity_type, lock=False):
            return dict(status="captured-not-solved", owning_frame="root", active=True, mate_type_raw=kind, lock_rotation=lock, entities=[dict(component_reference=r, reference_type_raw=entity_type, entity_params_raw=[1,2,0,0,0,1,0.01]) for r in ("p","c")])
        args = dict(parent_reference="p", child_reference="c", frame="root", complete=True)
        self.assertEqual(solve_captured_mates([record(1,4),record(0,3)], **args)["type"], "revolute")
        self.assertEqual(solve_captured_mates([record(1,4,True)], **args)["type"], "prismatic")
        self.assertEqual(solve_captured_mates([record(1,4,True),record(0,3)], **args)["type"], "fixed")
        nonunit = record(0,3)
        nonunit["entities"][1]["entity_params_raw"][3:6] = [1,0,1]
        with self.assertRaises(ValueError):
            solve_captured_mates([record(1,4), nonunit], **args)
        for bad in [record(5,3), {**record(1,4),"active":False}, {**record(1,4),"lock_rotation":None}]:
            with self.assertRaises(ValueError):
                solve_captured_mates([bad], **args)

class FeatureCaptureTests(unittest.TestCase):
    def test_native_feature_metadata(self):
        from types import SimpleNamespace as NS
        from webots_mcp.assembly import capture_mate_features
        mate = NS(Type=1, GetMateEntityCount=lambda: 1, MateEntity=lambda i: NS(ReferenceComponent=object(), ReferenceType2=4, EntityParams=[0,0,0,0,0,1,1]))
        sub = NS(IsSuppressed2=lambda opt,names: [False], GetSpecificFeature2=lambda: mate, GetDefinition=lambda: NS(LockRotation=True), GetNextSubFeature=lambda: None)
        group = NS(GetTypeName2=lambda: "MateGroup", GetFirstSubFeature=lambda: sub, GetNextFeature=lambda: None)
        model = NS(Extension=NS(GetPersistReference3=lambda obj: id(obj).to_bytes(8,"little")), FirstFeature=lambda: group, ConfigurationManager=NS(ActiveConfiguration=NS(Name="default")))
        records = capture_mate_features(model, owning_frame="root", configuration="default")
        self.assertTrue(records[0]["active"])
        self.assertTrue(records[0]["lock_rotation"])
        self.assertEqual(records[0]["owning_frame"], "root")
        sub.IsSuppressed2 = lambda opt,names: None
        self.assertEqual(capture_mate_features(model, owning_frame="root", configuration="default")[0]["status"], "unknown")

class JointFrameTests(unittest.TestCase):
    def test_rotated_parent_joint_frame(self):
        from webots_mcp.assembly import joint_from_solution
        s = dict(type="revolute",axis=[0,1,0],point_m=[1,3,0],frame="assembly")
        joint = joint_from_solution(s,joint_id="j",parent="p",child="c",parent_rotation=[[0,-1,0],[1,0,0],[0,0,1]],parent_translation_m=[1,2,0],owning_frame="assembly",limits=dict(lower=-1,upper=1,effort=1,velocity=1))
        self.assertEqual(joint["axis"], [1,0,0])
        self.assertEqual(joint["origin_xyz_m"], [1,0,0])

if __name__ == "__main__":
    unittest.main()
