import os, tempfile
# where things are: HAGRID_DATA = the folder of HaGRID test annotation .json files,
# HAGRID_WORK = a folder for intermediate files (default: a "hg" folder in the temp dir)
DATA = os.environ.get("HAGRID_DATA", "/mnt/user-data/uploads/test")
WORK = os.environ.get("HAGRID_WORK", os.path.join(tempfile.gettempdir(), "hg")); os.makedirs(WORK, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pickle, collections, csv, numpy as np
rows = pickle.load(open(WORK + "/rows.pkl", "rb"))
# The palm is rigid whatever the fingers do: solve per hand for the image aspect that makes its palm
# bones match a template, take the median per person, snap to the common photo shapes.
P = [(0,5),(0,9),(0,13),(0,17),(5,9),(9,13),(13,17),(5,17),(5,13),(9,17),(0,1),(1,5)]
K = [np.array(r[1:], float).reshape(21,2) for r in csv.reader(open("/tmp/ds/kinivi_hand-gesture-recognition-mediapipe/model/keypoint_classifier/keypoint.csv", encoding="utf-8-sig")) if r[0]=="0"]
tmpl = np.median([[np.sum((k[a]-k[b])**2)/np.sum((k[0]-k[9])**2) for a,b in P] for k in K], axis=0)
per_user = collections.defaultdict(list)
for fc, lab, user, lm, bb, key in rows:
    dx2 = np.array([(lm[a,0]-lm[b,0])**2 for a,b in P]); dy2 = np.array([(lm[a,1]-lm[b,1])**2 for a,b in P])
    sol, *_ = np.linalg.lstsq(np.stack([dx2, -tmpl], axis=1), -dy2, rcond=None)
    if sol[0] > 0 and sol[1] > 0: per_user[user].append(np.sqrt(sol[0]))
est = {u: float(np.median(v)) for u, v in per_user.items() if len(v) >= 3}
vals = np.array(list(est.values()))
print("people with estimate", len(est), "of", len({r[2] for r in rows}))
h, e = np.histogram(np.log2(vals), bins=np.linspace(-1.5, 1.5, 37))
for hh, ee in zip(h, e): print(f"{2**ee:5.2f} {'#'*(hh//25)} {hh}")
pickle.dump(est, open(WORK + "/aspect_raw.pkl", "wb"))
