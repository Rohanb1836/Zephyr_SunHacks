# packaging/Zephyr_win.spec  PyInstaller recipe for Zephyr.exe (Windows only). Owner: Swayam.
#
# Run it through packaging/build_windows.ps1, which installs PyInstaller, downloads the models,
# builds and zips the folder. Only the files listed in `datas` go into the app: never add .env,
# ~/.aws or clips here, because the zip gets shared.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent   # SPECPATH is the folder holding this spec

if not (ROOT / "models" / "hand_landmarker.task").exists():
    raise SystemExit("models/hand_landmarker.task is missing. Run: python tools/get_models.py")
models = sorted((ROOT / "models").glob("*.task"))

# MediaPipe needs its whole package in a PyInstaller app (Google's packaging note). Its LLM
# converter and Model Maker parts only pull in jax or torch, and Zephyr never imports them.
mp_datas, mp_binaries, mp_hidden = collect_all(
    "mediapipe", filter_submodules=lambda name: ".genai" not in name and "model_maker" not in name)

datas = mp_datas + [(str(m), "models") for m in models] + [
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "config_windows.yaml"), "."),   # the Windows tuning laid over config.yaml
    (str(ROOT / "zephyr" / "actions" / "profiles"), "zephyr/actions/profiles"),
    # gestures a first launch starts with; the app copies them to %APPDATA%\Zephyr
    (str(ROOT / "data" / "bindings.json"), "data"),
    (str(ROOT / "data" / "gestures"), "data/gestures"),
]

# run.py and the Windows backend import most modules inside functions; list them so none is missed
hiddenimports = mp_hidden + collect_submodules("zephyr") + collect_submodules("screen_brightness_control") + [
    "pycaw.pycaw", "comtypes", "comtypes.client", "win32api", "win32con", "win32gui", "win32process",
    "pywintypes", "psutil", "pyperclip", "pyautogui",
]

a = Analysis(
    [str(ROOT / "packaging" / "zephyr_app.py")],
    pathex=[str(ROOT)],
    binaries=mp_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["AppKit", "Foundation", "Quartz", "objc"],   # the Mac backend's imports
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Zephyr",
    icon=str(ROOT / "packaging" / "Zephyr.ico"),
    console=False,           # a windowed app: no terminal; output goes to %APPDATA%\Zephyr\zephyr.log
)
coll = COLLECT(exe, a.binaries, a.datas, name="Zephyr")
