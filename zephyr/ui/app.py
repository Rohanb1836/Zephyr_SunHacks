"""zephyr/ui/app.py  QApplication, overlay, menu bar icon and settings window. Owner: Swayam.

run.py calls run_ui() on the main thread (master section 6.9). It returns the
Qt exit code.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QSystemTrayIcon

from zephyr.custom.conf import value
from zephyr.ui.overlay import Overlay, make_overlay_native
from zephyr.ui.state import UiState

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def ask_for_accessibility() -> bool:
    """True when macOS lets Zephyr move the pointer and press keys. If not, macOS shows its
    prompt, which also adds Zephyr to Privacy & Security > Accessibility for one click.
    From Terminal the check is for Terminal, which usually has it already."""
    if sys.platform != "darwin":
        return True
    try:
        import ctypes
        ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        if ax.AXIsProcessTrusted():
            return True
        # AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: true}) shows the prompt
        keys = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(ax, "kAXTrustedCheckOptionPrompt").value)
        values = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value)
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_long] + [ctypes.c_void_p] * 2
        options = cf.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
        ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        trusted = ax.AXIsProcessTrustedWithOptions(options)
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease(options)
        if not trusted:
            print("[ui] Accessibility is off: allow Zephyr in System Settings > Privacy & Security > Accessibility")
        return bool(trusted)
    except Exception as exc:
        print(f"[ui] could not check Accessibility: {exc!r}")
        return True  # unknown, so no menu item


def zephyr_folder() -> Path:
    """Gestures, keys and the log. The app sets ZEPHYR_HOME; from the repo it is data/."""
    home = os.environ.get("ZEPHYR_HOME")
    if home:
        return Path(home)
    from zephyr.custom import store
    return store.DATA_DIR


# the Zephyr mark in the 44 px menu bar pixmap: a Z written in the air, flipped upside down,
# ending in a fingertip. packaging/make_icon.py draws the same stroke for the app icon
TRAY_CORNERS = ((3.6, 37.6), (36.6, 37.6), (4.7, 9.1), (37.8, 9.1))


def _toward(p: QPointF, q: QPointF, k: float) -> QPointF:
    """The point k pixels from p on the way to q."""
    v = q - p
    return p + v * (k / max((v.x() ** 2 + v.y() ** 2) ** 0.5, 1e-6))


def _tray_pixmap() -> QPixmap:
    a, b, c, d = (QPointF(x, y) for x, y in TRAY_CORNERS)
    path = QPainterPath(a)
    for corner, came_from, going_to in ((b, a, c), (c, b, d)):
        path.lineTo(_toward(corner, came_from, 6.3))     # round each corner
        path.quadTo(corner, _toward(corner, going_to, 6.3))
    path.lineTo(d)

    pm = QPixmap(44, 44)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    steps = 48
    last = path.pointAtPercent(0.0)
    for i in range(1, steps + 1):                        # thin at the start, thicker at the tip
        t = i / steps
        here = path.pointAtPercent(t)
        p.setPen(QPen(QColor(0, 0, 0), 3.4 + 1.2 * t, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(last, here)
        last = here
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(d, 4.4, 4.4)
    p.end()
    return pm


def activate_app() -> None:
    """A menu bar app has no Dock icon, so macOS must be told to bring its windows forward."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception as exc:
            print(f"[ui] could not activate app: {exc!r}")


def bring_to_front(window) -> None:
    window.show()
    window.raise_()
    window.activateWindow()
    activate_app()


class ZephyrUi:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.state = UiState()
        self.overlay = Overlay(app.primaryScreen(), self.state)
        self.overlay.show()
        self.native = make_overlay_native(self.overlay)
        self.settings = None

        icon = QIcon(_tray_pixmap())
        icon.setIsMask(True)  # macOS draws it in the menu bar colour
        self.tray = QSystemTrayIcon(icon)
        menu = QMenu()
        act_settings = QAction("Settings…", menu)
        act_settings.triggered.connect(self.open_settings)
        self.act_overlay = QAction("Hide overlay", menu)
        self.act_overlay.triggered.connect(self.toggle_overlay)
        act_folder = QAction("Open Zephyr folder", menu)
        act_folder.triggered.connect(self.open_folder)
        act_quit = QAction("Quit Zephyr", menu)
        act_quit.triggered.connect(app.quit)
        menu.addAction(act_settings)
        menu.addAction(self.act_overlay)
        menu.addAction(act_folder)
        if not ask_for_accessibility():
            # stays until the next launch; it is there for anyone who clicked Deny
            act_access = QAction("Allow Accessibility…", menu)
            act_access.triggered.connect(self.open_accessibility)
            menu.addAction(act_access)
        self._add_tv_menu(menu)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Zephyr")
        self.tray.show()
        self._menu = menu

    def open_settings(self) -> None:
        if self.settings is None:
            from zephyr.ui.settings import SettingsWindow
            self.settings = SettingsWindow()
        bring_to_front(self.settings)

    # ---------- Roku TV (zephyr/tv) ----------
    def _add_tv_menu(self, menu: QMenu) -> None:
        tv_menu = menu.addMenu("Roku TV")
        self.act_tv = QAction("Control the TV", tv_menu)
        self.act_tv.setCheckable(True)
        self.act_tv.triggered.connect(self.toggle_tv)
        act_find = QAction("Find TV on this Wi-Fi", tv_menu)
        act_find.triggered.connect(lambda: self._tv().find())
        act_address = QAction("Type TV address…", tv_menu)
        act_address.triggered.connect(self.type_tv_address)
        act_sim = QAction("Start TV simulator", tv_menu)
        act_sim.triggered.connect(self.start_tv_simulator)
        self.act_tv_status = QAction("No TV yet", tv_menu)
        self.act_tv_status.setEnabled(False)
        for act in (self.act_tv, act_find, act_address, act_sim):
            tv_menu.addAction(act)
        tv_menu.addSeparator()
        tv_menu.addAction(self.act_tv_status)
        self._tv_timer = QTimer()
        self._tv_timer.timeout.connect(self._refresh_tv)    # a gesture can turn TV mode on or off too
        self._tv_timer.start(400)

    @staticmethod
    def _tv():
        from zephyr.tv.remote import tv_remote
        return tv_remote()

    def toggle_tv(self, checked: bool) -> None:
        self._tv().set_mode("tv" if checked else "normal")

    def type_tv_address(self) -> None:
        tv = self._tv()
        activate_app()
        text, ok = QInputDialog.getText(None, "Roku TV", "The TV's address (on the TV: Settings > Network > About):",
                                        QLineEdit.Normal, tv.host)
        if ok and text.strip():
            tv.use(text.strip())

    def start_tv_simulator(self) -> None:
        from zephyr.tv import simulator
        self._tv().use(simulator.start(open_page=True), simulator.NAME)

    def _refresh_tv(self) -> None:
        tv = self._tv()
        if self.act_tv.isChecked() != tv.active:
            self.act_tv.setChecked(tv.active)
        self.act_tv_status.setText(tv.status_text())

    def open_folder(self) -> None:
        folder = zephyr_folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_accessibility(self) -> None:
        QDesktopServices.openUrl(QUrl(ACCESSIBILITY_PANE))

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
            self.act_overlay.setText("Show overlay")
        else:
            self.overlay.show()
            make_overlay_native(self.overlay)
            self.act_overlay.setText("Hide overlay")


def run_ui(open_settings: Optional[bool] = None) -> int:
    """run.py calls this with no arguments. Settings opens from the menu bar icon, or at
    start when ui.open_settings is true (opening it steals focus from the front app)."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Zephyr")
    app.setQuitOnLastWindowClosed(False)  # closing Settings must not quit the overlay
    ui = ZephyrUi(app)
    if open_settings is None:
        open_settings = bool(value("ui", "open_settings"))
    if open_settings:
        ui.open_settings()
    app._zephyr_ui = ui  # keep a reference alive
    return app.exec()
