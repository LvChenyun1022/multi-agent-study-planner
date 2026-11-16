# -*- coding: utf-8 -*-
"""SQLite 数据库：表结构与连接管理。

learning_events 表通过触发器在数据库层面强制
「只追加、不更新、不删除」的事件溯源纪律——
即使未来某个服务写错了代码，也无法静默篡改历史事件。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS students (
    student_id      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    academic_year   INTEGER NOT NULL,
    timezone        TEXT NOT NULL,
    learning_preferences TEXT NOT NULL DEFAULT '{}',
    available_time  TEXT NOT NULL DEFAULT '{}',
    profile_version INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    course_id   TEXT PRIMARY KEY,
    spec_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollments (
    student_id  TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    PRIMARY KEY (student_id, course_id)
);

CREATE TABLE IF NOT EXISTS learning_events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE NOT NULL,
    student_id      TEXT NOT NULL,
    course_id       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    knowledge_points TEXT NOT NULL,
    payload         TEXT NOT NULL,
    source_id       TEXT,
    source_type     TEXT,
    verified        INTEGER NOT NULL DEFAULT 1,
    corrects        TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_student_course
    ON learning_events (student_id, course_id, seq);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON learning_events
BEGIN
    SELECT RAISE(ABORT, 'learning_events 是只追加日志，禁止 UPDATE；请插入 corrects 纠正事件');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON learning_events
BEGIN
    SELECT RAISE(ABORT, 'learning_events 是只追加日志，禁止 DELETE；请插入 corrects 纠正事件');
END;

CREATE TABLE IF NOT EXISTS mastery_snapshots (
    student_id      TEXT NOT NULL,
    course_id       TEXT NOT NULL,
    knowledge_id    TEXT NOT NULL,
    mastery         REAL,
    effective_n     REAL NOT NULL,
    confidence      TEXT NOT NULL,
    n_evidence      INTEGER NOT NULL,
    last_updated    TEXT,
    profile_version INTEGER NOT NULL,
    evidence_version INTEGER NOT NULL,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (student_id, course_id, knowledge_id)
);
"""


class Database:
    """一个 SQLite 连接的最小封装；测试可用 ":memory:"。"""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(DDL)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
