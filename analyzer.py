"""Archive candidate signals without treating keyword matches as confirmed events."""
import json
import re

from storage import now

RULE_VERSION = 'candidate-v1'


def candidate(text):
    return bool(re.search(r'\breset(?:s|ting)?\b|\b(?:rate|usage) limits?\b|\breset credits?\b', text, re.I))


def classify_candidates(db):
    for row in db.execute('''SELECT p.* FROM posts p WHERE NOT EXISTS
      (SELECT 1 FROM post_analyses a WHERE a.post_id=p.post_id AND a.input_hash=p.content_hash
       AND a.engine='rules' AND a.prompt_version=?) ORDER BY created_at''', (RULE_VERSION,)).fetchall():
        post = dict(row)
        relevant = candidate(post['text'])
        result = {'relevant': relevant, 'state': 'unconfirmed', 'evidence': post['text'] if relevant else ''}
        with db:
            cursor = db.execute('''INSERT INTO post_analyses
              (post_id,input_hash,engine,model,prompt_version,input_snapshot,result_json,created_at)
              VALUES (?,?,?,?,?,?,?,?)''', (post['post_id'], post['content_hash'], 'rules', 'none',
                RULE_VERSION, json.dumps(post, ensure_ascii=False), json.dumps(result), now()))
            if not relevant:
                continue
            event_id = 'tibo-' + post['post_id']
            kind = 'credit' if re.search(r'\breset (?:card|credit)', post['text'], re.I) else 'reset'
            db.execute('''INSERT OR IGNORE INTO reset_events
              (event_id,type,state,title,summary,published_at,updated_at,scope,confidence)
              VALUES (?,?,?,?,?,?,?,?,?)''', (event_id, kind, 'unconfirmed', post['text'][:180],
                '原帖包含额度相关表述，等待结合上下文分析；暂不认定为重置预告或完成。',
                post['created_at'], now(), 'unknown', 'candidate'))
            db.execute('INSERT OR IGNORE INTO event_posts VALUES (?,?,?,?,?)',
                       (event_id, post['post_id'], cursor.lastrowid, 'candidate', post['text']))
