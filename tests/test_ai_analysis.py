import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import ai_analysis
import billing_guard
import collector
import storage


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.posts = {'1': {'post_id': '1', 'text': 'Landing tomorrow before 10am PST together with a small ship.',
                           'created_at': '2026-10-23T04:47:34Z'}}

    def test_tomorrow_uses_authors_local_day_not_utc_day(self):
        value = {'phrase': self.posts['1']['text'], 'date_ref_id': '1', 'clock': '10:00',
                 'timezone': 'PST', 'local_date': '2026-10-24', 'is_deadline': True}
        self.assertEqual(ai_analysis.time_from_evidence(value, self.posts), '2026-10-23T18:00:00Z')

    def test_pt_observes_daylight_saving_but_literal_pst_does_not(self):
        self.posts['1']['text'] = 'Landing tomorrow before 10am PT.'
        value = {'phrase': self.posts['1']['text'], 'date_ref_id': '1', 'clock': '10:00', 'timezone': 'PT'}
        self.assertEqual(ai_analysis.time_from_evidence(value, self.posts), '2026-10-23T17:00:00Z')

    def test_model_cannot_invent_time_or_clock(self):
        value = {'phrase': self.posts['1']['text'], 'date_ref_id': '1', 'clock': '12:00', 'timezone': 'PST'}
        self.assertIsNone(ai_analysis.time_from_evidence(value, self.posts))
        value['phrase'] = 'invented tomorrow 12pm PST'
        self.assertIsNone(ai_analysis.time_from_evidence(value, self.posts))

    def test_lone_done_does_not_prove_completed(self):
        self.posts['1']['text'] = 'Done'
        entry = {'post_id': '1', 'relevant': True, 'anchor_id': '1', 'type': 'reset',
                 'state': 'completed', 'category': 'completed', 'title': '完成', 'summary': '完成', 'scope': 'unknown',
                 'evidence': [{'post_id': '1', 'quote': 'Done', 'role': 'completed'}]}
        self.assertEqual(ai_analysis.validate(entry, self.posts, {'1'})['state'], 'unconfirmed')

    def test_fabricated_excerpt_is_rejected(self):
        entry = {'post_id': '1', 'relevant': True, 'anchor_id': '1', 'type': 'reset',
                 'state': 'scheduled', 'category': 'forecast', 'evidence': [{'post_id': '1', 'quote': 'All limits reset', 'role': 'forecast'}]}
        with self.assertRaises(ValueError):
            ai_analysis.validate(entry, self.posts, {'1'})

    def test_expired_or_non_postpaid_coupon_is_excluded(self):
        c = {'Status': 'Available', 'EffectiveTime': '2026-10-01T00:00:00Z',
             'ExpiryTime': '2026-12-01T00:00:00Z', 'Balance': '300',
             'ApplicableProducts': '通用（不适用商品名单除外）', 'ApplicableScenarios': '阿里云按量付费账单'}
        self.assertEqual(billing_guard.coupon_balance({'CashCoupon': [c]}, 1792746935), 300)
        c['ExpiryTime'] = '2026-10-02T00:00:00Z'
        self.assertEqual(billing_guard.coupon_balance({'CashCoupon': [c]}, 1792746935), 0)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.temp.name) / 'radar.db')
        raw = b'''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><ttl>40</ttl><item><title>Hello</title><dc:creator>@thsottiaux</dc:creator><description><![CDATA[<p>Hello</p>]]></description><link>https://nitter.kareem.one/thsottiaux/status/2113492502355562875</link><pubDate>Fri, 23 Oct 2026 04:47:34 GMT</pubDate></item></channel></rss>'''
        with self.db:
            storage.save_posts(self.db, collector.parse_feed(raw, 'u')[0])
        self.env = patch.dict(os.environ, {'AI_ENABLED': '1', 'AI_BASE_URL': 'https://ws-test.ap-southeast-1.maas.aliyuncs.com', 'DASHSCOPE_API_KEY': 'test-only'})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.db.close(); self.temp.cleanup()

    def test_funding_failure_makes_no_inference_request(self):
        with patch.object(billing_guard, 'verify', side_effect=billing_guard.FundingUnavailable('stop')), patch('ai_analysis.urllib.request.urlopen') as request:
            with self.assertRaises(billing_guard.FundingUnavailable):
                ai_analysis.analyze_batch(self.db)
            request.assert_not_called()

    def test_batch_disables_thinking_and_search_and_persists_once(self):
        result = {'id': 'test-response', 'choices': [{'message': {'content': json.dumps({'analyses': [{'post_id': '2113492502355562875', 'relevant': False}]})}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
        response = MagicMock(); response.__enter__.return_value.read.return_value = json.dumps(result).encode()
        with patch.object(billing_guard, 'verify'), patch('ai_analysis.urllib.request.urlopen', return_value=response) as request:
            self.assertTrue(ai_analysis.analyze_batch(self.db))
            body = json.loads(request.call_args.args[0].data)
            self.assertIs(body['enable_thinking'], False)
            self.assertIs(body['enable_search'], False)
            self.assertFalse(ai_analysis.analyze_batch(self.db))
            self.assertEqual(request.call_count, 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM post_analyses WHERE engine='qwen'").fetchone()[0], 1)

    def test_monthly_limit_prevents_inference(self):
        with self.db:
            self.db.execute('INSERT INTO ai_requests(request_key,month,status,reserved_cny,created_at) VALUES (?,?,?,?,?)', ('prior', storage.now()[:7], 'unknown', 50, storage.now()))
        with patch.object(billing_guard, 'verify'), patch('ai_analysis.urllib.request.urlopen') as request:
            with self.assertRaises(billing_guard.FundingUnavailable):
                ai_analysis.analyze_batch(self.db)
            request.assert_not_called()

    def test_unrelated_posts_and_rule_candidates_are_not_public_messages(self):
        with self.db:
            self.db.execute('''INSERT INTO reset_events(event_id,type,state,title,summary,published_at,updated_at,scope,confidence)
              VALUES ('candidate','reset','unconfirmed','request','',?,?, 'unknown','candidate')''', (storage.now(),storage.now()))
        result = {'id':'r', 'choices':[{'message':{'content':json.dumps({'analyses':[{'post_id':'2113492502355562875','relevant':False,'reason':'普通问候'}]})}}], 'usage':{'prompt_tokens':10,'completion_tokens':10}}
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(result).encode()
        with patch.object(billing_guard,'verify'),patch('ai_analysis.urllib.request.urlopen',return_value=response):
            ai_analysis.analyze_batch(self.db)
        data=storage.snapshot(self.db)
        self.assertEqual(data['messages'],[])
        self.assertEqual(data['events'],[])
        self.assertEqual(data['analysis']['reviewed'],1)

    def test_completed_message_keeps_original_and_category(self):
        post_id='2113492502355562875'
        with self.db:
            self.db.execute('UPDATE posts SET text=? WHERE post_id=?',("We've reset all limits.",post_id))
        entry={'post_id':post_id,'relevant':True,'reason':'明确完成','category':'completed','anchor_id':post_id,'type':'reset','state':'completed','title':'AI 改写标题','summary':'AI 改写摘要','scope':'unknown','confidence':'direct','evidence':[{'post_id':post_id,'quote':"We've reset all limits.",'role':'completed'}]}
        result={'id':'r','choices':[{'message':{'content':json.dumps({'analyses':[entry]})}}],'usage':{'prompt_tokens':10,'completion_tokens':10}}
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(result).encode()
        with patch.object(billing_guard,'verify'),patch('ai_analysis.urllib.request.urlopen',return_value=response):
            ai_analysis.analyze_batch(self.db)
        message=storage.snapshot(self.db)['messages'][0]
        self.assertEqual(message['text'],"We've reset all limits.")
        self.assertEqual(message['category'],'completed')
        self.assertEqual(message['event_id'],'tibo-'+post_id)


if __name__ == '__main__':
    unittest.main()
