"""Structured, bounded diagnostics without request contents or credentials."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import traceback
import urllib.parse


def redact(value):
    text = str(value)
    for key, secret in os.environ.items():
        if len(secret) >= 8 and re.search(r'KEY|SECRET|TOKEN|PASSWORD|DATABASE_URL|WEBHOOK', key):
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)Bearer\s+[^\s\"\']+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'postgres(?:ql)?://[^\s\"\']+', '[REDACTED_DATABASE]', text)
    def url(match):
        try:
            parsed = urllib.parse.urlsplit(match.group())
            return urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
        except ValueError:
            return '[REDACTED_URL]'
    text = re.sub(r'https?://[^\s<>\"\']+', url, text)
    text = re.sub(r'(?i)\b(token|api_key|password|secret|authorization|cookie|q)=([^\s&]+)', r'\1=[REDACTED]', text)
    return text[:12000]


def safe_path(value):
    try:
        return urllib.parse.urlsplit(value).path[:2048] or '/'
    except ValueError:
        return '<invalid>'


def emit(event, *, level='INFO', error=None, **fields):
    record = {'ts': datetime.now(timezone.utc).isoformat(), 'level': level,
              'service': 'reset-radar', 'event': event}
    for key, value in fields.items():
        if isinstance(value, (bool, int, float)) or value is None:
            record[key] = value
        elif isinstance(value, (list, tuple)):
            record[key] = [redact(item) for item in value[:30]]
        else:
            record[key] = redact(value)
    if error is not None:
        record.update(error_type=type(error).__name__, error_message=redact(error))
        # Capture frames without locals or source text (which may contain test/request data).
        record['stack'] = [{'file': os.path.basename(frame.filename), 'line': frame.lineno,
                            'function': frame.name} for frame in traceback.extract_tb(error.__traceback__)[-20:]]
    try:
        print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)
    except (OSError, ValueError):
        pass


@contextmanager
def operation(event, **fields):
    started = time.monotonic()
    emit(event + '.started', **fields)
    result = {}
    try:
        yield result
    except Exception as error:
        emit(event + '.failed', level='ERROR', error=error,
             duration_ms=round((time.monotonic() - started) * 1000, 3), **fields)
        raise
    else:
        emit(event + '.finished', duration_ms=round((time.monotonic() - started) * 1000, 3), **fields, **result)
