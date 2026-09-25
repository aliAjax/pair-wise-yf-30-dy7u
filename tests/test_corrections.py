import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, PharmacovigilanceService, iso, parse_time, utcnow
from pv_rules import (
    VERSION_DRAFT,
    VERSION_SUBMITTED,
    VERSION_SUPERSEDED,
    VERSION_VOID,
)


class ReportCorrectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = PharmacovigilanceService(Path(self.tmp.name) / "test.db")
        self.now = utcnow().replace(microsecond=0)
        self.case = self.svc.create_case(
            "reporter-a", "reporter", "CN",
            {"patient_ref": "P-9", "region": "CN", "product": "DrugA", "event_term": "皮疹",
             "source": "email", "dedupe_key": "corr-1", "received_at": iso(self.now)},
        )["case"]

    def tearDown(self):
        self.tmp.cleanup()

    def _report(self, country="CN"):
        return self.svc.create_report(
            self.case["id"], "lead-cn", "regional_lead", "CN",
            {"country": country, "content": {"narrative": "初稿"}},
        )

    def _submit(self, report_id, actor="lead-cn", receipt=None, **extra):
        body = {"submitted_at": iso(self.now + timedelta(days=1))}
        if receipt is not None:
            body["receipt"] = receipt
        body.update(extra)
        return self.svc.submit_report(report_id, actor, "regional_lead", "CN", body)["report"]

    def _correct(self, report_id, reason="监管退回：补充死亡信息", **extra):
        body = {"reason": reason, "content": {"narrative": "更正稿：已死亡"}}
        body.update(extra)
        return self.svc.correct_report(report_id, "lead-cn", "regional_lead", "CN", body)

    def test_correction_keeps_reason_old_draft_receipt_and_submitter(self):
        report = self._report()
        submitted = self._submit(report["id"], receipt={"gateway": "NMPA", "ack": "ACK-1"})
        self.assertEqual(submitted["effective_version"], 1)

        result = self._correct(report["id"])
        draft = result["version"]
        self.assertEqual(draft["version"], 2)
        self.assertEqual(draft["status"], VERSION_DRAFT)
        self.assertEqual(draft["reason"], "监管退回：补充死亡信息")
        # 旧稿、旧回执、原提交人完整保留
        self.assertEqual(draft["previous_content"], {"narrative": "初稿"})
        self.assertEqual(draft["previous_receipt"], {"gateway": "NMPA", "ack": "ACK-1"})
        self.assertEqual(draft["previous_submitted_by"], "lead-cn")
        self.assertIsNotNone(draft["previous_submitted_at"])

        # 未提交更正期间，当前有效版本仍是 v1
        detail = self.svc.get_case(self.case["id"], "regional_lead", "CN")
        item = next(r for r in detail["reports"] if r["id"] == report["id"])
        self.assertEqual(item["effective_version"], 1)
        self.assertEqual(item["pending_version"], 2)
        self.assertEqual(item["version_state"], "correction_pending")
        versions = item["versions"]
        self.assertEqual([v["version"] for v in versions], [1, 2])

        resubmitted = self.svc.submit_report(
            report["id"], "lead-cn2", "regional_lead", "CN",
            {"reason": "死亡转归补录后重提", "receipt": {"gateway": "NMPA", "ack": "ACK-2"}},
        )["report"]
        self.assertEqual(resubmitted["effective_version"], 2)
        self.assertEqual(resubmitted["current_version"], 2)
        self.assertEqual(resubmitted["submitted_by"], "lead-cn2")
        detail = self.svc.get_case(self.case["id"], "regional_lead", "CN")
        v1, v2 = detail["reports"][0]["versions"]
        self.assertEqual(v1["status"], VERSION_SUPERSEDED)
        self.assertEqual(v2["status"], VERSION_SUBMITTED)
        self.assertEqual(v2["receipt"], {"gateway": "NMPA", "ack": "ACK-2"})

    def test_resubmit_without_reason_is_rejected(self):
        report = self._report()
        self._submit(report["id"])
        self._correct(report["id"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.submit_report(report["id"], "lead-cn", "regional_lead", "CN", {})
        self.assertEqual(ctx.exception.code, "correction_reason_required")

    def test_correcting_before_submit_is_rejected(self):
        report = self._report()
        with self.assertRaises(ApiError) as ctx:
            self._correct(report["id"])
        self.assertEqual(ctx.exception.code, "report_not_submitted")

    def test_correction_without_reason_is_rejected(self):
        report = self._report()
        self._submit(report["id"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.correct_report(
                report["id"], "lead-cn", "regional_lead", "CN",
                {"content": {"narrative": "x"}},
            )
        self.assertEqual(ctx.exception.code, "correction_reason_required")

    def test_pending_correction_blocks_another_correction_without_replace_reason(self):
        report = self._report()
        self._submit(report["id"])
        self._correct(report["id"], reason="第一次更正")
        with self.assertRaises(ApiError) as ctx:
            self._correct(report["id"], reason="第二次更正")
        self.assertEqual(ctx.exception.code, "correction_already_pending")

        replaced = self._correct(
            report["id"], reason="第二次更正", replace_reason="首份更正稿信息填错，作废重开",
        )
        self.assertEqual(replaced["version"]["version"], 3)
        detail = self.svc.get_case(self.case["id"], "regional_lead", "CN")
        statuses = {v["version"]: v["status"] for v in detail["reports"][0]["versions"]}
        # v3 尚未提交，v1 仍是当前有效版本（submitted）；v2 作废、v3 草稿
        self.assertEqual(statuses[1], VERSION_SUBMITTED)
        self.assertEqual(statuses[2], VERSION_VOID)
        self.assertEqual(statuses[3], VERSION_DRAFT)
        self.assertEqual(detail["reports"][0]["pending_version"], 3)

    def test_other_region_cannot_operate(self):
        report = self._report()
        self._submit(report["id"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.correct_report(report["id"], "lead-us", "regional_lead", "US",
                                    {"reason": "越权更正"})
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.submit_report(report["id"], "lead-us", "regional_lead", "US", {})
        self.assertEqual(ctx.exception.status, 403)

    def test_duplicate_country_report_still_rejected(self):
        self._report("JP")
        with self.assertRaises(ApiError) as ctx:
            self._report("JP")
        self.assertEqual(ctx.exception.code, "report_exists")

    def test_severity_change_recomputes_country_deadline_from_change_time(self):
        # 非严重 90 天报告先提交
        report = self._report()
        self._submit(report["id"], receipt={"ack": "A"})
        v1_original_due = self.now + timedelta(days=90)

        # 10 天后医学审核裁定为死亡：按变更时间重算 7 天
        change_at = (self.now + timedelta(days=10)).replace(microsecond=0)
        review = self.svc.medical_review(
            self.case["id"], "reviewer-1", "medical_reviewer",
            {"expected_revision": 1, "serious": True, "fatal": True,
             "causality": "possibly_related", "rationale": "死亡证明已核验",
             "received_at": iso(change_at)},
        )
        # 当时没有未提交稿，重算只落在报告头，不改已提交存档版本
        self.assertEqual(review["deadlines_recomputed"], [])

        detail = self.svc.get_case(self.case["id"], "global_admin", "")
        item = detail["reports"][0]
        self.assertEqual(parse_time(item["due_at"]), change_at + timedelta(days=7))
        self.assertEqual(item["due_changed_at"], iso(change_at))
        # 已提交的 v1 是不可变存档，期限保持原 90 天
        self.assertEqual(parse_time(item["versions"][0]["due_at"]), v1_original_due)

        # 再发起更正，新稿继承重算后的 7 天期限
        corrected = self._correct(report["id"])
        self.assertEqual(parse_time(corrected["version"]["due_at"]), change_at + timedelta(days=7))
        self.assertEqual(corrected["version"]["due_changed_at"], iso(change_at))

    def test_deadline_recompute_applies_to_pending_correction_and_superseded_kept(self):
        report = self._report()
        self._submit(report["id"], receipt={"ack": "A"})
        v1_due = self.now + timedelta(days=90)
        self._correct(report["id"])
        change_at = (self.now + timedelta(days=5)).replace(microsecond=0)
        review = self.svc.medical_review(
            self.case["id"], "reviewer-1", "medical_reviewer",
            {"expected_revision": 1, "serious": True, "fatal": False,
             "causality": "related", "rationale": "住院记录", "received_at": iso(change_at)},
        )
        # 未提交更正稿被重算为 15 天
        self.assertEqual(len(review["deadlines_recomputed"]), 1)
        self.assertEqual(parse_time(review["deadlines_recomputed"][0]["due_at"]), change_at + timedelta(days=15))
        detail = self.svc.get_case(self.case["id"], "global_admin", "")
        v1, v2 = detail["reports"][0]["versions"]
        self.assertEqual(parse_time(v2["due_at"]), change_at + timedelta(days=15))
        # v1 已提交，是不可变存档，期限保持原 90 天
        self.assertEqual(parse_time(v1["due_at"]), v1_due)
        self.assertEqual(parse_time(detail["reports"][0]["due_at"]), change_at + timedelta(days=15))

        # 重提后 v1 被替代；再次变更期限时，v2（已提交）与 v1（已替代）存档期限都保持不变
        self.svc.submit_report(
            report["id"], "lead-cn", "regional_lead", "CN",
            {"reason": "严重升级重提", "submitted_at": iso(change_at + timedelta(days=1))},
        )
        second_change = (self.now + timedelta(days=8)).replace(microsecond=0)
        self.svc.medical_review(
            self.case["id"], "reviewer-1", "medical_reviewer",
            {"expected_revision": 2, "serious": True, "fatal": True,
             "causality": "related", "rationale": "死亡证明", "received_at": iso(second_change)},
        )
        detail = self.svc.get_case(self.case["id"], "global_admin", "")
        v1, v2 = detail["reports"][0]["versions"]
        self.assertEqual(v1["status"], VERSION_SUPERSEDED)
        self.assertEqual(parse_time(v1["due_at"]), v1_due)
        self.assertEqual(parse_time(v2["due_at"]), change_at + timedelta(days=15))
        # 报告头仍展示按最新死亡变更重算的 7 天期限，供下次更正继承
        self.assertEqual(parse_time(detail["reports"][0]["due_at"]), second_change + timedelta(days=7))


if __name__ == "__main__":
    unittest.main()
