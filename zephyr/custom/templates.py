"""zephyr/custom/templates.py  Landmark normalization and template building. Owner: Swayam.

A template is a (63,) float32 vector: 21 world landmarks with the wrist at the
origin, scaled so the wrist-to-middle-knuckle distance is 1, left hands mirrored.
There is no rotation normalization on purpose: thumb up and thumb down are
different gestures.

Distance is the worst finger: for each finger, the mean distance of its 4
landmarks, then the largest of the 5. A mean over all 21 points dilutes a
one-finger difference (rock on vs call me comes out at 0.07, under the 0.16
threshold); the worst finger keeps it at about 0.39.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from zephyr.contracts import HandObs, Pose, Side

BUILT_IN_POSES = frozenset({Pose.OPEN, Pose.FIST, Pose.POINT, Pose.TWO})


def normalize(world: np.ndarray, is_left: bool) -> np.ndarray:
    """(21, 3) world landmarks -> (63,) vector.
    Wrist at origin, palm size 1, left hand mirrored."""
    pts = world.astype(np.float32) - world[0]
    if is_left:
        pts[:, 0] *= -1.0
    size = float(np.linalg.norm(pts[9])) or 1e-6
    return (pts / size).reshape(-1)


def normalize_hand(hand: HandObs) -> np.ndarray:
    return normalize(hand.world, hand.side is Side.LEFT)


def worst_finger(norms: np.ndarray) -> np.ndarray:
    """(..., 21) per-landmark distances -> (...) worst-finger distance.
    Landmarks 1-4 thumb, 5-8 index, 9-12 middle, 13-16 ring, 17-20 pinky; 0 is the wrist."""
    lead = norms.shape[:-1]
    return norms[..., 1:].reshape(*lead, 5, 4).mean(axis=-1).max(axis=-1)


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """Worst-finger distance between two templates, in palm sizes."""
    return float(worst_finger(np.linalg.norm(a.reshape(21, 3) - b.reshape(21, 3), axis=1)))


def distances_to_many(vec: np.ndarray, templates: np.ndarray) -> np.ndarray:
    """Worst-finger distance from one (63,) vector to each row of an (N, 63) array."""
    if templates.size == 0:
        return np.empty(0, dtype=np.float32)
    diff = (templates - vec).reshape(len(templates), 21, 3)
    return worst_finger(np.linalg.norm(diff, axis=2))


@dataclass
class BuiltTemplate:
    template: np.ndarray   # (63,) float32
    threshold: float       # match threshold in palm sizes
    spread: float          # mean distance of the samples to the template


def build_template(samples: Sequence[np.ndarray], default_threshold: float,
                   max_threshold: float = 0.30) -> BuiltTemplate:
    """Per-coordinate median of the normalized samples.
    Threshold is max(default, 2.5 * mean distance of samples to the template),
    capped at max_threshold so one shaky recording cannot swallow its neighbours."""
    stack = np.stack([np.asarray(s, dtype=np.float32).reshape(-1) for s in samples])
    template = np.median(stack, axis=0).astype(np.float32)
    spread = float(distances_to_many(template, stack).mean())
    threshold = min(max(float(default_threshold), 2.5 * spread), float(max_threshold))
    return BuiltTemplate(template=template, threshold=threshold, spread=spread)
