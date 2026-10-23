#!/usr/bin/env python3
"""Import a SQLite snapshot into an empty PostgreSQL DB and verify every value."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import postgres

TABLES = ('posts', 'post_analyses', 'reset_events', 'event_posts', 'collection_jobs', 'sync_state', 'ai_requests')


def digest(rows):
    result = hashlib.sha256()
    for row in rows:
        result.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode())
        result.update(b'\n')
    return result.hexdigest()


def migrate(source, destination):
    source.row_factory = sqlite3.Row
    report = {}
    with destination:
        for table in TABLES:
            if destination.execute(f'SELECT count(*) FROM {table}').fetchone()[0]:
                raise ValueError(f'Destination table {table} is not empty')
        for table in TABLES:
            columns = [x[1] for x in source.execute(f'PRAGMA table_info({table})')]
            keys = [x[1] for x in source.execute(f'PRAGMA table_info({table})') if x[5]]
            order = ','.join(keys)
            count = 0
            for row in source.execute(f'SELECT * FROM {table} ORDER BY {order}'):
                destination.raw.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))})", tuple(row))
                count += 1
            query = f"SELECT {','.join(columns)} FROM {table} ORDER BY {order}"
            before = digest(source.execute(query))
            after = digest(destination.raw.execute(query))
            if before != after:
                raise ValueError(f'Value mismatch in {table}')
            report[table] = {'rows': count, 'sha256': after}
        destination.raw.execute("SELECT setval(pg_get_serial_sequence('post_analyses','analysis_id'),coalesce((SELECT max(analysis_id) FROM post_analyses),1),EXISTS(SELECT 1 FROM post_analyses))")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('sqlite')
    args = parser.parse_args()
    s = sqlite3.connect(f'file:{args.sqlite}?mode=ro', uri=True)
    d = postgres.connect(os.environ['DATABASE_URL'])
    print(json.dumps(migrate(s, d), indent=2))
    d.close(); s.close()
