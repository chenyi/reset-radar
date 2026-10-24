import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

import billing_guard
import storage
import translations
from notification_templates import render


class ChineseTranslationsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=storage.connect(Path(self.temp.name)/'radar.db')
        with self.db:
            self.db.execute('INSERT INTO posts(post_id,author,text,created_at,url,is_reply,raw_payload,source_url,content_hash,first_fetched_at,last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            ('1','thsottiaux','Landing tomorrow before 10am PST.','2026-10-23T04:00:00Z','https://x.com/thsottiaux/status/1',0,'<item/>','rss','hash1',storage.now(),storage.now()))
        self.post=dict(self.db.execute('SELECT * FROM posts').fetchone())
        self.env=patch.dict(os.environ,{'AI_ENABLED':'1','AI_BASE_URL':'https://ws-test.ap-southeast-1.maas.aliyuncs.com','DASHSCOPE_API_KEY':'test-only'})
        self.env.start()
        self.posts=patch.object(translations,'public_posts',side_effect=lambda db:[dict(db.execute('SELECT * FROM posts').fetchone())])
        self.posts.start()

    def tearDown(self):
        self.posts.stop();self.env.stop();self.db.close();self.temp.cleanup()

    def response(self,identity='1',text='明天上午 10 点 PST 之前上线。'):
        result={'id':'translation-test','choices':[{'message':{'content':json.dumps({'translations':[{'post_id':identity,'text_zh':text}]})}}],'usage':{'prompt_tokens':100,'completion_tokens':30}}
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(result).encode()
        return response

    def test_stores_separate_chinese_copy_and_deduplicates(self):
        with patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen',return_value=self.response()) as request:
            self.assertTrue(translations.translate_batch(self.db))
            self.assertFalse(translations.translate_batch(self.db))
            self.assertEqual(request.call_count,1)
            body=json.loads(request.call_args.args[0].data)
            self.assertFalse(body['enable_thinking']);self.assertFalse(body['enable_search'])
        self.assertEqual(self.db.execute('SELECT text FROM posts').fetchone()[0],self.post['text'])
        self.assertIn('明天',translations.translated(self.db)['1'])
        with self.db:self.db.execute("UPDATE posts SET content_hash='hash2' WHERE post_id='1'")
        self.assertNotIn('1',translations.translated(self.db))

    def test_coupon_failure_and_shared_monthly_cap_prevent_calls(self):
        with patch.object(billing_guard,'verify',side_effect=billing_guard.FundingUnavailable('stop')),patch('translations.urllib.request.urlopen') as request:
            with self.assertRaises(billing_guard.FundingUnavailable):translations.translate_batch(self.db)
            request.assert_not_called()
        with self.db:self.db.execute('INSERT INTO ai_requests(request_key,month,status,reserved_cny,created_at) VALUES (?,?,?,?,?)',('classification',storage.now()[:7],'reserved',50,storage.now()))
        with patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen') as request:
            with self.assertRaises(billing_guard.FundingUnavailable):translations.translate_batch(self.db)
            request.assert_not_called()

    def test_invalid_response_never_changes_original_and_replays_without_charge(self):
        with patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen',return_value=self.response('wrong')) as request:
            for _ in range(2):
                with self.assertRaises(ValueError):translations.translate_batch(self.db)
            self.assertEqual(request.call_count,1)
        self.assertFalse(translations.translated(self.db))
        self.assertEqual(self.db.execute('SELECT text FROM posts').fetchone()[0],self.post['text'])

    def test_interrupted_translation_keeps_reservation_and_bounds_retry(self):
        with patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen',side_effect=TimeoutError()) as request:
            for _ in range(2):
                with self.assertRaises(RuntimeError):translations.translate_batch(self.db)
            with self.assertRaises(billing_guard.FundingUnavailable):translations.translate_batch(self.db)
            self.assertEqual(request.call_count,2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM ai_requests WHERE status='interrupted'").fetchone()[0],2)

    def test_ordinary_reset_is_not_presented_as_a_reset_card(self):
        with patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen',return_value=self.response(text='这是一次重置卡。\\n请使用。')):
            translations.translate_batch(self.db)
        self.assertEqual(translations.translated(self.db)['1'],'这是一次额度重置。\n请使用。')

    def test_invalid_batch_splits_without_recharging_the_received_batch(self):
        self.posts.stop()
        with self.db:
            self.db.execute("INSERT INTO posts(post_id,author,text,created_at,url,is_reply,raw_payload,source_url,content_hash,first_fetched_at,last_fetched_at) SELECT '2',author,text,created_at,url,is_reply,raw_payload,source_url,content_hash,first_fetched_at,last_fetched_at FROM posts WHERE post_id='1'")
        with patch.object(translations,'public_posts',side_effect=lambda db:[dict(p) for p in db.execute('SELECT * FROM posts ORDER BY post_id')]),patch.object(billing_guard,'verify'),patch('translations.urllib.request.urlopen',side_effect=[self.response('wrong'),self.response('1'),self.response('2')]) as request:
            self.assertTrue(translations.translate_batch(self.db))
            self.assertTrue(translations.translate_batch(self.db))
            self.assertFalse(translations.translate_batch(self.db))
            self.assertEqual(request.call_count,3)
        self.assertEqual(len(translations.translated(self.db)),2)


class NotificationStylesTest(unittest.TestCase):
    def setUp(self):
        self.message={'kind':'forecast','event_id':'reset-1','target_at':'2026-10-23T18:00:00Z','time_label':'预计截止时间',
                      'post':{'text':'Landing tomorrow before 10am PST.','text_zh':'明天上午 10 点 PST 之前上线。','url':'https://x.com/thsottiaux/status/1'}}

    def test_default_chinese_card_preserves_deadline_and_source(self):
        text=render(self.message)
        self.assertIn('预计截止时间',text);self.assertIn('10月24日 02:00',text)
        self.assertIn('明天上午',text);self.assertNotIn('Landing tomorrow',text)
        self.assertIn('查看原文',text)

    def test_bilingual_is_explicit_and_preview_cannot_look_like_new_announcement(self):
        self.assertIn('Landing tomorrow',render(self.message,'bilingual'))
        self.message['preview']=True
        self.assertTrue(render(self.message).startswith('**样式预览 · 不是新公告**'))
        self.assertTrue(render(self.message,'brief').startswith('**样式预览 · 不是新公告**'))

    def test_missing_translation_has_chinese_fallback(self):
        del self.message['post']['text_zh']
        self.assertIn('中文译文暂不可用',render(self.message))
        self.assertNotIn('Landing tomorrow',render(self.message))

    def test_configured_https_origin_overrides_a_queued_legacy_origin(self):
        self.message['origin']='http://47.237.1.108'
        with patch.dict(os.environ,{'PUBLIC_ORIGIN':'https://radar.shadowearth.cn'}):
            text=render(self.message)
        self.assertIn('https://radar.shadowearth.cn/#event/reset-1',text)
        self.assertNotIn('http://47.237.1.108',text)
