"""Identify the foreground app and load its matching profile."""
import json
import sys
from pathlib import Path

PROFILE_DIR = Path(__file__).with_name('profiles')


def load_profiles():
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in PROFILE_DIR.glob('*.json')}


def front_app():
    if sys.platform != 'darwin':
        raise NotImplementedError('Foreground app detection currently supports macOS only')
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return '', ''
    return str(app.bundleIdentifier() or ''), str(app.localizedName() or '')


def find_profile(app_id, profiles, platform=None):
    platform = platform or ('mac' if sys.platform == 'darwin' else 'win')
    for profile in profiles.values():
        candidates = profile.get('match', {}).get(platform, [])
        if app_id.casefold() in {item.casefold() for item in candidates}:
            return profile
    return profiles['default']
