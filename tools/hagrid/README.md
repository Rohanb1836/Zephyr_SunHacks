# HaGRID checks (scratch scripts)

Scripts used on 15 Nov to test Zephyr against the HaGRID v2 test annotations (MediaPipe hand
landmarks, 100,747 hands from 3,726 people). They are working scripts, not a polished tool; paths are set as below.

Order: `load.py` (read the json files) -> `aspect.py` (estimate each person's image shape from the
rigid palm) -> `feat.py` (pinch ratio, palm width per class) -> `sweep.py` (threshold sweeps, pose
check) -> `orient.py` (hand tilt: the basis of hands.rest_tilt_deg and holds.palm_max_tilt_deg)
-> `sim.py <repo> mac|win` (real hand shapes held still or swinging through the full engine).

HaGRID landmarks are 2D only. Zephyr reads finger curl from MediaPipe's 3D landmarks, so the pose
numbers here are a guide; pinch ratio, palm width and tilt are 2D in Zephyr too and are tested fully.

Measured: pinch.down 0.25 catches 97.2% of real pinches ("ok") and 0.3% of pointing-up hands;
palm_min_width 0.45 rejects 0.9 to 1.6% of real palms and 83% of edge-on hands; deliberate palms
lean under 41 degrees (99.9th percentile) while resting hands hang at a median of 164.
`sim.py` before/after counts were not finished.

Paths: set `HAGRID_DATA` to the folder of HaGRID test annotation `.json` files and, if you like,
`HAGRID_WORK` for intermediate files (default: an `hg` folder in the temp folder). On Windows:

    set HAGRID_DATA=%USERPROFILE%\Downloads\test
    python tools\hagrid\load.py && python tools\hagrid\aspect.py && python tools\hagrid\feat.py
    python tools\hagrid\sim.py . mac 3000 branch
    python tools\hagrid\sim.py . win 3000 branch

sim.py takes the repo to test as its first argument, so `main` can be compared without switching
branches: `git worktree add ..\zephyr-main main`, then `python tools\hagrid\sim.py ..\zephyr-main mac 3000 main`.
