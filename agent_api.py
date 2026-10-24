"""Versioned, read-only projection of stored public announcement evidence."""
import base64
import copy
import json
import threading
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import storage


STATES = {'announced', 'scheduled', 'completed', 'cancelled', 'unconfirmed'}
TYPES = {'reset', 'credit'}


class RateLimited(Exception):
    pass


class Gateway:
    """Small public snapshot cache and bounded per-client request windows."""
    def __init__(self, clock=time.monotonic, limit=120):
        self.clock, self.limit = clock, limit
        self.lock = threading.Lock()
        self.clients = {}
        self.cached = None
        self.cached_at = 0

    def get(self, client, loader):
        with self.lock:
            at = self.clock()
            self.clients = {ip: value for ip, value in self.clients.items() if at - value[0] < 60}
            start, count = self.clients.get(client, (at, 0))
            if count >= self.limit or (client not in self.clients and len(self.clients) >= 5000):
                raise RateLimited()
            self.clients[client] = (start, count + 1)
            if self.cached is None or at - self.cached_at >= 3:
                self.cached = loader()
                self.cached_at = at
            return self.cached


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Time must include a timezone')
    return parsed.astimezone(timezone.utc)


def pending_analysis(db, version):
    ids = storage.get_state(db, 'incremental_post_ids')
    pending = db.execute('''SELECT p.post_id FROM posts p WHERE NOT EXISTS
      (SELECT 1 FROM post_analyses a WHERE a.post_id=p.post_id AND a.input_hash=p.content_hash
       AND a.engine='qwen' AND a.prompt_version=?)''', (version,)).fetchall()
    live = set(ids) if ids is not None else None
    return sum(live is None or row[0] in live for row in pending)


def metadata(snapshot, at):
    checked = snapshot.get('source_checked_at')
    success = snapshot.get('last_sync')
    age = (instant(at) - instant(success)).total_seconds() if success else None
    fresh_seconds = snapshot.get('fresh_seconds', 5400)
    source = ('unavailable' if not success else
              'fresh' if snapshot.get('sync_ok') and 0 <= age <= fresh_seconds else 'stale')
    analysis = ('up_to_date' if snapshot.get('analysis_pending', 0) == 0 else
                'pending' if snapshot.get('ai', {}).get('enabled') else 'unavailable')
    return dict(generated_at=at, source_checked_at=checked, source_last_success_at=success,
                source_status=source, analysis_status=analysis, freshness_seconds=fresh_seconds,
                coverage_note=snapshot.get('collection', {}).get('coverage_note', '仅包含公开 RSS 返回的原帖'))


def public_event(event, detail=False):
    item = {key: event.get(key) for key in ('event_id', 'type', 'state', 'published_at', 'activity_at',
            'expected_at', 'effective_at', 'scope', 'time_quote', 'original_timezone')}
    evidence = event.get('evidence', [])
    def category(post):
        return post.get('category') if post.get('analyzed_post_id', post['post_id']) == post['post_id'] else None
    forecasts = [p['created_at'] for p in evidence if category(p) == 'forecast' or p.get('role') == 'forecast']
    if not forecasts and event.get('time_quote'):
        forecasts = [p['created_at'] for p in evidence if event['time_quote'] in p.get('text', '')]
    completions = [p['created_at'] for p in evidence if category(p) == 'completed' or p.get('role') == 'completed']
    item['forecast_announced_at'] = min(forecasts, key=instant) if forecasts else None
    item['completed_announced_at'] = min(completions, key=instant) if completions else None
    item['activity_at'] = max((p['created_at'] for p in evidence), key=instant,
                              default=item.get('activity_at') or item['published_at'])
    # Metadata may have an explicit activity_at even when detail evidence is omitted by a fixture.
    if event.get('activity_at') and instant(event['activity_at']) > instant(item['activity_at']):
        item['activity_at'] = event['activity_at']
    kind = event.get('time_kind') if item.get('expected_at') else 'unknown'
    item['time_kind'] = kind or 'unknown'
    item['deadline_at'] = (event.get('window_end') or event.get('expected_at')) if kind == 'deadline' else None
    if kind == 'deadline':
        item['expected_at'] = None
    item['source_urls'] = list(dict.fromkeys(p['url'] for p in evidence if p.get('url')))
    if detail:
        item['evidence'] = copy.deepcopy(sorted(evidence, key=lambda p: instant(p['created_at'])))
    return item


def status(events, meta):
    resets = sorted((e for e in events if e['type'] == 'reset'),
                    key=lambda e: (instant(e['activity_at']), e['event_id']), reverse=True)
    latest = resets[0] if resets else None
    forecast = next((e for e in resets if e['forecast_announced_at']), None)
    completed = [e for e in resets if e['state'] == 'completed' and e['completed_announced_at']]
    last = max(completed, key=lambda e: instant(e['completed_announced_at']), default=None)
    active, state = None, 'none'
    if meta['source_status'] != 'fresh' or meta['analysis_status'] != 'up_to_date':
        state = 'unknown'
    elif latest and latest['state'] in ('scheduled', 'announced'):
        target = latest.get('deadline_at') or latest.get('expected_at')
        if target and instant(target) <= instant(meta['generated_at']):
            state = 'elapsed'
        else:
            active, state = latest, 'active'
    elif latest and latest['state'] == 'unconfirmed':
        state = 'unknown'
    return dict(latest_forecast=forecast, active_forecast=active, forecast_status=state, latest_completed=last)


def envelope(data, meta):
    return {'api_version': '1', 'data': data, 'meta': meta}


def failure(code, message):
    return {'api_version': '1', 'error': {'code': code, 'message': message}}


def event_list(events, query):
    allowed = {'type', 'state', 'since', 'limit', 'cursor'}
    if query.keys() - allowed or any(len(v) != 1 or not v[0] for v in query.values()):
        raise ValueError('Unknown, repeated or empty query parameter')
    filters = {key: query[key][0] for key in ('type', 'state', 'since') if key in query}
    if 'type' in filters and filters['type'] not in TYPES:
        raise ValueError('Invalid event type')
    if 'state' in filters and filters['state'] not in STATES:
        raise ValueError('Invalid event state')
    since = instant(filters['since']) if 'since' in filters else None
    limit = int(query.get('limit', ['20'])[0])
    if not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    boundary = None
    if 'cursor' in query:
        try:
            raw = query['cursor'][0]
            cursor = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
            if cursor['filters'] != filters or len(cursor['after']) != 2:
                raise ValueError()
            boundary = (instant(cursor['after'][0]), str(cursor['after'][1]))
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError('Invalid cursor or changed filters') from exc
    selected = [e for e in events if all(e[k] == v for k, v in filters.items() if k != 'since')
                and (since is None or instant(e['activity_at']) > since)]
    selected.sort(key=lambda e: (instant(e['activity_at']), e['event_id']), reverse=True)
    if boundary:
        selected = [e for e in selected if (instant(e['activity_at']), e['event_id']) < boundary]
    page = selected[:limit]
    next_cursor = None
    if len(selected) > limit:
        last = page[-1]
        raw = json.dumps({'filters': filters, 'after': [last['activity_at'], last['event_id']]}, separators=(',', ':'))
        next_cursor = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
    return dict(items=page, next_cursor=next_cursor, filters=filters)


def respond(path, query, snapshot, at=None):
    at = at or storage.now()
    try:
        meta = metadata(snapshot, at)
        if meta['source_status'] == 'unavailable' and not snapshot.get('events'):
            return 503, failure('unavailable', 'No public announcement snapshot is available yet')
        events = [public_event(e) for e in snapshot.get('events', [])]
        if path == '/api/v1/status':
            if query:
                raise ValueError('status accepts no query parameters')
            return 200, envelope(status(events, meta), meta)
        if path == '/api/v1/events':
            return 200, envelope(event_list(events, query), meta)
        if path.startswith('/api/v1/events/'):
            if query:
                raise ValueError('event detail accepts no query parameters')
            identity = unquote(path.removeprefix('/api/v1/events/'))
            event = next((e for e in snapshot.get('events', []) if e['event_id'] == identity), None)
            if event:
                return 200, envelope(public_event(event, True), meta)
        return 404, failure('not_found', 'Event or API endpoint not found')
    except (ValueError, TypeError, OverflowError) as exc:
        return 400, failure('invalid_parameter', str(exc))
