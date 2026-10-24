import copy
import tempfile
import unittest
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import agent_api
import storage


NOW = '2026-10-24T08:00:00Z'


def event(identity='reset-1', state='scheduled', **changes):
    result = dict(event_id=identity, type='reset', state=state, published_at='2026-10-24T06:00:00Z',
                  activity_at='2026-10-24T06:00:00Z', expected_at='2026-10-24T10:00:00Z',
                  scope='unknown', time_kind='point', time_quote='in the next 4 hours',
                  evidence=[dict(post_id='1', created_at='2026-10-24T06:00:00Z',
                                 category='forecast', role='forecast', text='Reset in the next 4 hours',
                                 url='https://x.com/thsottiaux/status/1')])
    result.update(changes)
    return result


def snapshot(events, **changes):
    result = dict(events=events, sync_ok=True, source_checked_at=NOW, last_sync=NOW,
                  fresh_seconds=5400, analysis_pending=0, ai={'enabled': True},
                  collection={'coverage_note': 'Public RSS only'})
    result.update(changes)
    return result


class AgentApiTests(unittest.TestCase):
    def request(self, data, path='/api/v1/status', query=''):
        return agent_api.respond(path, parse_qs(query, keep_blank_values=True), data, NOW)

    def test_active_and_unknown_time_forecasts_are_preserved(self):
        for target in ('2026-10-24T10:00:00Z', None):
            code, body = self.request(snapshot([event(expected_at=target)]))
            self.assertEqual(code, 200)
            self.assertEqual(body['data']['forecast_status'], 'active')
            self.assertEqual(body['data']['active_forecast']['expected_at'], target)

    def test_elapsed_deadline_never_claims_completion(self):
        _, body = self.request(snapshot([event(expected_at='2026-10-24T07:00:00Z')]))
        self.assertEqual(body['data']['forecast_status'], 'elapsed')
        self.assertIsNone(body['data']['latest_completed'])

    def test_stale_and_pending_are_unknown_not_none(self):
        for changes in ({'last_sync': '2026-10-23T00:00:00Z'}, {'analysis_pending': 1}, {'sync_ok': False}):
            _, body = self.request(snapshot([], **changes))
            self.assertEqual(body['data']['forecast_status'], 'unknown')
            self.assertIsNone(body['data']['active_forecast'])
        self.assertEqual(self.request(snapshot([]))[1]['data']['forecast_status'], 'none')

    def test_completion_uses_completion_evidence_not_later_progress(self):
        completed = event(state='completed', completed_announced_at='2026-10-24T07:59:00Z', evidence=[
            dict(post_id='1', text='Reset tomorrow', created_at='2026-10-23T06:00:00Z', category='forecast', role='forecast'),
            dict(post_id='2', text="We've reset all limits", created_at='2026-10-24T06:30:00Z', category='completed', role='completed'),
            dict(post_id='3', text='Enjoy the new feature', created_at='2026-10-24T07:59:00Z', category='progress', role='update')])
        _, body = self.request(snapshot([completed]))
        self.assertEqual(body['data']['latest_completed']['completed_announced_at'], '2026-10-24T06:30:00Z')
        self.assertIsNone(body['data']['latest_completed']['effective_at'])
        self.assertIsNone(body['data']['active_forecast'])

    def test_unproven_completion_time_does_not_fall_back_to_forecast(self):
        _, body = self.request(snapshot([event(state='completed', completed_announced_at=NOW)]))
        self.assertIsNone(body['data']['latest_completed'])

    def test_context_post_does_not_inherit_target_completion_category(self):
        evidence = [dict(post_id='1', analyzed_post_id='2', category='completed', role='forecast',
                         created_at='2026-10-23T06:00:00Z', text='Reset tomorrow'),
                    dict(post_id='2', analyzed_post_id='2', category='completed', role='completed',
                         created_at='2026-10-24T06:00:00Z', text="We've reset all limits")]
        _, body = self.request(snapshot([event(state='completed', evidence=evidence)]))
        self.assertEqual(body['data']['latest_completed']['completed_announced_at'], '2026-10-24T06:00:00Z')

    def test_credit_does_not_hide_forecast_but_later_reset_completion_does(self):
        credit = event('credit-1', type='credit', activity_at='2026-10-24T07:00:00Z')
        self.assertEqual(self.request(snapshot([credit, event()]))[1]['data']['forecast_status'], 'active')
        complete = event('reset-2', state='completed', activity_at='2026-10-24T07:00:00Z')
        self.assertIsNone(self.request(snapshot([complete, event()]))[1]['data']['active_forecast'])

    def test_cancelled_forecast_and_latest_completed_are_distinct(self):
        cancelled = event(state='cancelled')
        _, body = self.request(snapshot([cancelled]))
        self.assertEqual(body['data']['latest_forecast']['state'], 'cancelled')
        self.assertIsNone(body['data']['active_forecast'])

    def test_filter_pagination_and_cursor_binding(self):
        events = [event(str(i), activity_at=f'2026-10-24T0{i}:00:00Z') for i in (5, 6, 7)]
        data = snapshot(events)
        code, body = self.request(data, '/api/v1/events', 'limit=1&type=reset&since=2026-10-24T05:00:00Z')
        self.assertEqual(body['data']['items'][0]['event_id'], '7')
        cursor = body['data']['next_cursor']
        _, page = self.request(data, '/api/v1/events', f'limit=1&type=reset&since=2026-10-24T05:00:00Z&cursor={cursor}')
        self.assertEqual(page['data']['items'][0]['event_id'], '6')
        self.assertEqual(self.request(data, '/api/v1/events', f'cursor={cursor}&type=credit')[0], 400)
        self.assertEqual(self.request(data, '/api/v1/events', 'limit=0')[0], 400)
        self.assertEqual(self.request(data, '/api/v1/events', 'since=nonsense')[0], 400)
        self.assertEqual(self.request(data, '/api/v1/events', 'limit=2&limit=3')[0], 400)

    def test_detail_includes_original_evidence_and_is_read_only(self):
        data = snapshot([event()]); before = copy.deepcopy(data)
        _, body = self.request(data, '/api/v1/events/reset-1')
        self.assertEqual(body['data']['evidence'][0]['text'], 'Reset in the next 4 hours')
        self.assertEqual(data, before)
        self.assertEqual(self.request(data, '/api/v1/events/missing')[0], 404)
        self.assertEqual(self.request(data, query='unexpected=x')[0], 400)

    def test_pending_uses_current_hash_and_incremental_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            db = storage.connect(Path(folder) / 'test.db')
            for ident in ('live', 'history'):
                db.execute('''INSERT INTO posts(post_id,author,text,created_at,url,is_reply,raw_payload,
                  source_url,content_hash,first_fetched_at,last_fetched_at) VALUES (?,?,?,?,?,0,'','',?,?,?)''',
                           (ident, 'thsottiaux', 'hello', NOW, 'https://x.com', 'hash', NOW, NOW))
            storage.set_state(db, 'incremental_post_ids', ['live'])
            self.assertEqual(agent_api.pending_analysis(db, 'v1'), 1)
            db.execute('''INSERT INTO post_analyses(post_id,input_hash,engine,model,prompt_version,input_snapshot,
              result_json,created_at) VALUES ('live','hash','qwen','m','v1','{}','{}',?)''', (NOW,))
            self.assertEqual(agent_api.pending_analysis(db, 'v1'), 0)
            db.execute("UPDATE posts SET content_hash='changed' WHERE post_id='live'")
            self.assertEqual(agent_api.pending_analysis(db, 'v1'), 1)
            db.close()

    def test_cache_rechecks_source_age_and_limiter_expires(self):
        clock = [10]
        gateway = agent_api.Gateway(clock=lambda: clock[0], limit=2)
        calls = []
        def load():
            calls.append(1)
            return snapshot([])
        gateway.get('client', load); cached = gateway.get('client', load)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(agent_api.RateLimited): gateway.get('client', load)
        self.assertEqual(agent_api.respond('/api/v1/status', {}, cached, '2026-10-25T08:00:00Z')[1]['data']['forecast_status'], 'unknown')
        clock[0] += 61
        gateway.get('client', load)
        self.assertEqual(len(calls), 2)

    def test_no_snapshot_is_unavailable(self):
        self.assertEqual(self.request(snapshot([], last_sync=None))[0], 503)

    def test_http_handler_cache_and_429_retry_header(self):
        import server
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        with patch.object(server, 'snapshot', return_value=snapshot([])) as loader, \
             patch.object(server, 'agent_gateway', agent_api.Gateway(limit=1)):
            thread.start()
            try:
                url = f'http://127.0.0.1:{http.server_port}/api/v1/status'
                with urllib.request.urlopen(url) as response:
                    self.assertEqual(json.load(response)['api_version'], '1')
                with self.assertRaises(urllib.error.HTTPError) as failed:
                    urllib.request.urlopen(url)
                self.assertEqual(failed.exception.code, 429)
                self.assertEqual(failed.exception.headers['Retry-After'], '60')
                self.assertEqual(loader.call_count, 1)
            finally:
                http.shutdown(); http.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
