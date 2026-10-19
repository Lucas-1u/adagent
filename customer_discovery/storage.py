"""SQLite snapshots: each run retains its original request, leads and evidence."""
import json
import sqlite3
from pathlib import Path
from .models import Run


class Store:
    def __init__(self, path='data/discovery.sqlite3'):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS raw_responses (
                run_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (run_id, id));
        ''')

    def save(self, run: Run):
        with self.db:
            self.db.execute('INSERT INTO runs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload',
                            (run.id, run.started_at, run.status, run.model_dump_json()))

    def save_evidence(self, run_id, evidence):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO raw_responses VALUES (?,?,?)',
                            (run_id, evidence.id, evidence.model_dump_json()))

    def get(self, run_id):
        row = self.db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def list_runs(self, limit=20):
        rows = self.db.execute('SELECT id,started_at,status FROM runs ORDER BY started_at DESC LIMIT ?', (min(max(limit, 1), 100),))
        return [dict(zip(('id', 'started_at', 'status'), r)) for r in rows]

    def raw(self, run_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM raw_responses WHERE run_id=?', (run_id,))]

    def close(self):
        self.db.close()
