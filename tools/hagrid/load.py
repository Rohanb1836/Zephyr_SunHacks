import os, tempfile
# where things are: HAGRID_DATA = the folder of HaGRID test annotation .json files,
# HAGRID_WORK = a folder for intermediate files (default: a "hg" folder in the temp dir)
DATA = os.environ.get("HAGRID_DATA", "/mnt/user-data/uploads/test")
WORK = os.environ.get("HAGRID_WORK", os.path.join(tempfile.gettempdir(), "hg")); os.makedirs(WORK, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json, glob, os, pickle, collections
import numpy as np
rows = []   # (file_class, label, user, lm 21x2, bbox)
for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
    fc = os.path.basename(p)[:-5]
    d = json.load(open(p))
    for k, v in d.items():
        lms = v.get("hand_landmarks") or []
        for lab, lm, bb in zip(v["labels"], lms, v["bboxes"]):
            if lm and len(lm) == 21:
                rows.append((fc, lab, v["user_id"], np.array(lm, np.float64), bb, k))
print(len(rows), "hands;", len({r[2] for r in rows}), "people")
c = collections.Counter(r[1] for r in rows); print(c)
pickle.dump(rows, open(WORK + "/rows.pkl", "wb"))
