"""zephyr/core/features.py    Hand geometry features. Owner: Aadharsh.

Landmarks: wrist 0; thumb 1-4; index 5-8; middle 9-12; ring 13-16; pinky 17-20.
Knuckles 5, 9, 13, 17. Tips 4, 8, 12, 16, 20.
Image space (normalized) for positions and distances, world space (metres) for angles.
"""
from __future__ import annotations

import numpy as np

from zephyr.contracts import Side, dist2d

# (knuckle, middle joint, tip) for index, middle, ring, pinky
FINGERS = ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))
PALM_IDS = (0, 5, 9, 13, 17)


def joint_angle(p: np.ndarray, a: int, b: int, c: int) -> float:
    """Interior angle at b in degrees: about 180 when straight, small when bent."""
    v1 = p[a] - p[b]
    v2 = p[c] - p[b]
    n = float(np.linalg.norm(v1) * np.linalg.norm(v2))
    if n < 1e-9:
        return 180.0
    cos = float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def finger_angles(world: np.ndarray) -> tuple[float, float, float, float]:
    return tuple(joint_angle(world, a, b, c) for a, b, c in FINGERS)  # type: ignore[return-value]


def raw_scale(lm: np.ndarray) -> float:
    return max(dist2d(lm[0], lm[9]), 1e-4)


def pinch_ratio(lm: np.ndarray, scale: float) -> float:
    return dist2d(lm[4], lm[8]) / max(scale, 1e-4)


def palm_centre(lm: np.ndarray) -> tuple[float, float]:
    pts = lm[list(PALM_IDS), :2]
    return float(pts[:, 0].mean()), float(pts[:, 1].mean())


def palm_width(lm: np.ndarray, scale: float) -> float:
    """Index knuckle to pinky knuckle across the image, in hand scales. About 0.7 when the palm faces
    the camera square on; well under 0.4 when the hand is edge-on or tilted away."""
    return dist2d(lm[5], lm[17]) / max(scale, 1e-4)


def thumb_extended(lm: np.ndarray, world: np.ndarray, scale: float, thumb_deg: float, tip_scales: float) -> bool:
    return joint_angle(world, 2, 3, 4) > thumb_deg and dist2d(lm[4], lm[5]) / max(scale, 1e-4) > tip_scales


def palm_facing(lm: np.ndarray, side: Side, sign: float = 1.0) -> bool:
    """True when the palm faces the camera. In the mirrored frame (y down), a right palm facing the
    camera gives a positive z for cross(lm5 - lm0, lm17 - lm0); the left hand is the mirror image.
    If it reads backwards on your camera, set poses.palm_sign: -1 in config.yaml."""
    v1 = lm[5, :2] - lm[0, :2]
    v2 = lm[17, :2] - lm[0, :2]
    z = float(v1[0] * v2[1] - v1[1] * v2[0])
    if side is Side.LEFT:
        z = -z
    return z * sign > 0
