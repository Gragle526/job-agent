import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import Checkpoint, Job, State, now


class StaleRun(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(n INTEGER PRIMARY KEY, job_id TEXT, data TEXT, time TEXT);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, version INTEGER, data TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT, version INTEGER,
                status TEXT, checkpoint TEXT, time TEXT);
            CREATE TABLE IF NOT EXISTS events(n INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, data TEXT, time TEXT);
            CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY, run_id TEXT, reserved REAL,
                actual REAL, tokens INTEGER, model TEXT, status TEXT, time TEXT);
            CREATE TABLE IF NOT EXISTS conversation_meta(id TEXT PRIMARY KEY, title TEXT,
                title_source TEXT, archived INTEGER DEFAULT 0, updated TEXT);
            CREATE TABLE IF NOT EXISTS workspace_preferences(name TEXT PRIMARY KEY,value TEXT);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_job(self, job: Job):
        with self.db() as db:
            db.execute("INSERT INTO snapshots(job_id,data,time) VALUES(?,?,?)",
                       (job.id, job.model_dump_json(), now()))
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job.id,)).fetchone()
            # A list observation must not erase a previously acquired full JD.
            current = Job.model_validate_json(row[0]) if row else None
            if current and not job.description and current.description:
                current.locator.update(job.locator)
                job = current
            db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?)", (job.id, job.model_dump_json()))

    def job(self, jid: str) -> Job:
        with self.db() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise KeyError(jid)
        return Job.model_validate_json(row[0])

    def jobs(self) -> list[Job]:
        with self.db() as db:
            return [Job.model_validate_json(r[0]) for r in db.execute("SELECT data FROM jobs ORDER BY id")]

    def session(self, sid: str) -> State:
        with self.db() as db:
            row = db.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        return State.model_validate_json(row[0]) if row else State()

    def conversation_title(self, sid):
        with self.db() as db:
            row = db.execute('SELECT title,title_source FROM conversation_meta WHERE id=?', (sid,)).fetchone()
        return dict(row) if row else None

    def save_preference(self, name, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO workspace_preferences VALUES(?,?)', (name, value))

    def preference(self, name, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM workspace_preferences WHERE name=?', (name,)).fetchone()
        return row[0] if row else default

    def name_conversation(self, sid, title, source='manual', only_fallback=False):
        title = ' '.join(title.split())[:40] or '新会话'
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT title_source FROM conversation_meta WHERE id=?', (sid,)).fetchone()
            if only_fallback and old and old[0] != 'fallback':
                return
            db.execute('''INSERT INTO conversation_meta VALUES(?,?,?,0,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,title_source=excluded.title_source,
                updated=excluded.updated''', (sid, title, source, now()))

    def list_conversations(self, include_archived=False):
        with self.db() as db:
            rows = db.execute('''SELECT ids.id,s.data,m.title,m.title_source,COALESCE(m.archived,0) archived,
                COALESCE((SELECT MAX(time) FROM runs r WHERE r.session_id=ids.id),m.updated,'') updated
                FROM (SELECT id FROM sessions UNION SELECT id FROM conversation_meta) ids
                LEFT JOIN sessions s ON s.id=ids.id LEFT JOIN conversation_meta m ON m.id=ids.id
                ORDER BY updated DESC''').fetchall()
        result = []
        for row in rows:
            if row['archived'] and not include_archived:
                continue
            if row['title_source'] == 'draft' and not row['data']:
                continue
            state = State.model_validate_json(row['data']) if row['data'] else State()
            first = next((m['content'] for m in state.history if m['role'] == 'user'), '')
            title = row['title'] or (' '.join(first.split())[:24] if first else '新会话')
            result.append({'id': row['id'], 'title': title, 'archived': bool(row['archived']),
                           'first_message': first, 'updated': row['updated']})
        return result

    def archive_conversation(self, sid, archived=True):
        if not self.conversation_title(sid):
            first = next((c['title'] for c in self.list_conversations(True) if c['id'] == sid), '新会话')
            self.name_conversation(sid, first, 'fallback')
        with self.db() as db:
            db.execute('UPDATE conversation_meta SET archived=?,updated=? WHERE id=?', (int(archived), now(), sid))

    def begin(self, sid: str, message: str) -> tuple[str, State]:
        rid = uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
            state = State.model_validate_json(row[0]) if row else State()
            state.version += 1
            state.pending = message
            state.unapplied_messages.append(message)
            state.history.append({"role": "user", "content": message})
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)",
                       (sid, state.version, state.model_dump_json()))
            db.execute("UPDATE runs SET status='superseded' WHERE session_id=? AND status IN ('running','paused')",
                       (sid,))
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                       (rid, sid, state.version, "running", None, now()))
        return rid, state

    def check(self, rid: str):
        with self.db() as db:
            row = db.execute("SELECT r.status,r.version,s.version FROM runs r JOIN sessions s "
                             "ON r.session_id=s.id WHERE r.id=?", (rid,)).fetchone()
        if not row or row[0] not in ("running", "paused") or row[1] != row[2]:
            raise StaleRun("run is cancelled or belongs to an old requirement version")

    def commit(self, rid: str, cp: Checkpoint, status="running", resume=False):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT session_id,version,status FROM runs WHERE id=?", (rid,)).fetchone()
            if not row or row[2] not in ("running", "paused"):
                raise StaleRun()
            if row[2] == "paused" and not resume:
                status = "paused"
            changed = db.execute("UPDATE sessions SET data=? WHERE id=? AND version=?",
                                 (cp.state.model_dump_json(), row[0], row[1])).rowcount
            if not changed:
                raise StaleRun()
            db.execute("UPDATE runs SET checkpoint=?,status=? WHERE id=?",
                       (cp.model_dump_json(), status, rid))

    def run(self, rid: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return dict(row)

    def pause(self, sid: str):
        # Pause takes effect at the next step boundary; a new message advances the version.
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE runs SET status='paused' WHERE session_id=? AND status='running'", (sid,))

    def mark_failed(self, rid: str):
        with self.db() as db:
            db.execute("UPDATE runs SET status='failed' WHERE id=? AND status='running'", (rid,))

    def is_paused(self, rid: str) -> bool:
        return self.run(rid)["status"] == "paused"

    def event(self, rid: str, kind: str, data: dict):
        with self.db() as db:
            db.execute("INSERT INTO events(run_id,kind,data,time) VALUES(?,?,?,?)",
                       (rid, kind, json.dumps(data, ensure_ascii=False), now()))

    def events(self, rid: str, kinds=None) -> list[dict]:
        with self.db() as db:
            suffix = ' AND kind IN (' + ','.join('?' for _ in kinds) + ')' if kinds else ''
            return [dict(r) | {"data": json.loads(r["data"])} for r in db.execute(
                "SELECT kind,data,time FROM events WHERE run_id=?" + suffix + " ORDER BY n", (rid, *(kinds or ())))]

    def reserve(self, rid: str, cost: float, model: str, cap: float, tokens=0) -> str:
        cid = uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges").fetchone()[0]
            if total + cost > cap:
                raise BudgetExceeded("workspace CNY budget exhausted")
            db.execute("INSERT INTO charges VALUES(?,?,?,?,?,?,?,?)",
                       (cid, rid, cost, None, tokens, model, "reserved", now()))
        return cid

    def settle(self, cid: str, cost: float, tokens: int):
        with self.db() as db:
            db.execute("UPDATE charges SET actual=?,tokens=?,status='settled' WHERE id=?", (cost, tokens, cid))

    def usage(self, rid: str | None = None) -> dict:
        query = "SELECT COALESCE(SUM(COALESCE(actual,reserved)),0),COALESCE(SUM(tokens),0),COUNT(*) FROM charges"
        with self.db() as db:
            row = db.execute(query + (" WHERE run_id=?" if rid else ""), (rid,) if rid else ()).fetchone()
        return {"cost_cny": row[0], "tokens": row[1], "model_calls": row[2]}

    def import_model_charges(self, source_path, model_prefix):
        """Keep legacy charges, copying matching IDs once into a model ledger."""
        source = Path(source_path)
        if not source.exists() or source.resolve() == Path(self.path).resolve():
            return
        with sqlite3.connect(source) as db:
            rows = db.execute('SELECT * FROM charges WHERE model LIKE ?', (model_prefix + '%',)).fetchall()
        with self.db() as db:
            db.executemany('''INSERT INTO charges VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET actual=excluded.actual,tokens=excluded.tokens,status=excluded.status
                WHERE charges.status='reserved' AND excluded.status='settled' ''', rows)
