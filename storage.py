import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS posts (
        post_id TEXT PRIMARY KEY, author TEXT NOT NULL, text TEXT NOT NULL,
        created_at TEXT NOT NULL, url TEXT NOT NULL, is_reply INTEGER NOT NULL,
        reply_to_id TEXT, conversation_id TEXT, quote_id TEXT,
        quoted_text TEXT, community_note TEXT, raw_payload TEXT NOT NULL, source_url TEXT NOT NULL,
        content_hash TEXT NOT NULL, first_fetched_at TEXT NOT NULL,
        last_fetched_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS posts_created ON posts(created_at);
      CREATE TABLE IF NOT EXISTS post_analyses (
        analysis_id INTEGER PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(post_id),
        input_hash TEXT NOT NULL, engine TEXT NOT NULL, model TEXT NOT NULL,
        prompt_version TEXT NOT NULL, input_snapshot TEXT NOT NULL,
        result_json TEXT NOT NULL, created_at TEXT NOT NULL,
        relevant INTEGER CHECK(relevant IN (0,1)),
        category TEXT CHECK(category IN ('forecast','progress','completed','credit','correction')),
        UNIQUE(post_id,input_hash,engine,model,prompt_version)
      );
      CREATE TABLE IF NOT EXISTS reset_events (
        event_id TEXT PRIMARY KEY, type TEXT NOT NULL, state TEXT NOT NULL,
        title TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, expected_at TEXT, window_start TEXT, window_end TEXT,
        effective_at TEXT, completed_announced_at TEXT, scope TEXT NOT NULL,
        time_quote TEXT, time_kind TEXT, original_timezone TEXT,
        source TEXT NOT NULL DEFAULT 'tibo-rss', confidence TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS event_posts (
        event_id TEXT NOT NULL REFERENCES reset_events(event_id),
        post_id TEXT NOT NULL REFERENCES posts(post_id),
        analysis_id INTEGER NOT NULL REFERENCES post_analyses(analysis_id),
        role TEXT NOT NULL, evidence TEXT NOT NULL,
        PRIMARY KEY(event_id,post_id,analysis_id)
      );
      CREATE TABLE IF NOT EXISTS collection_jobs (
        job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, requested_start TEXT NOT NULL,
        requested_end TEXT NOT NULL, cursor TEXT, status TEXT NOT NULL,
        pages INTEGER NOT NULL DEFAULT 0, items INTEGER NOT NULL DEFAULT 0,
        oldest_at TEXT, newest_at TEXT, last_success_at TEXT, error TEXT,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS sync_state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS ai_requests (
        request_key TEXT PRIMARY KEY, month TEXT NOT NULL, status TEXT NOT NULL,
        reserved_cny REAL NOT NULL, actual_cny REAL,
        input_tokens INTEGER, output_tokens INTEGER, provider_request_id TEXT,
        created_at TEXT NOT NULL, response_json TEXT
      );
    ''')
    if db.execute('PRAGMA user_version').fetchone()[0] < 3:
        db.execute('BEGIN IMMEDIATE')
        try:
            columns = {x[1] for x in db.execute('PRAGMA table_info(posts)')}
            if 'community_note' not in columns:
                db.execute('ALTER TABLE posts ADD COLUMN community_note TEXT')
            columns = {x[1] for x in db.execute('PRAGMA table_info(post_analyses)')}
            if 'relevant' not in columns:
                db.execute('ALTER TABLE post_analyses ADD COLUMN relevant INTEGER CHECK(relevant IN (0,1))')
            if 'category' not in columns:
                db.execute("ALTER TABLE post_analyses ADD COLUMN category TEXT CHECK(category IN ('forecast','progress','completed','credit','correction'))")
            columns = {x[1] for x in db.execute('PRAGMA table_info(ai_requests)')}
            if 'response_json' not in columns:
                db.execute('ALTER TABLE ai_requests ADD COLUMN response_json TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS analyses_classification ON post_analyses(engine,relevant,category)')
            db.execute('PRAGMA user_version=3')
            db.commit()
        except Exception:
            db.rollback()
            raise
    return db


def set_state(db, key, value):
    db.execute('INSERT INTO sync_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, json.dumps(value, ensure_ascii=False)))


def get_state(db, key, default=None):
    row = db.execute('SELECT value FROM sync_state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def save_posts(db, posts):
    inserted = 0
    for post in posts:
        old = db.execute('SELECT content_hash FROM posts WHERE post_id=?', (post['post_id'],)).fetchone()
        inserted += old is None
        fields = list(post)
        db.execute('INSERT INTO posts (' + ','.join(fields) + ') VALUES (' + ','.join('?' for _ in fields) +
                   ') ON CONFLICT(post_id) DO UPDATE SET ' + ','.join(
                       f'{k}=excluded.{k}' for k in fields if k not in ('post_id', 'first_fetched_at')),
                   list(post.values()))
    return inserted


def snapshot(db, version=None):
    events = []
    for row in db.execute("SELECT * FROM reset_events WHERE confidence IN ('ai-direct','ai-context') ORDER BY published_at DESC"):
        event = dict(row)
        evidence = [dict(x) for x in db.execute('''
          SELECT p.post_id,p.text,p.created_at,p.url,p.is_reply,p.reply_to_id,
                 p.quoted_text,p.community_note,p.source_url,e.role,e.evidence,a.engine,a.model,a.prompt_version
          FROM event_posts e JOIN posts p USING(post_id)
          JOIN post_analyses a USING(analysis_id) WHERE e.event_id=? ORDER BY p.created_at,a.created_at
        ''', (event['event_id'],))]
        evidence = list({x['post_id']: x for x in evidence}.values())
        event.update(root_id=event['event_id'], source_urls=[x['url'] for x in evidence],
                     evidence=evidence, scope_verified=False)
        events.append(event)
    messages = []
    for row in db.execute('''SELECT p.*,a.analysis_id,a.category,a.result_json,a.created_at AS analyzed_at
      FROM posts p JOIN post_analyses a USING(post_id)
      WHERE a.engine='qwen' AND a.relevant=1 AND a.input_hash=p.content_hash
      AND (? IS NULL OR a.prompt_version=?)
      AND a.analysis_id=(SELECT max(b.analysis_id) FROM post_analyses b
        WHERE b.post_id=p.post_id AND b.engine='qwen' AND b.input_hash=p.content_hash)
      ORDER BY p.created_at DESC''', (version,version)):
        message = {k: row[k] for k in ('post_id','text','url','created_at','is_reply','reply_to_id',
                                     'quoted_text','community_note','category','analyzed_at')}
        result = json.loads(row['result_json'])
        link = db.execute('SELECT event_id FROM event_posts WHERE analysis_id=? AND post_id=? LIMIT 1',
                          (row['analysis_id'], row['post_id'])).fetchone()
        message.update(event_id=link[0] if link else None, reason=result.get('reason', ''),
                       state=result.get('state'), confidence=result.get('confidence'))
        messages.append(message)
    if version:
        eligible = {m['event_id'] for m in messages}
        events = [e for e in events if e['event_id'] in eligible]
    posts = db.execute('SELECT count(*),min(created_at),max(created_at) FROM posts').fetchone()
    jobs = [dict(x) for x in db.execute("SELECT * FROM collection_jobs WHERE kind='backfill' ORDER BY requested_start")]
    analyzed = db.execute("SELECT count(DISTINCT p.post_id) FROM posts p JOIN post_analyses a USING(post_id) WHERE a.engine='qwen' AND a.input_hash=p.content_hash AND (? IS NULL OR a.prompt_version=?)",(version,version)).fetchone()[0]
    return {'events': events, 'messages': messages, 'analysis': {'reviewed': analyzed, 'total': posts[0]},
            'collection': {'posts': posts[0], 'oldest_post': posts[1],
            'newest_post': posts[2], 'windows': jobs,
            'coverage_note': '仅表示公开 RSS 返回的记录；不保证覆盖 X 的全部原帖或回复。'},
            'last_sync': get_state(db, 'last_sync'), 'source_checked_at': get_state(db, 'last_sync'),
            'sync_ok': get_state(db, 'sync_ok', False), 'source_healthy': get_state(db, 'sync_ok', False),
            'ai': get_state(db, 'ai_status', {'enabled': False, 'reason': '等待代金券抵扣验证'})}


def list_posts(db, query='', offset=0, limit=20):
    pattern = '%' + query[:200] + '%'
    where = 'text LIKE ? OR created_at LIKE ? OR post_id LIKE ?'
    count = db.execute('SELECT count(*) FROM posts WHERE ' + where, (pattern,) * 3).fetchone()[0]
    rows = db.execute('''SELECT post_id,author,text,created_at,url,is_reply,reply_to_id,quoted_text,community_note,
      source_url,last_fetched_at FROM posts WHERE ''' + where + ' ORDER BY created_at DESC LIMIT ? OFFSET ?',
      (pattern, pattern, pattern, limit, offset)).fetchall()
    return {'posts': [dict(x) for x in rows], 'total': count, 'offset': offset,
            'next_offset': offset + len(rows) if offset + len(rows) < count else None}
