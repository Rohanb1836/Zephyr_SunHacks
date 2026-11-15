"""zephyr/core/selftest.py    Synthetic gesture tests for the engine (no camera, no model). Owner: Aadharsh.

    python -m zephyr.core.selftest

Feeds fake hand landmark sequences through the real tracker, poses and state machine and checks the
events. Run it after changing anything in core/ or the core sections of config.yaml.
"""
import sys
import numpy as np
from zephyr.bus import bus
from zephyr.config import cfg
from zephyr.contracts import T, Side, Role
from zephyr.core.hands import RawHand
from zephyr.core.engine import Engine

def hand(kind="open", ox=0.5, oy=0.6, s=0.1, side=Side.RIGHT):
    ext={"open":(1,1,1,1),"fist":(0,0,0,0),"point":(1,0,0,0),"two":(1,1,0,0),"rock":(1,0,0,1),"pinch":(1,1,1,1),"ppinch":(1,0,0,0)}[kind]
    thumb = kind in ("open","rock")
    lm=np.zeros((21,3),np.float32); lm[0]=[ox,oy,0]
    xs={5:-0.35,9:-0.1,13:0.15,17:0.38}
    mir = 1 if side is Side.RIGHT else -1
    for k in (5,9,13,17): lm[k]=[ox+mir*xs[k]*s, oy-1.0*s, 0]
    for k,e in zip((5,9,13,17),ext):
        x,y=lm[k][0],lm[k][1]
        if e:
            for j in (1,2,3): lm[k+j]=[x,y-0.35*s*j,0]
        else:
            lm[k+1]=[x,y-0.3*s,0]; lm[k+2]=[x,y-0.2*s,0]; lm[k+3]=[x,y-0.05*s,0]
    lm[1]=[ox-mir*0.3*s,oy-0.3*s,0]; lm[2]=[ox-mir*0.55*s,oy-0.5*s,0]
    if thumb: lm[3]=[ox-mir*0.8*s,oy-0.7*s,0]; lm[4]=[ox-mir*1.05*s,oy-0.9*s,0]
    else: lm[3]=[ox-mir*0.5*s,oy-0.75*s,0]; lm[4]=[ox-mir*0.3*s,oy-0.8*s,0]
    if kind in ("pinch","ppinch"):
        lm[4]=lm[8]+np.array([0.01*s,0,0],np.float32); lm[3]=(lm[2]+lm[4])/2
    return RawHand(lm, lm.copy(), side, 0.95)

ev=[]
for top in (T.GESTURE,T.SYSTEM,T.LEVEL,T.STROKE):
    bus.subscribe(top, lambda m,top=top: ev.append((top,m)))
ptr=[]; bus.subscribe(T.POINTER, lambda m: ptr.append(m))

class G:
    role=Role.OWNER; ok=(True,"ok"); guest=False
    def role_of(self,h): return self.role
    def can_act(self): return self.ok
    def on_arm(self,hs): return True
    def on_disarm(self): pass
    def guest_active(self): return self.guest
    def revoke_guest(self): self.guest=False

g=G(); eng=Engine(None, gate=g, detector=None)
t=[0]; fid=[0]
def run(frames):
    for raws in frames:
        t[0]+=33; fid[0]+=1; eng.process_raws(raws, t[0], fid[0])
def seq(n, fn): return [fn(i) for i in range(n)]
def gestures(): return [m.key for top,m in ev if top==T.GESTURE]
def clear(): ev.clear(); ptr.clear()
FAILS = []


def report(name, cond):
    if not cond:
        FAILS.append(name)
    print(("PASS " if cond else "FAIL ")+name, "" if cond else [ (top, getattr(m,'key',m)) for top,m in ev][:12])

# 1 arm
run(seq(36, lambda i:[hand("open",0.3,side=Side.LEFT), hand("open",0.7)]))
report("arm with both palms", "arm_toggle" in gestures() and eng.mode.value=="armed")
run(seq(10, lambda i:[]))  # hands down
clear()
# 2 swipe5 left then return
run(seq(8, lambda i:[hand("open",0.6)]))
run(seq(6, lambda i:[hand("open",0.6-0.06*(i+1))]))
run(seq(6, lambda i:[hand("open",0.24+0.06*(i+1))]))   # quick return
report("swipe5 left fires once, return blocked", gestures()==["swipe5.left"])
run(seq(30, lambda i:[])); clear()
# 3 swipe2 right
run(seq(8, lambda i:[hand("two",0.3)]))
run(seq(6, lambda i:[hand("two",0.3+0.06*(i+1))]))
report("swipe2 right", gestures()==["swipe2.right"])
run(seq(30, lambda i:[])); clear()
# 4 fist tap
run(seq(5, lambda i:[hand("open",0.5,oy=0.6)]))  # short, not long enough for palm hold? 165ms
run(seq(7, lambda i:[hand("fist",0.5)]))
run(seq(5, lambda i:[hand("open",0.5)]))
report("fist tap", gestures()==["fist_tap"])
run(seq(30, lambda i:[])); clear()
# 5 level right hand up
run(seq(14, lambda i:[hand("fist",0.5,0.6)]))
run(seq(20, lambda i:[hand("fist",0.5,0.6-0.01*i)]))
run(seq(10, lambda i:[hand("open",0.5,0.4)]))
lv=[m for top,m in ev if top==T.LEVEL]
report("level volume up then release", lv and all(m.kind=="volume" for m in lv) and lv[-1].active is False and all(m.delta>0 for m in lv[:-1]) and not gestures())
print("   level ticks:", len(lv)-1, "deltas", [round(m.delta,2) for m in lv[:4]])
run(seq(30, lambda i:[])); clear()
# 6 palm hold
run(seq(25, lambda i:[hand("open",0.5)]))
report("palm hold fires once", gestures()==["palm_hold"])
run(seq(30, lambda i:[])); clear()
# 7 pointer + pinch tap
run(seq(10, lambda i:[hand("point",0.5)]))
vis=[p for p in ptr if p.visible]
run(seq(4, lambda i:[hand("ppinch",0.5)]))
run(seq(4, lambda i:[hand("point",0.5)]))
gs=[m for top,m in ev if top==T.GESTURE]
report("pointer shows, pinch tap with pos", len(vis)>=6 and [m.key for m in gs]==["pinch_tap"] and gs[0].pos is not None)
run(seq(15, lambda i:[]))
report("pointer hides when hand gone", ptr[-1].visible is False)
clear()
# 8 pen
cfg.pen.__dict__["ink_default"]=True
run(seq(4, lambda i:[hand("open",0.5)]))
run(seq(25, lambda i:[hand("pinch",0.4+0.005*i)]))
run(seq(4, lambda i:[hand("open",0.6)]))
st=[m.phase for top,m in ev if top==T.STROKE]
report("pen stroke begin/points/end, no tap", st and st[0]=="begin" and st[-1]=="end" and st.count("point")>=20 and "pinch_tap" not in gestures())
cfg.pen.__dict__["ink_default"]=False
run(seq(30, lambda i:[])); clear()
# 9 stranger
g.role=Role.STRANGER
run(seq(25, lambda i:[hand("open",0.5)])); run(seq(14, lambda i:[hand("fist",0.5)]))
report("stranger produces nothing", not gestures() and not [1 for top,m in ev if top==T.LEVEL])
g.role=Role.OWNER; run(seq(30, lambda i:[])); clear()
# 10 suspended
run(seq(10, lambda i:[hand("point",0.5)]))
g.ok=(False,"looking_away"); run(seq(5, lambda i:[hand("point",0.5)]))
sy=[m for top,m in ev if top==T.SYSTEM]
report("suspend hides pointer", sy and sy[-1].mode.value=="suspended" and ptr[-1].visible is False)
g.ok=(True,"ok"); run(seq(3, lambda i:[hand("point",0.5)]))
report("resume armed", eng.mode.value=="armed")
run(seq(30, lambda i:[])); clear()
# 11 both palms while armed -> disarm
run(seq(36, lambda i:[hand("open",0.3,side=Side.LEFT), hand("open",0.7)]))
report("both palms pauses", eng.mode.value=="disarmed" and "palm_hold" not in gestures())

print("all passed" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
