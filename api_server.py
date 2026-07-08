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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import cv2
import numpy as np
from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from pose_analyzer import ScoliosisScreeningAnalyzer, THRESHOLDS

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

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

_analyzer: ScoliosisScreeningAnalyzer | None = None


def get_analyzer() -> ScoliosisScreeningAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ScoliosisScreeningAnalyzer(static_image_mode=True)
    return _analyzer


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
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


app.after_request(_add_cors_headers)


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(_: RequestEntityTooLarge):
    return jsonify({"error": "Фото слишком большое. Максимальный размер: 12 MB."}), 413


@app.route("/health", methods=["GET"])
def health():
    current_analyzer = get_analyzer()
    return jsonify(
        {
            "status": "ok",
            "service": "scoliosis-screening-api",
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "analysis_engine": current_analyzer.engine,
            "pose_model": str(getattr(current_analyzer, "task_model_path", "")),
        }
    )


@app.route("/analyze", methods=["POST", "OPTIONS"])
@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        frames = _decode_frames_from_request()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    student_id = _extract_student_id()
    current_analyzer = get_analyzer()

    if len(frames) == 1 and frames[0]["key"] == "single":
        frame = _resize_for_analysis(frames[0]["frame"])
        screening, mp_results = current_analyzer.analyze_frame(frame)
        annotated = current_analyzer.draw_overlay(frame, mp_results, screening)
        response = _build_response(student_id, screening, annotated)
        _persist_report(response, annotated)
        return jsonify(response)

    view_results = []
    annotated_views = {}
    for item in frames:
        frame = _resize_for_analysis(item["frame"])
        screening, mp_results = current_analyzer.analyze_frame(frame)
        annotated = current_analyzer.draw_overlay(frame, mp_results, screening)
        view_results.append(_build_view_response(item, screening, annotated))
        annotated_views[item["key"]] = annotated

    response = _build_multi_view_response(student_id, view_results)
    _persist_multi_view_report(response, annotated_views)

    return jsonify(response)


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
        "message": view_payload["message"],
        "overlay_image": view_payload["overlay_image"],
    }


def _build_view_response(view: dict[str, Any], screening, annotated: np.ndarray) -> dict[str, Any]:
    metrics = _metric_cards(screening)
    finding_count = sum(1 for metric in metrics if metric["triggered"])
    total_metrics = len(metrics)
    profile = RISK_PROFILE.get(screening.risk_level, RISK_PROFILE["unknown"])
    score = int(round((finding_count / total_metrics) * 100)) if screening.landmarks_found else 0

    return {
        "view_key": view["key"],
        "view_label": view["label"],
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


def _metric_cards(screening) -> list[dict[str, Any]]:
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
            "Переснять фото в полный рост при ровном освещении.",
            "Поставить камеру на уровне середины корпуса, без наклона.",
            "Попросить школьника стоять прямо, руки свободно вдоль тела.",
        ]

    if risk_level == "low":
        return [
            "Зафиксировать результат в журнале профилактического осмотра.",
            "Повторить скрининг по плановому графику.",
            "Следить за симметричной посадкой и нагрузкой рюкзака.",
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


def _encode_jpeg_data_url(image: np.ndarray) -> str:
    h, w = image.shape[:2]
    scale = min(1.0, MAX_RESPONSE_IMAGE_SIDE / max(h, w))
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        return ""

    encoded = base64.b64encode(buffer).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _persist_report(response: dict[str, Any], annotated: np.ndarray) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    report_id = response["report_id"]
    image_path = REPORTS_DIR / f"{report_id}.jpg"
    json_path = REPORTS_DIR / f"{report_id}.json"

    cv2.imwrite(str(image_path), annotated)

    persisted = {key: value for key, value in response.items() if key != "overlay_image"}
    persisted["image_file"] = str(image_path)

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(persisted, file, ensure_ascii=False, indent=2)


def _persist_multi_view_report(response: dict[str, Any], annotated_views: dict[str, np.ndarray]) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    report_id = response["report_id"]
    image_files = {}

    for view_key, annotated in annotated_views.items():
        image_path = REPORTS_DIR / f"{report_id}_{view_key}.jpg"
        cv2.imwrite(str(image_path), annotated)
        image_files[view_key] = str(image_path)

    persisted = _strip_overlay_images(response)
    persisted["image_files"] = image_files

    json_path = REPORTS_DIR / f"{report_id}.json"
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(persisted, file, ensure_ascii=False, indent=2)


def _strip_overlay_images(value):
    if isinstance(value, dict):
        return {key: _strip_overlay_images(item) for key, item in value.items() if key != "overlay_image"}
    if isinstance(value, list):
        return [_strip_overlay_images(item) for item in value]
    return value


def _report_id(student_id: str, timestamp: datetime) -> str:
    safe_student = re.sub(r"[^A-Za-z0-9_.-]+", "_", student_id).strip("_") or "student"
    return f"{safe_student}_{timestamp.strftime('%Y%m%d_%H%M%S')}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scoliosis screening API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=args.debug)
