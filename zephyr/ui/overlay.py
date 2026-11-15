"""zephyr/ui/overlay.py  Transparent, click-through, always-on-top overlay. Owner: Swayam.

Covers the main screen. A 16 ms QTimer pulls the latest state and repaints only
when something changed or an animation is running. On macOS it is also raised
above full-screen apps on every desktop and never takes focus.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from zephyr.custom.conf import section
from zephyr.ui import widgets
from zephyr.ui.state import UiState, now_ms


def make_overlay_native(widget: QWidget) -> bool:
    """Call once after widget.show(). macOS only. Returns True when it worked."""
    if sys.platform != "darwin":
        return False
    try:
        import objc
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory, NSStatusWindowLevel

        # accessory policy: no Dock icon, never steals focus
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        window.setLevel_(NSStatusWindowLevel + 1)
        # can join all spaces (1 << 0), stationary (1 << 4), full-screen auxiliary (1 << 8)
        window.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))
        window.setIgnoresMouseEvents_(True)
        window.setHidesOnDeactivate_(False)
        window.setHasShadow_(False)
        return True
    except Exception as exc:
        # keep running as a normal always-on-top window; tell the team at M1
        print(f"[ui] native overlay setup failed, using plain Qt flags: {exc!r}")
        return False


class Overlay(QWidget):
    def __init__(self, screen, state: UiState) -> None:
        super().__init__()
        self.state = state
        ui_cfg = section("ui")
        self.hud_size = tuple(ui_cfg.get("hud_size", [300, 225]))
        self.hud_corner = str(ui_cfg.get("hud_corner", "bottom_right"))

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        # macOS hides Tool windows when the app is inactive unless this is set
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setGeometry(screen.geometry())

        self._t = now_ms()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self) -> None:
        t = now_ms()
        if self.state.update(t):
            self._t = t
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        try:
            widgets.draw_all(p, float(self.width()), float(self.height()), self.state, now_ms(),
                             self.hud_size, self.hud_corner)
        except Exception as exc:  # one bad frame must never kill the overlay
            print(f"[ui] paint error: {exc!r}")
        finally:
            p.end()
