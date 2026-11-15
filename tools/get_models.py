"""tools/get_models.py    Downloads the three MediaPipe .task models into models/. Owner: Aadharsh.

    python tools/get_models.py
"""
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
BASE = "https://storage.googleapis.com/mediapipe-models"
FILES = {
    "hand_landmarker.task": f"{BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "face_landmarker.task": f"{BASE}/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    "pose_landmarker_lite.task": f"{BASE}/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
}


def main() -> int:
    MODELS.mkdir(exist_ok=True)
    failed = 0
    for name, url in FILES.items():
        dest = MODELS / name
        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"[models] {name} already there")
            continue
        print(f"[models] downloading {name} ...")
        tmp = dest.with_suffix(".part")
        try:
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(dest)
            print(f"[models] {name} ok ({dest.stat().st_size // 1024} KB)")
        except Exception as exc:
            failed += 1
            tmp.unlink(missing_ok=True)
            print(f"[models] FAILED {name}: {exc!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
