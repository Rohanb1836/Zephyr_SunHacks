"""Match visible bodies across frames and associate faces by nose position."""
from dataclasses import dataclass

from zephyr.config import cfg
from zephyr.contracts import dist2d


@dataclass(frozen=True)
class Person:
    person_id: int
    nose: tuple
    wrists: tuple
    shoulder_width: float
    face_matrix: object = None
    face_landmarks: object = None


def xy(point):
    return (float(point.x), float(point.y))


class PeopleTracker:
    def __init__(self):
        self.previous = {}
        self.next_id = 1

    def update(self, poses, faces, ts_ms):
        # Remember people through short detection dropouts (a turned body, an arm across the
        # chest). Forgetting the owner after 0.5 s gave them a new id and disarmed Zephyr.
        keep_ms = int(cfg.presence.get("reid_ms", 1500))
        self.previous = {i: v for i, v in self.previous.items() if ts_ms - v[1] <= keep_ms}
        bodies = []
        for lm in poses.pose_landmarks:
            if len(lm) < 17:
                continue
            width = dist2d(xy(lm[11]), xy(lm[12]))
            if width <= 1e-6:
                continue
            wrists = tuple(xy(lm[i]) for i in (15, 16) if lm[i].visibility > 0.5)
            bodies.append((xy(lm[0]), wrists, width))
        # Greedy global matching prevents two bodies acquiring the same previous ID.
        pairs = sorted((dist2d(nose, old.nose), index, pid)
                       for index, (nose, _, _) in enumerate(bodies)
                       for pid, (old, _) in self.previous.items())
        assigned, used = {}, set()
        for distance, index, pid in pairs:
            if distance <= 0.25 and index not in assigned and pid not in used:
                assigned[index] = pid
                used.add(pid)
        face_pairs = sorted((dist2d(nose, xy(face[1])), bi, fi)
                            for bi, (nose, _, _) in enumerate(bodies)
                            for fi, face in enumerate(faces.face_landmarks) if len(face) > 1)
        face_for, used_faces = {}, set()
        for distance, bi, fi in face_pairs:
            if distance <= 0.15 and bi not in face_for and fi not in used_faces:
                face_for[bi] = fi
                used_faces.add(fi)
        result = []
        matrices = faces.facial_transformation_matrixes or []
        for index, (nose, wrists, width) in enumerate(bodies):
            pid = assigned.get(index)
            if pid is None:
                pid, self.next_id = self.next_id, self.next_id + 1
            fi = face_for.get(index)
            person = Person(pid, nose, wrists, width,
                            matrices[fi] if fi is not None and fi < len(matrices) else None,
                            faces.face_landmarks[fi] if fi is not None else None)
            result.append(person)
            self.previous[pid] = (person, ts_ms)
        return tuple(result)
