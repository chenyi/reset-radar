import argparse
import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from storage import connect, get_state, now, save_posts, set_state
from analyzer import classify_candidates

SOURCE = 'https://nitter.kareem.one'
AUTHOR = 'thsottiaux'
MAX_FEED_BYTES = 2_000_000
fetch_lock = threading.Lock()
last_fetch = 0


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.quote_depth = 0
        self.bold = False
        self.in_note = False
        self.body, self.quote, self.note = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('b', 'strong'):
            self.bold = True
        if tag == 'blockquote':
            self.quote_depth += 1
        if tag in ('p', 'br'):
            (self.note if self.in_note else self.quote if self.quote_depth else self.body).append('\n')

    def handle_endtag(self, tag):
        if tag in ('b', 'strong'):
            self.bold = False
        if tag == 'blockquote':
            self.quote_depth = max(0, self.quote_depth - 1)

    def handle_data(self, data):
        if self.bold and data.strip() == 'Community note:':
            self.in_note = True
        (self.note if self.in_note else self.quote if self.quote_depth else self.body).append(data)


def parse_feed(raw, source_url):
    root = ET.fromstring(raw)
    channel = root.find('channel')
    if root.tag != 'rss' or channel is None:
        raise ValueError('Source did not return an RSS feed')
    posts = []
    for item in channel.findall('item'):
        creator = item.findtext('{http://purl.org/dc/elements/1.1/}creator', '').lstrip('@').lower()
        title = item.findtext('title', '')
        if creator != AUTHOR or title.startswith('RT by '):
            continue
        link = item.findtext('link', '')
        match = re.search(r'/thsottiaux/status/(\d+)', link, re.I)
        if not match:
            continue
        parser = TextParser()
        parser.feed(item.findtext('description', ''))
        text = ''.join(parser.body).strip()
        quoted = ''.join(parser.quote).strip()
        if not text:
            continue
        created = parsedate_to_datetime(item.findtext('pubDate')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        fetched = now()
        post = {'post_id': match[1], 'author': AUTHOR, 'text': text,
                'created_at': created, 'url': f'https://x.com/{AUTHOR}/status/{match[1]}',
                'is_reply': int(title.startswith('R to @')), 'reply_to_id': None,
                'conversation_id': None, 'quote_id': None, 'quoted_text': quoted or None,
                'community_note': ''.join(parser.note).strip() or None,
                'raw_payload': ET.tostring(item, encoding='unicode'), 'source_url': source_url,
                'content_hash': hashlib.sha256((text + '\n' + quoted).encode()).hexdigest(),
                'first_fetched_at': fetched, 'last_fetched_at': fetched}
        posts.append(post)
    return posts, max(2400, int(channel.findtext('ttl', '40')) * 60), len(channel.findall('item'))


def separate_archived_notes(db):
    rows = db.execute("SELECT * FROM posts WHERE text LIKE '%Community note:%' AND community_note IS NULL").fetchall()
    with db:
        for row in rows:
            raw = ('<rss><channel><ttl>40</ttl>' + row['raw_payload'] + '</channel></rss>').encode()
            parsed = parse_feed(raw, row['source_url'])[0]
            for post in parsed:
                post['first_fetched_at'] = row['first_fetched_at']
                post['last_fetched_at'] = row['last_fetched_at']
            save_posts(db, parsed)


def fetch(url):
    global last_fetch
    with fetch_lock:
        time.sleep(max(0, 3 - (time.monotonic() - last_fetch)))
        last_fetch = time.monotonic()
    request = urllib.request.Request(url, headers={'User-Agent': 'ResetRadar/1.1 (public announcement archive)',
                                                 'Accept': 'application/rss+xml, application/xml'})
    with urllib.request.urlopen(request, timeout=35) as response:
        raw = response.read(MAX_FEED_BYTES + 1)
        if len(raw) > MAX_FEED_BYTES:
            raise ValueError('Oversized RSS response')
        posts, ttl, item_count = parse_feed(raw, url)
        return posts, response.headers.get('Min-Id'), ttl, item_count


def collect_page(db, job):
    if job['kind'] == 'backfill':
        query = f'from:{AUTHOR} since:{job["requested_start"]} until:{job["requested_end"]}'
        url = SOURCE + '/search/rss?' + urllib.parse.urlencode({'f': 'tweets', 'q': query})
    else:
        url = SOURCE + '/' + AUTHOR + '/with_replies/rss'
    if job['cursor']:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode({'cursor': job['cursor']})
    try:
        posts, cursor, ttl, item_count = fetch(url)
        if job['kind'] == 'backfill':
            posts = [p for p in posts if job['requested_start'] <= p['created_at'][:10] < job['requested_end']]
        with db:
            inserted = save_posts(db, posts)
            dates = [p['created_at'] for p in posts]
            oldest = min(dates + ([job['oldest_at']] if job['oldest_at'] else []), default=None)
            newest = max(dates + ([job['newest_at']] if job['newest_at'] else []), default=None)
            status = 'running'
            if not cursor or not item_count:
                status = 'source_exhausted'
            elif cursor == job['cursor']:
                status = 'partial'
            elif job['kind'] == 'incremental' and inserted == 0:
                status = 'overlap_reached'
            db.execute('''UPDATE collection_jobs SET cursor=?,status=?,pages=pages+1,
              items=items+?,oldest_at=?,newest_at=?,last_success_at=?,error=NULL,updated_at=? WHERE job_id=?''',
                       (cursor, status, len(posts), oldest, newest, now(), now(), job['job_id']))
            if job['kind'] == 'incremental':
                set_state(db, 'last_sync', now())
                set_state(db, 'sync_ok', True)
            set_state(db, 'source_ttl', ttl)
        print(f'{job["job_id"]}: {len(posts)} posts, {inserted} new, {status}', flush=True)
        classify_candidates(db)
        return status
    except Exception as exc:
        with db:
            db.execute('UPDATE collection_jobs SET status=?,error=?,updated_at=? WHERE job_id=?',
                       ('partial', type(exc).__name__, now(), job['job_id']))
            if job['kind'] == 'incremental':
                set_state(db, 'sync_ok', False)
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
            time.sleep(min(60, max(10, int(exc.headers.get('Retry-After', '60')))))
        raise


def incremental(db):
    job_id = 'incremental-' + now()
    with db:
        db.execute('''INSERT INTO collection_jobs(job_id,kind,requested_start,requested_end,status,updated_at)
                      VALUES (?,?,?,?,?,?)''', (job_id, 'incremental', now()[:10], now(), 'pending', now()))
    for _ in range(30):
        job = dict(db.execute('SELECT * FROM collection_jobs WHERE job_id=?', (job_id,)).fetchone())
        if collect_page(db, job) != 'running':
            return
        time.sleep(3)
    with db:
        db.execute('UPDATE collection_jobs SET status=? WHERE job_id=?', ('partial', job_id))


def backfill(db, start, end):
    left = datetime.fromisoformat(start).date()
    until = datetime.fromisoformat(end).date()
    with db:
        while left < until:
            right = min(left + timedelta(days=7), until)
            job_id = f'backfill-{left}-{right}'
            db.execute('''INSERT OR IGNORE INTO collection_jobs
              (job_id,kind,requested_start,requested_end,status,updated_at) VALUES (?,?,?,?,?,?)''',
                       (job_id, 'backfill', str(left), str(right), 'pending', now()))
            left = right
    jobs = db.execute("SELECT job_id FROM collection_jobs WHERE kind='backfill' AND status!='source_exhausted' ORDER BY requested_start DESC").fetchall()
    for item in jobs:
        for _ in range(100):
            job = dict(db.execute('SELECT * FROM collection_jobs WHERE job_id=?', (item[0],)).fetchone())
            status = collect_page(db, job)
            time.sleep(3)
            if status != 'running':
                break
        else:
            with db:
                db.execute('UPDATE collection_jobs SET status=? WHERE job_id=?', ('partial', item[0]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--start')
    parser.add_argument('--end')
    args = parser.parse_args()
    db = connect(args.db)
    if args.start:
        backfill(db, args.start, args.end)
    else:
        incremental(db)
