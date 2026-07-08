import json
import tempfile
import unittest
from pathlib import Path

from storage import ScolioScanStorage


class StorageTests(unittest.TestCase):
    def test_initialize_creates_admin_with_hashed_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ScolioScanStorage(Path(temp_dir) / "scolioscan.db")
            storage.initialize(migrate_reports=False)

            admin = storage.authenticate_user("admin", "12345678")
            self.assertIsNotNone(admin)

            with storage.connect() as connection:
                row = connection.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()

            self.assertIn("pbkdf2_sha256", row["password_hash"])
            self.assertNotIn("12345678", row["password_hash"])

    def test_subscription_activation_enables_advanced_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ScolioScanStorage(Path(temp_dir) / "scolioscan.db")
            storage.initialize(migrate_reports=False)
            admin = storage.authenticate_user("admin", "12345678")

            self.assertFalse(storage.has_advanced_access(admin["id"]))

            subscription = storage.activate_plan(admin["id"], "individual_monthly")
            status = storage.billing_status(admin["id"])

            self.assertEqual(subscription["plan_id"], "individual_monthly")
            self.assertTrue(status["advanced_enabled"])
            self.assertEqual(status["subscription"]["price_usd"], 25)
            self.assertEqual(status["subscription"]["audience"], "individual")

    def test_migrates_legacy_reports_under_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            reports_dir.mkdir()
            image_path = reports_dir / "legacy.jpg"
            image_path.write_bytes(b"jpeg-bytes")

            report_payload = {
                "report_id": "legacy_20260708_120000",
                "student_id": "LEGACY",
                "timestamp": "2026-07-08T12:00:00+00:00",
                "mode": "single",
                "risk": {"level": "low", "score": 0},
                "image_file": str(image_path),
            }
            (reports_dir / "legacy_20260708_120000.json").write_text(
                json.dumps(report_payload, ensure_ascii=False),
                encoding="utf-8",
            )

            storage = ScolioScanStorage(root / "scolioscan.db")
            storage.initialize(reports_dir=reports_dir, migrate_reports=True)
            admin = storage.authenticate_user("admin", "12345678")
            stored = storage.get_report(admin["id"], "legacy_20260708_120000")

            self.assertIsNotNone(stored)
            self.assertEqual(stored["report"]["student_id"], "LEGACY")
            self.assertNotIn("image_file", stored["report"])
            self.assertEqual(stored["images"]["single"], b"jpeg-bytes")


if __name__ == "__main__":
    unittest.main()
