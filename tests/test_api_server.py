import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

import api_server
from pose_analyzer import ScreeningResult
from storage import ScolioScanStorage


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
        self.original_storage = api_server._storage
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = ScolioScanStorage(Path(self.temp_dir.name) / "test.db")
        self.storage.initialize(migrate_reports=False)
        api_server._analyzer = FakeAnalyzer()
        api_server._storage = self.storage
        self.admin = self.storage.authenticate_user("admin", "12345678")
        self.token = self.storage.create_session(self.admin["id"])
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = api_server.app.test_client()

    def tearDown(self):
        api_server._analyzer = self.original_analyzer
        api_server._storage = self.original_storage
        self.temp_dir.cleanup()

    def test_health_uses_configured_analyzer(self):
        response = self.client.get("/health")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["analysis_engine"], "unit_test_pose")
        self.assertEqual(payload["pose_model"], "models/unit-test.task")
        self.assertTrue(payload["database"].endswith("test.db"))

    def test_auth_login_me_and_logout(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "12345678"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["user"]["username"], "admin")
        self.assertTrue(payload["token"])

        headers = {"Authorization": f"Bearer {payload['token']}"}
        me_response = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_response.status_code, 200)
        me_payload = me_response.get_json()
        self.assertEqual(me_payload["user"]["username"], "admin")
        self.assertFalse(me_payload["billing"]["advanced_enabled"])

        logout_response = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)

    def test_auth_register_creates_user(self):
        response = self.client.post(
            "/api/auth/register",
            json={"username": "school01", "password": "12345678"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["user"]["username"], "school01")
        self.assertTrue(payload["token"])
        self.assertFalse(payload["billing"]["advanced_enabled"])

    def test_billing_checkout_enables_advanced(self):
        plans_response = self.client.get("/api/billing/plans")
        plans_payload = plans_response.get_json()
        self.assertEqual(plans_response.status_code, 200)
        self.assertTrue(any(plan["id"] == "individual_one_time" for plan in plans_payload["plans"]))
        self.assertTrue(any(plan["id"] == "individual_annual" for plan in plans_payload["plans"]))
        self.assertTrue(any(plan["id"] == "corporate_annual" for plan in plans_payload["plans"]))
        self.assertTrue(any(plan["id"] == "corporate_network_monthly" for plan in plans_payload["plans"]))

        status_response = self.client.get("/api/billing/status", headers=self.auth_headers)
        self.assertEqual(status_response.status_code, 200)
        self.assertFalse(status_response.get_json()["advanced_enabled"])

        checkout_response = self.client.post(
            "/api/billing/checkout",
            json={"plan_id": "corporate_monthly", "organization_name": "School 42"},
            headers=self.auth_headers,
        )
        checkout_payload = checkout_response.get_json()

        self.assertEqual(checkout_response.status_code, 200)
        self.assertTrue(checkout_payload["advanced_enabled"])
        self.assertEqual(checkout_payload["subscription"]["audience"], "corporate")
        self.assertEqual(checkout_payload["subscription"]["organization_name"], "School 42")

    def test_analyze_requires_login(self):
        response = self.client.post("/api/analyze", data={})
        payload = response.get_json()

        self.assertEqual(response.status_code, 401)
        self.assertIn("Войдите", payload["error"])

    def test_analyze_accepts_multipart_image_and_returns_report(self):
        response = self.client.post(
            "/api/analyze",
            data={
                "student_id": "7B-014",
                "image": (io.BytesIO(jpeg_bytes()), "student.jpg"),
            },
            content_type="multipart/form-data",
            headers=self.auth_headers,
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
        self.assertEqual(len(payload["care_plan"]), 4)
        self.assertEqual(payload["care_plan"][0]["level"], "urgent")
        self.assertTrue(payload["overlay_image"].startswith("data:image/jpeg;base64,"))

        stored = self.storage.get_report(self.admin["id"], payload["report_id"])
        self.assertIsNotNone(stored)
        self.assertIn("single", stored["images"])
        self.assertNotIn("overlay_image", stored["report"])

        restored_response = self.client.get(f"/api/reports/{payload['report_id']}", headers=self.auth_headers)
        self.assertEqual(restored_response.status_code, 200)
        self.assertTrue(restored_response.get_json()["overlay_image"].startswith("data:image/jpeg;base64,"))

    def test_advanced_protocol_requires_paid_plan(self):
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
            headers=self.auth_headers,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 402)
        self.assertEqual(payload["code"], "advanced_required")
        self.assertIn("Advanced", payload["error"])

    def test_advanced_protocol_requires_all_five_views(self):
        self.storage.activate_plan(self.admin["id"], "individual_one_time")

        response = self.client.post(
            "/api/analyze",
            data={
                "student_id": "7B-014",
                "image_front": (io.BytesIO(jpeg_bytes()), "front.jpg"),
            },
            content_type="multipart/form-data",
            headers=self.auth_headers,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("все 5 ракурсов", payload["error"])

    def test_analyze_accepts_five_view_protocol(self):
        self.storage.activate_plan(self.admin["id"], "individual_monthly")

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
            headers=self.auth_headers,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mode"], "multi_view")
        self.assertEqual(payload["student_id"], "7B-014")
        self.assertEqual(payload["view_count"], 5)
        self.assertEqual(payload["views_completed"], 5)
        self.assertEqual(len(payload["views"]), 5)
        self.assertEqual(payload["risk"]["level"], "high")
        self.assertEqual(payload["risk"]["finding_count"], 9)
        self.assertEqual(payload["risk"]["total_metrics"], 15)
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
        self.assertEqual(len(payload["care_plan"]), 4)
        self.assertTrue(any(item["title"] == "Ортопед приоритетно" for item in payload["care_plan"]))
        self.assertTrue(payload["overlay_image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(
            [view["view_key"] for view in payload["views"]],
            ["front", "back", "left", "right", "adams"],
        )
        side_views = [view for view in payload["views"] if view["view_key"] in {"left", "right"}]
        self.assertEqual(len(side_views), 2)
        self.assertTrue(all(view["view_role"] == "profile" for view in side_views))
        self.assertTrue(all(view["risk"]["total_metrics"] == 0 for view in side_views))
        self.assertTrue(all(view["risk"]["score"] == 0 for view in side_views))

        stored = self.storage.get_report(self.admin["id"], payload["report_id"])
        self.assertEqual(set(stored["images"]), {"front", "back", "left", "right", "adams"})

        reports_response = self.client.get("/api/reports", headers=self.auth_headers)
        self.assertEqual(reports_response.status_code, 200)
        self.assertTrue(any(item["report_id"] == payload["report_id"] for item in reports_response.get_json()["reports"]))

    def test_analyze_rejects_empty_request(self):
        response = self.client.post("/api/analyze", data={}, headers=self.auth_headers)
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("хотя бы одно фото", payload["error"])

    def test_report_id_sanitizes_student_id(self):
        timestamp = datetime(2026, 7, 8, 9, 30, 4, tzinfo=timezone.utc)

        self.assertEqual(
            api_server._report_id("7 B/014", timestamp),
            "7_B_014_20260708_093004",
        )

    def test_care_plan_handles_zero_risk(self):
        plan = api_server._care_plan("low", 0, True)

        self.assertEqual(plan[0]["level"], "ok")
        self.assertEqual(plan[0]["title"], "Плановый контроль")
        self.assertTrue(any("Обычная активность" == item["title"] for item in plan))

    def test_care_plan_handles_missing_landmarks(self):
        plan = api_server._care_plan("unknown", 0, False)

        self.assertEqual(plan[0]["level"], "neutral")
        self.assertEqual(plan[0]["title"], "Переснять протокол")

    def test_side_view_excludes_frontal_tilt_metrics(self):
        screening = ScreeningResult(
            landmarks_found=True,
            shoulder_tilt_deg=84.0,
            hip_tilt_deg=76.0,
            head_tilt_deg=42.0,
            trunk_shift_ratio=0.4,
            waist_asym_ratio=0.3,
            flags={
                "shoulder_tilt": True,
                "hip_tilt": True,
                "head_tilt": True,
                "trunk_shift": True,
                "waist_asymmetry": True,
            },
            risk_level="high",
            message="profile false positive",
            analysis_engine="unit_test",
            quality_score=0.88,
        )

        adjusted = api_server._screening_for_view(screening, "left")
        payload = api_server._build_view_response(
            {"key": "left", "label": "Левый бок"},
            adjusted,
            np.zeros((16, 16, 3), dtype=np.uint8),
        )

        self.assertEqual(payload["view_role"], "profile")
        self.assertFalse(payload["metrics_applicable"])
        self.assertEqual(payload["risk"]["level"], "low")
        self.assertEqual(payload["risk"]["finding_count"], 0)
        self.assertEqual(payload["risk"]["total_metrics"], 0)
        self.assertEqual(payload["risk"]["score"], 0)
        self.assertEqual(payload["metric_cards"], [])
        self.assertTrue(all(not value for value in payload["flags"].values()))


if __name__ == "__main__":
    unittest.main()
