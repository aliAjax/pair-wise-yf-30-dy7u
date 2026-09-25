"""报告版本规则：期限计算、版本流转校验与回执编号。

只包含纯领域规则，不依赖数据库与 HTTP 层，可单独维护与测试。
存档持久化见 report_archive.py，接口与页面编排见 app.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# 版本类型
KIND_ORIGINAL = "original"
KIND_CORRECTION = "correction"

# 版本状态
VERSION_PENDING = "pending"        # 已创建、未提交
VERSION_SUBMITTED = "submitted"    # 当前有效版本
VERSION_ARCHIVED = "archived"      # 历史有效版本（旧稿存档）
VERSION_SUPERSEDED = "superseded"  # 未提交即被新更正取代

# 报告状态
REPORT_PENDING = "pending"
REPORT_SUBMITTED = "submitted"
REPORT_CORRECTION_PENDING = "correction_pending"
REPORT_OVERDUE = "overdue"

SERIOUS_DAYS = 15
FATAL_DAYS = 7
NON_SERIOUS_DAYS = 90


class RuleViolation(Exception):
    """版本规则被违反，由服务层转换为 ApiError。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def report_deadline(changed_at: datetime, serious: bool, fatal: bool) -> datetime:
    """按变更时间计算该国报告期限：死亡 7 天、严重 15 天、非严重 90 天。"""
    if serious:
        return changed_at + timedelta(days=FATAL_DAYS if fatal else SERIOUS_DAYS)
    return changed_at + timedelta(days=NON_SERIOUS_DAYS)


def make_receipt(report_id: int, version_no: int, when: datetime) -> str:
    """监管回执编号；网关未返回回执时由系统生成。"""
    return f"RCPT-{report_id:06d}-V{version_no}-{when:%Y%m%dT%H%M%SZ}"


def ensure_may_correct(has_submitted_version: bool) -> None:
    if not has_submitted_version:
        raise RuleViolation(409, "report_not_submitted", "报告尚未提交，无需更正，请直接提交首版")


def ensure_correction_reason(reason: str) -> None:
    if not reason:
        raise RuleViolation(400, "reason_required", "发起更正必须填写更正原因")


def ensure_supersede_reason(pending_exists: bool, supersede_reason: str) -> None:
    if pending_exists and not supersede_reason:
        raise RuleViolation(409, "pending_correction_exists", "该国已有未提交更正，再次发起须用 supersede_reason 说明原因")


def ensure_submission_reason(kind: str, reason: str) -> None:
    if kind == KIND_CORRECTION and not reason:
        raise RuleViolation(400, "reason_required", "提交更正版本必须说明原因")


def snapshot_payload(case: dict[str, Any], report: dict[str, Any], version_no: int,
                     kind: str, reason: str | None, due_at: str) -> dict[str, Any]:
    """版本旧稿快照：存档该版本生效时的案例与报告要点。"""
    return {
        "case_no": case["case_no"],
        "patient_ref": case["patient_ref"],
        "region": case["region"],
        "product": case["product"],
        "event_term": case["event_term"],
        "serious": bool(case["serious"]),
        "fatal": bool(case["fatal"]),
        "causality": case["causality"],
        "case_revision": case["revision"],
        "country": report["country"],
        "version_no": version_no,
        "kind": kind,
        "reason": reason,
        "due_at": due_at,
    }
