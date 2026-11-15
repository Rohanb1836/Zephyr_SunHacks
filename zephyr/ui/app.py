"""zephyr/ui/app.py  QApplication, overlay, menu bar icon and settings window. Owner: Swayam.

run.py calls run_ui() on the main thread (master section 6.9). It returns the
Qt exit code.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

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


def _tray_pixmap() -> QPixmap:
    pm = QPixmap(44, 44)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0), 4))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(22, 22), 15, 15)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(QPointF(22, 22), 6, 6)
    p.end()
    return pm


def bring_to_front(window) -> None:
    window.show()
    window.raise_()
    window.activateWindow()
    if sys.platform == "darwin":
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception as exc:
            print(f"[ui] could not activate app: {exc!r}")


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
