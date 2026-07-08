import unittest

from pose_analyzer import ScreeningResult, ScoliosisScreeningAnalyzer, _angle_from_horizontal


class PoseAnalyzerTests(unittest.TestCase):
    def test_angle_from_horizontal(self):
        self.assertEqual(_angle_from_horizontal((0, 0), (10, 0)), 0.0)
        self.assertEqual(_angle_from_horizontal((10, 0), (0, 0)), 0.0)
        self.assertEqual(_angle_from_horizontal((0, 0), (0, 10)), 90.0)
        self.assertAlmostEqual(_angle_from_horizontal((100, 0), (0, 2)), 1.15, places=2)
        self.assertAlmostEqual(_angle_from_horizontal((0, 2), (100, 0)), 1.15, places=2)

    def test_score_points_flags_high_risk(self):
        analyzer = object.__new__(ScoliosisScreeningAnalyzer)
        points = {
            "left_shoulder": (0.0, 0.0),
            "right_shoulder": (100.0, 10.0),
            "left_hip": (20.0, 120.0),
            "right_hip": (120.0, 130.0),
            "left_ear": (35.0, -50.0),
            "right_ear": (65.0, -40.0),
            "left_elbow": (-15.0, 70.0),
            "right_elbow": (180.0, 70.0),
        }

        result = analyzer._score_points(points, "unit_test", quality_score=0.82)

        self.assertTrue(result.landmarks_found)
        self.assertEqual(result.analysis_engine, "unit_test")
        self.assertEqual(result.risk_level, "high")
        self.assertGreaterEqual(sum(result.flags.values()), 3)
        self.assertEqual(result.quality_score, 0.82)

    def test_score_points_treats_reversed_horizontal_lines_as_low_tilt(self):
        analyzer = object.__new__(ScoliosisScreeningAnalyzer)
        points = {
            "left_shoulder": (100.0, 0.0),
            "right_shoulder": (0.0, 2.0),
            "left_hip": (100.0, 120.0),
            "right_hip": (0.0, 122.0),
            "left_ear": (60.0, -50.0),
            "right_ear": (40.0, -51.0),
            "left_elbow": (110.0, 70.0),
            "right_elbow": (-10.0, 70.0),
        }

        result = analyzer._score_points(points, "unit_test", quality_score=0.9)

        self.assertEqual(result.risk_level, "low")
        self.assertFalse(result.flags["shoulder_tilt"])
        self.assertFalse(result.flags["hip_tilt"])
        self.assertFalse(result.flags["head_tilt"])
        self.assertAlmostEqual(result.shoulder_tilt_deg, 1.15, places=2)
        self.assertAlmostEqual(result.hip_tilt_deg, 1.15, places=2)
        self.assertAlmostEqual(result.head_tilt_deg, 2.86, places=2)

    def test_screening_result_to_dict_rounds_metrics(self):
        result = ScreeningResult(
            landmarks_found=True,
            shoulder_tilt_deg=2.345,
            hip_tilt_deg=1.234,
            head_tilt_deg=3.999,
            trunk_shift_ratio=0.123456,
            waist_asym_ratio=0.987654,
            quality_score=0.876,
            flags={"shoulder_tilt": False},
            risk_level="low",
            message="ok",
            analysis_engine="unit_test",
        )
        payload = result.to_dict()

        self.assertEqual(payload["quality_score"], 0.88)
        self.assertEqual(payload["metrics"]["shoulder_tilt_deg"], 2.35)
        self.assertEqual(payload["metrics"]["trunk_shift_ratio"], 0.1235)
        self.assertEqual(payload["metrics"]["waist_asym_ratio"], 0.9877)


if __name__ == "__main__":
    unittest.main()
