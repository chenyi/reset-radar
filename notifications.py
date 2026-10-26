"""Durable notification planning; channel credentials stay outside the database."""
import hashlib
import json
import notification_templates
import observability
from datetime import datetime, timedelta, timezone

SCHEMA = '''
CREATE TABLE IF NOT EXISTS notification_channels (
 channel_id TEXT PRIMARY KEY, kind TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_events (
 channel_id TEXT NOT NULL, event_id TEXT NOT NULL, revision TEXT NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY(channel_id,event_id)
);
CREATE TABLE IF NOT EXISTS notification_jobs (
 job_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, event_id TEXT NOT NULL, revision TEXT NOT NULL,
 kind TEXT NOT NULL, scheduled_at TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, sent_at TEXT
);
CREATE INDEX IF NOT EXISTS notification_due ON notification_jobs(status,scheduled_at);
'''


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def utc(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def initialize(db):
    with db:
        for sql in SCHEMA.split(';'):
            if sql.strip():
                db.execute(sql)


def register(db, channel_id, kind='dingtalk', at=None):
    at = at or datetime.now(timezone.utc)
    initialize(db)
    with db:
        db.execute('INSERT INTO notification_channels VALUES (?,?,?,?) ON CONFLICT(channel_id) DO NOTHING',
                   (channel_id, kind, 1, utc(at)))


def material(event):
    return {k: event.get(k) for k in ('event_id', 'state', 'expected_at', 'window_end', 'time_kind', 'scope')}


def queue(db, channel, event, revision, kind, due):
    title = {'forecast':'重置预告', 'reminder':'重置时间提醒', 'completed':'作者宣布重置完成',
             'correction':'重置时间更正', 'cancelled':'重置公告取消', 'verification':'预计时间已过，请核验额度'}[kind]
    deadline = event.get('window_end') or event.get('expected_at')
    label = '预计截止时间' if event.get('window_end') or event.get('time_kind') == 'deadline' else '预计时间'
    lines = [title]
    if deadline:
        local = stamp(deadline).astimezone(timezone(timedelta(hours=8)))
        lines.append(f'{label}：{local:%m/%d %H:%M}（北京时间）')
    if kind == 'reminder':
        lines.append('距公告预计时间或截止时间约 15 分钟，尚不代表已完成。')
    evidence = event.get('evidence') or []
    if evidence:
        original = evidence[-1]
        lines.extend([original.get('text', ''), original.get('url', '')])
    lines.append('公开公告不保证个人额度到账，请在自己的账号核对。')
    payload = {'title':title, 'text':'\n\n'.join(lines), 'kind':kind, 'event_id':event['event_id'],
               'target_at':deadline, 'time_label':label, 'post':evidence[-1] if evidence else {}}
    if kind == 'verification':
        payload['event_state'] = event['state']
    if kind in ('completed','cancelled'):
        payload['target_at'] = event.get('completed_announced_at') or (evidence[-1].get('created_at') if evidence else None) or event.get('published_at')
        payload['time_label'] = '完成公告时间' if kind=='completed' else '取消公告时间'
    payload['text'] = notification_templates.render(payload)
    job_id = hashlib.sha256(f'{channel}:{event["event_id"]}:{revision}:{kind}'.encode()).hexdigest()
    db.execute('''INSERT INTO notification_jobs
      (job_id,channel_id,event_id,revision,kind,scheduled_at,payload,status)
      VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload WHERE notification_jobs.status='pending'
      ''',
      (job_id, channel, event['event_id'], revision, kind, utc(due), json.dumps(payload,ensure_ascii=False), 'pending'))


def plan(db, snapshot, at=None):
    at = at or datetime.now(timezone.utc)
    with db:
        if hasattr(db, 'budget_lock'):
            db.budget_lock('notification-planner')
        for channel in db.execute('SELECT * FROM notification_channels WHERE enabled=1'):
            for event in snapshot['events']:
                current = material(event)
                revision = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
                old = db.execute('SELECT * FROM notification_events WHERE channel_id=? AND event_id=?',
                                 (channel['channel_id'], event['event_id'])).fetchone()
                previous = json.loads(old['body']) if old else None
                changed = previous is not None and revision != old['revision']
                deadline = event.get('window_end') or event.get('expected_at')
                state = event['state']
                # A new channel adopts history as a baseline; only future reminders may be queued.
                fresh_event = stamp(event.get('activity_at') or event['published_at']) >= stamp(channel['created_at'])
                if changed:
                    db.execute("UPDATE notification_jobs SET status='cancelled' WHERE channel_id=? AND event_id=? AND kind IN ('forecast','reminder','correction') AND status='pending'",
                               (channel['channel_id'], event['event_id']))
                if event.get('type') == 'reset':
                    kind = None
                    if state in ('completed','cancelled') and fresh_event and (not old or previous.get('state') != state):
                        kind = state
                    elif state in ('announced','scheduled'):
                        if fresh_event and (not old or previous.get('state') == 'unconfirmed'):
                            kind = 'forecast'
                        elif changed and fresh_event and (previous.get('expected_at'),previous.get('window_end')) != (current.get('expected_at'),current.get('window_end')):
                            kind = 'correction'
                    if kind:
                        queue(db, channel['channel_id'], event, revision, kind, at)
                    upcoming = snapshot.get('summary',{}).get('upcoming')
                    if deadline and upcoming and upcoming['event_id'] == event['event_id'] and not snapshot['summary']['stale']:
                        due = stamp(deadline) - timedelta(minutes=15)
                        if due > at:
                            queue(db, channel['channel_id'], event, revision, 'reminder', due)
                    if deadline:
                        previous_deadline = (previous or {}).get('window_end') or (previous or {}).get('expected_at')
                        if (state in ('announced','scheduled') and previous_deadline
                                and previous_deadline != deadline):
                            db.execute("UPDATE notification_jobs SET status='cancelled' WHERE channel_id=? AND event_id=? AND kind='verification' AND status='pending'",
                                       (channel['channel_id'],event['event_id']))
                        due = stamp(deadline) + timedelta(minutes=15)
                        if due > at:
                            check_revision = hashlib.sha256(f"{event['event_id']}:{deadline}".encode()).hexdigest()
                            queue(db, channel['channel_id'], event, check_revision, 'verification', due)
                db.execute('''INSERT INTO notification_events VALUES (?,?,?,?) ON CONFLICT(channel_id,event_id)
                  DO UPDATE SET revision=excluded.revision,body=excluded.body''',
                  (channel['channel_id'],event['event_id'],revision,json.dumps(current)))


def claim(db, snapshot, at=None):
    at = at or datetime.now(timezone.utc)
    with db:
        if hasattr(db, 'budget_lock'):
            db.budget_lock('notification-claim')
        jobs = db.execute("SELECT j.* FROM notification_jobs j JOIN notification_channels c USING(channel_id) WHERE j.status='pending' AND c.enabled=1 AND j.scheduled_at<=? ORDER BY j.scheduled_at", (utc(at),)).fetchall()
        for job in jobs:
            if job['kind'] in ('reminder','verification'):
                event = next((e for e in snapshot['events'] if e['event_id']==job['event_id']), None)
                if job['kind'] == 'reminder':
                    upcoming = snapshot.get('summary',{}).get('upcoming')
                    deadline = (upcoming or {}).get('target_at')
                    valid = (not snapshot['summary']['stale'] and upcoming and upcoming['event_id']==job['event_id']
                             and deadline and stamp(deadline) > at)
                    valid = valid and event and hashlib.sha256(json.dumps(material(event),sort_keys=True).encode()).hexdigest()==job['revision']
                else:
                    payload = json.loads(job['payload'])
                    deadline = payload.get('target_at')
                    current_deadline = (event or {}).get('window_end') or (event or {}).get('expected_at')
                    valid = deadline and stamp(deadline) + timedelta(minutes=15) <= at
                    if event and event['state'] in ('announced','scheduled') and current_deadline != deadline:
                        valid = False
                    if valid:
                        payload['event_state'] = (event or {}).get('state','unconfirmed')
                        payload['text'] = notification_templates.render(payload)
                        job = dict(job, payload=json.dumps(payload,ensure_ascii=False))
                        db.execute('UPDATE notification_jobs SET payload=? WHERE job_id=?',(job['payload'],job['job_id']))
                if not valid:
                    db.execute("UPDATE notification_jobs SET status='cancelled' WHERE job_id=?",(job['job_id'],))
                    continue
            db.execute("UPDATE notification_jobs SET status='sending',attempts=attempts+1 WHERE job_id=?",(job['job_id'],))
            return dict(job)
    return None


def recover(db):
    with db:
        db.execute("UPDATE notification_jobs SET status='unknown',last_error='Sender interrupted; delivery requires verification' WHERE status='sending'")


def deliver(db, job, sender):
    try:
        sender(json.loads(job['payload']))
    except TimeoutError as exc:
        observability.emit('notification.delivery_unknown', level='ERROR', error=exc, job_id=job['job_id'], channel=job['channel_id'])
        status, error = 'unknown', 'Request timed out; delivery requires verification'
    except Exception as exc:
        observability.emit('notification.delivery_failed', level='ERROR', error=exc, job_id=job['job_id'], channel=job['channel_id'])
        # Do not persist exception URLs, webhook tokens, or transport headers.
        status, error = 'failed', type(exc).__name__
    else:
        status, error = 'sent', None
    with db:
        db.execute('UPDATE notification_jobs SET status=?,last_error=?,sent_at=? WHERE job_id=?',
                   (status,error,utc(datetime.now(timezone.utc)) if status=='sent' else None,job['job_id']))
    return status
