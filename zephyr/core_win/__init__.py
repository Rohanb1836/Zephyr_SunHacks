"""zephyr/core_win    Windows-only copy of zephyr/core, tuned on Windows laptop webcams. Owner: Ryan.

run.py uses this package only on Windows; the Mac runs zephyr/core, which is unchanged. Settings for
it are in config_windows.yaml (laid over config.yaml on Windows only). Each change against zephyr/core
was measured on recorded clips (tools/live_check.py, tools/tune.py) and is pinned by a test in
tools/test_gestures.py.
"""
