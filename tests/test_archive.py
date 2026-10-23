import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector
import storage
from analyzer import classify_candidates


def feed(text='A reset tomorrow', title='R to @thsottiaux: A reset tomorrow', author='@thsottiaux'):
    return f'''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><ttl>40</ttl><item>
      <title>{title}</title><dc:creator>{author}</dc:creator>
      <description><![CDATA[<p>{text}</p><blockquote><p>Other author's reset is done</p></blockquote>]]></description>
      <link>https://nitter.kareem.one/thsottiaux/status/2113492502355562875#m</link>
      <pubDate>Fri, 23 Oct 2026 04:47:34 GMT</pubDate></item></channel></rss>'''.encode()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.temp.name) / 'radar.db')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_original_quote_and_reply_identity_stay_separate(self):
        posts, ttl, count = collector.parse_feed(feed(), 'https://example.test/feed')
        p = posts[0]
        self.assertEqual(p['post_id'], '2113492502355562875')
        self.assertEqual(p['text'], 'A reset tomorrow')
        self.assertEqual(p['quoted_text'], "Other author's reset is done")
        self.assertTrue(p['is_reply'])
        self.assertIsNone(p['reply_to_id'])
        self.assertEqual(p['created_at'], '2026-10-23T04:47:34Z')
        self.assertEqual(ttl, 2400)
        self.assertEqual(count, 1)

    def test_retweets_and_other_authors_are_not_tibo_evidence(self):
        self.assertEqual(collector.parse_feed(feed(title='RT by @thsottiaux: hi'), 'u')[0], [])
        self.assertEqual(collector.parse_feed(feed(author='@another'), 'u')[0], [])

    def test_duplicate_fetch_keeps_first_fetch_and_one_post(self):
        posts = collector.parse_feed(feed(), 'u')[0]
        with self.db:
            self.assertEqual(storage.save_posts(self.db, posts), 1)
            posts[0]['first_fetched_at'] = '2099-01-01T00:00:00Z'
            self.assertEqual(storage.save_posts(self.db, posts), 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0], 1)
        self.assertNotEqual(self.db.execute('SELECT first_fetched_at FROM posts').fetchone()[0], posts[0]['first_fetched_at'])

    def test_rule_match_never_claims_completed_or_scheduled(self):
        with self.db:
            storage.save_posts(self.db, collector.parse_feed(feed(), 'u')[0])
        classify_candidates(self.db)
        classify_candidates(self.db)
        event = dict(self.db.execute('SELECT * FROM reset_events').fetchone())
        self.assertEqual(event['state'], 'unconfirmed')
        self.assertIsNone(event['expected_at'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM post_analyses').fetchone()[0], 1)

    def test_quoted_reset_does_not_classify_unrelated_original(self):
        with self.db:
            storage.save_posts(self.db, collector.parse_feed(feed(text='Thanks for the feedback'), 'u')[0])
        classify_candidates(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reset_events').fetchone()[0], 0)

    def test_fetch_failure_retains_resume_cursor(self):
        with self.db:
            self.db.execute('''INSERT INTO collection_jobs
              (job_id,kind,requested_start,requested_end,cursor,status,updated_at)
              VALUES ('b','backfill','2026-10-17','2026-10-24','cursor-1','running',?)''', (storage.now(),))
        job = dict(self.db.execute('SELECT * FROM collection_jobs').fetchone())
        with patch.object(collector, 'fetch', side_effect=TimeoutError), self.assertRaises(TimeoutError):
            collector.collect_page(self.db, job)
        row = self.db.execute('SELECT cursor,status,pages FROM collection_jobs').fetchone()
        self.assertEqual(tuple(row), ('cursor-1', 'partial', 0))

    def test_search_is_parameterized_and_ids_remain_strings(self):
        with self.db:
            storage.save_posts(self.db, collector.parse_feed(feed(), 'u')[0])
        self.assertEqual(storage.list_posts(self.db, "' OR 1=1 --")['total'], 0)
        self.assertEqual(storage.list_posts(self.db, '2026-10')['posts'][0]['post_id'], '2113492502355562875')

    def test_community_notes_are_not_author_evidence(self):
        raw=feed(text='Original words</p><p><b>Community note:</b> A reset has completed')
        post=collector.parse_feed(raw,'u')[0][0]
        self.assertEqual(post['text'],'Original words')
        self.assertIn('A reset has completed',post['community_note'])
        with self.db:
            storage.save_posts(self.db,[post])
        classify_candidates(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reset_events').fetchone()[0],0)

    def test_link_preview_is_not_author_text_and_quote_stays_separate(self):
        raw=feed(text='Loading a banked reset</p><hr/><b>Link</b><p>Website preview: all limits reset already')
        post=collector.parse_feed(raw,'u')[0][0]
        self.assertEqual(post['text'],'Loading a banked reset')
        self.assertEqual(post['quoted_text'],"Other author's reset is done")

    def test_migration_preserves_existing_posts_and_adds_classification(self):
        path=Path(self.temp.name)/'legacy.db'
        import sqlite3
        legacy=sqlite3.connect(path)
        legacy.execute('''CREATE TABLE posts(post_id TEXT PRIMARY KEY,author TEXT,text TEXT,created_at TEXT,url TEXT,
          is_reply INTEGER,reply_to_id TEXT,conversation_id TEXT,quote_id TEXT,quoted_text TEXT,raw_payload TEXT,
          source_url TEXT,content_hash TEXT,first_fetched_at TEXT,last_fetched_at TEXT)''')
        legacy.execute("INSERT INTO posts(post_id,text) VALUES ('old','Keep original')")
        legacy.commit();legacy.close()
        migrated=storage.connect(path)
        self.assertEqual(migrated.execute("SELECT text FROM posts WHERE post_id='old'").fetchone()[0],'Keep original')
        self.assertEqual(migrated.execute('PRAGMA user_version').fetchone()[0],3)
        self.assertIn('category',{x[1] for x in migrated.execute('PRAGMA table_info(post_analyses)')})
        migrated.close()


if __name__ == '__main__':
    unittest.main()
