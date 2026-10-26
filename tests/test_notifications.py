import copy
import contextlib
import io
import os
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest

import notifications as n


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.db.row_factory=sqlite3.Row
        self.at=datetime(2026,10,23,12,tzinfo=timezone.utc)
        n.register(self.db,'test',at=self.at)
        self.event={'event_id':'reset-1','type':'reset','state':'scheduled','expected_at':n.utc(self.at+timedelta(hours=1)),
                    'published_at':n.utc(self.at+timedelta(seconds=1)),'time_kind':'deadline',
                    'evidence':[{'text':'A reset is coming','url':'https://x.com/thsottiaux/status/1'}]}

    def tearDown(self):
        self.db.close()

    def snapshot(self, event=None, stale=False):
        e=event or self.event
        upcoming=dict(e,target_at=e['expected_at']) if e['state']=='scheduled' else None
        return {'events':[e],'summary':{'stale':stale,'upcoming':upcoming,'latest':e}}

    def jobs(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM notification_jobs')]

    def test_new_event_and_repeat_are_idempotent(self):
        n.plan(self.db,self.snapshot(),self.at+timedelta(seconds=2))
        n.plan(self.db,self.snapshot(),self.at+timedelta(seconds=3))
        self.assertEqual(sorted(j['kind'] for j in self.jobs()),['forecast','reminder','verification'])

    def test_history_is_baseline_but_future_reminder_allowed(self):
        self.event['published_at']=n.utc(self.at-timedelta(days=1))
        n.plan(self.db,self.snapshot(),self.at)
        self.assertEqual([j['kind'] for j in self.jobs()],['reminder','verification'])

    def test_changed_time_cancels_old_reminder(self):
        n.plan(self.db,self.snapshot(),self.at)
        changed=copy.deepcopy(self.event);changed['expected_at']=n.utc(self.at+timedelta(hours=2))
        n.plan(self.db,self.snapshot(changed),self.at+timedelta(minutes=5))
        reminders=[j for j in self.jobs() if j['kind']=='reminder']
        self.assertEqual(sorted(j['status'] for j in reminders),['cancelled','pending'])
        self.assertEqual(len([j for j in self.jobs() if j['kind']=='correction']),1)

    def test_completion_cancels_reminder_and_sends_once(self):
        n.plan(self.db,self.snapshot(),self.at)
        e=dict(self.event,state='completed')
        n.plan(self.db,self.snapshot(e),self.at+timedelta(minutes=1))
        n.plan(self.db,self.snapshot(e),self.at+timedelta(minutes=2))
        self.assertEqual(len([j for j in self.jobs() if j['kind']=='completed']),1)
        self.assertEqual([j['status'] for j in self.jobs() if j['kind']=='reminder'],['cancelled'])

    def test_stale_source_cancels_due_reminder(self):
        self.event['published_at']=n.utc(self.at-timedelta(days=1))
        n.plan(self.db,self.snapshot(),self.at)
        job=n.claim(self.db,self.snapshot(stale=True),self.at+timedelta(minutes=46))
        self.assertIsNone(job)
        self.assertEqual(self.jobs()[0]['status'],'cancelled')

    def test_timeout_not_retried_and_restart_unknown(self):
        n.plan(self.db,self.snapshot(),self.at)
        job=n.claim(self.db,self.snapshot(),self.at+timedelta(seconds=3))
        def timeout(_): raise TimeoutError()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(n.deliver(self.db,job,timeout),'unknown')
        record=json.loads(output.getvalue())
        self.assertEqual(record['job_id'],job['job_id'])
        self.assertTrue(record['stack'])
        self.assertIsNone(n.claim(self.db,self.snapshot(),self.at+timedelta(minutes=1)))
        with self.db:self.db.execute("UPDATE notification_jobs SET status='sending' WHERE kind='forecast'")
        n.recover(self.db)
        self.assertEqual([j['status'] for j in self.jobs() if j['kind']=='forecast'],['unknown'])

    def test_success_and_failure_are_isolated(self):
        n.plan(self.db,self.snapshot(),self.at)
        job=n.claim(self.db,self.snapshot(),self.at+timedelta(seconds=3))
        sent=[]
        self.assertEqual(n.deliver(self.db,job,sent.append),'sent')
        self.assertIn('https://x.com/',sent[0]['text'])
        self.assertIn('截止',sent[0]['text'])
        self.assertNotIn('@all',sent[0]['text'])

    def test_no_time_means_no_reminder(self):
        self.event['expected_at']=None
        n.plan(self.db,self.snapshot(),self.at)
        self.assertEqual([j['kind'] for j in self.jobs()],['forecast'])

    def test_failed_sender_does_not_block_next_job(self):
        n.plan(self.db,self.snapshot(),self.at)
        job=n.claim(self.db,self.snapshot(),self.at+timedelta(seconds=3))
        def fail(_): raise RuntimeError('rejected')
        self.assertEqual(n.deliver(self.db,job,fail),'failed')
        next_job=n.claim(self.db,self.snapshot(),self.at+timedelta(minutes=46))
        self.assertEqual(next_job['kind'],'reminder')
        self.assertEqual(n.deliver(self.db,next_job,lambda _:None),'sent')


class ElapsedReminderTest(unittest.TestCase):
    setUp = NotificationsTest.setUp
    tearDown = NotificationsTest.tearDown
    snapshot = NotificationsTest.snapshot
    jobs = NotificationsTest.jobs

    def deliver_forecast(self):
        n.plan(self.db,self.snapshot(),self.at)
        job=n.claim(self.db,self.snapshot(),self.at+timedelta(seconds=2))
        self.assertEqual(n.deliver(self.db,job,lambda _:None),'sent')

    def test_no_completion_sends_once_fifteen_minutes_after_target(self):
        self.deliver_forecast()
        # This fixture did not deliver the pre-reminder: it must expire at the target.
        self.assertIsNone(n.claim(self.db,self.snapshot(),self.at+timedelta(minutes=74,seconds=59)))
        job=n.claim(self.db,self.snapshot(),self.at+timedelta(minutes=75))
        self.assertIsNotNone(job)
        self.assertEqual(job['kind'],'verification')
        sent=[]
        self.assertEqual(n.deliver(self.db,job,sent.append),'sent')
        self.assertIn('15 分钟',sent[0]['text'])
        self.assertIn('自行检查额度',sent[0]['text'])
        self.assertIn('尚未',sent[0]['text'])
        self.assertIn('https://x.com/',sent[0]['text'])
        self.assertEqual(self.event['state'],'scheduled')
        n.recover(self.db)
        n.plan(self.db,self.snapshot(),self.at+timedelta(minutes=76))
        self.assertIsNone(n.claim(self.db,self.snapshot(),self.at+timedelta(minutes=76)))
        self.assertEqual(len([j for j in self.jobs() if j['kind']=='verification']),1)

    def test_terminal_announcements_keep_check_but_time_change_reschedules(self):
        for state in ('completed','cancelled'):
            with self.subTest(state=state):
                self.deliver_forecast()
                e=dict(self.event,state=state)
                n.plan(self.db,self.snapshot(e),self.at+timedelta(minutes=65))
                check=[j for j in self.jobs() if j['kind']=='verification'][0]
                self.assertEqual(check['status'],'pending')
                terminal=n.claim(self.db,self.snapshot(e),self.at+timedelta(minutes=65))
                n.deliver(self.db,terminal,lambda _:None)
                job=n.claim(self.db,self.snapshot(e),self.at+timedelta(minutes=75))
                self.assertEqual(job['kind'],'verification')
                payload=json.loads(job['payload'])
                self.assertEqual(payload['event_state'],state)
                self.assertNotIn('尚未收录',payload['text'])
                with self.db:
                    self.db.execute('DELETE FROM notification_jobs')
                    self.db.execute('DELETE FROM notification_events')
        self.deliver_forecast()
        changed=dict(self.event,expected_at=n.utc(self.at+timedelta(hours=2)))
        n.plan(self.db,self.snapshot(changed),self.at+timedelta(minutes=65))
        checks=[j for j in self.jobs() if j['kind']=='verification']
        self.assertEqual(sorted(j['status'] for j in checks),['cancelled','pending'])
        self.assertEqual([j['scheduled_at'] for j in checks if j['status']=='pending'],
                         [n.utc(self.at+timedelta(minutes=135))])

    def test_late_completion_still_sends_after_check(self):
        self.deliver_forecast()
        at=self.at+timedelta(minutes=75)
        check=n.claim(self.db,self.snapshot(),at)
        self.assertEqual(check['kind'],'verification')
        n.deliver(self.db,check,lambda _:None)
        e=dict(self.event,state='completed')
        n.plan(self.db,self.snapshot(e),at+timedelta(minutes=5))
        job=n.claim(self.db,self.snapshot(e),at+timedelta(minutes=5))
        self.assertEqual(job['kind'],'completed')

    def test_stale_source_does_not_cancel_clock_based_check(self):
        self.deliver_forecast()
        job=n.claim(self.db,self.snapshot(stale=True),self.at+timedelta(minutes=75))
        self.assertEqual(job['kind'],'verification')
        self.assertIn('尚未收录',json.loads(job['payload'])['text'])

    def test_worker_reads_completion_before_planner_catches_up(self):
        self.deliver_forecast()
        e=dict(self.event,state='completed')
        job=n.claim(self.db,self.snapshot(e),self.at+timedelta(minutes=75))
        self.assertEqual(job['kind'],'verification')
        payload=json.loads(job['payload'])
        self.assertEqual(payload['event_state'],'completed')
        self.assertIn('已宣布完成',payload['text'])
        self.assertNotIn('尚未收录',payload['text'])

    def test_expired_history_and_uncertain_time_do_not_generate_checks(self):
        self.event['expected_at']=n.utc(self.at-timedelta(hours=1))
        self.event['published_at']=n.utc(self.at-timedelta(days=1))
        n.plan(self.db,self.snapshot(),self.at)
        self.assertFalse(any(j['kind']=='verification' for j in self.jobs()))
        self.event['expected_at']=None
        n.plan(self.db,self.snapshot(),self.at)
        self.assertFalse(any(j['kind']=='verification' for j in self.jobs()))
