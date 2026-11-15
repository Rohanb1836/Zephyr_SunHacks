"""zephyr/ui/style.py  Shared look for the settings window and wizard. Owner: Swayam.

Everything must work with the air pointer alone (Swayam doc 5.5): big buttons,
wide spacing, no drop-downs, no step that needs the keyboard.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

BUTTON_H = 110        # well above the 64 px minimum, so a shaky pinch still lands
SMALL_BUTTON_H = 84

STYLE = """
QWidget { background: #101216; color: #ffffff; font-size: 18pt; }
QLabel#title { font-size: 30pt; font-weight: 700; }
QLabel#step { font-size: 15pt; color: #8fd8ff; font-weight: 600; }
QLabel#hint { font-size: 16pt; color: #b8bcc4; }
QLabel#warn { font-size: 18pt; color: #ffcc66; font-weight: 600; }
QLabel#bad { font-size: 18pt; color: #ff8080; font-weight: 600; }
QLabel#good { font-size: 18pt; color: #7ee2a8; font-weight: 600; }
QLineEdit { background: #1c2028; border: 2px solid #2e3440; border-radius: 14px;
            padding: 14px 18px; font-size: 22pt; }
QLineEdit:focus { border-color: #40c4ff; }
QPushButton { background: #232833; border: 2px solid #313846; border-radius: 22px;
              padding: 12px 28px; font-size: 20pt; font-weight: 600; }
QPushButton:hover { background: #2c3340; border-color: #40c4ff; }
QPushButton:pressed { background: #40c4ff; color: #000000; }
QPushButton:checked { background: #40c4ff; color: #000000; border-color: #40c4ff; }
QPushButton#primary { background: #40c4ff; color: #000000; border-color: #40c4ff; }
QPushButton#primary:hover { background: #73d4ff; }
QPushButton#danger { color: #ff9a9a; }
QPushButton:disabled { color: #5a606c; border-color: #262b35; background: #181b21; }
QFrame#row { background: #171a20; border: 2px solid #252a34; border-radius: 22px; }
QScrollArea { border: none; }
"""


def big_button(text: str, kind: str = "", height: int = BUTTON_H, checkable: bool = False) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setMinimumHeight(height)
    b.setMinimumWidth(180)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)  # the air pointer clicks, nothing needs keyboard focus
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return b


def label(text: str, kind: str = "") -> QLabel:
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    lab.setWordWrap(True)
    return lab


def set_kind(widget, kind: str) -> None:
    """Switch a label's style (hint, good, warn, bad) and repaint it."""
    if widget.objectName() == kind:
        return
    widget.setObjectName(kind)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
