"""Offline native terrain generation and unresolved experiment plans, no execution.

Scenes use Webots ENU directly; this module never transforms CAD coordinates.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_recipes():
    """Expand the shipped catalog; each caller receives independent raw plans."""
    catalog = json.loads((ROOT / "recipes/catalog.json").read_text(encoding="utf-8"))
    result = {}
    for name, item in catalog["recipes"].items():
        recipe = deepcopy(catalog["defaults"])
        recipe.update(deepcopy(item))
        recipe["budget"] = {**catalog["defaults"]["budget"], **item.get("budget", {})}
        recipe["missing_information"] = [*catalog["defaults"]["missing_information"],
                                         *item.get("missing_information", [])]
        result[name] = recipe
    return result


def generate_scene(kind, parameters=None):
    """Return synthetic primitives, a self-contained .wbt and its UTF-8 hash.

    Parameter triples in templates.json are default/min/max, all lengths in m.
    No robot/Physics node, material calibration or physical judgement is added.
    """
    templates = json.loads((ROOT / "scenes/templates.json").read_text(encoding="utf-8"))
    if not isinstance(kind, str) or kind not in templates["templates"]:
        raise ValueError("unknown scene kind")
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        raise ValueError("scene parameters must be an object")
    spec = {**templates["common"], **templates["templates"][kind]}
    if parameters.keys() - spec.keys():
        raise ValueError("unknown scene parameters")
    p = {key: parameters.get(key, bounds[0]) for key, bounds in spec.items()}
    for key, value in p.items():
        if (type(value) not in (int, float) or not spec[key][1] <= value <= spec[key][2]
                or key in ("count", "time_step_ms", "seed") and type(value) is not int):
            raise ValueError(f"invalid scene parameter: {key}")

    def box(name, center, size):
        return {"name": name, "type": "Box", "translation": center, "size": size}

    geometry = [box("ground", [0, 0, -p["thickness_m"] / 2],
                    [p["length_m"], p["width_m"], p["thickness_m"]])]
    if kind in ("stairs", "slope"):
        run = p["count"] * p["tread_m"] if kind == "stairs" else p["run_m"]
        if (p["start_x_m"] < -p["length_m"] / 2 or
                p["start_x_m"] + run > p["length_m"] / 2 or
                p["feature_width_m"] > p["width_m"]):
            raise ValueError("terrain feature extends beyond ground footprint")
        if kind == "stairs":
            for i in range(p["count"]):
                height = (i + 1) * p["rise_m"]
                geometry.append(box(f"step_{i + 1}",
                    [p["start_x_m"] + (i + 0.5) * p["tread_m"], 0, height / 2],
                    [p["tread_m"], p["feature_width_m"], height]))
        else:
            geometry.append({"name": "slope", "type": "ElevationGrid",
                "translation": [p["start_x_m"], -p["feature_width_m"] / 2, 0],
                "height": [0, p["rise_m"], 0, p["rise_m"]],
                "xSpacing": p["run_m"], "ySpacing": p["feature_width_m"]})
    if kind == "obstacle":
        if (abs(p["x_m"]) + p["length_obstacle_m"] / 2 > p["length_m"] / 2 or
                abs(p["y_m"]) + p["width_obstacle_m"] / 2 > p["width_m"] / 2):
            raise ValueError("obstacle extends beyond ground footprint")
        geometry.append(box("obstacle", [p["x_m"], p["y_m"], p["height_m"] / 2],
            [p["length_obstacle_m"], p["width_obstacle_m"], p["height_m"]]))

    def vector(values):
        return " ".join(format(value, ".17g") for value in values)

    # Reuse the exact same native geometry for display and collision.
    world = '#VRML_SIM R2025a utf8\n# Synthetic terrain only; physical verdict unknown.\n'
    world += f'WorldInfo {{ coordinateSystem "ENU" basicTimeStep {p["time_step_ms"]} randomSeed {p["seed"]} }}\n'
    world += 'Viewpoint { position 3 -4 3 orientation 0.736 0.327 0.592 1.687 }\n'
    world += 'Background { skyColor [ 0.15 0.18 0.23 ] }\nDirectionalLight { }\n'
    for index, item in enumerate(geometry):
        shape = (f'Box {{ size {vector(item["size"])} }}' if item["type"] == "Box" else
                 'ElevationGrid { xDimension 2 yDimension 2 '
                 f'xSpacing {item["xSpacing"]:.17g} ySpacing {item["ySpacing"]:.17g} '
                 f'height [ {vector(item["height"])} ] thickness {p["thickness_m"]:.17g} }}')
        world += (f'DEF TERRAIN_{index} Solid {{\n translation {vector(item["translation"])}\n'
                  f' name "{item["name"]}"\n contactMaterial "terrain"\n'
                  ' children [ Shape { appearance PBRAppearance { baseColor 0.5 0.55 0.6 roughness 1 metalness 0 }\n'
                  f' geometry DEF GEOM_{index} {shape} }} ]\n boundingObject USE GEOM_{index}\n}}\n')
    return {"schema_version": 1, "provenance": "synthetic", "webots_version": "R2025a",
            "kind": kind, "parameters": p, "units": templates["units"], "coordinate_system": "ENU",
            "geometry": geometry, "world": world, "sha256": hashlib.sha256(world.encode("utf-8")).hexdigest(),
            "physical_verdict": "unknown", "missing_information": ["model", "contact_material_pair_and_friction",
            "real_world_load_and_collision_observations", "A04_physical_acceptance"]}


def prepare_experiment(recipe_id, scene_kind, parameters=None):
    """Prepare a report-compatible, unexecuted plan; required bindings stay null."""
    catalog = load_recipes()
    if not isinstance(recipe_id, str) or recipe_id not in catalog:
        raise ValueError("unknown recipe")
    recipe = catalog[recipe_id]
    if not isinstance(scene_kind, str) or scene_kind not in recipe["scene_kinds"]:
        raise ValueError("scene kind is not applicable to recipe")
    scene = generate_scene(scene_kind, parameters)
    budget = deepcopy(recipe["budget"])
    budget["time_step_ms"] = scene["parameters"]["time_step_ms"]
    if recipe_id == "paired_step_comparison":
        if budget["time_step_ms"] not in (16, 32):
            raise ValueError("paired comparison requires 32ms or 16ms")
        budget["max_steps"] = 64 if budget["time_step_ms"] == 32 else 128
    budget["sim_time_s"] = budget["max_steps"] * budget["time_step_ms"] / 1000
    initial_state = {**recipe["initial_state"], "seed": scene["parameters"]["seed"]}
    # ponytail: plans only; bind devices and enforce real deadlines in A04 worker.
    actions = []
    for start in range(0, budget["max_steps"], budget["batch_steps"]):
        actions.append({"kind": "step_batch", "steps": min(budget["batch_steps"], budget["max_steps"] - start),
                        "yield_wall_time_s": budget["batch_yield_s"], "dispatchable": False})
    return {"recipe": {"id": recipe_id, **recipe}, "scene": scene,
            "parameters": {"time_step": budget["time_step_ms"] / 1000, "seed": scene["parameters"]["seed"],
                           "initial_state": initial_state, "budget": budget},
            "actions": actions, "observations": [], "issues": [], "model": None,
            "execution_state": "not_started", "core_ready": False,
            "verdict": deepcopy(recipe["judgement"]),
            "missing_information": list(dict.fromkeys(recipe["missing_information"] + scene["missing_information"]))}
