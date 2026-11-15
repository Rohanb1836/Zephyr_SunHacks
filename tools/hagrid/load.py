import json, glob, os, pickle, collections
import numpy as np
rows = []   # (file_class, label, user, lm 21x2, bbox)
for p in sorted(glob.glob("/mnt/user-data/uploads/test/*.json")):
    fc = os.path.basename(p)[:-5]
    d = json.load(open(p))
    for k, v in d.items():
        lms = v.get("hand_landmarks") or []
        for lab, lm, bb in zip(v["labels"], lms, v["bboxes"]):
            if lm and len(lm) == 21:
                rows.append((fc, lab, v["user_id"], np.array(lm, np.float64), bb, k))
print(len(rows), "hands;", len({r[2] for r in rows}), "people")
c = collections.Counter(r[1] for r in rows); print(c)
pickle.dump(rows, open("/tmp/hg/rows.pkl", "wb"))
