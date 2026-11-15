"""zephyr/tv  TV mode: Zephyr gestures drive a Roku TV over the home network. Owner: Swayam.

    ecp.py        talks to the TV (Roku's External Control Protocol, HTTP on port 8060)
    remote.py     what each gesture does to the TV while TV mode is on
    simulator.py  a stand-in Roku TV in the browser, for demos without the TV
    check.py      tests: python -m zephyr.tv.check
"""
