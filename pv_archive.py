"""报告版本存档。

负责持久化每个分国家报告的版本链：每次更正都保留更正原因、
上一稿内容、旧回执、原提交人以及变更时间。只做存档读写，
规则判定在 pv_rules.py，编排与权限在 app.py。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

REPORT_VERSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    content_json TEXT NOT NULL,
    reason TEXT,
    previous_version_id INTEGER,
    previous_content_json TEXT,
    previous_receipt_json TEXT,
    previous_submitted_by TEXT,
    previous_submitted_at TEXT,
    receipt_json TEXT,
    submitted_at TEXT,
    submitted_by TEXT,
    late INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    due_changed_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(report_id, version)
);
CREATE INDEX IF NOT EXISTS idx_report_versions_report ON report_versions(report_id);
"""


def migrate_reports(conn: sqlite3.Connection) -> None:
    """老库的 reports 表补充版本头字段，并把旧报告回填为 v1。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
    if "current_version" not in cols:
        conn.execute("ALTER TABLE reports ADD COLUMN current_version INTEGER")
    if "due_changed_at" not in cols:
        conn.execute("ALTER TABLE reports ADD COLUMN due_changed_at TEXT")
    # 版本表已由 REPORT_VERSIONS_SCHEMA 建好；旧报告（版本表无记录）回填为 v1
    legacy = conn.execute(
        """SELECT r.*,c.event_term,c.product,c.received_at FROM reports r
           JOIN cases c ON c.id=r.case_id
           WHERE NOT EXISTS (SELECT 1 FROM report_versions v WHERE v.report_id=r.id)"""
    ).fetchall()
    for row in legacy:
        submitted = row["status"] == "submitted"
        content = {
            "country": row["country"],
            "product": row["product"],
            "event_term": row["event_term"],
            "case_received_at": row["received_at"],
            "migrated_from_legacy": True,
        }
        insert_version(conn, {
            "report_id": row["id"],
            "version": 1,
            "status": "submitted" if submitted else "draft",
            "content_json": dumps(content),
            "receipt_json": dumps({"migrated": True}) if submitted else None,
            "submitted_at": row["submitted_at"] if submitted else None,
            "submitted_by": row["submitted_by"] if submitted else None,
            "late": row["late"] or 0,
            "due_at": row["due_at"],
            "created_by": row["submitted_by"] or "migration",
            "created_at": row["submitted_at"] or _utcnow_iso(),
        })
        if submitted:
            conn.execute("UPDATE reports SET current_version=1 WHERE id=?", (row["id"],))


def list_versions(conn: sqlite3.Connection, report_ids: list[int] | None = None) -> dict[int, list[dict[str, Any]]]:
    if report_ids is not None and not report_ids:
        return {}
    sql = "SELECT * FROM report_versions"
    args: list[Any] = []
    if report_ids is not None:
        sql += f" WHERE report_id IN ({','.join('?' for _ in report_ids)})"
        args.extend(report_ids)
    sql += " ORDER BY report_id, version"
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(sql, args):
        grouped.setdefault(row["report_id"], []).append(serialize(row))
    return grouped


def get_version(conn: sqlite3.Connection, version_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM report_versions WHERE id=?", (version_id,)).fetchone()
    return serialize(row) if row else None


def get_pending(conn: sqlite3.Connection, report_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM report_versions WHERE report_id=? AND status='draft' ORDER BY version DESC LIMIT 1",
        (report_id,),
    ).fetchone()


def insert_version(conn: sqlite3.Connection, fields: dict[str, Any]) -> int:
    """fields 键对应 report_versions 列；JSON 列由调用方先 dumps。"""
    columns = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO report_versions({columns}) VALUES({placeholders})",
        tuple(fields.values()),
    )
    return cur.lastrowid


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def serialize(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for column, key in (
        ("content_json", "content"),
        ("previous_content_json", "previous_content"),
        ("previous_receipt_json", "previous_receipt"),
        ("receipt_json", "receipt"),
    ):
        raw = data.pop(column)
        data[key] = json.loads(raw) if raw else None
    return data
