import json
import io
import os
import tempfile
import unittest
import urllib.error
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

    def test_relative_window_comes_from_original_timestamp(self):
        self.posts['1']['text']='Landing within the next 2 hours'
        self.assertEqual(ai_analysis.time_from_evidence({'phrase':self.posts['1']['text'],'date_ref_id':'1'},self.posts),'2026-10-23T06:47:34Z')

    def test_model_cannot_invent_time_or_clock(self):
        value = {'phrase': self.posts['1']['text'], 'date_ref_id': '1', 'clock': '12:00', 'timezone': 'PST'}
        self.assertEqual(ai_analysis.time_from_evidence(value, self.posts), '2026-10-23T18:00:00Z')
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

    def test_source_completion_overrides_model_forecast(self):
        self.posts['1']['text']="Day 15/ Nevermind, we're still cooking... and reset. Enjoy."
        e={'post_id':'1','relevant':True,'category':'forecast','anchor_id':'1','type':'reset','state':'announced',
           'evidence':[{'post_id':'1','quote':self.posts['1']['text'],'role':'update'}]}
        result=ai_analysis.validate(e,self.posts,{'1'})
        self.assertEqual((result['state'],result['category']),('completed','completed'))

    def test_future_reset_with_past_relief_is_still_a_forecast(self):
        text='Codex usage limits will be fully reset again in the next hour.\n\nImmediate relief: We reset usage limits while rolling out the fixes.'
        self.posts['1']['text']=text
        e={'post_id':'1','relevant':True,'category':'forecast','anchor_id':'1','type':'reset','state':'announced',
           'evidence':[{'post_id':'1','quote':text,'role':'update'}], 'time':{'phrase':'in the next hour','date_ref_id':'1'}}
        result=ai_analysis.validate(e,self.posts,{'1'})
        self.assertEqual((result['state'],result['category']),('scheduled','forecast'))
        self.assertEqual(result['expected_at'],'2026-10-23T05:47:34Z')

    def test_street_card_joke_and_cash_compensation_are_excluded(self):
        for text in ["People have escaped the TL and are asking me for resets on the street now. I’ll make little cards to hand out", "If this affected you, we'll add credits to your account."]:
            self.posts['1']['text']=text
            e={'post_id':'1','relevant':True,'category':'credit','anchor_id':'1','type':'credit','state':'unconfirmed',
               'evidence':[{'post_id':'1','quote':text,'role':'update'}]}
            self.assertFalse(ai_analysis.validate(e,self.posts,{'1'})['relevant'])

    def test_banked_reset_is_a_card_not_an_automatic_full_reset(self):
        self.posts['1']['text']="Loading a banked reset in everyone's paid accounts."
        e={'post_id':'1','relevant':True,'category':'forecast','anchor_id':'1','type':'reset','state':'announced',
           'evidence':[{'post_id':'1','role':'update'}]}
        result=ai_analysis.validate(e,self.posts,{'1'})
        self.assertEqual((result['category'],result['type']),('credit','credit'))
        self.posts['1']['text']="I have reset everyone's Codex usage limits. This is a hard reset given some users had banked resets already."
        e={'post_id':'1','relevant':True,'category':'completed','anchor_id':'1','type':'reset','state':'completed',
           'evidence':[{'post_id':'1','role':'completed'}]}
        result=ai_analysis.validate(e,self.posts,{'1'})
        self.assertEqual((result['category'],result['type']),('completed','reset'))

    def test_done_with_verified_tibo_quote_chain_confirms_completion(self):
        self.posts['1'].update(text='Done and dusted',quote_id='2')
        self.posts['2']={'post_id':'2','text':'Landing within the next 2 hours','created_at':'2026-10-23T03:00:00Z','author':'thsottiaux','quote_id':'3'}
        self.posts['3']={'post_id':'3','text':'Day 10/ Reset','created_at':'2026-10-23T02:59:00Z','author':'thsottiaux','quote_id':None}
        result=ai_analysis.validate({'post_id':'1','relevant':False,'reason':'Done'},self.posts,{'1'})
        self.assertEqual((result['relevant'],result['category'],result['state'],result['anchor_id']),(True,'completed','completed','3'))
        self.assertEqual({p['post_id'] for p in result['evidence']},{'1','2','3'})
        self.posts['2']['author']='another'
        self.assertFalse(ai_analysis.validate({'post_id':'1','relevant':False},self.posts,{'1'})['relevant'])

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

    def test_long_context_shrinks_batch_before_inference(self):
        post=dict(self.db.execute('SELECT * FROM posts').fetchone())
        with self.db:
            for index in range(25):
                value={**post,'post_id':str(2113492502355562876+index),'text':'Long product announcement. '*250}
                storage.save_posts(self.db,[value])
        def respond(request,**kwargs):
            self.assertLessEqual(len(request.data),32000)
            body=json.loads(request.data)
            targets=json.loads(body['messages'][1]['content'])['targets']
            self.assertLess(len(targets),20)
            content={'analyses':[{'post_id':ident,'relevant':False,'reason':'普通产品消息'} for ident in targets]}
            result={'id':'bounded','choices':[{'message':{'content':json.dumps(content)}}],'usage':{'prompt_tokens':100,'completion_tokens':20}}
            response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(result).encode()
            return response
        with patch.object(billing_guard,'verify'),patch('ai_analysis.urllib.request.urlopen',side_effect=respond) as request:
            self.assertTrue(ai_analysis.analyze_batch(self.db))
            self.assertEqual(request.call_count,1)

    def test_timeout_reservation_is_retained_and_retry_is_bounded(self):
        with patch.object(billing_guard,'verify'),patch('ai_analysis.urllib.request.urlopen',side_effect=TimeoutError) as request:
            for attempt in range(2):
                with self.assertRaises(TimeoutError):ai_analysis.analyze_batch(self.db)
            with self.assertRaises(billing_guard.FundingUnavailable):ai_analysis.analyze_batch(self.db)
            self.assertEqual(request.call_count,2)
        rows=self.db.execute('SELECT status,reserved_cny FROM ai_requests').fetchall()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['status']=='interrupted' and r['reserved_cny']>0 for r in rows))

    def test_provider_error_is_diagnosable_without_disclosing_keys(self):
        body=json.dumps({'error':{'code':'InvalidParameter','message':'bad schema sk-private-test'}}).encode()
        error=urllib.error.HTTPError('https://test.invalid',400,'Bad Request',{},io.BytesIO(body))
        with patch.object(billing_guard,'verify'),patch('ai_analysis.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(RuntimeError):ai_analysis.analyze_batch(self.db)
        detail=storage.get_state(self.db,'ai_last_api_error')
        self.assertEqual(detail['status'],400)
        self.assertIn('InvalidParameter',detail['detail'])
        self.assertNotIn('sk-private-test',detail['detail'])


if __name__ == '__main__':
    unittest.main()
