"""Standard-library tests of API routing, independent of the Rust compiler."""

import unittest
from array import array
from unittest.mock import patch

from scrollweave import (
    Evidence,
    GeometryMode,
    Goal,
    Image,
    LayeredOptions,
    LayeredResult,
    PlanOptions,
    Viewport,
    ViewSuggestion,
    plan_views,
    stitch_basic,
    stitch_layered,
)


def synthetic(w=8, h=12):
    return Image(w, h, bytes(i * 17 % 256 for i in range(w * h * 3)))


def layered_result():
    evidence = Evidence(
        source_ids=array('i', [0]),
        support=array('H', [0]),
        views=array('H', [0]),
        conflict=b'\x00',
        unverified=b'\x01',
        invalid_source=b'\x00',
        estimated_ui=b'\x00',
        ui_probability=b'\x00',
        rejected_prior_rows=b'\x00',
    )
    return LayeredResult(
        image=synthetic(),
        scroll=(0, 4),
        viewport=Viewport(0, 12),
        evidence=evidence,
        geometry_mode=GeometryMode.INSUFFICIENT_MOTION,
        support_fraction=0.0,
        unverified_fraction=1.0,
        _native=object(),
    )


class DomainAPITests(unittest.TestCase):
    def test_layered_options_forward_to_native(self):
        frames = [synthetic(), synthetic()]
        with patch('scrollweave._binding.reconstruct_layered', return_value=object()) as native:
            result = stitch_layered(frames, scroll=[0, 4], viewport=Viewport(0, 12))
            self.assertIsInstance(result, object)
            self.assertEqual(native.call_args.args[1], (0, 4))
            self.assertEqual(native.call_args.args[2], Viewport(0, 12))
            self.assertIsInstance(native.call_args.args[3], LayeredOptions)
            self.assertFalse(native.call_args.args[4])

    def test_basic_alignment_forwards_positions(self):
        frames = [synthetic(), synthetic()]
        with patch('scrollweave._binding.stitch_basic', return_value=object()) as native:
            stitch_basic(frames, positions=[0, 4], viewport=Viewport(0, 12))
            self.assertEqual(native.call_args.args[1], ((0, 0), (0, 4)))

    def test_planner_uses_layered_context(self):
        result = layered_result()
        with patch('scrollweave._binding.plan_layered', return_value=('suggestion',)) as native:
            views = plan_views(result, PlanOptions(count=2, goal=Goal.VERIFICATION))
            self.assertEqual(views, ('suggestion',))
            args = native.call_args.args
            self.assertIs(args[0], result)
            self.assertEqual(args[1].count, 2)
            self.assertEqual(args[1].goal, Goal.VERIFICATION)

    def test_planner_requires_layered_result(self):
        with self.assertRaises(TypeError):
            plan_views(object())

    def test_prior_config_structs_are_exposed(self):
        from scrollweave._binding import _LayeredConfig, _LayeredResponse

        self.assertTrue(_LayeredConfig._fields_)
        self.assertTrue(_LayeredResponse._fields_)

    def test_view_suggestion_uses_goal_enum(self):
        view = ViewSuggestion(100, 0.8, 123, 0.6, Goal.REPAIR)
        self.assertEqual(view.goal, Goal.REPAIR)


if __name__ == '__main__':
    unittest.main()
