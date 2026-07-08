# -*- coding: utf-8 -*-
"""
Flask backend for ScolioScan School.

Run locally:
    uv run python api_server.py --host 0.0.0.0 --port 5000

The Next.js app sends a multipart/form-data request with an image file to
POST /api/analyze. The response includes risk level, posture metrics,
recommendations, and an annotated preview image.
"""

import argparse
import base64
import json
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import cv2
import numpy as np
from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from pose_analyzer import ScoliosisScreeningAnalyzer, THRESHOLDS
from storage import ScolioScanStorage, normalize_username, public_user, validate_password

REPORTS_DIR = Path("reports")
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_IMAGE_SIDE = 1280

VIEW_DEFS = [
    {"key": "front", "field": "image_front", "label": "Вид спереди"},
    {"key": "back", "field": "image_back", "label": "Вид со спины"},
    {"key": "left", "field": "image_left", "label": "Левый бок"},
    {"key": "right", "field": "image_right", "label": "Правый бок"},
    {"key": "adams", "field": "image_adams", "label": "Тест Адамса"},
]
RISK_RANK = {"unknown": 0, "low": 1, "moderate": 2, "high": 3}
SIDE_VIEW_KEYS = {"left", "right"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

_analyzer: ScoliosisScreeningAnalyzer | None = None
_storage: ScolioScanStorage | None = None


def get_analyzer() -> ScoliosisScreeningAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ScoliosisScreeningAnalyzer(static_image_mode=True)
    return _analyzer


def get_storage() -> ScolioScanStorage:
    global _storage
    if _storage is None:
        _storage = ScolioScanStorage()
        _storage.initialize(reports_dir=REPORTS_DIR)
    return _storage


METRIC_DEFS = [
    {
        "key": "shoulder_tilt_deg",
        "flag": "shoulder_tilt",
        "title": "Наклон плеч",
        "description": "Разница высоты левого и правого плеча",
        "unit": "deg",
        "scale": 1.0,
    },
    {
        "key": "hip_tilt_deg",
        "flag": "hip_tilt",
        "title": "Наклон таза",
        "description": "Разница высоты опорных точек таза",
        "unit": "deg",
        "scale": 1.0,
    },
    {
        "key": "head_tilt_deg",
        "flag": "head_tilt",
        "title": "Наклон головы",
        "description": "Отклонение линии ушей от горизонтали",
        "unit": "deg",
        "scale": 1.0,
    },
    {
        "key": "trunk_shift_ratio",
        "flag": "trunk_shift",
        "title": "Смещение корпуса",
        "description": "Смещение центра плеч относительно центра таза",
        "unit": "%",
        "scale": 100.0,
    },
    {
        "key": "waist_asym_ratio",
        "flag": "waist_asymmetry",
        "title": "Асимметрия талии",
        "description": "Разница расстояний локоть-таз слева и справа",
        "unit": "%",
        "scale": 100.0,
    },
]


RISK_PROFILE = {
    "low": {
        "label": "Низкий риск",
        "accent": "green",
        "headline": "Критичных признаков асимметрии не найдено",
    },
    "moderate": {
        "label": "Средний риск",
        "accent": "amber",
        "headline": "Есть отдельные признаки асимметрии",
    },
    "high": {
        "label": "Высокий риск",
        "accent": "red",
        "headline": "Обнаружено несколько выраженных признаков",
    },
    "unknown": {
        "label": "Нужен новый снимок",
        "accent": "gray",
        "headline": "Качество кадра недостаточно для анализа",
    },
}


def _add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


app.after_request(_add_cors_headers)


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(_: RequestEntityTooLarge):
    return jsonify({"error": "Фото слишком большое. Максимальный размер: 12 MB."}), 413


@app.route("/health", methods=["GET"])
def health():
    current_analyzer = get_analyzer()
    current_storage = get_storage()
    return jsonify(
        {
            "status": "ok",
            "service": "scoliosis-screening-api",
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "analysis_engine": current_analyzer.engine,
            "pose_model": str(getattr(current_analyzer, "task_model_path", "")),
            "database": str(current_storage.db_path),
        }
    )


@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    try:
        user = get_storage().create_user(username, password)
        token = get_storage().create_session(user["id"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"token": token, "user": user, "billing": get_storage().billing_status(user["id"])}), 201


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    try:
        normalize_username(username)
        validate_password(password)
    except ValueError:
        return jsonify({"error": "Неверный логин или пароль."}), 401

    user = get_storage().authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Неверный логин или пароль."}), 401

    token = get_storage().create_session(user["id"])
    return jsonify({"token": token, "user": public_user(user), "billing": get_storage().billing_status(user["id"])})


@app.route("/api/auth/me", methods=["GET", "OPTIONS"])
def me():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    return jsonify({"user": public_user(user), "billing": get_storage().billing_status(user["id"])})


@app.route("/api/auth/logout", methods=["POST", "OPTIONS"])
def logout():
    if request.method == "OPTIONS":
        return ("", 204)

    get_storage().revoke_session(_extract_bearer_token())
    return jsonify({"status": "ok"})


@app.route("/api/billing/plans", methods=["GET", "OPTIONS"])
def billing_plans():
    if request.method == "OPTIONS":
        return ("", 204)

    return jsonify({"plans": get_storage().list_plans()})


@app.route("/api/billing/status", methods=["GET", "OPTIONS"])
def billing_status():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    return jsonify(get_storage().billing_status(user["id"]))


@app.route("/api/billing/checkout", methods=["POST", "OPTIONS"])
def billing_checkout():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    payload = request.get_json(silent=True) or {}
    plan_id = str(payload.get("plan_id", "")).strip()
    if plan_id != "plus":
        return jsonify({"error": "Через checkout подключается только Plus. Corporate активируется через школьный доступ."}), 400

    try:
        get_storage().activate_plan(user["id"], plan_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(get_storage().billing_status(user["id"]))


@app.route("/api/billing/verify-student", methods=["POST", "OPTIONS"])
def billing_verify_student():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    payload = request.get_json(silent=True) or {}
    school_code = str(payload.get("school_code", "")).strip()
    student_external_id = str(payload.get("student_id", "")).strip()

    try:
        get_storage().verify_student_access(user["id"], school_code, student_external_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(get_storage().billing_status(user["id"]))


@app.route("/api/reports", methods=["GET", "OPTIONS"])
def list_reports():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    reports = get_storage().list_reports(user["id"])
    return jsonify({"reports": reports})


@app.route("/api/reports/<report_id>", methods=["GET", "OPTIONS"])
def get_report(report_id: str):
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    stored = get_storage().get_report(user["id"], report_id)
    if not stored:
        return jsonify({"error": "Отчёт не найден."}), 404

    return jsonify(_restore_overlay_images(stored["report"], stored["images"]))


@app.route("/analyze", methods=["POST", "OPTIONS"])
@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    user, error_response = _require_user()
    if error_response:
        return error_response

    try:
        frames = _decode_frames_from_request()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    is_advanced = _is_advanced_request(frames)
    if is_advanced:
        missing_views = _missing_protocol_views(frames)
        if missing_views:
            return jsonify({"error": f"Для Advanced-анализа добавьте все 5 ракурсов: {', '.join(missing_views)}."}), 400

    analysis_type = "advanced" if is_advanced else "basic"
    allowance = get_storage().analysis_allowance(user["id"], analysis_type)
    if not allowance["allowed"]:
        return (
            jsonify(
                {
                    "error": allowance["error"],
                    "code": allowance["code"],
                    "billing": allowance["billing"],
                }
            ),
            402,
        )

    student_id = _extract_student_id()
    current_analyzer = get_analyzer()

    if len(frames) == 1 and frames[0]["key"] == "single":
        frame = _resize_for_analysis(frames[0]["frame"])
        screening, mp_results = current_analyzer.analyze_frame(frame)
        annotated = current_analyzer.draw_overlay(frame, mp_results, screening)
        response = _build_response(student_id, screening, annotated)
        _persist_report(response, annotated, user["id"])
        get_storage().record_usage(user["id"], response["report_id"], "basic")
        return jsonify(response)

    view_results = []
    annotated_views = {}
    for item in frames:
        frame = _resize_for_analysis(item["frame"])
        screening, mp_results = current_analyzer.analyze_frame(frame)
        screening = _screening_for_view(screening, item["key"])
        annotated = current_analyzer.draw_overlay(frame, mp_results, screening)
        view_results.append(_build_view_response(item, screening, annotated))
        annotated_views[item["key"]] = annotated

    response = _build_multi_view_response(student_id, view_results)
    _persist_multi_view_report(response, annotated_views, user["id"])
    get_storage().record_usage(user["id"], response["report_id"], "advanced")

    return jsonify(response)


def _extract_bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.headers.get("X-Session-Token", "").strip()


def _require_user():
    token = _extract_bearer_token()
    user = get_storage().user_by_token(token)
    if not user:
        return None, (jsonify({"error": "Войдите в аккаунт."}), 401)
    return user, None


def _extract_student_id() -> str:
    if request.form:
        return request.form.get("student_id", "unknown").strip() or "unknown"

    data = request.get_json(silent=True) or {}
    return str(data.get("student_id", "unknown")).strip() or "unknown"


def _decode_frames_from_request() -> list[dict[str, Any]]:
    frames = []

    if request.files:
        for view in VIEW_DEFS:
            uploaded = request.files.get(view["field"])
            if uploaded:
                frames.append({**view, "frame": _decode_image_bytes(uploaded.read())})

        if frames:
            return frames

        if "image" in request.files:
            return [
                {
                    "key": "single",
                    "field": "image",
                    "label": "Фото",
                    "frame": _decode_image_bytes(request.files["image"].read()),
                }
            ]

    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}

    encoded = data.get("image_base64")
    if encoded:
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(encoded)
        except Exception as exc:
            raise ValueError(f"Некорректный base64: {exc}") from exc
        return [{"key": "single", "field": "image_base64", "label": "Фото", "frame": _decode_image_bytes(image_bytes)}]

    raise ValueError("Передайте хотя бы одно фото в протокол скрининга.")


def _is_advanced_request(frames: list[dict[str, Any]]) -> bool:
    return any(frame.get("key") != "single" for frame in frames)


def _missing_protocol_views(frames: list[dict[str, Any]]) -> list[str]:
    present = {frame.get("key") for frame in frames}
    return [view["label"] for view in VIEW_DEFS if view["key"] not in present]


def _decode_image_bytes(image_bytes: bytes) -> np.ndarray:

    if not image_bytes:
        raise ValueError("Файл изображения пустой.")

    img_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Не удалось прочитать изображение. Используйте JPG, PNG или HEIC, конвертированный телефоном.")

    return frame


def _resize_for_analysis(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = min(1.0, MAX_RESPONSE_IMAGE_SIDE / max(h, w))
    if scale >= 1.0:
        return frame
    return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _build_response(student_id: str, screening, annotated: np.ndarray) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    view_payload = _build_view_response({"key": "single", "label": "Фото"}, screening, annotated)
    report_id = _report_id(student_id, now)

    return {
        "report_id": report_id,
        "student_id": student_id,
        "timestamp": now.isoformat(),
        "mode": "single",
        "landmarks_found": view_payload["landmarks_found"],
        "analysis_engine": view_payload["analysis_engine"],
        "quality_score": view_payload["quality_score"],
        "risk": view_payload["risk"],
        "metrics": view_payload["metrics"],
        "flags": view_payload["flags"],
        "metric_cards": view_payload["metric_cards"],
        "recommendations": _recommendations(screening.risk_level, screening.landmarks_found),
        "care_plan": _care_plan(screening.risk_level, view_payload["risk"]["score"], screening.landmarks_found),
        "message": view_payload["message"],
        "overlay_image": view_payload["overlay_image"],
    }


def _build_view_response(view: dict[str, Any], screening, annotated: np.ndarray) -> dict[str, Any]:
    view_key = view["key"]
    metrics = _metric_cards(screening, view_key)
    finding_count = sum(1 for metric in metrics if metric["triggered"])
    total_metrics = len(metrics)
    profile = RISK_PROFILE.get(screening.risk_level, RISK_PROFILE["unknown"])
    score = int(round((finding_count / total_metrics) * 100)) if screening.landmarks_found and total_metrics else 0

    return {
        "view_key": view_key,
        "view_label": view["label"],
        "view_role": "profile" if _is_side_view(view_key) else "screening",
        "metrics_applicable": total_metrics > 0,
        "landmarks_found": screening.landmarks_found,
        "analysis_engine": screening.analysis_engine,
        "quality_score": round(screening.quality_score, 2),
        "risk": {
            "level": screening.risk_level,
            "label": profile["label"],
            "accent": profile["accent"],
            "headline": profile["headline"],
            "score": score,
            "finding_count": finding_count,
            "total_metrics": total_metrics,
        },
        "metrics": screening.to_dict()["metrics"],
        "flags": screening.flags,
        "metric_cards": metrics,
        "recommendations": _recommendations(screening.risk_level, screening.landmarks_found),
        "message": screening.message,
        "overlay_image": _encode_jpeg_data_url(annotated),
    }


def _build_multi_view_response(student_id: str, views: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    report_id = _report_id(student_id, now)
    risk_level = _aggregate_risk_level(views)
    profile = RISK_PROFILE.get(risk_level, RISK_PROFILE["unknown"])
    metric_cards = _aggregate_metric_cards(views)
    finding_count = sum(view["risk"]["finding_count"] for view in views)
    total_metrics = sum(view["risk"]["total_metrics"] for view in views)
    score = int(round((finding_count / total_metrics) * 100)) if total_metrics else 0
    primary_view = next((view for view in views if view["view_key"] == "front"), views[0])
    engines = sorted({view["analysis_engine"] for view in views})
    found_count = sum(1 for view in views if view["landmarks_found"])

    return {
        "report_id": report_id,
        "student_id": student_id,
        "timestamp": now.isoformat(),
        "mode": "multi_view",
        "view_count": len(views),
        "views_completed": found_count,
        "landmarks_found": found_count > 0,
        "analysis_engine": engines[0] if len(engines) == 1 else "multi_view",
        "quality_score": round(sum(view["quality_score"] for view in views) / len(views), 2),
        "risk": {
            "level": risk_level,
            "label": profile["label"],
            "accent": profile["accent"],
            "headline": profile["headline"],
            "score": score,
            "finding_count": finding_count,
            "total_metrics": total_metrics,
        },
        "metrics": primary_view["metrics"],
        "flags": _aggregate_flags(views),
        "metric_cards": metric_cards,
        "recommendations": _recommendations(risk_level, found_count > 0),
        "care_plan": _care_plan(risk_level, score, found_count > 0),
        "message": f"Проанализировано ракурсов: {found_count}/{len(views)}.",
        "overlay_image": primary_view["overlay_image"],
        "views": views,
    }


def _aggregate_risk_level(views: list[dict[str, Any]]) -> str:
    if not any(view["landmarks_found"] for view in views):
        return "unknown"
    return max((view["risk"]["level"] for view in views), key=lambda level: RISK_RANK.get(level, 0))


def _aggregate_metric_cards(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for item in METRIC_DEFS:
        view_cards = [
            metric
            for view in views
            for metric in view["metric_cards"]
            if metric["key"] == item["key"]
        ]
        if not view_cards:
            continue
        strongest = max(view_cards, key=lambda metric: metric.get("severity_ratio", 0))
        cards.append(
            {
                **strongest,
                "description": f"Максимальное значение по протоколу: {strongest['description']}",
                "triggered": any(metric["triggered"] for metric in view_cards),
                "severity_ratio": max(metric.get("severity_ratio", 0) for metric in view_cards),
            }
        )
    return cards


def _aggregate_flags(views: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        item["flag"]: any(view["flags"].get(item["flag"], False) for view in views)
        for item in METRIC_DEFS
    }


def _is_side_view(view_key: str) -> bool:
    return view_key in SIDE_VIEW_KEYS


def _screening_for_view(screening, view_key: str):
    if not _is_side_view(view_key) or not screening.landmarks_found:
        return screening

    return replace(
        screening,
        shoulder_tilt_deg=0.0,
        hip_tilt_deg=0.0,
        head_tilt_deg=0.0,
        trunk_shift_ratio=0.0,
        waist_asym_ratio=0.0,
        flags={item["flag"]: False for item in METRIC_DEFS},
        risk_level="low",
        message="Профильный ракурс используется для контроля протокола; фронтальные метрики асимметрии к нему не применяются.",
    )


def _metric_cards(screening, view_key: str = "single") -> list[dict[str, Any]]:
    if _is_side_view(view_key):
        return []

    raw = screening.to_dict()["metrics"]
    cards = []

    for item in METRIC_DEFS:
        raw_value = raw[item["key"]]
        value = round(raw_value * item["scale"], 2)
        threshold = round(THRESHOLDS[item["key"]] * item["scale"], 2)
        triggered = bool(screening.flags.get(item["flag"], False))
        ratio = min(value / threshold, 2.0) if threshold else 0.0

        cards.append(
            {
                "key": item["key"],
                "title": item["title"],
                "description": item["description"],
                "value": value,
                "threshold": threshold,
                "unit": item["unit"],
                "triggered": triggered,
                "severity_ratio": round(ratio, 2),
            }
        )

    return cards


def _recommendations(risk_level: str, landmarks_found: bool) -> list[str]:
    if not landmarks_found:
        return [
            "Повторить протокол съёмки при ровном освещении.",
            "Поставить камеру на уровне середины корпуса, без наклона.",
            "Проверить, что все пять ракурсов видны полностью.",
        ]

    if risk_level == "low":
        return [
            "Зафиксировать результат в журнале профилактического осмотра.",
            "Повторить скрининг по плановому графику.",
            "Поддерживать обычную физическую активность и симметричную нагрузку.",
        ]

    if risk_level == "moderate":
        return [
            "Провести повторный снимок для подтверждения результата.",
            "Назначить контрольный осмотр у школьного медработника.",
            "При повторном среднем риске направить к ортопеду.",
        ]

    if risk_level == "high":
        return [
            "Поставить ученика в приоритет на очный осмотр.",
            "Передать отчёт школьному медработнику или ортопеду.",
            "Проверить динамику: сравнить с предыдущими результатами, если они есть.",
        ]

    return ["Повторить скрининг с новым снимком."]


def _care_plan(risk_level: str, score: int, landmarks_found: bool) -> list[dict[str, str]]:
    if not landmarks_found:
        return [
            {
                "title": "Переснять протокол",
                "body": "Повторить пять ракурсов: спереди, со спины, левый бок, правый бок и тест Адамса.",
                "level": "neutral",
            },
            {
                "title": "Проверить качество кадра",
                "body": "Полный рост, ровная камера, контрастный фон, плечи, таз и спина без перекрытия одеждой.",
                "level": "neutral",
            },
        ]

    if risk_level == "low" and score == 0:
        return [
            {
                "title": "Плановый контроль",
                "body": "Признаки риска не обнаружены. Достаточно сохранить результат и повторить скрининг по школьному графику.",
                "level": "ok",
            },
            {
                "title": "Обычная активность",
                "body": "Можно продолжать физкультуру и спорт с нормальной техникой, без специальных ограничений по этому скринингу.",
                "level": "ok",
            },
            {
                "title": "Профилактика нагрузки",
                "body": "Следить за симметричной посадкой, рюкзаком на двух лямках и регулярной общей физической активностью.",
                "level": "ok",
            },
        ]

    if risk_level == "low":
        return [
            {
                "title": "Наблюдение",
                "body": "Критичных признаков нет, но отдельные небольшие отклонения стоит сравнить с будущими скринингами.",
                "level": "ok",
            },
            {
                "title": "Повтор по графику",
                "body": "Сохранить отчёт и повторить скрининг в плановый срок или раньше при видимом ухудшении осанки.",
                "level": "ok",
            },
        ]

    if risk_level == "moderate":
        return [
            {
                "title": "Подтверждение результата",
                "body": "Повторить протокол в тот же день или на ближайшем контрольном осмотре, чтобы исключить ошибку позы и кадра.",
                "level": "attention",
            },
            {
                "title": "Очный школьный осмотр",
                "body": "Провести тест Адамса и измерение ATR сколиометром; при повторном среднем риске направить к ортопеду.",
                "level": "attention",
            },
            {
                "title": "ЛФК и тренировки",
                "body": "До очной оценки избегать тяжёлой осевой нагрузки; использовать симметричную ОФП, упражнения на контроль корпуса и дыхание.",
                "level": "attention",
            },
        ]

    if risk_level == "high":
        return [
            {
                "title": "Ортопед приоритетно",
                "body": "Передать отчёт специалисту и провести очную оценку: тест Адамса, ATR сколиометром, оценка роста и риска прогрессии.",
                "level": "urgent",
            },
            {
                "title": "Подтверждение степени",
                "body": "При показаниях специалист назначает рентген и измерение угла Cobb; от угла и роста зависит наблюдение, ЛФК, корсет или хирургическая оценка.",
                "level": "urgent",
            },
            {
                "title": "Без самолечения нагрузкой",
                "body": "Зал и турник не заменяют лечение. До плана ортопеда не начинать тяжёлые приседы, становую, жимы стоя и асимметричные нагрузки.",
                "level": "urgent",
            },
            {
                "title": "Рабочий план",
                "body": "После подтверждения обычно выбирают наблюдение, специальные антисколиотические упражнения/ЛФК, корсет при прогрессирующих дугах у растущих детей или направление на хирургическую оценку при больших дугах.",
                "level": "urgent",
            },
        ]

    return [
        {
            "title": "Повторить скрининг",
            "body": "Сделать новый протокол и сравнить результат с текущим отчётом.",
            "level": "neutral",
        }
    ]


def _encode_jpeg_data_url(image: np.ndarray) -> str:
    encoded = base64.b64encode(_encode_jpeg_bytes(image)).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _encode_jpeg_bytes(image: np.ndarray) -> bytes:
    h, w = image.shape[:2]
    scale = min(1.0, MAX_RESPONSE_IMAGE_SIDE / max(h, w))
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        return b""

    return buffer.tobytes()


def _jpeg_data_url_from_bytes(image_bytes: bytes) -> str:
    if not image_bytes:
        return ""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _persist_report(response: dict[str, Any], annotated: np.ndarray, user_id: int) -> None:
    get_storage().save_report(
        user_id,
        _strip_overlay_images(response),
        {"single": _encode_jpeg_bytes(annotated)},
    )


def _persist_multi_view_report(response: dict[str, Any], annotated_views: dict[str, np.ndarray], user_id: int) -> None:
    get_storage().save_report(
        user_id,
        _strip_overlay_images(response),
        {view_key: _encode_jpeg_bytes(annotated) for view_key, annotated in annotated_views.items()},
    )


def _strip_overlay_images(value):
    if isinstance(value, dict):
        return {
            key: _strip_overlay_images(item)
            for key, item in value.items()
            if key not in {"overlay_image", "image_file", "image_files"}
        }
    if isinstance(value, list):
        return [_strip_overlay_images(item) for item in value]
    return value


def _restore_overlay_images(report: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
    restored = json.loads(json.dumps(report, ensure_ascii=False))

    if restored.get("mode") == "multi_view" and isinstance(restored.get("views"), list):
        for view in restored["views"]:
            view_key = view.get("view_key")
            if view_key in images:
                view["overlay_image"] = _jpeg_data_url_from_bytes(images[view_key])
        primary_key = "front" if "front" in images else next(iter(images), "")
        restored["overlay_image"] = _jpeg_data_url_from_bytes(images.get(primary_key, b""))
    else:
        primary_key = "single" if "single" in images else next(iter(images), "")
        restored["overlay_image"] = _jpeg_data_url_from_bytes(images.get(primary_key, b""))

    return restored


def _report_id(student_id: str, timestamp: datetime) -> str:
    safe_student = re.sub(r"[^A-Za-z0-9_.-]+", "_", student_id).strip("_") or "student"
    return f"{safe_student}_{timestamp.strftime('%Y%m%d_%H%M%S')}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scoliosis screening API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    get_storage()
    app.run(host=args.host, port=args.port, debug=args.debug)
