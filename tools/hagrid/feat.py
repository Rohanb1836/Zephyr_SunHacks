import os, tempfile
# where things are: HAGRID_DATA = the folder of HaGRID test annotation .json files,
# HAGRID_WORK = a folder for intermediate files (default: a "hg" folder in the temp dir)
DATA = os.environ.get("HAGRID_DATA", "/mnt/user-data/uploads/test")
WORK = os.environ.get("HAGRID_WORK", os.path.join(tempfile.gettempdir(), "hg")); os.makedirs(WORK, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, pickle, collections, numpy as np
sys.path.insert(0, ROOT)
from zephyr import contracts
from zephyr.core import features as F
from zephyr.contracts import Side
contracts.ASPECT = 4/3
rows = pickle.load(open(WORK + "/rows.pkl", "rb"))
raw = pickle.load(open(WORK + "/aspect_raw.pkl", "rb"))
def conf_aspect(v):
    if 0.72 <= v <= 0.90: return 0.75
    if 1.30 <= v <= 1.62: return 4/3
    return None
out = []
for fc, lab, user, lm, bb, key in rows:
    a = conf_aspect(raw.get(user, 0))
    if a is None: continue
    h = np.zeros((21, 3), np.float32)
    h[:, 0] = (lm[:, 0] - lm[:, 0].mean()) * a / (4/3) + 0.5
    h[:, 1] = lm[:, 1] - lm[:, 1].mean() + 0.5
    w = np.zeros((21, 3), np.float32); w[:, 0] = lm[:, 0] * a; w[:, 1] = lm[:, 1]
    out.append((lab, user, h, w))
print(len(out), "hands from", len({o[1] for o in out}), "people with a confident image shape")
pickle.dump(out, open(WORK + "/hands.pkl", "wb"))
feat = collections.defaultdict(list)
for lab, user, h, w in out:
    s = F.raw_scale(h)
    ang = F.finger_angles(w)
    v1 = h[5, :2] - h[0, :2]; v2 = h[17, :2] - h[0, :2]
    up = (h[0, 1] - h[9, 1]) / s      # 1 = fingers straight up
    feat[lab].append((F.pinch_ratio(h, s), F.palm_width(h, s), s, *ang, up))
feat = {k: np.array(v) for k, v in feat.items()}; pickle.dump(feat, open(WORK + "/feat.pkl", "wb"))
pc = lambda x: np.percentile(x, [1, 5, 25, 50, 75, 95, 99]).round(2)
print("class            n   pinch<.25 <.40 <.50 | palm_width<.45 <.50 | scale p5/p50 | pinch pcts")
for lab in ["one","point","ok","thumb_index","palm","stop","stop_inverted","four","three","three2","peace","two_up","fist","grip","grabbing","like","rock","no_gesture"]:
    f = feat[lab]; p, w, s = f[:, 0], f[:, 1], f[:, 2]
    print(f"{lab:14s} {len(f):5d}   {np.mean(p<.25):6.1%} {np.mean(p<.40):6.1%} {np.mean(p<.50):6.1%} |   {np.mean(w<.45):6.1%} {np.mean(w<.5):6.1%}   | {np.percentile(s,5):.3f} {np.percentile(s,50):.3f} | {pc(p)}")
