# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "12345678"
DEFAULT_DB_PATH = Path("data") / "scolioscan.db"
PASSWORD_ITERATIONS = 260_000
DEFAULT_CORPORATE_CODE = "SCHOOL-ACCESS-2026"
DEFAULT_CORPORATE_ORG = "ScolioScan Partner School"
PLAN_CATALOG = [
    {
        "id": "free",
        "name": "Free",
        "audience": "individual",
        "audience_label": "Индивидуальный",
        "billing": "free",
        "billing_label": "бесплатно",
        "price_usd": 0,
        "price_label": "$0",
        "period": "month",
        "duration_days": None,
        "basic_quota": 5,
        "advanced_quota": 0,
        "advanced_enabled": False,
        "description": "Стартовый уровень для быстрых индивидуальных проверок.",
        "features": ["5 Basic-анализов в месяц", "История отчётов", "Подходит для первичной проверки"],
    },
    {
        "id": "plus",
        "name": "Plus",
        "audience": "individual",
        "audience_label": "Индивидуальный",
        "billing": "monthly",
        "billing_label": "ежемесячно",
        "price_usd": 19,
        "price_label": "$19/мес",
        "period": "month",
        "duration_days": 30,
        "basic_quota": 80,
        "advanced_quota": 4,
        "advanced_enabled": True,
        "description": "Больше быстрых проверок и ежемесячный пакет Advanced-протоколов.",
        "features": ["80 Basic-анализов в месяц", "4 Advanced-анализа в месяц", "Для семьи и регулярного контроля"],
    },
    {
        "id": "corporate",
        "name": "Corporate",
        "audience": "corporate",
        "audience_label": "Корпоративный",
        "billing": "custom",
        "billing_label": "по договору",
        "price_usd": 0,
        "price_label": "Custom",
        "period": "custom",
        "duration_days": 30,
        "basic_quota": 2000,
        "advanced_quota": 300,
        "advanced_enabled": True,
        "description": "Масштабируемый доступ для школ: цена и лимиты фиксируются в договоре.",
        "features": ["Подтверждение статуса ученика", "Согласованные лимиты школы", "Для классов и медкабинетов"],
        "requires_student_verification": True,
    },
]
PLAN_BY_ID = {plan["id"]: plan for plan in PLAN_CATALOG}
LEGACY_PLAN_ALIASES = {
    "individual_one_time": "plus",
    "individual_monthly": "plus",
    "individual_annual": "plus",
    "corporate_monthly": "corporate",
    "corporate_annual": "corporate",
    "corporate_network_monthly": "corporate",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _code_hash(code: str) -> str:
    normalized = (code or "").strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def current_period_key(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.strftime("%Y-%m")


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_report_image_path(raw_path: str, reports_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute() and path.exists():
        return path
    if path.exists():
        return path
    return reports_dir / path.name


class ScolioScanStorage:
    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.environ.get("SCOLISCAN_DB") or DEFAULT_DB_PATH
        self.db_path = Path(configured)
        self._initialized = False

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self, reports_dir: str | Path = "reports", migrate_reports: bool = True) -> None:
        if self._initialized:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            self._apply_schema(connection)
            admin = self.ensure_user(ADMIN_USERNAME, ADMIN_PASSWORD, connection=connection)

        if migrate_reports:
            self.migrate_reports_directory(Path(reports_dir), admin["id"])

        self._initialized = True

    def _apply_schema(self, connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                student_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                report_json TEXT NOT NULL,
                migrated_from TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS report_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id TEXT NOT NULL,
                view_key TEXT NOT NULL,
                image_jpeg BLOB NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(report_id, view_key),
                FOREIGN KEY(report_id) REFERENCES reports(report_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id TEXT NOT NULL,
                audience TEXT NOT NULL,
                billing TEXT NOT NULL,
                price_usd INTEGER NOT NULL,
                price_label TEXT,
                status TEXT NOT NULL,
                advanced_enabled INTEGER NOT NULL,
                basic_quota INTEGER,
                advanced_quota INTEGER,
                organization_name TEXT,
                student_external_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                report_id TEXT NOT NULL,
                analysis_type TEXT NOT NULL,
                period_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS corporate_access_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT NOT NULL UNIQUE,
                organization_name TEXT NOT NULL,
                basic_quota INTEGER NOT NULL,
                advanced_quota INTEGER NOT NULL,
                active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._ensure_column(connection, "subscriptions", "price_label", "TEXT")
        self._ensure_column(connection, "subscriptions", "basic_quota", "INTEGER")
        self._ensure_column(connection, "subscriptions", "advanced_quota", "INTEGER")
        self._ensure_column(connection, "subscriptions", "student_external_id", "TEXT")
        self._ensure_default_corporate_code(connection)
        self._sync_plan_quota_defaults(connection)

    @staticmethod
    def _ensure_column(connection, table_name: str, column_name: str, definition: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        if column_name not in {row["name"] for row in rows}:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _ensure_default_corporate_code(self, connection) -> None:
        code_hash = _code_hash(DEFAULT_CORPORATE_CODE)
        exists = connection.execute(
            "SELECT 1 FROM corporate_access_codes WHERE code_hash = ?",
            (code_hash,),
        ).fetchone()
        if exists:
            return
        corporate_plan = plan_by_id("corporate")
        connection.execute(
            """
            INSERT INTO corporate_access_codes (
                code_hash, organization_name, basic_quota,
                advanced_quota, active, created_at
            )
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                code_hash,
                DEFAULT_CORPORATE_ORG,
                int(corporate_plan["basic_quota"]),
                int(corporate_plan["advanced_quota"]),
                utc_now(),
            ),
        )

    def _sync_plan_quota_defaults(self, connection) -> None:
        connection.execute(
            """
            UPDATE subscriptions
            SET advanced_quota = ?
            WHERE plan_id IN ('plus', 'individual_one_time', 'individual_monthly', 'individual_annual')
              AND advanced_quota = 8
            """,
            (int(plan_by_id("plus")["advanced_quota"]),),
        )
        connection.execute(
            """
            UPDATE subscriptions
            SET basic_quota = ?
            WHERE plan_id = 'free'
              AND basic_quota = 10
            """,
            (int(plan_by_id("free")["basic_quota"]),),
        )

    def ensure_user(self, username: str, password: str, connection=None) -> dict[str, Any]:
        username = normalize_username(username)
        owns_connection = connection is None
        if owns_connection:
            connection = self.connect()

        try:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return dict(row)

            connection.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, _password_hash(password), utc_now()),
            )
            if owns_connection:
                connection.commit()
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return dict(row)
        finally:
            if owns_connection:
                connection.close()

    def create_user(self, username: str, password: str) -> dict[str, Any]:
        username = normalize_username(username)
        validate_password(password)
        with self.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, _password_hash(password), utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Пользователь уже существует.") from exc

            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return public_user(dict(row))

    def authenticate_user(self, username: str, password: str) -> dict[str, Any] | None:
        username = normalize_username(username)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row or not _verify_password(password, row["password_hash"]):
                return None
            return dict(row)

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
                (_token_hash(token), user_id, utc_now()),
            )
        return token

    def user_by_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                """,
                (_token_hash(token),),
            ).fetchone()
            return dict(row) if row else None

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def list_plans(self) -> list[dict[str, Any]]:
        return [public_plan(plan) for plan in PLAN_CATALOG]

    def billing_status(self, user_id: int) -> dict[str, Any]:
        return self.entitlement_for_user(user_id)

    def has_advanced_access(self, user_id: int) -> bool:
        status = self.entitlement_for_user(user_id)
        return int(status["remaining"]["advanced"]) > 0

    def entitlement_for_user(self, user_id: int) -> dict[str, Any]:
        subscription = self.active_subscription(user_id)
        plan = plan_by_id(subscription["plan_id"] if subscription else "free")
        period_key = current_period_key()
        used = self.usage_for_period(user_id, period_key)
        limits = {
            "basic": int(subscription.get("basic_quota") if subscription else plan["basic_quota"]),
            "advanced": int(subscription.get("advanced_quota") if subscription else plan["advanced_quota"]),
        }
        remaining = {
            "basic": max(0, limits["basic"] - used["basic"]),
            "advanced": max(0, limits["advanced"] - used["advanced"]),
        }

        return {
            "advanced_enabled": remaining["advanced"] > 0,
            "subscription": subscription,
            "plan": public_plan(plan),
            "period_key": period_key,
            "usage": used,
            "limits": limits,
            "remaining": remaining,
        }

    def usage_for_period(self, user_id: int, period_key: str) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT analysis_type, COUNT(*) AS count
                FROM usage_events
                WHERE user_id = ? AND period_key = ?
                GROUP BY analysis_type
                """,
                (user_id, period_key),
            ).fetchall()

        counts = {row["analysis_type"]: int(row["count"]) for row in rows}
        return {
            "basic": counts.get("basic", 0),
            "advanced": counts.get("advanced", 0),
        }

    def analysis_allowance(self, user_id: int, analysis_type: str) -> dict[str, Any]:
        if analysis_type not in {"basic", "advanced"}:
            raise ValueError("Некорректный тип анализа.")

        status = self.entitlement_for_user(user_id)
        remaining = int(status["remaining"][analysis_type])
        allowed = remaining > 0
        if allowed:
            return {"allowed": True, "billing": status}

        if analysis_type == "advanced" and status["plan"]["id"] == "free":
            message = "Advanced-анализ доступен на Plus или Corporate."
            code = "advanced_required"
        else:
            message = "Лимит анализов на текущем уровне закончился."
            code = "quota_exceeded"

        return {
            "allowed": False,
            "error": message,
            "code": code,
            "billing": status,
        }

    def record_usage(self, user_id: int, report_id: str, analysis_type: str) -> None:
        if analysis_type not in {"basic", "advanced"}:
            raise ValueError("Некорректный тип анализа.")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_events (user_id, report_id, analysis_type, period_key, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, report_id, analysis_type, current_period_key(), utc_now()),
            )

    def active_subscription(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM subscriptions
                WHERE user_id = ?
                  AND status = 'active'
                  AND advanced_enabled = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id, utc_now()),
            ).fetchone()
        return subscription_payload(dict(row)) if row else None

    def activate_plan(
        self,
        user_id: int,
        plan_id: str,
        organization_name: str = "",
        basic_quota: int | None = None,
        advanced_quota: int | None = None,
        student_external_id: str = "",
    ) -> dict[str, Any]:
        plan = plan_by_id(plan_id)

        now = datetime.now(timezone.utc)
        duration_days = plan.get("duration_days")
        expires_at = (now + timedelta(days=int(duration_days))).isoformat() if duration_days else None
        organization = organization_name.strip()[:120] or None
        student_id = student_external_id.strip()[:80] or None
        resolved_basic_quota = int(basic_quota if basic_quota is not None else plan["basic_quota"])
        resolved_advanced_quota = int(advanced_quota if advanced_quota is not None else plan["advanced_quota"])

        with self.connect() as connection:
            connection.execute(
                "UPDATE subscriptions SET status = 'replaced' WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            connection.execute(
                """
                INSERT INTO subscriptions (
                    user_id, plan_id, audience, billing, price_usd,
                    price_label, status, advanced_enabled, basic_quota,
                    advanced_quota, organization_name, student_external_id,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    plan["id"],
                    plan["audience"],
                    plan["billing"],
                    int(plan["price_usd"]),
                    plan.get("price_label", ""),
                    1 if plan["advanced_enabled"] else 0,
                    resolved_basic_quota,
                    resolved_advanced_quota,
                    organization,
                    student_id,
                    now.isoformat(),
                    expires_at,
                ),
            )

        subscription = self.active_subscription(user_id)
        if not subscription:
            raise ValueError("Не удалось активировать тариф.")
        return subscription

    def verify_student_access(self, user_id: int, school_code: str, student_external_id: str) -> dict[str, Any]:
        normalized_student_id = student_external_id.strip()
        if len(normalized_student_id) < 2:
            raise ValueError("Введите ID ученика.")

        code_hash = _code_hash(school_code)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM corporate_access_codes
                WHERE code_hash = ? AND active = 1
                """,
                (code_hash,),
            ).fetchone()

        if not row:
            raise ValueError("Код школы не найден или больше не активен.")

        return self.activate_plan(
            user_id,
            "corporate",
            organization_name=row["organization_name"],
            basic_quota=int(row["basic_quota"]),
            advanced_quota=int(row["advanced_quota"]),
            student_external_id=normalized_student_id,
        )

    def save_report(
        self,
        user_id: int,
        report_payload: dict[str, Any],
        images: dict[str, bytes] | None = None,
        migrated_from: str | None = None,
    ) -> None:
        report_id = str(report_payload["report_id"])
        student_id = str(report_payload.get("student_id") or "unknown")
        risk = report_payload.get("risk") or {}
        created_at = str(report_payload.get("timestamp") or utc_now())
        mode = str(report_payload.get("mode") or "single")
        risk_level = str(risk.get("level") or "unknown")
        risk_score = int(risk.get("score") or 0)

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reports (
                    report_id, user_id, student_id, created_at, mode,
                    risk_level, risk_score, report_json, migrated_from
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    student_id = excluded.student_id,
                    created_at = excluded.created_at,
                    mode = excluded.mode,
                    risk_level = excluded.risk_level,
                    risk_score = excluded.risk_score,
                    report_json = excluded.report_json,
                    migrated_from = excluded.migrated_from
                """,
                (
                    report_id,
                    user_id,
                    student_id,
                    created_at,
                    mode,
                    risk_level,
                    risk_score,
                    _json_dumps(report_payload),
                    migrated_from,
                ),
            )

            if images:
                connection.execute("DELETE FROM report_images WHERE report_id = ?", (report_id,))
                for view_key, image_bytes in images.items():
                    if image_bytes:
                        connection.execute(
                            """
                            INSERT INTO report_images (report_id, view_key, image_jpeg, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (report_id, view_key, sqlite3.Binary(image_bytes), utc_now()),
                        )

    def list_reports(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT report_id, student_id, created_at, mode, risk_level, risk_score
                FROM reports
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_report(self, user_id: int, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM reports WHERE user_id = ? AND report_id = ?",
                (user_id, report_id),
            ).fetchone()
            if not row:
                return None

            image_rows = connection.execute(
                "SELECT view_key, image_jpeg FROM report_images WHERE report_id = ?",
                (report_id,),
            ).fetchall()

        return {
            "report": json.loads(row["report_json"]),
            "images": {image_row["view_key"]: bytes(image_row["image_jpeg"]) for image_row in image_rows},
        }

    def migrate_reports_directory(self, reports_dir: Path, user_id: int) -> int:
        if not reports_dir.exists():
            return 0

        migrated = 0
        for json_path in sorted(reports_dir.glob("*.json")):
            payload = _read_json_file(json_path)
            if not payload or not payload.get("report_id"):
                continue

            images = {}
            image_file = payload.get("image_file")
            if isinstance(image_file, str):
                path = _resolve_report_image_path(image_file, reports_dir)
                if path.exists():
                    images["single"] = path.read_bytes()

            image_files = payload.get("image_files")
            if isinstance(image_files, dict):
                for view_key, raw_path in image_files.items():
                    if not isinstance(raw_path, str):
                        continue
                    path = _resolve_report_image_path(raw_path, reports_dir)
                    if path.exists():
                        images[str(view_key)] = path.read_bytes()

            payload.pop("image_file", None)
            payload.pop("image_files", None)
            self.save_report(user_id, payload, images, migrated_from=str(json_path))
            migrated += 1

        return migrated


def normalize_username(username: str) -> str:
    value = (username or "").strip()
    if not 3 <= len(value) <= 32:
        raise ValueError("Логин должен быть от 3 до 32 символов.")
    if not all(ch.isalnum() or ch in "._-" for ch in value):
        raise ValueError("В логине можно использовать буквы, цифры, точку, дефис и подчёркивание.")
    return value


def validate_password(password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("Пароль должен быть не короче 8 символов.")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"],
    }


def plan_by_id(plan_id: str) -> dict[str, Any]:
    resolved_id = LEGACY_PLAN_ALIASES.get(plan_id, plan_id)
    plan = PLAN_BY_ID.get(resolved_id)
    if not plan:
        raise ValueError("Тариф не найден.")
    return plan


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plan["id"],
        "name": plan["name"],
        "audience": plan["audience"],
        "audience_label": plan["audience_label"],
        "billing": plan["billing"],
        "billing_label": plan["billing_label"],
        "price_usd": plan["price_usd"],
        "price_label": plan.get("price_label") or _plan_price_label(plan),
        "period": plan["period"],
        "basic_quota": int(plan["basic_quota"]),
        "advanced_quota": int(plan["advanced_quota"]),
        "advanced_enabled": bool(plan["advanced_enabled"]),
        "description": plan["description"],
        "features": list(plan.get("features", [])),
        "requires_student_verification": bool(plan.get("requires_student_verification", False)),
    }


def _plan_price_label(plan: dict[str, Any]) -> str:
    if plan["billing"] == "custom":
        return "Custom"
    if plan["price_usd"] == 0:
        return "$0"
    suffix = "/мес" if plan["period"] == "month" else ""
    return f"${plan['price_usd']}{suffix}"


def subscription_payload(row: dict[str, Any]) -> dict[str, Any]:
    plan = plan_by_id(row["plan_id"])
    return {
        "id": row["id"],
        "plan_id": plan["id"],
        "source_plan_id": row["plan_id"],
        "name": plan["name"],
        "audience": row["audience"],
        "audience_label": plan["audience_label"],
        "billing": row["billing"],
        "billing_label": plan["billing_label"],
        "price_usd": row["price_usd"],
        "price_label": row.get("price_label") or plan.get("price_label") or _plan_price_label(plan),
        "status": row["status"],
        "advanced_enabled": bool(row["advanced_enabled"]),
        "basic_quota": int(row.get("basic_quota") or plan["basic_quota"]),
        "advanced_quota": int(row.get("advanced_quota") or plan["advanced_quota"]),
        "organization_name": row.get("organization_name") or "",
        "student_external_id": row.get("student_external_id") or "",
        "created_at": row["created_at"],
        "expires_at": row.get("expires_at"),
    }
