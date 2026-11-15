import os, tempfile
# where things are: HAGRID_DATA = the folder of HaGRID test annotation .json files,
# HAGRID_WORK = a folder for intermediate files (default: a "hg" folder in the temp dir)
DATA = os.environ.get("HAGRID_DATA", "/mnt/user-data/uploads/test")
WORK = os.environ.get("HAGRID_WORK", os.path.join(tempfile.gettempdir(), "hg")); os.makedirs(WORK, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, pickle, collections, numpy as np
sys.path.insert(0, ROOT)
from zephyr import contracts; contracts.ASPECT = 4/3
from zephyr.core.poses import PoseState
from zephyr.core import features as F
from zephyr.contracts import Side
feat = pickle.load(open(WORK + "/feat.pkl", "rb"))
print("pinch.down sweep: share of hands reading as pinched")
print("thr    ok(real pinch)  one(pointing up)  palm   stop   peace  like   no_gesture")
for t in (0.22, 0.25, 0.28, 0.30, 0.32, 0.35):
    print(f"{t:.2f}   " + "  ".join(f"{np.mean(feat[c][:,0] < t):7.2%}" for c in ("ok","one","palm","stop","peace","like","no_gesture")))
print("\npinch.up sweep: share reading as clearly open (release / palm allowed)")
for t in (0.35, 0.40, 0.45, 0.50):
    print(f"{t:.2f}   " + "  ".join(f"{c}:{np.mean(feat[c][:,0] > t):7.2%}" for c in ("ok","one","palm","stop","stop_inverted","four")))
print("\npalm_min_width sweep: share rejected")
for t in (0.35, 0.40, 0.45, 0.50, 0.55):
    print(f"{t:.2f}   " + "  ".join(f"{c}:{np.mean(feat[c][:,1] < t):6.1%}" for c in ("palm","stop","stop_inverted","four","grip","no_gesture")))
# pose proxy
hands = pickle.load(open(WORK + "/hands.pkl", "rb"))
conf = collections.defaultdict(collections.Counter); elig = collections.Counter(); n = collections.Counter()
for lab, user, h, w in hands:
    ps = PoseState(); s = F.raw_scale(h)
    for _ in range(4): p = ps.update(h, w, s, 0.95)
    conf[lab][p.value] += 1; n[lab] += 1
    facing = F.palm_facing(h, Side.LEFT) or F.palm_facing(h, Side.RIGHT)
    if p.value == "open" and F.pinch_ratio(h, s) > 0.40 and F.palm_width(h, s) >= 0.45: elig[lab] += 1
    if p.value == "open" and F.pinch_ratio(h, s) > 0.40: elig[lab + "_nowidth"] += 1
print("\npose (flat 2D angles stand in for 3D: a guide only) and share that could start Search")
for lab in ["palm","stop","stop_inverted","four","three","three2","peace","two_up","one","point","fist","ok","like","rock","grip","grabbing","no_gesture"]:
    c = conf[lab]
    print(f"{lab:14s} " + " ".join(f"{k}:{v/n[lab]:.0%}" for k, v in c.most_common(4)) + f"   | search-eligible {elig[lab]/n[lab]:.1%} (without width rule {elig[lab+'_nowidth']/n[lab]:.1%})")
