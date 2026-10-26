"""Replay the October 25 public announcement chain without external calls."""
import copy
import json
import sqlite3
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
