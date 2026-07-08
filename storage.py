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
PLAN_CATALOG = [
    {
        "id": "individual_one_time",
        "name": "Advanced разово",
        "audience": "individual",
        "audience_label": "Индивидуальный",
        "billing": "one_time",
        "billing_label": "разовый доступ",
        "price_usd": 10,
        "period": "once",
        "duration_days": None,
        "advanced_enabled": True,
        "description": "Пять ракурсов для разовой расширенной проверки.",
        "features": ["Однократная оплата", "Отчёт с разметкой", "История результата"],
    },
    {
        "id": "individual_monthly",
        "name": "Advanced месяц",
        "audience": "individual",
        "audience_label": "Индивидуальный",
        "billing": "monthly",
        "billing_label": "ежемесячно",
        "price_usd": 25,
        "period": "month",
        "duration_days": 30,
        "advanced_enabled": True,
        "description": "Неограниченный Advanced-анализ для личного аккаунта.",
        "features": ["Advanced без лимита", "История отчётов", "Подходит для семьи"],
    },
    {
        "id": "individual_annual",
        "name": "Advanced год",
        "audience": "individual",
        "audience_label": "Индивидуальный",
        "billing": "annual",
        "billing_label": "ежегодно",
        "price_usd": 199,
        "period": "year",
        "duration_days": 365,
        "advanced_enabled": True,
        "description": "Годовой доступ к Advanced-анализу с выгодой по сравнению с оплатой помесячно.",
        "features": ["12 месяцев доступа", "Экономия против месячного", "История динамики"],
    },
    {
        "id": "corporate_monthly",
        "name": "School Advanced месяц",
        "audience": "corporate",
        "audience_label": "Корпоративный",
        "billing": "monthly",
        "billing_label": "ежемесячно",
        "price_usd": 99,
        "period": "month",
        "duration_days": 30,
        "advanced_enabled": True,
        "description": "Расширенный протокол для школ, классов и медкабинетов.",
        "features": ["Классы и медкабинет", "Локальная история", "Ежемесячная оплата"],
    },
    {
        "id": "corporate_annual",
        "name": "School Advanced год",
        "audience": "corporate",
        "audience_label": "Корпоративный",
        "billing": "annual",
        "billing_label": "ежегодно",
        "price_usd": 999,
        "period": "year",
        "duration_days": 365,
        "advanced_enabled": True,
        "description": "Годовой тариф для одной школы с постоянным доступом к Advanced-протоколу.",
        "features": ["Годовой школьный доступ", "Профосмотры по графику", "Архив отчётов"],
    },
    {
        "id": "corporate_network_monthly",
        "name": "District Advanced",
        "audience": "corporate",
        "audience_label": "Корпоративный",
        "billing": "monthly",
        "billing_label": "ежемесячно",
        "price_usd": 249,
        "period": "month",
        "duration_days": 30,
        "advanced_enabled": True,
        "description": "Тариф для сети школ, районного проекта или нескольких медкабинетов.",
        "features": ["Несколько школ", "Массовый скрининг", "Единый цифровой архив"],
    },
]
PLAN_BY_ID = {plan["id"]: plan for plan in PLAN_CATALOG}


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
                status TEXT NOT NULL,
                advanced_enabled INTEGER NOT NULL,
                organization_name TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
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
        return [dict(plan) for plan in PLAN_CATALOG]

    def billing_status(self, user_id: int) -> dict[str, Any]:
        subscription = self.active_subscription(user_id)
        return {
            "advanced_enabled": bool(subscription and subscription.get("advanced_enabled")),
            "subscription": subscription,
        }

    def has_advanced_access(self, user_id: int) -> bool:
        status = self.billing_status(user_id)
        return bool(status["advanced_enabled"])

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

    def activate_plan(self, user_id: int, plan_id: str, organization_name: str = "") -> dict[str, Any]:
        plan = PLAN_BY_ID.get(plan_id)
        if not plan:
            raise ValueError("Тариф не найден.")

        now = datetime.now(timezone.utc)
        duration_days = plan.get("duration_days")
        expires_at = (now + timedelta(days=int(duration_days))).isoformat() if duration_days else None
        organization = organization_name.strip()[:120] or None

        with self.connect() as connection:
            connection.execute(
                "UPDATE subscriptions SET status = 'replaced' WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            connection.execute(
                """
                INSERT INTO subscriptions (
                    user_id, plan_id, audience, billing, price_usd,
                    status, advanced_enabled, organization_name, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    plan["id"],
                    plan["audience"],
                    plan["billing"],
                    int(plan["price_usd"]),
                    1 if plan["advanced_enabled"] else 0,
                    organization,
                    now.isoformat(),
                    expires_at,
                ),
            )

        subscription = self.active_subscription(user_id)
        if not subscription:
            raise ValueError("Не удалось активировать тариф.")
        return subscription

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


def subscription_payload(row: dict[str, Any]) -> dict[str, Any]:
    plan = PLAN_BY_ID.get(row["plan_id"], {})
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "name": plan.get("name", row["plan_id"]),
        "audience": row["audience"],
        "audience_label": plan.get("audience_label", row["audience"]),
        "billing": row["billing"],
        "billing_label": plan.get("billing_label", row["billing"]),
        "price_usd": row["price_usd"],
        "status": row["status"],
        "advanced_enabled": bool(row["advanced_enabled"]),
        "organization_name": row.get("organization_name") or "",
        "created_at": row["created_at"],
        "expires_at": row.get("expires_at"),
    }
