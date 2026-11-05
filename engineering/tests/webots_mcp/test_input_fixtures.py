"""Offline fixture checks only. Run: python engineering/tests/webots_mcp/test_input_fixtures.py

This is a fixture guard/oracle, not the production importer or a physics validator.
No CAD, converter, Webots, DSH, network, or third-party module is invoked.
"""
from pathlib import Path
import copy
import json
import math
import re
import unittest
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent / "fixtures"


class InputError(ValueError):
    pass


def inspect_input(data):
    """Check this corpus's static boundaries; leave units/frames exactly as supplied."""
    source = data["source"]
    units = source.get("units", {})
    if any(k not in units for k in ("length", "mass", "angle", "time", "inertia")):
        raise InputError("MISSING_UNITS")
    if not source.get("coordinate_frame"):
        raise InputError("MISSING_COORDINATE_FRAME")
    components = data["components"]
    ids = [c["id"] for c in components]
    if any(not id for id in ids) or len(set(ids)) != len(ids):
        raise InputError("DUPLICATE_COMPONENT_ID")
    if source["kind"] == "analytic_box":
        return {"kind": "analytic_box", "units": units["length"], "dimensions": source["dimensions"]}
    path = (ROOT / source["path"]).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise InputError("PATH_OUTSIDE_FIXTURES")
    raw = path.read_bytes()
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", raw, re.I):
        raise InputError("UNSAFE_XML")
    try:
        robot = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise InputError("INVALID_XML") from exc
    if robot.tag != "robot":
        raise InputError("INVALID_XML")
    links = robot.findall("link")
    names = [link.get("name") for link in links]
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise InputError("DUPLICATE_LINK_ID")
    joints = robot.findall("joint")
    joint_names = [joint.get("name") for joint in joints]
    if any(not name for name in joint_names) or len(set(joint_names)) != len(joint_names):
        raise InputError("DUPLICATE_JOINT_ID")
    for link in links:
        mass = link.find("inertial/mass")
        if mass is not None:
            value = float(mass.get("value"))
            if not math.isfinite(value):
                raise InputError("NONFINITE_NUMBER")
            if value <= 0:
                raise InputError("INVALID_MASS")
        inertia = link.find("inertial/inertia")
        if inertia is not None:
            diagonal = [float(inertia.get(k)) for k in ("ixx", "iyy", "izz")]
            if any(not math.isfinite(v) for v in diagonal):
                raise InputError("NONFINITE_NUMBER")
            if any(v <= 0 for v in diagonal):
                raise InputError("INVALID_INERTIA_DIAGONAL")
            # This rejects explicit bad diagonals only. Tensor physics/sign/frame rules belong to A01.
    for joint in joints:
        if joint.get("type") in ("revolute", "prismatic") and joint.find("limit") is None:
            raise InputError("MISSING_JOINT_LIMIT")
    for mesh in robot.iter("mesh"):
        scale = [float(v) for v in mesh.get("scale", "1 1 1").split()]
        if any(not math.isfinite(v) for v in scale):
            raise InputError("NONFINITE_NUMBER")
        filename = unquote(mesh.get("filename", ""))
        if "://" in filename:
            raise InputError("REMOTE_MESH")
        mesh_path = (path.parent / filename).resolve()
        if not mesh_path.is_relative_to(ROOT.resolve()):
            raise InputError("PATH_OUTSIDE_FIXTURES")
        if not mesh_path.is_file():
            raise InputError("MESH_NOT_FOUND")
    return {"links": names, "joint_types": [j.get("type") for j in joints],
            "mesh_count": len(list(robot.iter("mesh")))}


class FixtureChecks(unittest.TestCase):
    def test_fixture_boundaries(self):
        """Removing a guard must make its malformed fixture get accepted and fail."""
        catalog = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], "webots-mcp-input-fixtures/v1")
        for case in catalog["cases"]:
            with self.subTest(case=case["fixture_id"]):
                before = copy.deepcopy(case["input"])
                expected = case["expected"]
                if expected["error_code"]:
                    with self.assertRaises(InputError) as caught:
                        inspect_input(case["input"])
                    self.assertEqual(str(caught.exception), expected["error_code"])
                else:
                    actual = inspect_input(case["input"])
                    self.assertEqual(actual, expected["summary"])
                self.assertEqual(case["input"], before, "inspection must not rewrite source metadata")

    def test_asymmetric_box_mesh_has_known_dimensions(self):
        """A wrong mesh scale or swapped extent must fail independently of metadata."""
        vertices = [list(map(float, line.split()[1:])) for line in
                    (ROOT / "valid/meshes/asymmetric_box.obj").read_text().splitlines()
                    if line.startswith("v ")]
        self.assertEqual(len(vertices), 8)
        extents = [max(v[i] for v in vertices) - min(v[i] for v in vertices) for i in range(3)]
        for actual, expected in zip(extents, [0.1, 0.05, 0.02]):
            self.assertAlmostEqual(actual, expected, places=12)

    def test_m_and_mm_remain_distinct_sources(self):
        """Normalizing raw metadata in place would hide a second scale and fail."""
        cases = json.loads((ROOT / "cases.json").read_text())["cases"]
        values = {c["fixture_id"]: c["input"]["source"] for c in cases}
        self.assertEqual(values["box_metadata_m"]["units"]["length"], "m")
        self.assertEqual(values["box_metadata_m"]["dimensions"], [0.1, 0.05, 0.02])
        self.assertEqual(values["box_metadata_mm"]["units"]["length"], "mm")
        self.assertEqual(values["box_metadata_mm"]["dimensions"], [100, 50, 20])

    def test_instance_and_joint_goldens_preserve_source_identity(self):
        """Collapsing repeated part files, losing instance pose, or swapping joint units fails."""
        cases = json.loads((ROOT / "cases.json").read_text())["cases"]
        repeated = next(c["input"] for c in cases if c["fixture_id"] == "repeated_instances")
        first, second = repeated["components"]
        self.assertEqual(first["source_file"], second["source_file"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["instance_path"], second["instance_path"])
        self.assertEqual([first["urdf_link"], second["urdf_link"]], ["box_1", "box_2"])
        self.assertTrue(all(c["persistent_reference"] is None for c in repeated["components"]))
        tree = ET.parse(ROOT / repeated["source"]["path"])
        self.assertEqual(tree.find("joint[@name='fixed_2']/origin").get("rpy"), "0 0 1.5707963267948966")
        for kind, axis, limits in [("revolute", "0 0 1", [-0.5, 0.75, 1.0, 0.5]),
                                   ("prismatic", "1 0 0", [0.0, 0.05, 10.0, 0.02])]:
            joint = ET.parse(ROOT / f"valid/single_{kind}.urdf").find("joint")
            self.assertEqual(joint.get("type"), kind)
            self.assertEqual(joint.find("axis").get("xyz"), axis)
            self.assertEqual([float(joint.find("limit").get(k)) for k in
                              ("lower", "upper", "effort", "velocity")], limits)

    def test_mass_array_goldens_are_method_specific(self):
        """Wrong slot order or treating a 13-slot method as 12 must fail."""
        goldens = json.loads((ROOT / "sw-mass-properties.json").read_text())
        slots = ["CenterOfMassX", "CenterOfMassY", "CenterOfMassZ", "Volume", "Area",
                 "Mass", "MomXX", "MomYY", "MomZZ", "MomXY", "MomZX", "MomYZ"]
        methods = {item["source_method"]: item for item in goldens["goldens"]}
        for method, count in [("IModelDocExtension.GetMassProperties2", 12),
                              ("IBody2.GetMassProperties", 12),
                              ("IModelDocExtension.GetMassProperties", 13)]:
            item = methods[method]
            self.assertFalse(item["real_sw_export"])
            self.assertEqual(item["array_fields"], slots + (["Accuracy"] if count == 13 else []))
            self.assertEqual(len(item["raw"]), count)
            readout = dict(zip(item["array_fields"], item["raw"]))
            self.assertEqual([readout[k] for k in slots[:3]], [0.011, -0.023, 0.037])
            self.assertEqual(readout["Volume"], 0.0001)
            self.assertEqual(readout["Area"], 0.016)
            self.assertEqual(readout["Mass"], 0.2)
            self.assertEqual([readout[k] for k in slots[9:]], [0.000001, 0.000002, 0.000003])
            self.assertEqual(item["expected_readout"], readout)
        self.assertEqual(methods["IBody2.GetMassProperties"]["coordinate_frame"], "body-creation-frame")
        self.assertEqual(goldens["unverified_methods"][0]["source_method"], "IModelDoc2.GetMassProperties")
        self.assertIsNone(goldens["unverified_methods"][0]["array_fields"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
