import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, PharmacovigilanceService, iso, utcnow


class ReportCorrectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = PharmacovigilanceService(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _submitted_report(self, serious=True, fatal=False, dedupe="corr-1"):
        case = self.svc.create_case(
            "reporter-a", "reporter", "CN",
            {"patient_ref": "P-1", "region": "CN", "product": "DrugA", "event_term": "肝损伤",
             "source": "email", "dedupe_key": dedupe, "received_at": iso(utcnow()),
             "serious": serious, "fatal": fatal},
        )["case"]
        report = self.svc.create_report(case["id"], "lead-cn", "regional_lead", "CN", {"country": "CN"})
        self.svc.submit_report(report["id"], "lead-cn", "regional_lead", "CN", {"receipt": "RCPT-INIT-1"})
        return case, report

    def _report_with_versions(self, case_id, country="CN"):
        detail = self.svc.get_case(case_id, "global_admin", "")
        report = next(r for r in detail["reports"] if r["country"] == country)
        return report, report["versions"]

    def test_correction_archive_and_resubmit(self):
        case, report = self._submitted_report()
        created = self.svc.create_correction(
            report["id"], "lead-cn", "regional_lead", "CN", {"reason": "监管退回：补录死亡转归"})
        self.assertEqual(created["version"]["version_no"], 2)
        self.assertEqual(created["version"]["kind"], "correction")
        self.assertEqual(created["report"]["status"], "correction_pending")

        # 有未提交更正时，提交必须说明原因
        with self.assertRaises(ApiError) as ctx:
            self.svc.submit_report(report["id"], "lead-cn", "regional_lead", "CN", {})
        self.assertEqual(ctx.exception.code, "reason_required")

        done = self.svc.submit_report(
            report["id"], "lead-cn", "regional_lead", "CN",
            {"reason": "按监管意见补录死亡转归", "receipt": "RCPT-DEATH-2"})
        self.assertEqual(done["report"]["status"], "submitted")
        self.assertEqual(done["version"]["receipt"], "RCPT-DEATH-2")

        report_row, versions = self._report_with_versions(case["id"])
        self.assertEqual([v["version_no"] for v in versions], [1, 2])
        v1, v2 = versions
        # 旧稿、回执、提交人完整保留
        self.assertEqual(v1["status"], "archived")
        self.assertFalse(v1["is_current"])
        self.assertEqual(v1["receipt"], "RCPT-INIT-1")
        self.assertEqual(v1["submitted_by"], "lead-cn")
        self.assertIn("case_no", v1["payload_json"])
        # 当前有效版本为更正版，保留更正原因
        self.assertEqual(v2["status"], "submitted")
        self.assertTrue(v2["is_current"])
        self.assertEqual(v2["reason"], "监管退回：补录死亡转归")
        self.assertEqual(v2["submitted_by"], "lead-cn")
        self.assertEqual(report_row["status"], "submitted")
        # 版本列表接口与详情一致
        self.assertEqual(len(self.svc.list_versions(report["id"], "global_admin", "")), 2)

    def test_pending_correction_requires_supersede_reason(self):
        case, report = self._submitted_report(dedupe="corr-2")
        self.svc.create_correction(report["id"], "lead-cn", "regional_lead", "CN", {"reason": "第一次更正"})
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_correction(report["id"], "lead-cn", "regional_lead", "CN", {"reason": "再次更正"})
        self.assertEqual(ctx.exception.code, "pending_correction_exists")
        created = self.svc.create_correction(
            report["id"], "lead-cn", "regional_lead", "CN",
            {"reason": "再次更正", "supersede_reason": "上一稿数据有误"})
        self.assertEqual(created["version"]["version_no"], 3)
        _, versions = self._report_with_versions(case["id"])
        self.assertEqual([v["status"] for v in versions], ["submitted", "superseded", "pending"])
        self.assertEqual(versions[1]["supersede_reason"], "上一稿数据有误")

    def test_correction_requires_submitted_report_and_reason(self):
        case = self.svc.create_case(
            "reporter-a", "reporter", "CN",
            {"patient_ref": "P-1", "region": "CN", "product": "DrugA", "event_term": "肝损伤",
             "source": "email", "dedupe_key": "corr-3", "received_at": iso(utcnow()), "serious": True},
        )["case"]
        report = self.svc.create_report(case["id"], "lead-cn", "regional_lead", "CN", {"country": "CN"})
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_correction(report["id"], "lead-cn", "regional_lead", "CN", {"reason": "补充信息"})
        self.assertEqual(ctx.exception.code, "report_not_submitted")
        self.svc.submit_report(report["id"], "lead-cn", "regional_lead", "CN", {})
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_correction(report["id"], "lead-cn", "regional_lead", "CN", {"reason": "  "})
        self.assertEqual(ctx.exception.code, "reason_required")

    def test_severity_change_recalculates_country_deadline(self):
        case, report = self._submitted_report(serious=True, fatal=False, dedupe="corr-4")
        # 在途更正的期限也应随严重性变化一并重算
        self.svc.create_correction(report["id"], "lead-cn", "regional_lead", "CN", {"reason": "补充信息"})
        change_at = utcnow() + timedelta(hours=3)
        revision = self.svc.get_case(case["id"], "global_admin", "")["case"]["revision"]
        self.svc.medical_review(
            case["id"], "reviewer-1", "medical_reviewer",
            {"expected_revision": revision, "serious": True, "fatal": True,
             "causality": "related", "rationale": "死亡证明已核验", "received_at": iso(change_at)})
        report_row, versions = self._report_with_versions(case["id"])
        expected = iso(change_at + timedelta(days=7))
        self.assertEqual(report_row["due_at"], expected)
        pending = next(v for v in versions if v["status"] == "pending")
        self.assertEqual(pending["due_at"], expected)
        detail = self.svc.get_case(case["id"], "global_admin", "")
        self.assertIn("report_deadline_recalculated", [a["action"] for a in detail["audit"]])

    def test_other_region_cannot_operate(self):
        case, report = self._submitted_report(dedupe="corr-5")
        with self.assertRaises(ApiError) as ctx:
            self.svc.create_correction(report["id"], "lead-us", "regional_lead", "US", {"reason": "越区更正"})
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.submit_report(report["id"], "lead-us", "regional_lead", "US", {})
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.list_versions(report["id"], "reporter", "US")
        self.assertEqual(ctx.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
