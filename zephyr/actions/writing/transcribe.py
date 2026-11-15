"""Handwriting transcription. Configure credentials through the environment."""
import base64
import logging

LOG = logging.getLogger(__name__)
_warned = False
SYSTEM_PROMPT = '''You read handwriting drawn in the air. Return only the most likely intended text.
Split words and correct spelling using the supplied context. Never add words the person did
not write. In launcher mode return the closest known application name. If unreadable return
exactly UNREADABLE. No explanation or quotes.'''


def transcribe(png, app_label, mode, known, model, timeout_s, provider='anthropic'):
    global _warned
    try:
        import anthropic
        if provider == 'bedrock':
            client = anthropic.AnthropicBedrock(max_retries=0)
        elif provider == 'anthropic':
            client = anthropic.Anthropic(max_retries=0)
        else:
            raise ValueError('Unknown LLM provider')
        response = client.messages.create(
            model=model, max_tokens=60, system=SYSTEM_PROMPT, timeout=timeout_s,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                    'data': base64.b64encode(png).decode('ascii')}},
                {'type': 'text', 'text': f"App in front: {app_label}\nMode: {mode}\nKnown names: {', '.join(known)}"},
            ]}])
        return response.content[0].text.strip()
    except Exception as exc:
        if not _warned:
            LOG.warning('Writing transcription unavailable: %s', type(exc).__name__)
            _warned = True
        return 'UNREADABLE'
