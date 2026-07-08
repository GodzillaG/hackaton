import io
import unittest
from datetime import datetime, timezone

import cv2
import numpy as np

import api_server
from pose_analyzer import ScreeningResult


class FakeAnalyzer:
    engine = "unit_test_pose"
    task_model_path = "models/unit-test.task"

    def analyze_frame(self, frame):
        self.frame_shape = frame.shape
        return (
            ScreeningResult(
                landmarks_found=True,
                shoulder_tilt_deg=3.2,
                hip_tilt_deg=3.1,
                head_tilt_deg=1.4,
                trunk_shift_ratio=0.05,
                waist_asym_ratio=0.03,
                flags={
                    "shoulder_tilt": True,
                    "hip_tilt": True,
                    "head_tilt": False,
                    "trunk_shift": True,
                    "waist_asymmetry": False,
                },
                risk_level="high",
                message="Unit test result",
                analysis_engine=self.engine,
                quality_score=0.91,
            ),
            {"source": "unit-test"},
        )

    @staticmethod
    def draw_overlay(frame, _context, _screening):
        return frame


def jpeg_bytes(width=64, height=96):
    image = np.full((height, width, 3), 240, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("Failed to encode test image")
    return buffer.tobytes()


class ApiServerTests(unittest.TestCase):
    def setUp(self):
        self.original_analyzer = api_server._analyzer
        self.original_persist_report = api_server._persist_report
        self.original_persist_multi_view_report = api_server._persist_multi_view_report
        api_server._analyzer = FakeAnalyzer()
        api_server._persist_report = lambda _response, _annotated: None
        api_server._persist_multi_view_report = lambda _response, _annotated_views: None
        self.client = api_server.app.test_client()

    def tearDown(self):
        api_server._analyzer = self.original_analyzer
        api_server._persist_report = self.original_persist_report
        api_server._persist_multi_view_report = self.original_persist_multi_view_report

    def test_health_uses_configured_analyzer(self):
        response = self.client.get("/health")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["analysis_engine"], "unit_test_pose")
        self.assertEqual(payload["pose_model"], "models/unit-test.task")

    def test_analyze_accepts_multipart_image_and_returns_report(self):
        response = self.client.post(
            "/api/analyze",
            data={
                "student_id": "7B-014",
                "image": (io.BytesIO(jpeg_bytes()), "student.jpg"),
            },
            content_type="multipart/form-data",
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["student_id"], "7B-014")
        self.assertEqual(payload["mode"], "single")
        self.assertEqual(payload["landmarks_found"], True)
        self.assertEqual(payload["analysis_engine"], "unit_test_pose")
        self.assertEqual(payload["risk"]["level"], "high")
        self.assertEqual(payload["risk"]["score"], 60)
        self.assertEqual(payload["risk"]["finding_count"], 3)
        self.assertEqual(len(payload["metric_cards"]), 5)
        self.assertEqual(len(payload["recommendations"]), 3)
        self.assertTrue(payload["overlay_image"].startswith("data:image/jpeg;base64,"))

    def test_analyze_accepts_five_view_protocol(self):
        response = self.client.post(
            "/api/analyze",
            data={
                "student_id": "7B-014",
                "image_front": (io.BytesIO(jpeg_bytes()), "front.jpg"),
                "image_back": (io.BytesIO(jpeg_bytes()), "back.jpg"),
                "image_left": (io.BytesIO(jpeg_bytes()), "left.jpg"),
                "image_right": (io.BytesIO(jpeg_bytes()), "right.jpg"),
                "image_adams": (io.BytesIO(jpeg_bytes()), "adams.jpg"),
            },
            content_type="multipart/form-data",
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mode"], "multi_view")
        self.assertEqual(payload["student_id"], "7B-014")
        self.assertEqual(payload["view_count"], 5)
        self.assertEqual(payload["views_completed"], 5)
        self.assertEqual(len(payload["views"]), 5)
        self.assertEqual(payload["risk"]["level"], "high")
        self.assertEqual(payload["risk"]["finding_count"], 15)
        self.assertEqual(payload["risk"]["total_metrics"], 25)
        self.assertEqual(payload["risk"]["score"], 60)
        self.assertEqual(
            payload["flags"],
            {
                "shoulder_tilt": True,
                "hip_tilt": True,
                "head_tilt": False,
                "trunk_shift": True,
                "waist_asymmetry": False,
            },
        )
        self.assertEqual(len(payload["metric_cards"]), 5)
        self.assertTrue(payload["overlay_image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(
            [view["view_key"] for view in payload["views"]],
            ["front", "back", "left", "right", "adams"],
        )

    def test_analyze_rejects_empty_request(self):
        response = self.client.post("/api/analyze", data={})
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("хотя бы одно фото", payload["error"])

    def test_report_id_sanitizes_student_id(self):
        timestamp = datetime(2026, 7, 8, 9, 30, 4, tzinfo=timezone.utc)

        self.assertEqual(
            api_server._report_id("7 B/014", timestamp),
            "7_B_014_20260708_093004",
        )


if __name__ == "__main__":
    unittest.main()
