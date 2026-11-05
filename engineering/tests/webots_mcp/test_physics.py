"""Synthetic analytic checks only: never connects to CAD or simulation."""
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from webots_mcp import physics as p


class PhysicsChecks(unittest.TestCase):
    def test_method_slots_not_legacy_guess(self):
        data = json.loads((Path(__file__).parent / 'fixtures/sw-mass-properties.json').read_text())
        for g in data['goldens']:
            self.assertEqual(p.parse_mass_array(g['source_method'], g['raw']), g['expected_readout'])
        for method, raw in [('IModelDoc2.GetMassProperties', [0]*12),
                            ('IBody2.GetMassProperties', [0]*11)]:
            with self.assertRaises(ValueError):
                p.parse_mass_array(method, raw)

    def test_units_and_sw_product_sign(self):
        a = p.mass_properties(2, [0.01, .02, .03], [[5, -.2, -.3], [-.2, 6, -.4], [-.3, -.4, 7]],
                              length_unit='m', mass_unit='kg', frame='part')
        b = p.mass_properties(2000, [10, 20, 30], [[5e9, -2e8, -3e8], [-2e8, 6e9, -4e8], [-3e8, -4e8, 7e9]],
                              length_unit='mm', mass_unit='g', frame='part')
        self.assertEqual(a['mass_kg'], b['mass_kg'])
        for x, y in zip(a['com_m'], b['com_m']): self.assertAlmostEqual(x, y)
        for row_a, row_b in zip(a['inertia_kg_m2'], b['inertia_kg_m2']):
            for x, y in zip(row_a, row_b): self.assertAlmostEqual(x, y)
        self.assertEqual(p.sw_inertia_tensor([5,.2,.3,.2,6,.4,.3,.4,7]), a['inertia_kg_m2'])

    def test_transform_rotation_and_frame_guard(self):
        raw = [0,1,0, -1,0,0, 0,0,1, 1,2,3, 1,0,0,0]
        r, t = p.sw_transform(raw)
        body = p.mass_properties(2, [1,2,3], [[2,0,0],[0,3,0],[0,0,4]],
                                 length_unit='m', mass_unit='kg', frame='part')
        moved = p.transform_mass(body, r, t, source_frame='part', target_frame='assembly')
        self.assertEqual(moved['com_m'], [-1,3,6])
        self.assertEqual(moved['inertia_kg_m2'], [[3,0,0],[0,2,0],[0,0,4]])
        with self.assertRaises(ValueError):
            p.transform_mass(moved, r, t, source_frame='part', target_frame='assembly')
        for index, value in [(12, .001), (0, .5)]:
            bad = raw.copy(); bad[index] = value
            with self.assertRaises(ValueError): p.sw_transform(bad)

    def test_merge_parallel_axis_and_duplicate_rejection(self):
        bodies = []
        for name, mass, com in [('a', 2, [1,2,0]), ('b', 1, [-2,-4,0])]:
            body = p.mass_properties(mass, com, [[1,0,0],[0,1,0],[0,0,1]],
                                     length_unit='m', mass_unit='kg', frame='assembly')
            bodies.append((name, body))
        result = p.merge_rigid_bodies(bodies, frame='assembly')
        self.assertEqual(result['mass_kg'], 3)
        self.assertEqual(result['com_m'], [0,0,0])
        self.assertEqual(result['inertia_kg_m2'], [[26,-12,0],[-12,8,0],[0,0,32]])
        self.assertEqual(result['component_ids'], ['a','b'])
        with self.assertRaises(ValueError): p.merge_rigid_bodies(bodies + bodies, frame='assembly')

    def test_invalid_inputs_and_realizability(self):
        for tensor in ([[1,0,0],[0,1,0],[0,0,3]], [[1,2,0],[2,1,0],[0,0,1]],
                       [[1,0,0],[.1,1,0],[0,0,1]], [[math.nan,0,0],[0,1,0],[0,0,1]]):
            with self.assertRaises(ValueError):
                p.mass_properties(1, [0,0,0], tensor, length_unit='m', mass_unit='kg', frame='a')
        with self.assertRaises(ValueError):
            p.mass_properties(0, [0,0,0], [[1,0,0],[0,1,0],[0,0,1]], length_unit='m', mass_unit='kg', frame='a')
        with self.assertRaises(ValueError): p.sw_transform([-1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0])

    def test_oblique_rotation_cross_terms_and_energy(self):
        c = math.sqrt(.5)
        r = [[c,-c,0],[c,c,0],[0,0,1]]
        body = p.mass_properties(1, [0,0,0], [[2,0,0],[0,3,0],[0,0,4]],
                                 length_unit='m', mass_unit='kg', frame='local')
        moved = p.transform_mass(body, r, [0,0,0], source_frame='local', target_frame='root')
        expected = [[2.5,-.5,0],[-.5,2.5,0],[0,0,4]]
        for actual_row, expected_row in zip(moved['inertia_kg_m2'], expected):
            for actual, wanted in zip(actual_row, expected_row): self.assertAlmostEqual(actual, wanted)
        back = p.transform_mass(moved, list(map(list, zip(*r))), [0,0,0], source_frame='root', target_frame='local')
        for actual_row, expected_row in zip(back['inertia_kg_m2'], body['inertia_kg_m2']):
            for actual, wanted in zip(actual_row, expected_row): self.assertAlmostEqual(actual, wanted)
        for r_bad in ([[2,0,0],[0,.5,0],[0,0,1]], [[1,1,0],[0,1,0],[0,0,1]]):
            with self.assertRaises(ValueError): p.validate_rotation(r_bad)


if __name__ == '__main__':
    unittest.main()
