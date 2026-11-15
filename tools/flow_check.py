"""Flow probes: what happens between gestures. Usage: flow.py mac|win"""
import sys, importlib, importlib.util
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
which = sys.argv[1]
if which == "win":
    real = sys.platform; sys.platform = "win32"; import zephyr.config; sys.platform = real
import numpy as np
mod = "tools/test_gestures.py" if which == "win" else "tools/test_gestures_mac.py"
spec = importlib.util.spec_from_file_location("tg", str(__import__("pathlib").Path(__file__).resolve().parent.parent / mod)); tg = importlib.util.module_from_spec(spec); spec.loader.exec_module(tg)
from zephyr.contracts import T, Side
from zephyr.bus import bus
ptr = []; bus.subscribe(T.POINTER, ptr.append)
hand, n = tg.hand, tg.n
def probe(name, build, ink=False, expect=None):
    ptr.clear(); r = tg.rig(ink=ink); build(r)
    vis = [p.visible for p in ptr]; hides = sum(1 for a, b in zip(vis, vis[1:]) if a and not b)
    lv = [e for e in r.of(T.LEVEL) if e.delta]
    extra = [m for t, m in r.events if t == "win.right_click"]
    print(f"[{which}] {name}: gestures={r.keys()} pointer_hides={hides} level_changes={len(lv)}" + (f" right_clicks={len(extra)}" if extra else "") + (f"   <-- want {expect}" if expect else ""))
    return r
# 1 click that reads as a fist while pinching: does the pointer blink off afterwards?
probe("click (reads fist), keep pointing", lambda r: (r.run(n(10, lambda i:[hand("point")])), r.run(n(10, lambda i:[hand("fpinch")])), r.run(n(3, lambda i:[hand("fist")])), r.run(n(30, lambda i:[hand("point")]))), expect="1 pinch_tap, 0 hides")
probe("three clicks in a row", lambda r: (r.run(n(10, lambda i:[hand("point")])), [ (r.run(n(8, lambda i:[hand("ppinch")])), r.run(n(22, lambda i:[hand("point")]))) for _ in range(3)]), expect="3 taps, 0 hides")
# 2 fist tap, then the open hand lingers
probe("fist tap, open hand lingers 2 s", lambda r: (r.run(n(6, lambda i:[hand("open")])), r.run(n(8, lambda i:[hand("fist")])), r.run(n(60, lambda i:[hand("open")]))), expect="fist_tap only")
# 3 knob, release, open hand lingers
def knob(r):
    r.run(n(20, lambda i:[hand("fist")]))
    def turned(deg):
        raw = hand("fist"); th = np.radians(deg); lm = raw.landmarks.copy(); d = lm[:, :2] - lm[0, :2]
        lm[:, 0] = lm[0, 0] + d[:, 0]*np.cos(th) - d[:, 1]*np.sin(th); lm[:, 1] = lm[0, 1] + d[:, 0]*np.sin(th) + d[:, 1]*np.cos(th)
        return tg.RawHand(lm, raw.world, raw.side, raw.score)
    r.run(n(20, lambda i:[turned(2*(i+1))])); r.run(n(60, lambda i:[hand("open")]))
probe("knob turn, let go, open hand lingers 2 s", knob, expect="no palm_hold, no fist_tap")
# 4 pen stroke ends, open hand lingers (thinking between letters)
probe("pen stroke, then open hand lingers 2 s", lambda r: (r.run(n(10, lambda i:[hand("open")])), r.run(n(25, lambda i:[hand("ppinch", ox=0.4+0.005*i)])), r.run(n(60, lambda i:[hand("open", ox=0.52)]))), ink=True, expect="no palm_hold")
# 5 one hand rests as a fist (chin on fist, holding a cup); the other hand works
probe("left hand rests as a fist 8 s, right hand points and clicks at 5 s", lambda r: (r.run(n(150, lambda i:[hand("fist", 0.25, side=Side.LEFT)])), r.run(n(12, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("point", 0.7)])), r.run(n(8, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("ppinch", 0.7)])), r.run(n(25, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("point", 0.7)]))), expect="1 pinch_tap")
probe("left hand rests as a fist, right hand swipes at 5 s", lambda r: (r.run(n(150, lambda i:[hand("fist", 0.25, side=Side.LEFT)])), r.run(n(8, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("open", 0.5)])), r.run(n(6, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("open", 0.5+0.06*(i+1))])), r.run(n(10, lambda i:[hand("fist", 0.25, side=Side.LEFT), hand("open", 0.86)]))), expect="swipe5.right")
# 6 swipes at different cadences: 4 swipes right with a return each
def swipes(period):
    def f(r):
        r.run(n(8, lambda i:[hand("open", 0.35)]))
        for k in range(4):
            r.run(n(5, lambda i:[hand("open", 0.35+0.06*(i+1))]))
            back = max(period - 5, 5)
            r.run(n(back, lambda i:[hand("open", 0.65-0.30*(i+1)/back)]))
    return f
for per in (36, 30, 24, 18):
    probe(f"4 swipes right, one every {per*33} ms", swipes(per), expect="4 x swipe5.right")
# 7 swipe then change your mind: swipe right, 1 s later swipe left
probe("swipe right, return, pause 1 s, swipe left", lambda r: (r.run(n(8, lambda i:[hand("open",0.35)])), r.run(n(5, lambda i:[hand("open",0.35+0.06*(i+1))])), r.run(n(10, lambda i:[hand("open",0.65)])), r.run(n(30, lambda i:[hand("open",0.65)])), r.run(n(5, lambda i:[hand("open",0.65-0.06*(i+1))])), r.run(n(10, lambda i:[hand("open",0.35)]))), expect="right, left")
# 8 pointer to swipe to pointer
probe("point, open and swipe, point again", lambda r: (r.run(n(15, lambda i:[hand("point",0.4)])), r.run(n(6, lambda i:[hand("open",0.4)])), r.run(n(5, lambda i:[hand("open",0.4+0.06*(i+1))])), r.run(n(8, lambda i:[hand("open",0.7)])), r.run(n(15, lambda i:[hand("point",0.7)]))), expect="swipe5.right, pointer back")
# 9 fist tap twice quickly (pause, play)
probe("two fist taps 1.3 s apart", lambda r: [ (r.run(n(10, lambda i:[hand("open")])), r.run(n(10, lambda i:[hand("fist")])), r.run(n(20, lambda i:[hand("open")]))) for _ in range(2)], expect="2 fist_tap, no palm_hold")
# 10 palm hold, then keep the hand up
probe("palm hold, hand stays up 3 s", lambda r: r.run(n(120, lambda i:[hand("open")])), expect="1 palm_hold")
