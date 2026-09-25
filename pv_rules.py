"""报告版本与期限规则。

只放纯规则，不依赖数据库：版本号、版本状态、当前有效版本判定，
以及严重性/死亡转归变化后的分国家期限重算。存档见 pv_archive.py，
页面/HTTP 操作见 app.py，三者分开维护。
"""
from __future__ import annotations

from datetime import datetime, timedelta

DEADLINE_DAYS_FATAL = 7
DEADLINE_DAYS_SERIOUS = 15
DEADLINE_DAYS_NON_SERIOUS = 90

# 版本状态：未提交稿 / 当前有效（已提交）/ 被新版本替代 / 未提交即作废
VERSION_DRAFT = "draft"
VERSION_SUBMITTED = "submitted"
VERSION_SUPERSEDED = "superseded"
VERSION_VOID = "void"

# 报告头汇总状态
REPORT_PENDING = "pending"
REPORT_SUBMITTED = "submitted"
REPORT_CORRECTION_PENDING = "correction_pending"


def report_deadline(received_at: datetime, serious: bool, fatal: bool) -> datetime:
    """按变更/接收时间计算该国期限：死亡 7 天、严重 15 天、非严重 90 天。"""
    if serious:
        return received_at + timedelta(days=DEADLINE_DAYS_FATAL if fatal else DEADLINE_DAYS_SERIOUS)
    return received_at + timedelta(days=DEADLINE_DAYS_NON_SERIOUS)


def severity_changed(before: tuple[bool, bool], after: tuple[bool, bool]) -> bool:
    return before != after


def next_version(versions: list) -> int:
    return max((v["version"] for v in versions), default=0) + 1


def effective_version(versions: list):
    """当前有效版本：版本号最大的已提交版本（未提交更正不影响其效力）。"""
    submitted = [v for v in versions if v["status"] == VERSION_SUBMITTED]
    return max(submitted, key=lambda v: v["version"], default=None)


def pending_version(versions: list):
    """未提交更正：版本号最大的草稿（初稿或更正稿）。"""
    drafts = [v for v in versions if v["status"] == VERSION_DRAFT]
    return max(drafts, key=lambda v: v["version"], default=None)


def report_state(versions: list) -> str:
    has_draft = pending_version(versions) is not None
    has_effective = effective_version(versions) is not None
    if has_draft and has_effective:
        return REPORT_CORRECTION_PENDING
    if has_draft:
        return REPORT_PENDING
    if has_effective:
        return REPORT_SUBMITTED
    return REPORT_PENDING
