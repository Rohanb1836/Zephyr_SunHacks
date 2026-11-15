"""Run supported action specs on the action worker."""
import time


def describe_action_types():
    return [
        {'type': 'url', 'label': 'Open a website', 'value_hint': 'https://...'},
        {'type': 'app', 'label': 'Open an app', 'value_hint': 'Spotify'},
        {'type': 'hotkey', 'label': 'Keyboard shortcut', 'value_hint': 'mod+t'},
        {'type': 'text', 'label': 'Paste text', 'value_hint': 'Text to paste'},
        {'type': 'open', 'label': 'Open a file', 'value_hint': '~/Desktop/file.pdf'},
        {'type': 'media', 'label': 'Media control', 'value_hint': 'playpause, next, previous'},
        {'type': 'tv', 'label': 'Roku TV', 'value_hint': 'Home, Play, launch YouTube, mode on'},
    ]


class ActionRunner:
    def __init__(self, backend, search=None, set_mode=None, tv=None):
        self.backend = backend
        self.search = search
        self.set_mode = set_mode
        self.tv = tv          # zephyr.tv.remote.TvRemote.run: one Roku TV action

    def run(self, spec, confirmed=False):
        if not isinstance(spec, dict):
            raise ValueError('Action must be an object')
        if spec.get('confirm') and not confirmed:
            raise PermissionError('Action requires confirmation')
        kind, value = spec.get('type'), spec.get('value')
        if kind == 'hotkey':
            keys = [key.strip() for key in value.split('+') if key.strip()]
            if not keys:
                raise ValueError('Empty shortcut')
            return self.backend.hotkey(*keys)
        if kind == 'url': return self.backend.open_url(value)
        if kind == 'app': return self.backend.open_app(value)
        if kind == 'open': return self.backend.open_file(value)
        if kind == 'text': return self.backend.paste_text(value)
        if kind == 'media': return self.backend.media(value)
        if kind == 'system':
            if value == 'desktop_next': return self.backend.desktop('next')
            if value == 'desktop_prev': return self.backend.desktop('prev')
            if value == 'search' and self.search is not None: return self.search()
            if value == 'mute': return self.backend.hotkey('volumemute')
            if value == 'lock': return self.backend.hotkey('ctrl', 'command', 'q')
            raise ValueError(f'Unsupported system action: {value}')
        if kind == 'macro':
            for step in value:
                time.sleep(max(0, float(step.get('wait_ms', 0))) / 1000)
                self.run(step['action'], confirmed=confirmed)
            return
        if kind == 'mode' and self.set_mode is not None:
            if value not in ('normal', 'tv'): raise ValueError('Invalid mode')
            return self.set_mode(value)
        if kind == 'tv' and self.tv is not None: return self.tv(value)
        # Shell and HTTP require a confirmation and security design before execution.
        raise ValueError(f'Unsupported action type: {kind}')
