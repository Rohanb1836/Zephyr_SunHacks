"""MediaPipe face and body inference on mirrored camera frames."""
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from zephyr.config import cfg

ROOT = Path(__file__).resolve().parents[2]


class Models:
    def __init__(self):
        mode = vision.RunningMode.VIDEO
        self.pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/pose_landmarker_lite.task")),
            running_mode=mode, num_poses=int(cfg.presence.max_people)))
        try:
            self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/face_landmarker.task")),
                running_mode=mode, num_faces=int(cfg.presence.max_people),
                output_facial_transformation_matrixes=True))
        except Exception:
            self.pose.close()
            raise

    def detect(self, frame, ts_ms):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        return self.pose.detect_for_video(image, ts_ms), self.face.detect_for_video(image, ts_ms)

    def close(self):
        self.pose.close()
        self.face.close()
