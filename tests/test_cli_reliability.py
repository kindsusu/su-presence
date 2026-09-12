import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# `tests` 패키지를 import 하려면 **저장소 루트**가 경로에 있어야 한다.
# 루트에서 실행할 때만 cwd 덕에 우연히 되던 것을 명시한다 — 설치된 스킬처럼
# 다른 위치에서 돌려도 같은 결과가 나와야 한다.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import seo_geo
import report
from tests.test_report import AUDIT


class TestUnifiedCli(unittest.TestCase):
    def test_audit_writes_report_and_preserves_previous_observation(self):
        data = copy.deepcopy(AUDIT)
        data["coverage"] = {"complete": True}
        with tempfile.TemporaryDirectory() as tmp, patch("crawl.build_report", return_value=data):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(seo_geo.main(["audit", "example.com", "--out", tmp]), 0)
                self.assertEqual(seo_geo.main(["audit", "example.com", "--out", tmp]), 0)
            root = Path(tmp) / "example.com"
            self.assertTrue((root / "report.html").is_file())
            self.assertEqual(len(list((root / "observations").glob("*.json"))), 1)

    def test_incomplete_audit_is_saved_but_not_successful(self):
        data = copy.deepcopy(AUDIT)
        data["coverage"] = {"complete": False, "reasons": ["page_limit"]}
        with tempfile.TemporaryDirectory() as tmp, patch("crawl.build_report", return_value=data):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(seo_geo.main(["audit", "example.com", "--out", tmp]), 2)
            self.assertTrue((Path(tmp) / "example.com" / "audit.json").is_file())

    def test_status_missing_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(seo_geo.main(["status", str(Path(tmp) / "audit.json")]), 2)
            self.assertEqual(json.loads(output.getvalue())["artifacts"]["audit"]["state"], "missing")


class TestReportEvidence(unittest.TestCase):
    def test_host_and_time_are_escaped_in_document_header(self):
        data = copy.deepcopy(AUDIT)
        data["target"]["host"] = '</title><script>alert(1)</script>'
        data["generated_at"] = '<img src=x onerror=alert(1)>'
        html = report.render(data, "ko")
        self.assertNotIn("<script>alert(1)", html)
        self.assertNotIn("<img src=x", html)

    def test_empty_audit_has_no_invented_strengths(self):
        data = copy.deepcopy(AUDIT)
        data["pages"] = []
        section = report.section_strengths(data, report.L["en"], lambda s: s, "en")
        self.assertNotIn("No noindex accident", section)
        self.assertIn("No strength confirmed", section)

    def test_scope_is_explicit_and_external_fonts_are_absent(self):
        data = copy.deepcopy(AUDIT)
        data["coverage"] = {"complete": False, "reasons": ["page_limit"], "queued_remaining": 6}
        html = report.render(data, "en")
        self.assertIn("Incomplete / unknown coverage", html)
        self.assertIn("page_limit", html)
        self.assertNotIn("fonts.googleapis.com", html)


if __name__ == "__main__":
    unittest.main()
