"""Replay the October 25 public announcement chain without external calls."""
import copy
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import storage
import unittest
from datetime import datetime, timezone

import ai_analysis as a
import notifications as n
from notification_templates import render


class NotificationRegressionTest(unittest.TestCase):
    def setUp(self):
        self.posts = {
            'root': {'post_id':'root','author':'thsottiaux','text':'Day 20/ Rest and reset',
                     'created_at':'2026-10-25T03:39:31Z','is_reply':0,'quote_id':None},
            'time': {'post_id':'time','author':'thsottiaux','text':'By midnight PST',
                     'created_at':'2026-10-25T03:40:04Z','is_reply':1,'quote_id':None,
                     'event_anchor_id':'root'},
            'done': {'post_id':'done','author':'thsottiaux',
                     'text':'It is all propagated. Enjoy some mad codexing tomorrow. Fun week ahead too.',
                     'created_at':'2026-10-25T06:06:16Z','is_reply':0,'quote_id':'time'},
        }

    def test_quoted_propagation_overrides_excluded_model_result(self):
        result=a.validate({'post_id':'done','relevant':False,'reason':'No reset keyword'},self.posts,{'done'})
        self.assertEqual((result['relevant'],result.get('category'),result.get('state'),result.get('anchor_id')),
                         (True,'completed','completed','root'))
        self.assertEqual({e['post_id'] for e in result['evidence']},{'root','time','done'})
        self.posts['root']['created_at']='2026-10-20T03:39:31Z'
        self.posts['done'].update(text='Done',quote_id='root')
        result=a.validate({'post_id':'done','relevant':False},self.posts,{'done'})
        self.assertEqual((result['state'],result['anchor_id']),('completed','root'))

    def test_unlinked_cross_author_future_and_cyclic_quotes_do_not_prove_completion(self):
        for change in ('unlinked','cross-author','future','cycle'):
            with self.subTest(change=change):
                posts=copy.deepcopy(self.posts)
                if change=='unlinked': posts['done']['quote_id']=None
                if change=='cross-author': posts['time']['author']='another'
                if change=='future': posts['done']['text']='It will all be propagated tomorrow.'
                if change=='cycle': posts['time']['event_anchor_id']='done'
                result=a.validate({'post_id':'done','relevant':False},posts,{'done'})
                self.assertFalse(result['relevant'])

    def test_midnight_respects_original_local_date_and_timezone(self):
        for zone,expected in [('PST','2026-10-25T08:00:00Z'),('PT','2026-10-25T07:00:00Z'),
                              ('PDT','2026-10-25T07:00:00Z')]:
            with self.subTest(zone=zone):
                self.posts['time']['text']='By midnight '+zone
                self.assertEqual(a.time_from_evidence({'phrase':self.posts['time']['text'],'date_ref_id':'time'},self.posts),expected)

    def test_original_fallback_works_for_every_style_without_duplicate_text(self):
        post=self.posts['root']
        for style in ('clear','brief','bilingual'):
            with self.subTest(style=style):
                text=render({'kind':'forecast','event_id':'root','target_at':None,'post':post},style)
                self.assertEqual(text.count(post['text']),1)
                self.assertIn('原文',text)

    def test_real_sequence_sends_forecast_and_completion_cancels_reminder(self):
        db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
        try:
            n.register(db,'test',at=datetime(2026,10,23,13,10,53,tzinfo=timezone.utc))
            forecast={'event_id':'tibo-root','type':'reset','state':'scheduled',
                      'expected_at':'2026-10-25T08:00:00Z','time_kind':'deadline',
                      'published_at':self.posts['root']['created_at'],'evidence':[self.posts['root'],self.posts['time']]}
            snapshot={'events':[forecast],'summary':{'stale':False,'upcoming':dict(forecast,target_at=forecast['expected_at'])}}
            at=datetime(2026,10,25,4,14,43,tzinfo=timezone.utc)
            n.plan(db,snapshot,at)
            sent=[]
            self.assertEqual(n.deliver(db,n.claim(db,snapshot,at),sent.append),'sent')
            result=a.validate({'post_id':'done','relevant':False},self.posts,{'done'})
            self.assertTrue(result['relevant'])
            completed=dict(forecast,state=result['state'],activity_at=self.posts['done']['created_at'],
                           completed_announced_at=self.posts['done']['created_at'],
                           evidence=[self.posts['root'],self.posts['time'],self.posts['done']])
            final={'events':[completed],'summary':{'stale':False,'upcoming':None}}
            at=datetime(2026,10,25,6,15,12,tzinfo=timezone.utc)
            for _ in range(2): n.plan(db,final,at)
            self.assertEqual(n.deliver(db,n.claim(db,final,at),sent.append),'sent')
            n.recover(db)
            n.plan(db,final,at)
            self.assertIsNone(n.claim(db,final,datetime(2026,10,25,7,45,tzinfo=timezone.utc)))
            self.assertEqual([p['kind'] for p in sent],['forecast','completed'])
            self.assertEqual(db.execute("SELECT status FROM notification_jobs WHERE kind='reminder'").fetchone()[0],'cancelled')
        finally:
            db.close()


class StoredEvidenceReplayTest(unittest.TestCase):
    def test_revalidate_saved_analyses_repairs_same_event_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            db=storage.connect(Path(directory)/'replay.db')
            try:
                posts=[
                    {'post_id':'root','text':'Day 20/ Rest and reset','created_at':'2026-10-25T03:39:31Z','is_reply':0},
                    {'post_id':'time','text':'By midnight PST','created_at':'2026-10-25T03:40:04Z','is_reply':1},
                    {'post_id':'done','text':'It is all propagated. Enjoy some mad codexing tomorrow. Fun week ahead too.',
                     'created_at':'2026-10-25T06:06:16Z','is_reply':0,'quote_id':'time'},
                ]
                values=[dict(author='thsottiaux',url='https://x.com/thsottiaux/status/'+p['post_id'],
                             raw_payload='<item/>',source_url='https://example.test/rss',content_hash=p['post_id'],
                             first_fetched_at=p['created_at'],last_fetched_at=p['created_at'],**p) for p in posts]
                with db: storage.save_posts(db,values)
                body={'messages':[{'role':'system','content':'fixture'},
                                  {'role':'user','content':json.dumps({'context':posts})}]}
                root={'post_id':'root','relevant':True,'category':'forecast','type':'reset','state':'announced',
                      'anchor_id':'root','scope':'unknown','confidence':'contextual','validation_version':5,
                      'evidence':[{'post_id':'root','role':'forecast'}]}
                timing=dict(root,post_id='time',category='progress',state='scheduled',expected_at=None,
                            evidence=[{'post_id':'time','role':'update'},{'post_id':'root','role':'forecast'}],
                            time={'phrase':'By midnight PST','date_ref_id':'time'})
                done={'post_id':'done','relevant':False,'reason':'previous validation rejected it','validation_version':5}
                for entry in [root,timing,done]:
                    with db:
                        db.execute("""INSERT INTO post_analyses
                          (post_id,input_hash,engine,model,prompt_version,input_snapshot,result_json,created_at,relevant,category)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (entry['post_id'],entry['post_id'],'qwen',a.MODEL,a.VERSION,json.dumps(body),
                           json.dumps(entry),'2026-10-25T06:15:12Z',int(entry['relevant']),entry.get('category')))
                with patch('ai_analysis.urllib.request.urlopen') as request:
                    a.revalidate_current(db)
                    a.revalidate_current(db)
                    request.assert_not_called()
                events=[dict(r) for r in db.execute("SELECT * FROM reset_events WHERE confidence IN ('ai-direct','ai-context')")]
                self.assertEqual(len(events),1)
                self.assertEqual((events[0]['event_id'],events[0]['state'],events[0]['completed_announced_at']),
                                 ('tibo-root','completed','2026-10-25T06:06:16Z'))
                timing=json.loads(db.execute("SELECT result_json FROM post_analyses WHERE post_id='time'").fetchone()[0])
                self.assertEqual(timing['expected_at'],'2026-10-25T08:00:00Z')
                self.assertEqual(db.execute("SELECT count(*) FROM ai_requests").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT relevant FROM post_analyses WHERE post_id='done'").fetchone()[0],1)
                with db:
                    db.execute('DELETE FROM event_posts')
                    db.execute('DELETE FROM reset_events')
                    db.execute('DELETE FROM post_analyses')
                response={'id':'fixture-response','choices':[{'message':{'content':json.dumps({'analyses':[done,timing,root]})}}],
                          'usage':{'prompt_tokens':100,'completion_tokens':30}}
                from unittest.mock import MagicMock
                reply=MagicMock();reply.__enter__.return_value.read.return_value=json.dumps(response).encode()
                with patch.dict(os.environ,{'AI_ENABLED':'1','AI_BASE_URL':'https://ws-test.ap-southeast-1.maas.aliyuncs.com',
                                            'DASHSCOPE_API_KEY':'test-only'}), patch('billing_guard.verify'), \
                        patch('ai_analysis.urllib.request.urlopen',return_value=reply) as request:
                    self.assertTrue(a.analyze_batch(db))
                    self.assertEqual(request.call_count,1)
                events=list(db.execute("SELECT event_id,state FROM reset_events WHERE confidence IN ('ai-direct','ai-context')"))
                self.assertEqual([(e['event_id'],e['state']) for e in events],[('tibo-root','completed')])
            finally:
                db.close()
