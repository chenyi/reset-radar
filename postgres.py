"""PostgreSQL connection with the existing storage query/row contract."""
import re
import threading
from storage import SCHEMA

import psycopg

_initialized = set()
_schema_lock = threading.Lock()


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def sql_for_postgres(sql):
    ignore = bool(re.search(r'INSERT\s+OR\s+IGNORE', sql, re.I))
    sql = re.sub(r'INSERT\s+OR\s+IGNORE', 'INSERT', sql, flags=re.I)
    sql = re.sub(r'julianday\((created_at|\?)\)', r'EXTRACT(EPOCH FROM \1::timestamptz)', sql)
    sql = re.sub(r'\bLIKE\b', 'ILIKE', sql)
    # SQL strings use % only as literal LIKE patterns; psycopg needs them escaped.
    sql = sql.replace('%', '%%').replace('?', '%s')
    sql = sql.replace('%s IS NULL', '%s::text IS NULL')
    if ignore:
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self.cursor, self.lastrowid = cursor, lastrowid

    def __iter__(self):
        return iter(self.cursor)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount


class Connection:
    def __init__(self, dsn):
        self.raw = psycopg.connect(dsn, autocommit=True, row_factory=row_factory)
        self.transactions = []

    def execute(self, sql, args=()):
        insert_analysis = bool(re.match(r'\s*INSERT(?: OR IGNORE)? INTO post_analyses\b', sql, re.I))
        sql = sql_for_postgres(sql)
        if insert_analysis:
            sql += ' RETURNING analysis_id'
        cursor = self.raw.execute(sql, args or None)
        identity = cursor.fetchone()[0] if insert_analysis else None
        return Cursor(cursor, identity)

    def __enter__(self):
        transaction = self.raw.transaction()
        transaction.__enter__()
        self.transactions.append(transaction)
        return self

    def __exit__(self, *args):
        return self.transactions.pop().__exit__(*args)

    def budget_lock(self, month):
        self.raw.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('ai-budget:' + month,))

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def connect(dsn):
    db = Connection(dsn)
    with _schema_lock:
        if dsn not in _initialized:
            schema = SCHEMA
            schema = schema.replace('analysis_id INTEGER PRIMARY KEY', 'analysis_id BIGSERIAL PRIMARY KEY')
            with db:
                for statement in schema.split(';'):
                    if statement.strip():
                        db.raw.execute(statement)
                db.raw.execute('CREATE INDEX IF NOT EXISTS analyses_classification ON post_analyses(engine,relevant,category)')
            _initialized.add(dsn)
    return db
