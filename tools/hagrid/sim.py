"""Real hand shapes through the full engine. sim.py <zephyr root> mac|win"""
import sys, pickle, collections
root, which = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
if which == "win":
    real = sys.platform; sys.platform = "win32"; import zephyr.config; sys.platform = real
import importlib, numpy as np
from zephyr import contracts; contracts.ASPECT = 4/3
from zephyr.bus import bus
from zephyr.contracts import T, Side, Role, SystemMode
pk = "zephyr.core_win" if which == "win" else "zephyr.core"
Engine = importlib.import_module(pk + ".engine").Engine
RawHand = importlib.import_module(pk + ".hands").RawHand
F = importlib.import_module(pk + ".features")
class Owner:
    def role_of(self, h): return Role.OWNER
    def can_act(self): return True, "ok"
    def on_arm(self, hs): return True
    def on_disarm(self): pass
    def guest_active(self): return False
    def revoke_guest(self): pass
ev = []
for top in (T.GESTURE, T.LEVEL, T.POINTER):
    bus.subscribe(top, lambda m, top=top: ev.append((top, m)))
bus.publish(T.PRESENCE, None); bus.publish(T.CONTEXT, None); bus.publish(T.WRITE, None)
hands = pickle.load(open("/tmp/hg/hands.pkl", "rb"))
rng = np.random.default_rng(0)
by = collections.defaultdict(list)
for lab, user, h, w in hands: by[lab].append((h, w))
def side_of(h):   # the side for which the hand shows its palm, else right
    return Side.RIGHT if F.palm_facing(h, Side.RIGHT) else Side.LEFT
def run(h, w, mode, mirror_side=False):
    ev.clear()
    eng = Engine(None, gate=Owner(), detector=None); eng.mode = SystemMode.ARMED
    s = F.raw_scale(h); side = side_of(h)
    if mirror_side: side = Side.LEFT if side is Side.RIGHT else Side.RIGHT
    k = 0.15 / s                      # put every hand at a desk distance (scale 0.15)
    base = (h - h.mean(0)) * k; base[:, 2] = 0
    t = 0
    for i in range(75):               # 2.5 s
        t += 33
        off = np.array([0.5, 0.55, 0], np.float32)
        if mode == "swing": off[0] += 0.25 * np.sin(2 * np.pi * i / 30)     # arm swinging at 1 Hz
        lm = (base + off).astype(np.float32); lm[:, :2] += rng.normal(0, 0.0015, (21, 2)).astype(np.float32)
        eng.process_raws([RawHand(lm, w, side, 0.95)], t, i + 1)
    g = [m.key.split(".")[0] for top, m in ev if top == T.GESTURE]
    lv = any(m.delta for top, m in ev if top == T.LEVEL)
    knob = any(c.state == "level" for c in eng.ctxs.values())
    ptr = any(m.visible for top, m in ev if top == T.POINTER)
    return g, lv, knob, ptr
N = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
res = {}
for lab, mode in [("no_gesture","still"), ("no_gesture","swing"), ("palm","still"), ("stop","still"), ("fist","still"), ("one","still"), ("peace","still"), ("two_up","still"), ("grip","still"), ("grabbing","still"), ("like","still"), ("ok","still"), ("rock","still"), ("three","still")]:
    items = by[lab]; idx = rng.choice(len(items), min(N if lab != "no_gesture" else 4000, len(items)), replace=False)
    c = collections.Counter()
    for j in idx:
        g, lv, knob, ptr = run(*items[j], mode)
        for x in set(g): c[x] += 1
        c["level_moved"] += lv; c["knob_grab"] += knob; c["pointer"] += ptr; c["anything"] += bool(g or lv)
    n = len(idx); res[(lab, mode)] = {k: v / n for k, v in c.items()}
    print(f"[{which}] {lab:11s} {mode:5s} n={n:4d} " + "  ".join(f"{k}:{v/n:.1%}" for k, v in sorted(c.items()) if v))
pickle.dump(res, open(f"/tmp/hg/sim_{which}_{sys.argv[4] if len(sys.argv)>4 else 'x'}.pkl", "wb"))
