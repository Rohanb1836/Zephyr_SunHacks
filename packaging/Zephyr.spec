# packaging/Zephyr.spec  PyInstaller recipe for Zephyr.app (macOS only). Owner: Swayam.
#
# Run it through packaging/build_mac.sh, which installs PyInstaller, downloads the models,
# builds, signs and makes the .dmg. Only the files listed in `datas` go into the app:
# never add .env, ~/.aws or clips here, because the .dmg gets shared.
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
    (str(ROOT / "zephyr" / "actions" / "profiles"), "zephyr/actions/profiles"),
    # gestures a first launch starts with; the app copies them to Application Support
    (str(ROOT / "data" / "bindings.json"), "data"),
    (str(ROOT / "data" / "gestures"), "data/gestures"),
]

# run.py imports most modules inside functions; list the whole package so none is missed
hiddenimports = mp_hidden + collect_submodules("zephyr") + ["AppKit", "Foundation", "Quartz", "objc"]

a = Analysis(
    [str(ROOT / "packaging" / "zephyr_app.py")],
    pathex=[str(ROOT)],
    binaries=mp_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Zephyr",
    console=False,           # a windowed app: no terminal
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Zephyr")
app = BUNDLE(
    coll,
    name="Zephyr.app",
    icon=str(ROOT / "packaging" / "Zephyr.icns"),
    bundle_identifier="edu.asu.sunhacks.zephyr",
    version="0.1.0",
    info_plist={
        "CFBundleName": "Zephyr",
        "CFBundleDisplayName": "Zephyr",
        "LSUIElement": True,              # lives in the menu bar, no Dock icon
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSCameraUsageDescription":
            "Zephyr watches your hands so you can control this Mac with gestures. Video stays on this Mac.",
        "NSAppleEventsUsageDescription":
            "Zephyr changes the volume, controls Spotify and switches desktops when you make a gesture.",
    },
)
