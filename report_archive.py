"""报告版本存档：旧稿快照、回执与提交记录的持久化。

仅负责 report_versions 表的读写，不含业务规则（见 version_rules.py）。
所有方法在调用方事务内执行，由 app.py 的服务层统一提交。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import version_rules


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class ReportArchive:
    """report_versions 的存档操作。"""

    @staticmethod
    def init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS report_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id),
                version_no INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT,
                supersede_reason TEXT,
                payload_json TEXT NOT NULL,
                serious INTEGER NOT NULL,
                fatal INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                receipt TEXT,
                submitted_at TEXT,
                submitted_by TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(report_id, version_no)
            );
            """
        )

    @staticmethod
    def next_version_no(conn: sqlite3.Connection, report_id: int) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM report_versions WHERE report_id=?",
            (report_id,),
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def create(conn: sqlite3.Connection, report_id: int, kind: str, reason: str | None,
               payload: dict[str, Any], serious: bool, fatal: bool, due_at: str,
               actor: str, now: str) -> dict[str, Any]:
        version_no = ReportArchive.next_version_no(conn, report_id)
        cursor = conn.execute(
            """INSERT INTO report_versions(report_id,version_no,kind,status,reason,payload_json,
               serious,fatal,due_at,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, version_no, kind, version_rules.VERSION_PENDING, reason,
             json.dumps(payload, ensure_ascii=False, sort_keys=True),
             int(serious), int(fatal), due_at, actor, now),
        )
        return dict(conn.execute("SELECT * FROM report_versions WHERE id=?", (cursor.lastrowid,)).fetchone())

    @staticmethod
    def pending(conn: sqlite3.Connection, report_id: int) -> dict[str, Any] | None:
        return _as_dict(conn.execute(
            "SELECT * FROM report_versions WHERE report_id=? AND status=? ORDER BY version_no DESC LIMIT 1",
            (report_id, version_rules.VERSION_PENDING),
        ).fetchone())

    @staticmethod
    def latest_submitted(conn: sqlite3.Connection, report_id: int) -> dict[str, Any] | None:
        return _as_dict(conn.execute(
            "SELECT * FROM report_versions WHERE report_id=? AND status=? ORDER BY version_no DESC LIMIT 1",
            (report_id, version_rules.VERSION_SUBMITTED),
        ).fetchone())

    @staticmethod
    def versions(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
        """按版本号顺序返回全部版本，并标记当前有效版本。"""
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM report_versions WHERE report_id=? ORDER BY version_no", (report_id,))]
        current = ReportArchive.latest_submitted(conn, report_id)
        current_id = current["id"] if current else None
        for row in rows:
            row["is_current"] = row["id"] == current_id
        return rows

    @staticmethod
    def supersede(conn: sqlite3.Connection, version_id: int, reason: str) -> None:
        conn.execute(
            "UPDATE report_versions SET status=?,supersede_reason=? WHERE id=? AND status=?",
            (version_rules.VERSION_SUPERSEDED, reason, version_id, version_rules.VERSION_PENDING),
        )

    @staticmethod
    def archive_submitted(conn: sqlite3.Connection, report_id: int, keep_version_id: int) -> None:
        """新版本生效后，把历史已提交版本归档为旧稿。"""
        conn.execute(
            "UPDATE report_versions SET status=? WHERE report_id=? AND status=? AND id!=?",
            (version_rules.VERSION_ARCHIVED, report_id, version_rules.VERSION_SUBMITTED, keep_version_id),
        )

    @staticmethod
    def mark_submitted(conn: sqlite3.Connection, version_id: int, submitted_at: str,
                       submitted_by: str, receipt: str) -> None:
        conn.execute(
            "UPDATE report_versions SET status=?,submitted_at=?,submitted_by=?,receipt=? WHERE id=?",
            (version_rules.VERSION_SUBMITTED, submitted_at, submitted_by, receipt, version_id),
        )

    @staticmethod
    def recalc_pending_deadline(conn: sqlite3.Connection, report_id: int, due_at: str) -> None:
        """严重性/死亡转归变化后，同步重算在途版本的期限。"""
        conn.execute(
            "UPDATE report_versions SET due_at=? WHERE report_id=? AND status=?",
            (due_at, report_id, version_rules.VERSION_PENDING),
        )
