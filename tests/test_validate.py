"""Repository contract validation regression tests."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import validate  # noqa: E402


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("SKILL.md", '---\nname: su-presence\ndescription: "Audit: sites"\n---\n# Start\n')
        self.write("en/SKILL.md", "---\nname: 'su-presence'\ndescription: 'Audit: sites'\n---\n# Start\n")
        self.write(".claude-plugin/plugin.json", json.dumps({"name": "su-presence", "version": "2.1.0"}))
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "su-presence", "plugins": [{"name": "su-presence", "version": "2.1.0"}]}))
        self.write("CHANGELOG.md", "# Changes\n\n## [Unreleased]\n\n## [2.1.0] — today\n")
        self.write("lanes/seo.md", "# Search\n")
        self.write("en/lanes/seo.md", "# Search\n")
        self.write("ops/measure.md", "# Measure\n")
        self.write("en/ops/measure.md", "# Measure\n")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def errors(self):
        return "\n".join(validate.validate(self.root))

    def test_clean_fixture_and_cli(self):
        self.write("README.md", "# A heading\n# A heading\n[second](#a-heading-1)\n[guide](lanes/seo.md)\n[web](https://example.com)\n````md\n[example](missing.md)\n````\n")
        self.assertEqual("", self.errors())
        result = subprocess.run([sys.executable, str(Path(validate.__file__)), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unquoted_colon_space_is_rejected_without_yaml_dependency(self):
        self.write("SKILL.md", "---\nname: su-presence\ndescription: Audit: sites\n---\n")
        self.assertIn("quote a value containing colon-space", self.errors())

    def test_name_and_description_contract(self):
        self.write("SKILL.md", '---\nname: Bad_Name\ndescription: "two\\nlines"\n---\n')
        result = self.errors()
        self.assertIn("name must be kebab-case", result)
        self.assertIn("description must be one line", result)
        self.write("SKILL.md", "---\nname: " + "a" * 65 + "\ndescription: " + "x" * 1025 + "\n---\n")
        result = self.errors()
        self.assertIn("at most 64 characters", result)
        self.assertIn("at most 1024 characters", result)

    def test_malformed_frontmatter_and_duplicate_key(self):
        self.write("SKILL.md", "---\nname: su-presence\nname: extra\ndescription:\n  nested: invalid\n---\n")
        result = self.errors()
        self.assertIn("duplicate frontmatter", result)
        self.assertIn("empty frontmatter", result)
        self.assertIn("expected flat key", result)

    def test_metadata_drift_and_changelog_version(self):
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "wrong", "plugins": [{"name": "wrong", "version": "2.2.0"}]}))
        result = self.errors()
        self.assertIn("name differs", result)
        self.assertIn("versions must match", result)
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "su-presence", "plugins": [{"name": "su-presence", "version": "2.1.0"}]}))
        self.write("CHANGELOG.md", "## [Unreleased]\n## [2.0.0]\n")
        self.assertIn("latest released version", self.errors())

    def test_broken_links_and_anchor_but_ignores_fenced_example(self):
        self.write("README.md", "[missing](absent.md)\n[anchor](lanes/seo.md#absent)\n```md\n[example](also-absent.md)\n```\n")
        result = self.errors()
        self.assertIn("missing link target: absent.md", result)
        self.assertIn("missing anchor", result)
        self.assertNotIn("also-absent", result)

    def test_inline_code_heading_anchor_and_bad_url(self):
        self.write("README.md", "# Use `collect`\n[good](#use-collect)\n[bad](http://[invalid)\n")
        result = self.errors()
        self.assertNotIn("missing anchor", result)
        self.assertIn("malformed link target", result)

    def test_missing_mirror(self):
        (self.root / "en/ops/measure.md").unlink()
        self.assertIn("missing English mirror", self.errors())

    def test_cli_fails_on_invalid_repository(self):
        self.write("SKILL.md", "---\nname: su-presence\ndescription: Audit: sites\n---\n")
        result = subprocess.run([sys.executable, str(Path(validate.__file__)), "--root", str(self.root)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)


TODAY = date(2026, 11, 12)
CRAWLER_TABLE = (
    "# Crawlers\n\n"
    "| Vendor | Token | {header} |\n"
    "|---|---|---|\n"
    "| **Alpha** | `AlphaBot` | {first} |\n"
    "| **Beta** | `BetaBot` | {second} |\n\n"
    "```text\n| not | a | table |\n|---|---|---|\n| x | y | bad-date |\n```\n"
)


class CrawlerFreshnessTests(unittest.TestCase):
    """확인일 열은 형식 오류만 실패시키고, 낡은 행은 기본적으로 경고다 — 오늘 날짜는 주입한다."""

    setUp = ValidationTests.setUp
    write = ValidationTests.write

    def tables(self, first="2026-11-01", second="2026-10-20", ko_header="확인일", en_header="Verified"):
        self.write("ops/crawlers.md", CRAWLER_TABLE.format(header=ko_header, first=first, second=second))
        self.write("en/ops/crawlers.md", CRAWLER_TABLE.format(header=en_header, first=first, second=second))

    def run_validate(self, today=TODAY, crawler_max_age=None):
        warnings = []
        errors = validate.validate(self.root, warnings, today=today, crawler_max_age=crawler_max_age)
        return "\n".join(errors), "\n".join(warnings)

    def test_fresh_rows_pass_without_warnings(self):
        self.tables()
        self.assertEqual(("", ""), self.run_validate())

    def test_stale_row_warns_by_default_and_fails_with_max_age(self):
        self.tables(second="2026-06-01")  # 164 days before TODAY
        errors, warnings = self.run_validate()
        self.assertEqual("", errors)
        self.assertIn("ops/crawlers.md:6: crawler table row verified 2026-06-01 is 164 days old", warnings)
        self.assertIn("en/ops/crawlers.md:6:", warnings)
        self.assertNotIn(":5:", warnings)
        errors, warnings = self.run_validate(crawler_max_age=120)
        self.assertIn("164 days old (limit 120)", errors)
        self.assertEqual("", warnings)
        errors, _ = self.run_validate(crawler_max_age=200)
        self.assertEqual("", errors)

    def test_default_run_never_fails_because_time_passed(self):
        self.tables()
        errors, warnings = self.run_validate(today=date(2100, 1, 1))
        self.assertEqual("", errors)
        self.assertIn("days old", warnings)

    def test_repository_tables_only_warn_when_far_in_the_future(self):
        repo = Path(validate.__file__).resolve().parents[1]
        errors, warnings = [], []
        validate.crawler_freshness(repo, errors, warnings, date(2100, 1, 1))
        self.assertEqual([], errors)
        self.assertTrue(any(w.startswith("ops/crawlers.md:") for w in warnings))
        self.assertTrue(any(w.startswith("en/ops/crawlers.md:") for w in warnings))

    def test_missing_or_invalid_dates_are_errors(self):
        self.tables(first="", second="2026-13-01")
        errors, _ = self.run_validate()
        self.assertIn("ops/crawlers.md:5: crawler table date must be YYYY-MM-DD, found ''", errors)
        self.assertIn("ops/crawlers.md:6: crawler table date is not a calendar date: 2026-13-01", errors)
        self.tables(first="2026/11/01")
        self.assertIn("found '2026/11/01'", self.run_validate()[0])
        self.assertNotIn("bad-date", self.run_validate()[0])

    def test_short_row_and_future_date(self):
        self.write("ops/crawlers.md", "| A | 확인일 |\n|---|---|\n| only |\n| x | 2026-12-31 |\n")
        self.write("en/ops/crawlers.md", "| A | Verified |\n|:---|---:|\n| x | 2026-11-13 |\n")
        errors, warnings = self.run_validate()
        self.assertIn("ops/crawlers.md:3: crawler table date must be YYYY-MM-DD, found ''", errors)
        self.assertIn("ops/crawlers.md:4: crawler table date 2026-12-31 is in the future", warnings)
        self.assertNotIn("en/ops/crawlers.md", errors + warnings)  # one day ahead: time-zone slack

    def test_table_without_date_column_is_an_error(self):
        self.tables(ko_header="Checked")
        errors, _ = self.run_validate()
        self.assertIn("ops/crawlers.md: crawler policy table with a", errors)
        self.assertNotIn("en/ops/crawlers.md", errors)

    def test_cli_max_age_option(self):
        self.tables(second="2026-06-01")
        command = [sys.executable, str(Path(validate.__file__)), "--root", str(self.root), "--today", "2026-11-12"]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("warning: ops/crawlers.md:6:", result.stderr)
        self.assertIn("passed with 2 warning(s)", result.stdout)
        result = subprocess.run(command + ["--crawler-max-age", "120"], capture_output=True, text=True,
                                encoding="utf-8", env=env)
        self.assertEqual(1, result.returncode)
        self.assertIn("Validation failed: 2 error(s)", result.stderr)
        result = subprocess.run(command + ["--crawler-max-age", "-1"], capture_output=True, text=True,
                                encoding="utf-8", env=env)
        self.assertEqual(2, result.returncode)


if __name__ == "__main__":
    unittest.main()
