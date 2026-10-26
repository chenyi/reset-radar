"""Replay Day21's misclassified original and its notification lifecycle."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import ai_analysis as a
import notifications as n
import storage


class UndatedForecastTest(unittest.TestCase):
    def setUp(self):
        self.post={'post_id':'2114588174249071102','author':'thsottiaux',
                   'text':'Day 21/ Wonder what happens the remaining seven days. But today is a reset.',
                   'created_at':'2026-10-26T05:21:22Z','is_reply':0,'quote_id':None}
        self.posts={self.post['post_id']:self.post}

    def entry(self, state='completed'):
        return {'post_id':self.post['post_id'],'relevant':True,'category':'completed' if state=='completed' else 'progress',
                'anchor_id':self.post['post_id'],'type':'reset','state':state,'scope':'unknown','confidence':'contextual',
                'evidence':[{'post_id':self.post['post_id'],'role':'update'}],'time':None}

    def test_clear_announcement_is_forecast_even_if_model_says_completed_unconfirmed_or_irrelevant(self):
        for entry in [self.entry(),self.entry('unconfirmed'),{'post_id':self.post['post_id'],'relevant':False}]:
            with self.subTest(entry=entry):
                result=a.validate(entry,self.posts,{self.post['post_id']})
                self.assertTrue(result['relevant'])
                self.assertEqual((result['category'],result['state']),('forecast','announced'))
                self.assertIsNone(result['expected_at'])
                self.assertEqual(result['evidence'][0]['role'],'forecast')

    def test_request_question_negation_and_card_do_not_become_new_reset_announcements(self):
        for text in ['I hope today is a reset.','Today is a reset?','Today is not a reset.',
                     'I demand a reset.','We are handing out reset cards today.',
                     'Today is a reset. Cancelled.']:
            with self.subTest(text=text):
                post=dict(self.post,text=text)
                result=a.validate({'post_id':post['post_id'],'relevant':False},{post['post_id']:post},{post['post_id']})
                self.assertFalse(result['relevant'])

    def test_explicit_completion_is_not_downgraded_to_forecast(self):
        self.post['text']="Today is a reset. We've reset all limits."
        result=a.validate(self.entry(),self.posts,{self.post['post_id']})
        self.assertEqual((result['category'],result['state']),('completed','completed'))

    def test_unconfirmed_baseline_auto_forecasts_then_completes_and_retains_clock_check(self):
        import sqlite3
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        at=datetime(2026,10,26,6,tzinfo=timezone.utc)
        try:
            n.register(db,'test',at=at-timedelta(days=3))
            e={'event_id':'tibo-'+self.post['post_id'],'type':'reset','state':'unconfirmed',
               'published_at':self.post['created_at'],'expected_at':None,'evidence':[self.post]}
            def snap(event):
                return {'events':[event],'summary':{'stale':False,'latest':event,
                        'upcoming':dict(event,target_at=event['expected_at']) if event['state']=='scheduled' else None}}
            n.plan(db,snap(e),at)
            self.assertIsNone(n.claim(db,snap(e),at))
            fixed=dict(e,state='announced')
            sent=[]
            for _ in range(2):n.plan(db,snap(fixed),at)
            job=n.claim(db,snap(fixed),at)
            self.assertIsNotNone(job)
            self.assertEqual(n.deliver(db,job,sent.append),'sent')
            self.assertEqual([p['kind'] for p in sent],['forecast'])
            self.assertIsNone(n.claim(db,snap(fixed),at))
            timed=dict(fixed,state='scheduled',expected_at=n.utc(at+timedelta(hours=1)),time_kind='deadline')
            n.plan(db,snap(timed),at+timedelta(minutes=1))
            job=n.claim(db,snap(timed),at+timedelta(minutes=1))
            self.assertEqual(job['kind'],'correction')
            n.deliver(db,job,sent.append)
            changed=dict(timed,expected_at=n.utc(at+timedelta(hours=2)))
            n.plan(db,snap(changed),at+timedelta(minutes=2))
            n.deliver(db,n.claim(db,snap(changed),at+timedelta(minutes=2)),sent.append)
            n.deliver(db,n.claim(db,snap(changed),at+timedelta(minutes=105)),sent.append)
            self.assertEqual(sent[-1]['kind'],'reminder')
            done={'post_id':'done','author':'thsottiaux','text':'It is all propagated. Enjoy some mad codexing tomorrow.',
                  'created_at':'2026-10-26T07:50:00Z','is_reply':0,'quote_id':self.post['post_id']}
            result=a.validate({'post_id':'done','relevant':False},{**self.posts,'done':done},{'done'})
            self.assertEqual((result['state'],result['anchor_id']),('completed',self.post['post_id']))
            final=dict(changed,state=result['state'],activity_at=done['created_at'],
                       completed_announced_at=done['created_at'],evidence=[self.post,done])
            for _ in range(2):n.plan(db,snap(final),at+timedelta(minutes=110))
            n.deliver(db,n.claim(db,snap(final),at+timedelta(minutes=110)),sent.append)
            n.recover(db)
            n.plan(db,snap(final),at+timedelta(minutes=135))
            n.deliver(db,n.claim(db,snap(final),at+timedelta(minutes=135)),sent.append)
            self.assertEqual([p['kind'] for p in sent],
                             ['forecast','correction','correction','reminder','completed','verification'])
            self.assertEqual(sent[-1]['event_state'],'completed')
            self.assertIsNone(n.claim(db,snap(final),at+timedelta(minutes=136)))
        finally:db.close()

    def test_stored_misclassification_revalidates_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            db=storage.connect(Path(directory)/'replay.db')
            try:
                post=dict(self.post,url='https://x.com/thsottiaux/status/'+self.post['post_id'],raw_payload='<item/>',
                          source_url='https://example.test/rss',content_hash='day21',
                          first_fetched_at='2026-10-26T05:59:17Z',last_fetched_at='2026-10-26T05:59:17Z')
                with db:storage.save_posts(db,[post])
                entry=self.entry('unconfirmed');entry['validation_version']=6
                body={'messages':[{'role':'system','content':'fixture'},
                                  {'role':'user','content':json.dumps({'context':[self.post]})}]}
                with db:
                    db.execute("""INSERT INTO post_analyses
                      (post_id,input_hash,engine,model,prompt_version,input_snapshot,result_json,created_at,relevant,category)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",(post['post_id'],'day21','qwen',a.MODEL,a.VERSION,
                      json.dumps(body),json.dumps(entry),'2026-10-26T05:59:36Z',1,'progress'))
                with patch('ai_analysis.urllib.request.urlopen') as request:
                    a.revalidate_current(db);a.revalidate_current(db);request.assert_not_called()
                row=db.execute('SELECT state,expected_at,completed_announced_at FROM reset_events WHERE event_id=?',
                               ('tibo-'+post['post_id'],)).fetchone()
                self.assertEqual(row['state'],'announced')
                self.assertIsNone(row['expected_at']);self.assertIsNone(row['completed_announced_at'])
                self.assertEqual(db.execute('SELECT count(*) FROM ai_requests').fetchone()[0],0)
            finally:db.close()
