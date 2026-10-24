import contextlib
import io
import json
import os
import socket
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import observability
import server


class StructuredLoggingTest(unittest.TestCase):
    def test_error_has_frames_but_never_locals_source_or_secrets(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'fixture-private-key', 'DATABASE_URL': 'postgresql://user:password@db/database'}), contextlib.redirect_stdout(output):
            try:
                raise ValueError('fixture-private-key Bearer auth-fixture https://user:pass@example.com/path?q=private-search&token=private-token postgresql://user:password@db/database')
            except ValueError as error:
                observability.emit('test.failed', level='ERROR', error=error, job_id='job-1')
        text = output.getvalue()
        record = json.loads(text)
        self.assertEqual(record['error_type'], 'ValueError')
        self.assertEqual(record['job_id'], 'job-1')
        self.assertTrue(record['stack'])
        for secret in ['fixture-private-key', 'auth-fixture', 'private-search', 'private-token', 'user:pass', 'user:password']:
            self.assertNotIn(secret, text)
        self.assertNotIn('raise ValueError', text)

    def test_operation_records_failure_with_the_same_job_id(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(ValueError):
                with observability.operation('collection', job_id='job-2'):
                    raise ValueError('fixture')
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['event'] for r in records], ['collection.started', 'collection.failed'])
        self.assertTrue(all(r['job_id'] == 'job-2' for r in records))
        self.assertIn('duration_ms', records[-1])

    def test_logging_sink_failure_does_not_fail_the_operation(self):
        with patch('builtins.print', side_effect=OSError('disk unavailable')):
            with observability.operation('test') as result:
                result['worked'] = True


class HttpDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.redirect.__exit__(None, None, None)

    def records(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_public_request_id_correlates_without_request_secrets(self):
        identity = str(uuid.uuid4())
        request = urllib.request.Request(self.base + '/not-found?q=private-search', headers={
            'X-Request-ID': identity, 'Authorization': 'Bearer private-token', 'Cookie': 'session=private-cookie'})
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request)
        self.assertEqual(failure.exception.code, 404)
        self.assertEqual(failure.exception.headers['X-Request-ID'], identity)
        failure.exception.read(); failure.exception.close()
        self.http.shutdown()
        record = self.records()[-1]
        self.assertEqual((record['request_id'], record['path'], record['status']), (identity, '/not-found', 404))
        for secret in ['private-search', 'private-token', 'private-cookie']:
            self.assertNotIn(secret, self.output.getvalue())

    def test_missing_path_on_malformed_request_does_not_crash_logging(self):
        with socket.create_connection(self.http.server_address, timeout=3) as connection:
            connection.sendall(b'bad request with too many tokens\r\n\r\n')
            connection.shutdown(socket.SHUT_WR)
            while connection.recv(65536):
                pass
        self.http.shutdown()
        records = self.records()
        self.assertEqual(records[-1]['status'], 400)
        self.assertNotIn('AttributeError', self.output.getvalue())
        self.assertNotIn('bad request with too many tokens', self.output.getvalue())

    def test_unhandled_request_failure_has_a_trace_and_a_500(self):
        with patch.object(server, 'snapshot', side_effect=RuntimeError('provider unavailable')):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(self.base + '/api/events')
            self.assertEqual(failure.exception.code, 500)
            identity = failure.exception.headers['X-Request-ID']
            failure.exception.read(); failure.exception.close()
        self.http.shutdown()
        records = self.records()
        error = next(r for r in records if r['event'] == 'http.failed')
        self.assertEqual(error['request_id'], identity)
        self.assertEqual(error['error_type'], 'RuntimeError')
        self.assertTrue(error['stack'])
        self.assertEqual(records[-1]['status'], 500)

    def test_successful_health_does_not_duplicate_application_access_logs(self):
        health = {'collection': {'posts': 1}, 'summary': {'stale': False}, 'events': [],
                  'translation': {}, 'ai': {'enabled': False}}
        with patch.object(server, 'snapshot', return_value=health):
            with urllib.request.urlopen(self.base + '/health') as response:
                self.assertEqual(response.status, 200)
        self.http.shutdown()
        self.assertEqual(self.records(), [])
