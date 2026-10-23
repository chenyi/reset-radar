#!/usr/bin/env python3
"""Read-only public announcement dashboard. No OpenAI account access."""
import copy
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from email.utils import format_datetime
import collector
import storage
from analyzer import classify_candidates
from ai_analysis import analyze_batch, revalidate_current, recover_requests, VERSION as AI_VERSION
from billing_guard import FundingUnavailable

ROOT = Path(__file__).resolve().parent
# Legacy mirror recovery code is retained; runtime collection now uses collector.py.
UPSTREAM = 'https://www.starshoreai.com/quota-orbit/'
SOURCE_NAME = '额度星盘 · Quota Orbit'
ORIGIN = os.environ.get('PUBLIC_ORIGIN', 'http://127.0.0.1:8787').rstrip('/')
CACHE_FILE = Path(os.environ.get('STATE_DIRECTORY', str(ROOT / '.local'))) / 'events.json'
DB_FILE = CACHE_FILE.parent / 'radar.db'
INTERVAL = max(300, int(os.environ.get('SYNC_INTERVAL', '300')))
STATE_LABELS = {'scheduled': '重置预告', 'announced': '已公布', 'completed': '宣布完成', 'cancelled': '已取消', 'unconfirmed': '待确认'}
SCOPE_LABELS = {'global': '全局', 'paid': '付费用户', 'plus-pro-business': 'Plus / Pro / Business', 'unknown': '适用范围未明确'}
lock = threading.Lock()
cache = {'events': [], 'source_healthy': False, 'source_checked_at': None, 'last_sync': None, 'sync_ok': False}
etag = None


def timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def summarize(data, now=None):
    now = time.time() if now is None else now
    checked = timestamp(data.get('source_checked_at'))
    stale = not (data.get('sync_ok') and data.get('source_healthy') and checked is not None and 0 <= now - checked <= data.get('fresh_seconds', 1800))
    events = sorted(data.get('events', []), key=lambda e: timestamp(e.get('activity_at') or e.get('published_at')) or 0, reverse=True)
    analyzed = [e for e in events if e.get('confidence') != 'candidate' and e.get('state') != 'unconfirmed']
    latest = analyzed[0] if analyzed else events[0] if events else None
    completed_roots = {e.get('root_id') or e['event_id'] for e in events if e.get('state') in ('completed', 'cancelled')}
    upcoming = None
    # Only the latest public event can set the current countdown. Old forecasts never resurface.
    if latest and latest.get('state') in ('scheduled', 'announced'):
        deadline = latest.get('window_end') or latest.get('expected_at') or latest.get('window_start')
        target = timestamp(deadline)
        if target and target > now and (latest.get('root_id') or latest['event_id']) not in completed_roots and not stale:
            upcoming = {**latest, 'target_at': deadline}
    completions = [e for e in events if e.get('state')=='completed' and e.get('type')=='reset']
    latest_completed = max(completions,key=lambda e:timestamp(e.get('completed_announced_at') or e.get('published_at')) or 0,default=None)
    return {'stale': stale, 'latest': latest, 'upcoming': upcoming, 'latest_completed': latest_completed,
            'count': len(events), 'completed_count': sum(e.get('state') == 'completed' and e.get('type') == 'reset' for e in events)}


def safe_sources(values):
    return [url for url in (values or []) if isinstance(url, str) and urllib.parse.urlsplit(url).scheme == 'https' and urllib.parse.urlsplit(url).hostname in ('x.com', 'twitter.com', 'www.starshoreai.com')]


def fetch_all():
    global etag
    url = UPSTREAM + 'api/v1/events.json'
    first = None
    records = {}
    new_etag = None
    for page in range(10):
        headers = {'User-Agent': 'ResetRadar/1.0 (public read-only dashboard)', 'Accept': 'application/json'}
        if page == 0 and etag:
            headers['If-None-Match'] = etag
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise ValueError('Oversized upstream response')
                data = json.loads(payload)
                if page == 0:
                    new_etag = response.headers.get('ETag')
        except urllib.error.HTTPError as exc:
            if page == 0 and exc.code == 304:
                with lock:
                    cache.update(last_sync=utcnow(), sync_ok=True)
                return
            raise
        if data.get('schema_version') != 1 or not isinstance(data.get('events'), list):
            raise ValueError('Unsupported upstream schema')
        if first is None:
            first = data
        for event in data['events']:
            if not isinstance(event, dict) or not isinstance(event.get('event_id'), str):
                raise ValueError('Invalid event')
            event['source_urls'] = safe_sources(event.get('source_urls'))
            records[event['event_id']] = event
        if not data.get('next'):
            break
        url = urllib.parse.urljoin(UPSTREAM, data['next'])
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.starshoreai.com' or not parsed.path.startswith('/quota-orbit/api/v1/events/'):
            raise ValueError('Invalid upstream pagination URL')
        time.sleep(0.65)
    else:
        raise ValueError('Upstream pagination exceeds limit')
    result = {'events': sorted(records.values(), key=lambda e: timestamp(e.get('published_at')) or 0, reverse=True),
              'source_healthy': first.get('source_healthy') is True, 'source_checked_at': first.get('source_checked_at'),
              'generated_at': first.get('generated_at'), 'last_sync': utcnow(), 'sync_ok': True}
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False))
    temp.replace(CACHE_FILE)
    with lock:
        cache.clear()
        cache.update(result)
    etag = new_etag
    print(f'sync ok: {len(records)} public events', flush=True)


def sync_loop():
    while True:
        db = storage.connect(DB_FILE)
        try:
            collector.incremental(db)
            classify_candidates(db)
        except Exception as exc:
            print(f'RSS sync failed: {type(exc).__name__}; preserving archived posts', flush=True)
        interval = storage.get_state(db, 'source_ttl', 2400)
        db.close()
        time.sleep(interval)


def backfill_loop():
    while True:
        db = storage.connect(DB_FILE)
        try:
            collector.backfill(db, os.environ.get('HISTORY_START', '2026-04-23'),
                               os.environ.get('HISTORY_END', '2026-10-24'))
            remaining = db.execute("SELECT count(*) FROM collection_jobs WHERE kind='backfill' AND status!='source_exhausted'").fetchone()[0]
            if not remaining:
                return
        except Exception as exc:
            print(f'Historical collection paused: {type(exc).__name__}; checkpoint retained', flush=True)
        finally:
            classify_candidates(db)
            db.close()
        time.sleep(300)


def analysis_loop():
    while True:
        db = storage.connect(DB_FILE)
        try:
            worked = analyze_batch(db)
        except Exception as exc:
            worked = False
            with db:
                storage.set_state(db, 'ai_status', {'enabled': False, 'reason':
                    str(exc) if isinstance(exc, FundingUnavailable) else 'AI 分析暂不可用，原帖归档继续'})
            print(f'AI analysis paused: {type(exc).__name__}: {str(exc)[:250]}', flush=True)
        finally:
            db.close()
        time.sleep(0.2 if worked else 60)


def snapshot():
    db = storage.connect(DB_FILE)
    try:
        result = storage.snapshot(db, AI_VERSION)
    finally:
        db.close()
    result['fresh_seconds'] = 5400
    result['summary'] = summarize(result)
    result['source'] = {'name': 'Tibo @thsottiaux · 公开 RSS 镜像', 'url': 'https://x.com/thsottiaux',
                        'feed': collector.SOURCE + '/thsottiaux/with_replies/rss'}
    result['refresh_seconds'] = 2400
    return result


def event_title(event):
    original = next((p['text'] for p in event.get('evidence', []) if p['post_id'] == event['event_id'].removeprefix('tibo-')), None)
    if original:
        return STATE_LABELS.get(event.get('state'), '待确认') + ' · ' + original[:120]
    if event.get('title'):
        return STATE_LABELS.get(event.get('state'), '待确认') + ' · ' + event['title']
    kind = '重置卡' if event.get('type') == 'credit' else '额度重置'
    return kind + ' · ' + STATE_LABELS.get(event.get('state'), '待确认')


def rss(data):
    root = ET.Element('rss', version='2.0')
    channel = ET.SubElement(root, 'channel')
    for key, value in [('title', '重置雷达 · Codex 公开重置动态'), ('link', ORIGIN), ('description', '公开预告、完成公告与重置卡动态；个人额度请在自己的账号确认。'), ('language', 'zh-CN'), ('ttl', '5')]:
        ET.SubElement(channel, key).text = value
    if data['summary']['stale']:
        ET.SubElement(channel, 'category').text = '来源暂不可用或数据过期：条目仅供回看'
    for event in data.get('messages', data['events'])[:30]:
        item = ET.SubElement(channel, 'item')
        message = 'post_id' in event
        identity = event['post_id'] if message else event['event_id']
        labels = {'forecast':'重置预告','progress':'重置进展','completed':'重置完成','credit':'重置卡','correction':'取消／更正'}
        ET.SubElement(item, 'title').text = labels.get(event.get('category'), '') + ' · ' + event['text'][:120] if message else event_title(event)
        ET.SubElement(item, 'link').text = ORIGIN + ('/#post/' if message else '/#event/') + identity
        ET.SubElement(item, 'guid', isPermaLink='false').text = 'reset-radar:' + identity
        published = timestamp(event.get('created_at') if message else event.get('published_at'))
        if published:
            ET.SubElement(item, 'pubDate').text = format_datetime(datetime.fromtimestamp(published, timezone.utc))
        ET.SubElement(item, 'description').text = (event.get('text', event.get('summary', '')) + ' 来源：Tibo 公开原帖，经 RSS 镜像采集。' +
            SCOPE_LABELS.get(event.get('scope'), '适用范围未明确') + '。公告不代表个人额度到账。原帖：' + (event['url'] if message else ' '.join(event.get('source_urls', []))))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def calendar(data):
    event = data['summary']['upcoming']
    if not event:
        return None
    start = datetime.fromtimestamp(timestamp(event['target_at']), timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Reset Radar//CN', 'CALSCALE:GREGORIAN',
             'BEGIN:VEVENT', 'UID:' + event['event_id'] + '@reset-radar', 'DTSTAMP:' + stamp, 'DTSTART:' + start,
             'DURATION:PT5M', 'SUMMARY:Codex 重置预告：到站点核对进展',
             'DESCRIPTION:预计时间可能变化。此日历文件为单次快照，不会自动更新。请核对原帖与个人额度。',
             'URL:' + ORIGIN + '/#event/' + event['event_id'], 'BEGIN:VALARM', 'TRIGGER:-PT15M',
             'ACTION:DISPLAY', 'DESCRIPTION:15分钟后到预计时间，请查看最新公告', 'END:VALARM', 'END:VEVENT', 'END:VCALENDAR', '']
    # RFC 5545 line folding is byte-based; retain complete UTF-8 characters.
    folded = []
    for line in lines:
        chunk = ''
        for char in line:
            if len((chunk + char).encode()) > 73:
                folded.append(chunk)
                chunk = ' ' + char
            else:
                chunk += char
        folded.append(chunk)
    return '\r\n'.join(folded).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ResetRadar/1.0'
    sys_version = ''

    def do_HEAD(self):
        self.do_GET()

    def send(self, status, body, mime, disposition=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        self.send_header('Cache-Control', 'no-cache')
        if disposition:
            self.send_header('Content-Disposition', disposition)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/health':
            data = snapshot()
            body = json.dumps({'ok': True, 'ready': bool(data['collection']['posts']),
                'source_fresh': not data['summary']['stale'], 'events': len(data['events']),
                'posts': data['collection']['posts'], 'ai_enabled': data['ai']['enabled']}).encode()
            return self.send(200, body, 'application/json; charset=utf-8')
        if path == '/api/events':
            return self.send(200, json.dumps(snapshot(), ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        if path == '/api/posts':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                offset = max(0, int(query.get('offset', ['0'])[0]))
                limit = min(100, max(1, int(query.get('limit', ['20'])[0])))
            except ValueError:
                return self.send(400, b'Invalid pagination', 'text/plain')
            db = storage.connect(DB_FILE)
            try:
                data = storage.list_posts(db, query.get('q', [''])[0], offset, limit)
            finally:
                db.close()
            return self.send(200, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        if path == '/rss.xml':
            return self.send(200, rss(snapshot()), 'application/rss+xml; charset=utf-8')
        if path == '/reminder.ics':
            body = calendar(snapshot())
            if body:
                return self.send(200, body, 'text/calendar; charset=utf-8', 'attachment; filename="codex-reset.ics"')
            return self.send(409, '当前没有可订阅的有效预告，请等待新公告。'.encode(), 'text/plain; charset=utf-8')
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
        if path in files:
            name, mime = files[path]
            return self.send(200, (ROOT / 'public' / name).read_bytes(), mime + '; charset=utf-8')
        return self.send(404, b'Not found', 'text/plain; charset=utf-8')

    def log_message(self, fmt, *args):
        if urllib.parse.urlsplit(self.path).path != '/health':
            print(f'http {self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args)>1 else ""}', flush=True)


if __name__ == '__main__':
    initial_db = storage.connect(DB_FILE)
    collector.separate_archived_notes(initial_db)
    collector.repair_archived_previews(initial_db)
    revalidate_current(initial_db)
    recover_requests(initial_db)
    initial_db.close()
    if CACHE_FILE.exists():
        try:
            stored = json.loads(CACHE_FILE.read_text())
            if isinstance(stored.get('events'), list):
                cache.update(stored)
                cache['sync_ok'] = False
        except (ValueError, OSError):
            pass
    threading.Thread(target=sync_loop, daemon=True).start()
    threading.Thread(target=backfill_loop, daemon=True).start()
    threading.Thread(target=analysis_loop, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8787'))), Handler)
    server.timeout = 15
    print('Reset Radar is listening', flush=True)
    server.serve_forever()
