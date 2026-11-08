import sqlite3
from contextlib import contextmanager
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, brand TEXT, service TEXT,
  mentee TEXT, mentee_nickname TEXT, goal TEXT, item TEXT, critical_question TEXT,
  status TEXT NOT NULL DEFAULT '진행 중', phase INTEGER NOT NULL DEFAULT 1, org TEXT,
  planned_sessions INTEGER NOT NULL DEFAULT 3, planned_hours REAL NOT NULL DEFAULT 6,
  start_date TEXT, end_date TEXT, notes TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')), completed_at TEXT);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT, text TEXT NOT NULL, source TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS checklist (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL,
  text TEXT NOT NULL, due TEXT, done INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL DEFAULT '직접', approved INTEGER NOT NULL DEFAULT 1, sort INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')), done_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  no INTEGER NOT NULL, date TEXT, start_time TEXT, end_time TEXT, place TEXT,
  method TEXT DEFAULT '오프라인', topic TEXT, status TEXT NOT NULL DEFAULT '예정',
  photo1 TEXT, photo2 TEXT, status_note TEXT, discussion TEXT, outputs TEXT, next_tasks TEXT,
  ev_diligence TEXT, ev_growth TEXT, ev_excellence TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')), UNIQUE(project_id, no));
CREATE TABLE IF NOT EXISTS inputs (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  kind TEXT, media TEXT, filename TEXT, stored_path TEXT, text TEXT, ai_result TEXT,
  state TEXT NOT NULL DEFAULT '대기', created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS input_files (
  id INTEGER PRIMARY KEY, input_id INTEGER NOT NULL REFERENCES inputs(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, stored_path TEXT NOT NULL, media TEXT, mime TEXT, size INTEGER,
  extracted_text TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS session_digests (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  input_ids TEXT, result TEXT, feedback TEXT, state TEXT NOT NULL DEFAULT '정리됨', applied TEXT, report_id INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS intakes (
  id INTEGER PRIMARY KEY, created_at TEXT DEFAULT (datetime('now','localtime')),
  text TEXT, folder TEXT, files TEXT, result TEXT, project_id INTEGER);
CREATE TABLE IF NOT EXISTS ai_usage (
  id INTEGER PRIMARY KEY, at TEXT DEFAULT (datetime('now','localtime')), purpose TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, project_id INTEGER);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT, text TEXT NOT NULL, owner TEXT, due TEXT, priority TEXT DEFAULT '보통',
  status TEXT NOT NULL DEFAULT '미결', decision TEXT, decided_by TEXT, decided_at TEXT, basis TEXT,
  origin TEXT NOT NULL DEFAULT '직접', created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS milestones (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  date TEXT NOT NULL, label TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memos (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL, body TEXT, sort INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form INTEGER NOT NULL, session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  body TEXT, char_count INTEGER, version INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime')));
"""

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

@contextmanager
def get_db():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()

MIGRATIONS = [  # 기존 DB에 나중에 추가된 열 (이미 있으면 건너뜀)
    "ALTER TABLE projects ADD COLUMN plan_confirmed INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE reports ADD COLUMN feedback TEXT",
    "ALTER TABLE reports ADD COLUMN origin TEXT",
    "ALTER TABLE reports ADD COLUMN meta TEXT",
    "ALTER TABLE projects ADD COLUMN channel TEXT",
]

def init_db():
    with get_db() as con:
        con.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                con.execute(sql)
            except sqlite3.OperationalError:
                pass

def get_setting(key, default=None):
    with get_db() as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_setting(key, value):
    with get_db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
