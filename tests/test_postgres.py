"""Run against a disposable DB: TEST_POSTGRES_URL=... python -m unittest discover."""
import os
import threading
import unittest
import uuid


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URL'), 'requires disposable PostgreSQL database')
class PostgresIntegrationTest(unittest.TestCase):
    def setUp(self):
        import postgres
        self.module=postgres
        self.db=postgres.connect(os.environ['TEST_POSTGRES_URL'])
        self.key='test-'+uuid.uuid4().hex

    def tearDown(self):
        with self.db:
            self.db.execute('DELETE FROM ai_requests WHERE request_key LIKE ?',(self.key+'%',))
            self.db.execute('DELETE FROM sync_state WHERE key=?',(self.key,))
        self.db.close()

    def test_floating_precision_and_parameterized_like(self):
        value=0.40375984
        with self.db:
            self.db.execute('INSERT INTO ai_requests(request_key,month,status,reserved_cny,created_at) VALUES (?,?,?,?,?)',
                            (self.key,'2026-10','reserved',value,'2026-10-23T00:00:00Z'))
        row=self.db.execute('SELECT reserved_cny FROM ai_requests WHERE request_key LIKE ?',(self.key.upper(),)).fetchone()
        self.assertEqual(row[0],value)
        self.assertEqual(row['reserved_cny'],value)

    def test_insert_analysis_returns_generated_identity(self):
        with self.assertRaisesRegex(RuntimeError, 'rollback fixture'):
            with self.db:
                self.db.execute('INSERT INTO posts(post_id,author,text,created_at,url,is_reply,raw_payload,source_url,content_hash,first_fetched_at,last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                (self.key,'thsottiaux','reset','2026-10-23T00:00:00Z','https://x.com/thsottiaux/status/1',0,'<item/>','rss','hash','2026-10-23T00:00:00Z','2026-10-23T00:00:00Z'))
                cursor=self.db.execute('INSERT INTO post_analyses(post_id,input_hash,engine,model,prompt_version,input_snapshot,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                                       (self.key,'hash','test','test','test','{}','{}','2026-10-23T00:00:00Z'))
                self.assertGreater(cursor.lastrowid,0)
                raise RuntimeError('rollback fixture')

    def test_transaction_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.db:
                self.db.execute('INSERT INTO sync_state VALUES (?,?)',(self.key,'1'))
                raise RuntimeError('rollback')
        self.assertIsNone(self.db.execute('SELECT * FROM sync_state WHERE key=?',(self.key,)).fetchone())

    def test_budget_lock_serializes_concurrent_reservations(self):
        barrier=threading.Barrier(2)
        def reserve(index):
            db=self.module.connect(os.environ['TEST_POSTGRES_URL'])
            barrier.wait()
            with db:
                db.budget_lock(self.key)
                total=db.execute('SELECT coalesce(sum(reserved_cny),0) FROM ai_requests WHERE request_key LIKE ?',(self.key+'%',)).fetchone()[0]
                if total+30<=50:
                    db.execute('INSERT INTO ai_requests(request_key,month,status,reserved_cny,created_at) VALUES (?,?,?,?,?)',
                               (self.key+str(index),'2026-10','reserved',30,'2026-10-23T00:00:00Z'))
            db.close()
        threads=[threading.Thread(target=reserve,args=(i,)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        total=self.db.execute('SELECT sum(reserved_cny) FROM ai_requests WHERE request_key LIKE ?',(self.key+'%',)).fetchone()[0]
        self.assertEqual(total,30)
