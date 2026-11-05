"""Offline scene/recipe checks; no controller or simulator is started."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))


class RecipeChecks(unittest.TestCase):
    def api(self):
        self.assertTrue((TOOLS / "webots_mcp/recipes.py").exists(),
                        "S06 scene/recipe generation is not implemented")
        return importlib.import_module("webots_mcp.recipes")

    def test_stairs_have_requested_rise_tread_and_ground_height(self):
        """Wrong Box center/size or axis must change independently known extents."""
        scene = self.api().generate_scene("stairs", {"count": 3, "rise_m": 0.05, "tread_m": 0.2})
        floor, *steps = scene["geometry"]
        self.assertEqual(floor["size"], [4.0, 2.0, 0.1])
        self.assertEqual(floor["translation"], [0, 0, -0.05])
        for item, x, height in zip(steps, [0.6, 0.8, 1.0], [0.05, 0.1, 0.15]):
            self.assertAlmostEqual(item["translation"][0], x)
            self.assertAlmostEqual(item["translation"][2], height / 2)
            self.assertAlmostEqual(item["size"][0], 0.2)
            self.assertAlmostEqual(item["size"][2], height)

    def test_slope_uses_native_z_up_grid_and_obstacle_has_grounded_box(self):
        """Wrong height ordering or obstacle origin makes this fail."""
        slope = self.api().generate_scene("slope", {"run_m": 1.0, "rise_m": 0.3})
        grid = slope["geometry"][1]
        self.assertEqual(grid["translation"], [0.5, -0.5, 0])
        self.assertEqual(grid["height"], [0, 0.3, 0, 0.3])
        self.assertEqual((grid["xSpacing"], grid["ySpacing"]), (1.0, 1.0))
        obstacle = self.api().generate_scene("obstacle", {"x_m": -0.4, "y_m": 0.3, "height_m": 0.2})
        self.assertEqual(obstacle["geometry"][1]["translation"], [-0.4, 0.3, 0.1])

    def test_world_is_deterministic_native_and_collision_geometry_is_shared(self):
        """Omitting collision geometry, leaking PROTO or unstable output must fail."""
        api = self.api()
        for kind in ("ground", "stairs", "slope", "obstacle"):
            scene = api.generate_scene(kind)
            world = scene["world"]
            self.assertEqual(scene, api.generate_scene(kind))
            self.assertTrue(world.startswith("#VRML_SIM R2025a utf8\n"))
            self.assertIn('coordinateSystem "ENU"', world)
            self.assertNotIn("EXTERNPROTO", world)
            self.assertEqual(world.count("boundingObject USE"), len(scene["geometry"]))
            self.assertEqual(scene["sha256"], hashlib.sha256(world.encode()).hexdigest())
            self.assertEqual(scene["physical_verdict"], "unknown")
        self.assertNotEqual(api.generate_scene("stairs")["sha256"],
                            api.generate_scene("stairs", {"rise_m": 0.06})["sha256"])

    def test_invalid_and_unbounded_scene_parameters_are_rejected(self):
        """Removing finite/type/count/extent guards must admit a forbidden world."""
        api = self.api()
        for kind, params in [("missing", {}), ([], {}), ("ground", []),
                ("ground", {"length_m": True}), ("ground", {"length_m": float("nan")}),
                ("ground", {"length_m": 10**400}),
                ("ground", {"width_m": float("inf")}), ("ground", {"thickness_m": 0}),
                ("stairs", {"count": 1.5}), ("stairs", {"count": 33}),
                ("stairs", {"rise_m": -0.1}), ("slope", {"run_m": 0}),
                ("obstacle", {"x_m": 100}), ("stairs", {"count": 20}),
                ("ground", {"unknown": 3}), ("ground", {"coordinateSystem": "NUE"})]:
            with self.subTest(kind=kind, params=params), self.assertRaises(ValueError):
                api.generate_scene(kind, params)

    def test_recipe_plan_is_bounded_unexecuted_and_preserves_missing_physics(self):
        """A fabricated PASS, hidden unbound model or unbounded step plan must fail."""
        api = self.api()
        plan = api.prepare_experiment("terrain_traverse", "stairs")
        self.assertEqual(plan["execution_state"], "not_started")
        self.assertFalse(plan["core_ready"])
        self.assertEqual(plan["verdict"]["status"], "unknown")
        self.assertIsNone(plan["parameters"]["initial_state"]["model_pose"])
        self.assertIn("model_identity_and_pose", plan["missing_information"])
        batches = [a["steps"] for a in plan["actions"] if a["kind"] == "step_batch"]
        self.assertEqual(sum(batches), 128)
        self.assertTrue(all(1 <= count <= 8 for count in batches))
        self.assertAlmostEqual(plan["parameters"]["budget"]["sim_time_s"], 4.096)
        self.assertIsNone(plan["parameters"]["budget"]["working_set_bytes"])
        self.assertFalse(plan["observations"])
        self.assertEqual(api.prepare_experiment("static_preflight", "ground")["actions"], [])
        with self.assertRaises(ValueError):
            api.prepare_experiment("absent", "ground")

    def test_paired_step_plans_have_equal_simulation_duration_and_seed(self):
        """A nominal half-step comparison must not silently halve its duration."""
        api = self.api()
        first = api.prepare_experiment("paired_step_comparison", "slope", {"seed": 17})
        second = api.prepare_experiment("paired_step_comparison", "slope", {"seed": 17, "time_step_ms": 16})
        self.assertEqual(first["parameters"]["budget"]["max_steps"], 64)
        self.assertEqual(second["parameters"]["budget"]["max_steps"], 128)
        self.assertEqual(first["parameters"]["budget"]["sim_time_s"], 2.048)
        self.assertEqual(second["parameters"]["budget"]["sim_time_s"], 2.048)
        self.assertEqual(first["parameters"]["seed"], 17)
        self.assertEqual(first["scene"]["geometry"], second["scene"]["geometry"])
        self.assertIn("basicTimeStep 16 randomSeed 17", second["scene"]["world"])
        self.assertNotEqual(first["scene"]["sha256"], second["scene"]["sha256"])
        with self.assertRaises(ValueError):
            api.prepare_experiment("paired_step_comparison", "ground", {"time_step_ms": 10})

    def test_catalog_plans_cover_ladder_and_roundtrip_through_existing_carrier(self):
        """Lost recipe requirements or upgrading synthetic data in reporting fails."""
        api = self.api()
        catalog = api.load_recipes()
        self.assertEqual({r["level"] for r in catalog.values()}, {"L0", "L1", "L2", "L3", "L4", "L5"})
        for name, recipe in catalog.items():
            for field in ("goal", "applies_to", "units", "initial_state", "actions", "observations",
                          "collision_coverage", "budget", "cancel_conditions", "judgement", "missing_information"):
                self.assertTrue(recipe[field], (name, field))
            for kind in recipe["scene_kinds"]:
                json.dumps(api.prepare_experiment(name, kind), allow_nan=False)
        from webots_mcp.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as root:
            store = EvidenceStore(Path(root), task_id="S06", room_id="offline")
            data = api.prepare_experiment("terrain_traverse", "obstacle")
            report = store.new_report("experiment_report", "recipe-1", provenance="synthetic", data=data,
                                      missing_information=data["missing_information"])
            store.write_report(report)
            actual = store.read_report("recipe-1", "experiment_report")
            self.assertEqual(actual["data"], data)
            self.assertEqual(actual["provenance"]["kind"], "synthetic")
            self.assertIsNone(actual["source_revision"])


if __name__ == "__main__":
    unittest.main()
