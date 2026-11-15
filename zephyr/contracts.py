"""zephyr/contracts.py      Shared types for Zephyr. Owner: Aadharsh.
Frozen after kickoff. Do not edit without telling the whole team."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

import numpy as np

ASPECT = 640 / 480     # frame width / height. Set once from the real camera size (core.camera does this).


def dist2d(a, b, aspect: Optional[float] = None) -> float:
    """Distance between two normalized image points, in frame heights.
    aspect defaults to the live contracts.ASPECT (read at call time, so the camera can update it)."""
    if aspect is None:
        aspect = ASPECT
    return float(np.hypot((a[0] - b[0]) * aspect, a[1] - b[1]))


class Pose(str, Enum):
    NONE = "none"              # not enough stable frames yet
    OPEN = "open"              # five fingers extended
    FIST = "fist"              # four fingers curled, thumb tucked
    POINT = "point"            # index extended; middle, ring, pinky curled; thumb free
    TWO = "two"                # index and middle extended; ring and pinky curled
    UNKNOWN = "unknown"        # a clear hand that is none of the above (custom gestures live here)


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class Role(str, Enum):
    OWNER = "owner"
    GUEST = "guest"
    STRANGER = "stranger"


class SystemMode(str, Enum):
    DISARMED = "disarmed"
    ARMED = "armed"
    SUSPENDED = "suspended"       # armed, but the controller is not looking or not present


class GestureKind(str, Enum):
    ARM_TOGGLE = "arm_toggle"
    SWIPE = "swipe"
    PALM_HOLD = "palm_hold"
    FIST_TAP = "fist_tap"
    PINCH_TAP = "pinch_tap"
    CUSTOM = "custom"


class T:
    """Bus topic names. Always use these constants, never raw strings."""
    HANDS = "hands"                       # HandsFrame         core -> ui, presence, custom recorder
    PRESENCE = "presence"                 # PresenceState      presence -> core, ui
    SYSTEM = "system"                     # SystemState        core -> ui, actions
    GESTURE = "gesture"                   # GestureEvent       core -> actions, ui
    HOLD = "hold"                         # HoldProgress       core, presence -> ui
    POINTER = "pointer"                   # PointerEvent       core -> actions, ui
    LEVEL = "level"                       # LevelEvent         core -> actions
    LEVEL_VALUE = "level_value"           # LevelValue         actions -> ui
    STROKE = "stroke"                     # StrokeEvent        core -> actions.writing, ui
    CONTEXT = "context"                   # ContextState       actions -> core, ui
    WRITE = "write"                       # WriteState         actions -> ui
    ACTION = "action"                     # ActionFired        actions -> ui
    ACTION_REQUEST = "action_request"     # ActionRequest      ui, custom -> actions
    CUSTOM_RELOAD = "custom_reload"       # None               ui -> core (templates changed)
    LATENCY = "latency"                   # dict               anyone -> tools


@dataclass(frozen=True, eq=False)
class HandObs:
    hand_id: int               # stable across frames (core tracker)
    side: Side
    landmarks: np.ndarray      # (21, 3) float32. x, y normalized in the MIRRORED frame.
    world: np.ndarray          # (21, 3) float32. metres, origin near the hand centre.
    scale: float               # dist2d(wrist, middle knuckle). The "hand scale".
    pose: Pose                 # debounced pose
    pinch: float               # dist2d(thumb tip, index tip) / scale
    score: float               # detection confidence 0..1
    role: Role = Role.OWNER    # filled by the engine from Gate.role_of()

    @property
    def wrist(self) -> tuple[float, float]:
        return float(self.landmarks[0, 0]), float(self.landmarks[0, 1])


@dataclass(frozen=True, eq=False)
class HandsFrame:
    frame_id: int
    ts_ms: int
    hands: tuple[HandObs, ...]


@dataclass(frozen=True)
class PersonObs:
    person_id: int
    role: Role
    nose: tuple[float, float]
    wrists: tuple[tuple[float, float], ...]    # visible wrists only, image coords
    shoulder_width: float                      # dist2d between shoulders
    attentive: bool                            # looking at the screen (with grace periods)


@dataclass(frozen=True)
class PresenceState:
    ts_ms: int
    people: tuple[PersonObs, ...]
    owner_id: Optional[int]
    controller_id: Optional[int]    # owner, or the guest after a handshake
    controller_role: Optional[Role]
    controller_attentive: bool
    grant_progress: float           # 0..1 while a handshake is being held
    reason: str = ""                # ok, looking_away, no_owner, owner_left, guest_active


@dataclass(frozen=True)
class SystemState:
    mode: SystemMode
    reason: str = ""


@dataclass(frozen=True)
class GestureEvent:
    kind: GestureKind
    ts_ms: int
    hand_id: int
    side: Side
    role: Role
    direction: Optional[str] = None              # "left" or "right" for SWIPE
    fingers: Optional[int] = None                # 5 or 2 for SWIPE
    name: Optional[str] = None                   # custom gesture id for CUSTOM
    pos: Optional[tuple[float, float]] = None    # screen-normalized, for PINCH_TAP

    @property
    def key(self) -> str:
        """Profile lookup key: 'swipe5.left', 'swipe2.right', 'palm_hold', 'custom.rock_on'."""
        if self.kind is GestureKind.SWIPE:
            return f"swipe{self.fingers}.{self.direction}"
        if self.kind is GestureKind.CUSTOM:
            return f"custom.{self.name}"
        return self.kind.value


@dataclass(frozen=True)
class HoldProgress:
    kind: str           # "arm_toggle", "palm_hold", "level", "custom", "grant"
    progress: float     # 0..1. Publish 0.0 when a hold is cancelled.
    hand_id: int = -1


@dataclass(frozen=True)
class PointerEvent:
    visible: bool
    x: float = 0.0      # screen-normalized
    y: float = 0.0
    frozen: bool = False       # True while a pinch is in progress
    ts_ms: int = 0


@dataclass(frozen=True)
class LevelEvent:
    kind: str         # "volume" or "brightness"
    delta: float      # percentage points to add on this tick (signed)
    active: bool      # False on the final event when the fist is released


@dataclass(frozen=True)
class LevelValue:
    kind: str         # "volume" or "brightness"
    value: float      # 0..100, the real value after the change


@dataclass(frozen=True)
class StrokeEvent:
    phase: str        # "begin", "point", "end"
    stroke_id: int
    x: float          # screen-normalized pen position
    y: float
    ts_ms: int


@dataclass(frozen=True)
class ContextState:
    app_id: str                # macOS bundle id or Windows exe name
    app_label: str             # "Spotify"
    profile: str               # "spotify"
    ink_enabled: bool          # a held pinch draws ink in this context
    mode: str = "normal"       # "normal", "search", "write", "tv"


@dataclass(frozen=True)
class WriteState:
    # phase: idle, ready, inking, transcribing, preview, accepted, cancelled, error
    phase: str
    text: str = ""
    anchor: Optional[tuple[float, float]] = None      # where to draw the preview bubble


@dataclass(frozen=True)
class ActionFired:
    label: str                 # "Next track"
    app_label: str             # "Spotify"
    ok: bool = True
    ts_ms: int = 0


@dataclass(frozen=True)
class ActionRequest:
    spec: dict                 # an action spec, see section 6.5
    source: str = "gesture"    # "gesture" or "settings_test"


class GateProtocol(Protocol):
    """Implemented by Ryan (presence/gate.py). Called from the core thread.
    Must be fast and thread-safe."""
    def role_of(self, hand: HandObs) -> Role: ...
    # can_act reason: ok, looking_away, no_owner, owner_left
    def can_act(self) -> tuple[bool, str]: ...
    # on_arm enrolls the owner. Returns False if no person was found for those hands.
    def on_arm(self, hands: tuple[HandObs, ...]) -> bool: ...
    def on_disarm(self) -> None: ...
    def guest_active(self) -> bool: ...
    def revoke_guest(self) -> None: ...


class MatcherProtocol(Protocol):
    """Implemented by Swayam (custom/matcher.py).
    Called from the core thread for UNKNOWN poses."""
    # match returns (gesture id, distance), or None when nothing matches.
    def match(self, hand: HandObs) -> Optional[tuple[str, float]]: ...
    def reload(self) -> None: ...
