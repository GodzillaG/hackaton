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

    def test_plus_subscription_enables_advanced_access_and_quotas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ScolioScanStorage(Path(temp_dir) / "scolioscan.db")
            storage.initialize(migrate_reports=False)
            admin = storage.authenticate_user("admin", "12345678")

            self.assertFalse(storage.has_advanced_access(admin["id"]))
            self.assertEqual(storage.billing_status(admin["id"])["remaining"]["basic"], 5)

            subscription = storage.activate_plan(admin["id"], "plus")
            status = storage.billing_status(admin["id"])

            self.assertEqual(subscription["plan_id"], "plus")
            self.assertTrue(status["advanced_enabled"])
            self.assertEqual(status["subscription"]["price_usd"], 19)
            self.assertEqual(status["subscription"]["audience"], "individual")
            self.assertEqual(status["remaining"], {"basic": 80, "advanced": 4})

    def test_corporate_student_verification_uses_school_resources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ScolioScanStorage(Path(temp_dir) / "scolioscan.db")
            storage.initialize(migrate_reports=False)
            admin = storage.authenticate_user("admin", "12345678")

            subscription = storage.verify_student_access(admin["id"], "SCHOOL-ACCESS-2026", "7B-014")
            status = storage.billing_status(admin["id"])

            self.assertEqual(subscription["plan_id"], "corporate")
            self.assertEqual(subscription["organization_name"], "ScolioScan Partner School")
            self.assertEqual(subscription["student_external_id"], "7B-014")
            self.assertIsNotNone(subscription["expires_at"])
            self.assertEqual(subscription["billing"], "custom")
            self.assertEqual(status["remaining"], {"basic": 2000, "advanced": 300})

    def test_usage_records_reduce_current_period_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ScolioScanStorage(Path(temp_dir) / "scolioscan.db")
            storage.initialize(migrate_reports=False)
            admin = storage.authenticate_user("admin", "12345678")

            storage.record_usage(admin["id"], "report-1", "basic")
            status = storage.billing_status(admin["id"])

            self.assertEqual(status["usage"]["basic"], 1)
            self.assertEqual(status["remaining"]["basic"], 4)

    def test_initialize_updates_legacy_plus_advanced_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scolioscan.db"
            storage = ScolioScanStorage(db_path)
            storage.initialize(migrate_reports=False)
            admin = storage.authenticate_user("admin", "12345678")

            with storage.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO subscriptions (
                        user_id, plan_id, audience, billing, price_usd,
                        price_label, status, advanced_enabled, basic_quota,
                        advanced_quota, organization_name, student_external_id,
                        created_at, expires_at
                    )
                    VALUES (?, 'plus', 'individual', 'monthly', 19, '$19/мес',
                            'active', 1, 80, 8, NULL, NULL, ?, NULL)
                    """,
                    (admin["id"], "2026-07-09T00:00:00+00:00"),
                )

            storage = ScolioScanStorage(db_path)
            storage.initialize(migrate_reports=False)
            status = storage.billing_status(admin["id"])

            self.assertEqual(status["remaining"], {"basic": 80, "advanced": 4})

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
