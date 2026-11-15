import sys, pickle, collections, numpy as np
sys.path.insert(0, "/home/claude/zephyr")
from zephyr import contracts; contracts.ASPECT = 4/3
from zephyr.core.poses import PoseState
from zephyr.core import features as F
hands = pickle.load(open("/tmp/hg/hands.pkl", "rb"))
def tilt(h):
    """Degrees between 'wrist to middle knuckle' and straight up in the picture (0 = fingers up, 180 = down)."""
    dx = (h[9,0]-h[0,0]) * contracts.ASPECT; dy = h[0,1]-h[9,1]
    return abs(np.degrees(np.arctan2(dx, dy)))
T = collections.defaultdict(list); E = collections.defaultdict(list)
for lab, user, h, w in hands:
    t = tilt(h); T[lab].append(t)
    ps = PoseState(); s = F.raw_scale(h)
    for _ in range(4): p = ps.update(h, w, s, 0.95)
    if p.value == "open" and F.pinch_ratio(h, s) > 0.40 and F.palm_width(h, s) >= 0.45: E[lab].append(t)
for lab in ["palm","stop","stop_inverted","four","fist","one","peace","two_up","ok","grabbing","no_gesture"]:
    t = np.array(T[lab]); e = np.array(E[lab])
    print(f"{lab:14s} tilt pcts 50/90/95/99/99.9: {np.percentile(t,[50,90,95,99,99.9]).round(0)}  | of search-eligible: tilt>60 {np.mean(e>60) if len(e) else 0:.1%} >75 {np.mean(e>75) if len(e) else 0:.1%} >90 {np.mean(e>90) if len(e) else 0:.1%}  n_elig={len(e)}")
n = len(T["no_gesture"])
for lim in (60, 75, 90, 110):
    print(f"limit {lim}: no_gesture still search-eligible {np.sum(np.array(E['no_gesture'])<=lim)/n:.1%};  real palms lost: palm {np.mean(np.array(T['palm'])>lim):.2%} stop {np.mean(np.array(T['stop'])>lim):.2%} four {np.mean(np.array(T['four'])>lim):.2%}")
print()
P = collections.defaultdict(list)
for lab, user, h, w in hands:
    ps = PoseState(); s = F.raw_scale(h)
    for _ in range(4): p = ps.update(h, w, s, 0.95)
    P[(lab, p.value)].append(tilt(h))
for lab in ["point","like","grip","rock","thumb_index","three","grabbing"]:
    t = np.array(T[lab]); print(f"{lab:12s} tilt 50/90/95/99/99.9: {np.percentile(t,[50,90,95,99,99.9]).round(0)}  >90: {np.mean(t>90):.2%}  >120: {np.mean(t>120):.2%}")
ng = np.array(T["no_gesture"]); print("no_gesture   tilt <=90:", f"{np.mean(ng<=90):.1%}", "<=120:", f"{np.mean(ng<=120):.1%}")
tot = len(T["no_gesture"])
for pose in ("open","fist","point","two","unknown"):
    t = np.array(P[("no_gesture", pose)])
    print(f"resting hands reading {pose:7s}: {len(t)/tot:.1%} of all;  still there with a 120 limit: {np.sum(t<=120)/tot:.1%}")
