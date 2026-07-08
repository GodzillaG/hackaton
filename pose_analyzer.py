# -*- coding: utf-8 -*-
"""
Pose and silhouette analysis for the scoliosis screening complex.

Primary engine: MediaPipe Tasks Pose Landmarker with the bundled lite model.
Secondary engine: OpenCV silhouette analysis for frames where pose landmarks
cannot be estimated reliably.
"""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import cv2
import numpy as np

try:
    import mediapipe as mp

    _solutions = getattr(mp, "solutions", None)
    mp_pose = _solutions.pose if _solutions and hasattr(_solutions, "pose") else None
    mp_drawing = _solutions.drawing_utils if _solutions and hasattr(_solutions, "drawing_utils") else None
    mp_drawing_styles = (
        _solutions.drawing_styles if _solutions and hasattr(_solutions, "drawing_styles") else None
    )
except Exception:
    mp = None
    mp_pose = None
    mp_drawing = None
    mp_drawing_styles = None


# Empirical screening thresholds for primary risk stratification.
THRESHOLDS = {
    "shoulder_tilt_deg": 2.5,
    "hip_tilt_deg": 2.5,
    "head_tilt_deg": 3.0,
    "trunk_shift_ratio": 0.04,
    "waist_asym_ratio": 0.08,
}


POSE_CONNECTIONS_FALLBACK = [
    ("left_ear", "right_ear"),
    ("left_shoulder", "right_shoulder"),
    ("left_hip", "right_hip"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_elbow", "left_hip"),
    ("right_elbow", "right_hip"),
]

TASK_POSE_CONNECTIONS = [
    (7, 8),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (27, 31),
    (24, 26),
    (26, 28),
    (28, 32),
]

TASK_LANDMARKS = {
    "left_ear": 7,
    "right_ear": 8,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_hip": 23,
    "right_hip": 24,
}


@dataclass
class ScreeningResult:
    landmarks_found: bool
    shoulder_tilt_deg: float = 0.0
    hip_tilt_deg: float = 0.0
    head_tilt_deg: float = 0.0
    trunk_shift_ratio: float = 0.0
    waist_asym_ratio: float = 0.0
    flags: Dict[str, bool] = field(default_factory=dict)
    risk_level: str = "unknown"
    message: str = ""
    analysis_engine: str = "unknown"
    quality_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "landmarks_found": self.landmarks_found,
            "analysis_engine": self.analysis_engine,
            "quality_score": round(self.quality_score, 2),
            "metrics": {
                "shoulder_tilt_deg": round(self.shoulder_tilt_deg, 2),
                "hip_tilt_deg": round(self.hip_tilt_deg, 2),
                "head_tilt_deg": round(self.head_tilt_deg, 2),
                "trunk_shift_ratio": round(self.trunk_shift_ratio, 4),
                "waist_asym_ratio": round(self.waist_asym_ratio, 4),
            },
            "flags": self.flags,
            "risk_level": self.risk_level,
            "message": self.message,
        }


@dataclass
class SilhouetteResult:
    points: Dict[str, tuple[float, float]]
    mask: np.ndarray
    bbox: tuple[int, int, int, int]
    engine: str = "opencv_silhouette"


@dataclass
class TaskPoseResult:
    landmarks: list[Any]
    points: Dict[str, tuple[float, float]]
    image_size: tuple[int, int]
    engine: str = "mediapipe_tasks"


def _angle_from_horizontal(p_left, p_right) -> float:
    dx = p_right[0] - p_left[0]
    dy = p_right[1] - p_left[1]
    if dx == 0:
        return 90.0
    angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
    return min(angle, 180.0 - angle)


def _midpoint(a, b) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _default_task_model_path() -> Path:
    configured_path = os.environ.get("SCOLISCAN_POSE_MODEL")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parent / "models" / "pose_landmarker_lite.task"


class ScoliosisScreeningAnalyzer:
    """
    Computes posture asymmetry metrics for a single image or camera frame.

    The public API is stable:
        result, draw_context = analyzer.analyze_frame(frame_bgr)
        annotated = analyzer.draw_overlay(frame_bgr, draw_context, result)
    """

    def __init__(
        self,
        static_image_mode: bool = False,
        model_complexity: int = 1,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        pose_model_path: Optional[str] = None,
    ):
        self.engine = "opencv_silhouette"
        self.pose = None
        self.task_landmarker = None
        self.task_model_path = Path(pose_model_path) if pose_model_path else _default_task_model_path()

        if mp and self.task_model_path.exists() and hasattr(mp, "tasks") and hasattr(mp.tasks, "vision"):
            try:
                options = mp.tasks.vision.PoseLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(self.task_model_path)),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,
                    num_poses=1,
                    min_pose_detection_confidence=min_detection_confidence,
                    min_pose_presence_confidence=min_detection_confidence,
                    min_tracking_confidence=min_tracking_confidence,
                    output_segmentation_masks=False,
                )
                self.task_landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
                self.engine = "mediapipe_tasks_lite"
                return
            except Exception as exc:
                print(f"MediaPipe pose model unavailable, using silhouette analysis: {exc}")

        if mp_pose:
            self.pose = mp_pose.Pose(
                static_image_mode=static_image_mode,
                model_complexity=model_complexity,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self.engine = "mediapipe_pose"

    def close(self):
        if self.task_landmarker:
            self.task_landmarker.close()
        if self.pose:
            self.pose.close()

    def _extract_point(self, landmarks, idx, w, h):
        lm = landmarks[idx]
        return (lm.x * w, lm.y * h), lm.visibility

    def _extract_task_point(self, landmarks, idx, w, h):
        lm = landmarks[idx]
        return (lm.x * w, lm.y * h), self._task_visibility(lm)

    def _task_visibility(self, landmark) -> float:
        visibility = getattr(landmark, "visibility", None)
        presence = getattr(landmark, "presence", None)
        scores = [value for value in (visibility, presence) if value is not None]
        if not scores:
            return 1.0
        return float(max(0.0, min(scores)))

    def analyze_frame(self, frame_bgr: np.ndarray) -> tuple[ScreeningResult, Optional[Any]]:
        if self.task_landmarker:
            return self._analyze_with_tasks(frame_bgr)
        if self.pose:
            return self._analyze_with_mediapipe(frame_bgr)
        return self._analyze_with_silhouette(frame_bgr)

    def _analyze_with_tasks(self, frame_bgr: np.ndarray) -> tuple[ScreeningResult, Optional[TaskPoseResult]]:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        results = self.task_landmarker.detect(mp_image)

        if not results.pose_landmarks:
            return self._analyze_with_silhouette(frame_bgr)

        landmarks = results.pose_landmarks[0]
        points: Dict[str, tuple[float, float]] = {}
        visibility_scores = []

        for name, idx in TASK_LANDMARKS.items():
            point, visibility = self._extract_task_point(landmarks, idx, w, h)
            points[name] = point
            visibility_scores.append(visibility)

        key_vis = [
            self._task_visibility(landmarks[TASK_LANDMARKS["left_shoulder"]]),
            self._task_visibility(landmarks[TASK_LANDMARKS["right_shoulder"]]),
            self._task_visibility(landmarks[TASK_LANDMARKS["left_hip"]]),
            self._task_visibility(landmarks[TASK_LANDMARKS["right_hip"]]),
        ]
        min_visibility = 0.25
        if min(key_vis) < min_visibility:
            fallback_result, fallback_context = self._analyze_with_silhouette(frame_bgr)
            if fallback_result.landmarks_found:
                return fallback_result, fallback_context
            return (
                ScreeningResult(
                    landmarks_found=False,
                    message="Ключевые точки плеч и таза видны плохо. Нужен новый снимок.",
                    analysis_engine=self.engine,
                    quality_score=float(min(key_vis)),
                ),
                TaskPoseResult(landmarks, points, (w, h)),
            )

        quality = float(min(1.0, max(0.0, sum(visibility_scores) / len(visibility_scores))))
        result = self._score_points(points, self.engine, quality_score=quality)
        return result, TaskPoseResult(landmarks, points, (w, h))

    def _analyze_with_mediapipe(self, frame_bgr: np.ndarray) -> tuple[ScreeningResult, Optional[Any]]:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self.pose.process(rgb)

        if not results.pose_landmarks:
            return (
                ScreeningResult(
                    landmarks_found=False,
                    message="Человек не обнаружен в кадре. Нужен снимок в полный рост.",
                    analysis_engine=self.engine,
                ),
                results,
            )

        lms = results.pose_landmarks.landmark

        (l_sh, v_l_sh), (r_sh, v_r_sh) = self._extract_point(
            lms, mp_pose.PoseLandmark.LEFT_SHOULDER, w, h
        ), self._extract_point(lms, mp_pose.PoseLandmark.RIGHT_SHOULDER, w, h)
        (l_hip, v_l_hip), (r_hip, v_r_hip) = self._extract_point(
            lms, mp_pose.PoseLandmark.LEFT_HIP, w, h
        ), self._extract_point(lms, mp_pose.PoseLandmark.RIGHT_HIP, w, h)
        (l_ear, v_l_ear), (r_ear, v_r_ear) = self._extract_point(
            lms, mp_pose.PoseLandmark.LEFT_EAR, w, h
        ), self._extract_point(lms, mp_pose.PoseLandmark.RIGHT_EAR, w, h)
        (l_elb, v_l_elb), (r_elb, v_r_elb) = self._extract_point(
            lms, mp_pose.PoseLandmark.LEFT_ELBOW, w, h
        ), self._extract_point(lms, mp_pose.PoseLandmark.RIGHT_ELBOW, w, h)

        min_visibility = 0.4
        key_vis = [v_l_sh, v_r_sh, v_l_hip, v_r_hip]
        if min(key_vis) < min_visibility:
            return (
                ScreeningResult(
                    landmarks_found=False,
                    message="Ключевые точки плеч и таза видны плохо. Нужен новый снимок.",
                    analysis_engine=self.engine,
                    quality_score=float(min(key_vis)),
                ),
                results,
            )

        points = {
            "left_shoulder": l_sh,
            "right_shoulder": r_sh,
            "left_hip": l_hip,
            "right_hip": r_hip,
            "left_ear": l_ear,
            "right_ear": r_ear,
            "left_elbow": l_elb,
            "right_elbow": r_elb,
        }
        result = self._score_points(points, self.engine, quality_score=float(min(key_vis)))
        return result, results

    def _analyze_with_silhouette(self, frame_bgr: np.ndarray) -> tuple[ScreeningResult, Optional[SilhouetteResult]]:
        mask, bbox = self._extract_silhouette(frame_bgr)
        if mask is None or bbox is None:
            return (
                ScreeningResult(
                    landmarks_found=False,
                    message="Силуэт не выделен. Нужен контрастный фон и полный рост в кадре.",
                    analysis_engine=self.engine,
                ),
                None,
            )

        points = self._estimate_points_from_silhouette(mask, bbox)
        if points is None:
            return (
                ScreeningResult(
                    landmarks_found=False,
                    message="Силуэт найден, но ключевые уровни корпуса выделены плохо.",
                    analysis_engine=self.engine,
                    quality_score=0.35,
                ),
                SilhouetteResult({}, mask, bbox),
            )

        x, y, w, h = bbox
        area_ratio = cv2.countNonZero(mask[y : y + h, x : x + w]) / max(w * h, 1)
        quality = float(min(1.0, max(0.35, area_ratio * 2.5)))
        result = self._score_points(points, self.engine, quality_score=quality)
        return result, SilhouetteResult(points, mask, bbox)

    def _score_points(
        self, points: Dict[str, tuple[float, float]], engine: str, quality_score: float
    ) -> ScreeningResult:
        l_sh = points["left_shoulder"]
        r_sh = points["right_shoulder"]
        l_hip = points["left_hip"]
        r_hip = points["right_hip"]
        l_ear = points["left_ear"]
        r_ear = points["right_ear"]
        l_elb = points["left_elbow"]
        r_elb = points["right_elbow"]

        shoulder_width = math.hypot(r_sh[0] - l_sh[0], r_sh[1] - l_sh[1]) or 1.0
        shoulder_tilt = abs(_angle_from_horizontal(l_sh, r_sh))
        hip_tilt = abs(_angle_from_horizontal(l_hip, r_hip))
        head_tilt = abs(_angle_from_horizontal(l_ear, r_ear))
        trunk_shift = abs(_midpoint(l_sh, r_sh)[0] - _midpoint(l_hip, r_hip)[0]) / shoulder_width
        waist_asym = abs(abs(l_elb[0] - l_hip[0]) - abs(r_elb[0] - r_hip[0])) / shoulder_width

        flags = {
            "shoulder_tilt": shoulder_tilt > THRESHOLDS["shoulder_tilt_deg"],
            "hip_tilt": hip_tilt > THRESHOLDS["hip_tilt_deg"],
            "head_tilt": head_tilt > THRESHOLDS["head_tilt_deg"],
            "trunk_shift": trunk_shift > THRESHOLDS["trunk_shift_ratio"],
            "waist_asymmetry": waist_asym > THRESHOLDS["waist_asym_ratio"],
        }

        n_flags = sum(flags.values())
        if n_flags == 0:
            risk = "low"
            message = "Явных признаков асимметрии осанки не обнаружено."
        elif n_flags <= 2:
            risk = "moderate"
            message = "Обнаружены отдельные признаки асимметрии."
        else:
            risk = "high"
            message = "Обнаружено несколько признаков асимметрии осанки."

        return ScreeningResult(
            landmarks_found=True,
            shoulder_tilt_deg=shoulder_tilt,
            hip_tilt_deg=hip_tilt,
            head_tilt_deg=head_tilt,
            trunk_shift_ratio=trunk_shift,
            waist_asym_ratio=waist_asym,
            flags=flags,
            risk_level=risk,
            message=message,
            analysis_engine=engine,
            quality_score=quality_score,
        )

    def _extract_silhouette(self, frame_bgr: np.ndarray) -> tuple[Optional[np.ndarray], Optional[tuple[int, int, int, int]]]:
        h, w = frame_bgr.shape[:2]
        if h < 120 or w < 120:
            return None, None

        fast_mask = self._fast_foreground_mask(frame_bgr)
        clean, bbox = self._largest_valid_silhouette(fast_mask, h, w)
        if clean is not None and bbox is not None:
            return clean, bbox

        rect = (int(w * 0.08), int(h * 0.03), int(w * 0.84), int(h * 0.94))
        mask = np.zeros((h, w), np.uint8)
        bg_model = np.zeros((1, 65), np.float64)
        fg_model = np.zeros((1, 65), np.float64)

        try:
            cv2.grabCut(frame_bgr, mask, rect, bg_model, fg_model, 2, cv2.GC_INIT_WITH_RECT)
            mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
        except Exception:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (7, 7), 0)
            _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        return self._largest_valid_silhouette(mask, h, w)

    def _fast_foreground_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        patch = max(12, min(h, w) // 12)
        corners = np.concatenate(
            [
                frame_bgr[:patch, :patch].reshape(-1, 3),
                frame_bgr[:patch, -patch:].reshape(-1, 3),
                frame_bgr[-patch:, :patch].reshape(-1, 3),
                frame_bgr[-patch:, -patch:].reshape(-1, 3),
            ],
            axis=0,
        )
        bg_color = np.median(corners.astype(np.float32), axis=0)
        distance = np.linalg.norm(frame_bgr.astype(np.float32) - bg_color, axis=2)
        distance = np.clip(distance, 0, 255).astype("uint8")
        distance = cv2.GaussianBlur(distance, (5, 5), 0)
        _, mask = cv2.threshold(distance, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return mask

    def _largest_valid_silhouette(
        self, mask: np.ndarray, h: int, w: int
    ) -> tuple[Optional[np.ndarray], Optional[tuple[int, int, int, int]]]:
        kernel = np.ones((7, 7), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None, None

        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < h * w * 0.03:
            return None, None

        clean = np.zeros_like(mask)
        cv2.drawContours(clean, [contour], -1, 255, thickness=cv2.FILLED)
        x, y, bw, bh = cv2.boundingRect(contour)
        if bh < h * 0.35 or bw < w * 0.12:
            return None, None

        return clean, (x, y, bw, bh)

    def _estimate_points_from_silhouette(
        self, mask: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> Optional[Dict[str, tuple[float, float]]]:
        x, y, w, h = bbox
        records = []
        for row in range(y, y + h):
            xs = np.flatnonzero(mask[row] > 0)
            if xs.size > max(12, w * 0.12):
                records.append((row, float(xs[0]), float(xs[-1]), float(xs[-1] - xs[0])))

        if len(records) < 20:
            return None

        def band(start: float, end: float):
            y0 = y + int(h * start)
            y1 = y + int(h * end)
            return [item for item in records if y0 <= item[0] <= y1]

        def side_points(items):
            if not items:
                return None
            max_width = max(item[3] for item in items)
            filtered = [item for item in items if item[3] >= max_width * 0.55] or items
            left = min(filtered, key=lambda item: item[1])
            right = max(filtered, key=lambda item: item[2])
            return (left[1], float(left[0])), (right[2], float(right[0]))

        shoulder = side_points(band(0.16, 0.36))
        hip = side_points(band(0.46, 0.66))
        head = side_points(band(0.04, 0.18))
        elbow = side_points(band(0.32, 0.58))

        if not shoulder or not hip or not head or not elbow:
            return None

        return {
            "left_shoulder": shoulder[0],
            "right_shoulder": shoulder[1],
            "left_hip": hip[0],
            "right_hip": hip[1],
            "left_ear": head[0],
            "right_ear": head[1],
            "left_elbow": elbow[0],
            "right_elbow": elbow[1],
        }

    @staticmethod
    def draw_overlay(frame_bgr: np.ndarray, mp_results, screening: ScreeningResult) -> np.ndarray:
        annotated = frame_bgr.copy()

        if (
            mp_results
            and mp_drawing
            and mp_pose
            and hasattr(mp_results, "pose_landmarks")
            and mp_results.pose_landmarks
        ):
            mp_drawing.draw_landmarks(
                annotated,
                mp_results.pose_landmarks,
                mp_pose.POSE_CONNECTIONS,
                landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style()
                if mp_drawing_styles
                else None,
            )
        elif isinstance(mp_results, TaskPoseResult):
            annotated = _draw_task_pose_context(annotated, mp_results)
        elif isinstance(mp_results, SilhouetteResult):
            annotated = _draw_silhouette_context(annotated, mp_results)

        y = 30
        color_map = {
            "low": (42, 168, 95),
            "moderate": (0, 165, 255),
            "high": (35, 35, 220),
            "unknown": (180, 180, 180),
        }
        color = color_map.get(screening.risk_level, (180, 180, 180))

        lines = [
            f"Risk level: {screening.risk_level.upper()}",
            f"Shoulder tilt: {screening.shoulder_tilt_deg:.1f} deg",
            f"Hip tilt: {screening.hip_tilt_deg:.1f} deg",
            f"Head tilt: {screening.head_tilt_deg:.1f} deg",
            f"Trunk shift: {screening.trunk_shift_ratio * 100:.1f}%",
            f"Waist asym: {screening.waist_asym_ratio * 100:.1f}%",
        ]
        for line in lines:
            cv2.putText(annotated, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)
            y += 24

        return annotated


def _draw_silhouette_context(frame_bgr: np.ndarray, result: SilhouetteResult) -> np.ndarray:
    overlay = frame_bgr.copy()
    tint = np.zeros_like(frame_bgr)
    tint[:, :] = (35, 120, 220)
    mask_bool = result.mask > 0
    overlay[mask_bool] = cv2.addWeighted(frame_bgr, 0.66, tint, 0.34, 0)[mask_bool]

    x, y, w, h = result.bbox
    cv2.rectangle(overlay, (x, y), (x + w, y + h), (35, 120, 220), 2)

    points = result.points
    for start, end in POSE_CONNECTIONS_FALLBACK:
        if start in points and end in points:
            cv2.line(
                overlay,
                tuple(map(int, points[start])),
                tuple(map(int, points[end])),
                (255, 255, 255),
                3,
                cv2.LINE_AA,
            )

    for point in points.values():
        cv2.circle(overlay, tuple(map(int, point)), 6, (20, 230, 190), -1, cv2.LINE_AA)
        cv2.circle(overlay, tuple(map(int, point)), 7, (20, 20, 20), 1, cv2.LINE_AA)

    return overlay


def _draw_task_pose_context(frame_bgr: np.ndarray, result: TaskPoseResult) -> np.ndarray:
    overlay = frame_bgr.copy()
    w, h = result.image_size

    def to_xy(index: int) -> Optional[tuple[int, int]]:
        if index >= len(result.landmarks):
            return None
        landmark = result.landmarks[index]
        x = int(round(landmark.x * w))
        y = int(round(landmark.y * h))
        if x < -w * 0.2 or x > w * 1.2 or y < -h * 0.2 or y > h * 1.2:
            return None
        return x, y

    for start, end in TASK_POSE_CONNECTIONS:
        p1 = to_xy(start)
        p2 = to_xy(end)
        if p1 and p2:
            cv2.line(overlay, p1, p2, (255, 255, 255), 4, cv2.LINE_AA)
            cv2.line(overlay, p1, p2, (35, 120, 220), 2, cv2.LINE_AA)

    for index in TASK_LANDMARKS.values():
        point = to_xy(index)
        if point:
            cv2.circle(overlay, point, 7, (20, 20, 20), -1, cv2.LINE_AA)
            cv2.circle(overlay, point, 5, (20, 230, 190), -1, cv2.LINE_AA)

    return overlay
