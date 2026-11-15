"""zephyr/presence_win    Windows-only copy of zephyr/presence. Owner: Ryan.

run.py uses this package only on Windows; the Mac runs zephyr/presence, which is unchanged.
Differences: a hand must be within arm's reach of a person (reach_ratio), people keep their id through
short dropouts (reid_ms), a face hidden by a hand is not looking away (face_lost_hold_ms), and a body
missing for a frame or two does not pause. Settings are in config_windows.yaml. Tests:
tools/test_presence_replay.py.
"""
