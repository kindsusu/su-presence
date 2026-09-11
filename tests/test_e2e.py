# -*- coding: utf-8 -*-
"""E2E 통합 테스트 — 외부 네트워크 없이 전 루프를 실제 CLI로 돌린다.

tests/fixtures/site/ 를 임시 폴더에 복사해 http.server로 127.0.0.1 임시 포트에 띄우고,
crawl → report → generate → (배포 흉내) → verify → drift/measure 를 전부 subprocess로 호출한다.
도구를 임포트해 함수를 부르는 것이 아니라 **사용자가 치는 명령과 같은 경로**로 돈다.

픽스처 사이트에 일부러 심어 둔 결함:
  · 중복 title 2페이지 (guide/beta.html · service/gamma.html)
  · JSON-LD 0페이지
  · canonical 누락 1페이지 (guide/alpha.html)
  · noindex 1페이지 (draft.html)
  · robots.txt는 있으나 Sitemap 선언 없음 · 사이트맵 파일 없음
  · /private/ 는 robots.txt Disallow — 크롤 결과에 나오면 안 된다

실행: python -m unittest discover tests
"""

import csv
import functools
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import generate  # noqa: E402  (slug_of — 사람이 스니펫을 어느 페이지에 붙일지 정하는 규칙)

TODAY = date.today()
D1 = (TODAY - timedelta(days=14)).isoformat()   # 기준선 (배포 전)
D2 = TODAY.isoformat()                          # 배포 후 재측정


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server(docroot):
    handler = functools.partial(_QuietHandler, directory=docroot)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


def run_tool(name, *args, expect=0):
    """tools/<name>을 실제 CLI로 호출한다. expect=None이면 종료 코드를 따지지 않는다."""
    cmd = [sys.executable, os.path.join(ROOT, "tools", name)] + [str(a) for a in args]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            "%s %s → exit %s (기대 %s)\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (name, " ".join(str(a) for a in args), proc.returncode, expect,
               proc.stdout[-4000:], proc.stderr[-4000:]))
    return proc


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def local_path(docroot, url):
    rel = urllib.parse.urlsplit(url).path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    return os.path.join(docroot, *rel.split("/"))


def fill_form_csv(src, dst, answer):
    """폼 CSV를 사람이 채운 것처럼 채운다. answer(row) → (cited, urls)."""
    with open(src, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        cited, urls = answer(row)
        row["cited"] = cited
        row["cited_urls"] = urls
    with open(dst, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def codes(audit):
    return {f["code"] for f in audit.get("findings") or []}


def check_status(verify_json):
    return {c["id"]: c["status"] for c in verify_json["checks"]}


class TestEndToEnd(unittest.TestCase):
    """루프를 setUpClass에서 한 번 돌리고, 단계별 산출물을 각 테스트가 검증한다."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="su-presence-e2e-")
        cls.docroot = os.path.join(cls.tmp, "www")
        shutil.copytree(os.path.join(FIXTURES, "site"), cls.docroot)
        cls.outroot = os.path.join(cls.tmp, "out")
        cls.httpd, cls.base = start_server(cls.docroot)
        try:
            cls._run_loop()
        except BaseException:
            cls.httpd.shutdown()
            cls.httpd.server_close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ─────────────────────────────────────────────── 루프 (실제 CLI 호출)

    @classmethod
    def _run_loop(cls):
        # ① crawl — 배포 전 진단
        run_tool("crawl.py", cls.base, "--out", cls.outroot, "--delay", "0")
        matches = glob.glob(os.path.join(cls.outroot, "*", "audit.json"))
        assert len(matches) == 1, "audit.json이 하나여야 한다: %s" % matches
        cls.audit_path = matches[0]
        cls.hostdir = os.path.dirname(cls.audit_path)
        cls.audit_before = read_json(cls.audit_path)

        # ② report — 사람에게 보고할 HTML
        run_tool("report.py", cls.audit_path)
        cls.report_html = read_text(os.path.join(cls.hostdir, "report.html"))

        # ③ generate all — 배포 산출물 초안
        site_json = os.path.join(cls.hostdir, "site.json")
        with open(site_json, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(read_text(os.path.join(FIXTURES, "site.template.json"))
                     .replace("__BASE__", cls.base))
        run_tool("generate.py", "all", cls.audit_path, "--site", site_json)
        cls.deploy_dir = os.path.join(cls.hostdir, "deploy")

        # ④ 측정 기준선 — 배포 전에 잡는다 (SKILL.md Phase 8)
        run_tool("measure.py", "init", cls.audit_path)
        cls.measure_dir = os.path.join(cls.hostdir, "measure")
        shutil.copyfile(os.path.join(FIXTURES, "queries.json"),
                        os.path.join(cls.measure_dir, "queries.json"))
        cls.measure_round(D1, nonbrand_cited=False)
        run_tool("drift.py", "snapshot", cls.audit_path, "--date", D1,
                 "--measure", os.path.join(cls.measure_dir, "summary.json"),
                 "--label", "기준선")

        # ⑤ 배포 흉내 — 생성된 파일을 그대로 웹루트에 올린다
        cls.deploy_to_docroot()

        # ⑥ verify deploy — 라이브에서 항목별 증명 (fail이 남아 있으므로 exit 1)
        cls.verify_proc = run_tool("verify.py", "deploy", cls.audit_path,
                                   "--delay", "0", expect=None)
        cls.verify = read_json(os.path.join(cls.hostdir, "verify.json"))

        # ⑦ 배포 후 재크롤 + 재측정 → 스냅샷 → 비교
        run_tool("crawl.py", cls.base, "--out", cls.outroot, "--delay", "0")
        cls.audit_after = read_json(cls.audit_path)
        cls.measure_round(D2, nonbrand_cited=True)
        run_tool("drift.py", "snapshot", cls.audit_path, "--date", D2,
                 "--measure", os.path.join(cls.measure_dir, "summary.json"),
                 "--label", "배포 후")
        cls.compare_proc = run_tool("drift.py", "compare", cls.audit_path, expect=None)
        cls.drift = read_json(os.path.join(cls.hostdir, "drift.json"))
        cls.history = read_json(os.path.join(cls.hostdir, "history", "index.json"))

    @classmethod
    def measure_round(cls, date_str, nonbrand_cited):
        """form → (사람이 채움) → import → report 한 바퀴."""
        run_tool("measure.py", "form", cls.audit_path,
                 "--engines", "chatgpt", "--runs", "2", "--date", date_str)
        src = os.path.join(cls.measure_dir, "form-%s.csv" % date_str)
        dst = os.path.join(cls.measure_dir, "form-%s-filled.csv" % date_str)

        def answer(row):
            if row["type"] == "brand":
                return "Y", cls.base + "/"
            if nonbrand_cited and row["query_id"] == "Q03":
                return "Y", cls.base + "/faq.html"
            return "N", ""

        cls.rows_written = fill_form_csv(src, dst, answer)
        run_tool("measure.py", "import", cls.audit_path, dst)
        run_tool("measure.py", "report", cls.audit_path)

    @classmethod
    def deploy_to_docroot(cls):
        """사람이 배포 패키지를 웹루트에 올리고 LD 스니펫을 <head>에 붙이는 단계."""
        for name in ("robots.txt", "sitemap.xml", "llms.txt"):
            shutil.copyfile(os.path.join(cls.deploy_dir, name),
                            os.path.join(cls.docroot, name))

        def snippet(name):
            path = os.path.join(cls.deploy_dir, "jsonld", name)
            return read_text(path) if os.path.exists(path) else ""

        home = cls.base + "/"
        injected = 0
        for page in cls.audit_before["pages"]:
            if page["status"] != 200:
                continue
            blocks = snippet("%s.snippet.html" % generate.slug_of(page["url"]))
            if page["url"] == home:
                blocks = snippet("organization.snippet.html") + blocks
            if not blocks.strip():
                continue
            path = local_path(cls.docroot, page["url"])
            html = read_text(path)
            assert "<!--JSONLD-->" in html, "픽스처에 삽입 자리가 없다: %s" % path
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(html.replace("<!--JSONLD-->", blocks))
            injected += 1
        cls.pages_injected = injected

    # ─────────────────────────────────────────────── ① crawl

    def test_crawl_finds_every_page_and_respects_robots(self):
        urls = [p["url"] for p in self.audit_before["pages"]]
        self.assertEqual(self.audit_before["stats"]["pages_crawled"], 8, urls)
        self.assertEqual([u for u in urls if "/private/" in u], [],
                         "robots.txt의 Disallow를 어겼다")
        self.assertTrue(all(p["status"] == 200 for p in self.audit_before["pages"]), urls)

    def test_crawl_reports_the_planted_defects(self):
        found = codes(self.audit_before)
        for code in ("NOINDEX", "TITLE_DUPLICATE", "CANONICAL_MISSING", "JSONLD_MISSING",
                     "SITEMAP_MISSING", "SITEMAP_NOT_DECLARED", "FAQ_MISSING",
                     "ORG_JSONLD_MISSING", "LLMS_TXT_MISSING"):
            self.assertIn(code, found)
        stats = self.audit_before["stats"]
        self.assertEqual(stats["pages_noindex"], 1)
        self.assertEqual(stats["pages_with_jsonld"], 0)
        self.assertEqual(self.audit_before["scorecard"]["SEO"]["status"], "bad")
        # FAQ/JSON-LD 누락은 선택적 개선 기회이며 접근성 장애가 아니다.
        self.assertEqual(self.audit_before["scorecard"]["AEO"]["status"], "ok")
        self.assertTrue(all(f["severity"] == "info" for f in
                            self.audit_before["findings"] if f["code"] == "FAQ_MISSING"))

    def test_crawl_skips_alt_host_probe_for_ip_targets(self):
        # www.127.0.0.1 조회는 의미도 없고 외부 DNS를 건드린다 — na로 남긴다
        self.assertEqual(
            self.audit_before["site"]["hygiene"]["alt_host"]["result"], "na")

    # ─────────────────────────────────────────────── ② report

    def test_report_has_all_eight_pages(self):
        for n in range(1, 9):
            self.assertIn('<section id="p%d">' % n, self.report_html)
        self.assertNotIn("${", self.report_html)
        self.assertIn("127.0.0.1", self.report_html)

    # ─────────────────────────────────────────────── ③ generate

    def test_generate_writes_the_whole_package(self):
        for rel in ("robots.txt", "sitemap.xml", "llms.txt", "meta-draft.csv",
                    "meta-draft.json", "DEPLOY.md", "jsonld/organization.json"):
            self.assertTrue(os.path.exists(os.path.join(self.deploy_dir, rel)), rel)

    def test_sitemap_excludes_the_noindex_page(self):
        locs = re.findall(r"<loc>([^<]+)</loc>",
                          read_text(os.path.join(self.deploy_dir, "sitemap.xml")))
        self.assertEqual(len(locs), 7)
        self.assertNotIn(self.base + "/draft.html", locs)
        self.assertIn(self.base + "/faq.html", locs)

    def test_robots_preserves_the_original_verbatim(self):
        before = self.audit_before["site"]["robots"]["raw"]
        after = read_text(os.path.join(self.deploy_dir, "robots.txt"))
        self.assertTrue(after.startswith(before.rstrip("\n")))
        self.assertIn("Disallow: /private/", after)
        self.assertIn("User-agent: GPTBot", after)
        self.assertIn("Sitemap: %s/sitemap.xml" % self.base, after)

    # ─────────────────────────────────────────────── ⑥ verify deploy

    def test_verify_proves_the_deploy_is_live(self):
        status = check_status(self.verify)
        for cid in ("noindex", "robots.status", "robots.preserved", "robots.policy",
                    "robots.sitemap", "sitemap.reachable", "sitemap.locs",
                    "sitemap.noindex", "sitemap.canonical", "llms.status",
                    "jsonld.present", "jsonld.type", "jsonld.visible", "jsonld.org_id"):
            self.assertEqual(status.get(cid), "pass", "%s: %s" % (cid, status.get(cid)))
        self.assertGreaterEqual(self.verify["summary"]["pass"], 14)

    def test_verify_remaining_failures_are_the_expected_ones(self):
        # llms.txt TODO, meta 초안 불일치, 중복 title을 실제 배포 결함으로 잡는다.
        fails = {c["id"] for c in self.verify["checks"] if c["status"] == "fail"}
        self.assertEqual(fails, {"llms.todo", "meta.applied", "meta.duplicate"})
        self.assertEqual(self.verify["exit_code"], 1)
        self.assertEqual(self.verify_proc.returncode, 1)
        self.assertTrue(os.path.exists(os.path.join(self.hostdir, "VERIFY.md")))

    def test_reviewed_package_can_complete_live_verification(self):
        """남은 초안을 실제 반영하면 CLI가 통과해야 한다. 관측값을 모킹하지 않는다."""
        originals = {}

        def replace(path, content):
            if path not in originals:
                with open(path, "rb") as stream:
                    originals[path] = stream.read()
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)

        try:
            # 이 사이트는 합성 테스트 픽스처다. TODO를 명시적 예시 설명으로 채운다.
            llms = re.sub(r"<<TODO[^>]*>>", "테스트용 예시 서비스 안내",
                          read_text(os.path.join(self.deploy_dir, "llms.txt")))
            replace(os.path.join(self.deploy_dir, "llms.txt"), llms)
            replace(os.path.join(self.docroot, "llms.txt"), llms)
            from html import escape
            for row in read_json(os.path.join(self.deploy_dir, "meta-draft.json")):
                path = local_path(self.docroot, row["url"])
                html = read_text(path)
                title = row.get("draft_title", "")
                if title and not title.startswith("<<TODO"):
                    html = re.sub(r"<title>.*?</title>",
                                  lambda _: "<title>%s</title>" % escape(title), html, flags=re.S)
                description = row.get("draft_description", "")
                if description and not description.startswith("<<TODO"):
                    html = re.sub(r'<meta\s+name="description"[^>]*>', "", html, flags=re.I)
                    html = html.replace("</head>", '<meta name="description" content="%s">\n</head>'
                                        % escape(description, quote=True))
                replace(path, html)
            with tempfile.TemporaryDirectory() as results:
                run_tool("verify.py", "deploy", self.audit_path, "--delay", "0",
                         "--out", os.path.join(results, "verify.json"))
                checked = read_json(os.path.join(results, "verify.json"))
                self.assertEqual(checked["exit_code"], 0)
                self.assertEqual(checked["summary"]["fail"], 0)
                self.assertEqual(check_status(checked)["meta.applied"], "pass")
                self.assertEqual(check_status(checked)["llms.todo"], "pass")
        finally:
            for path, content in originals.items():
                with open(path, "wb") as stream:
                    stream.write(content)

    # ─────────────────────────────────────────────── ⑦ 배포 후 재크롤

    def test_second_crawl_shows_the_fixes(self):
        before, after = codes(self.audit_before), codes(self.audit_after)
        for code in ("SITEMAP_MISSING", "SITEMAP_NOT_DECLARED", "LLMS_TXT_MISSING",
                     "FAQ_MISSING", "ORG_JSONLD_MISSING"):
            self.assertIn(code, before)
            self.assertNotIn(code, after)
        self.assertEqual(self.audit_after["stats"]["pages_with_jsonld"], 7)
        self.assertEqual(self.audit_after["stats"]["pages_noindex"], 1)
        self.assertEqual(self.audit_after["scorecard"]["AEO"]["status"], "ok")

    # ─────────────────────────────────────────────── ④ measure

    def test_measure_form_and_import_round_trip(self):
        self.assertEqual(self.rows_written, 8)   # 질의 4 × 엔진 1 × 2회차
        for name in ("queries.json", "log.jsonl", "summary.json", "MEASURE.md",
                     "form-%s.csv" % D1, "form-%s.html" % D1, "form-%s.csv" % D2):
            self.assertTrue(os.path.exists(os.path.join(self.measure_dir, name)), name)
        rows = [json.loads(l) for l in
                read_text(os.path.join(self.measure_dir, "log.jsonl")).splitlines() if l.strip()]
        self.assertEqual(len(rows), 16)          # 두 측정일 × 8행
        self.assertEqual({r["engine"] for r in rows}, {"chatgpt"})
        summary = read_json(os.path.join(self.measure_dir, "summary.json"))
        self.assertTrue(summary["schema"].startswith("su-multi-geo/measure/"))

    # ─────────────────────────────────────────────── drift

    def test_history_keeps_both_snapshots_and_a_baseline(self):
        for name in ("audit-%s.json" % D1, "measure-%s.json" % D1,
                     "audit-%s.json" % D2, "measure-%s.json" % D2):
            self.assertTrue(
                os.path.exists(os.path.join(self.hostdir, "history", name)), name)
        self.assertEqual(self.history["baseline_date"], D1)
        self.assertEqual(len(self.history["snapshots"]), 4)

    def test_compare_records_technical_improvements_and_only_proposes_next_run(self):
        self.assertEqual(self.drift["regressions"], [])
        improved = {i["code"] for i in self.drift["improvements"]}
        for code in ("jsonld_pages", "sitemap_urls"):
            self.assertIn(code, improved)
        self.assertNotIn("scorecard", improved)
        self.assertNotIn("nonbrand_rate", improved)  # 회차별 비브랜드 표본 4개: 결론 보류
        self.assertEqual(self.drift["exit_code"], 0)
        self.assertEqual(self.compare_proc.returncode, 0)
        self.assertEqual(self.drift["next_due"],
                         (TODAY + timedelta(days=14)).isoformat())
        self.assertTrue(os.path.exists(os.path.join(self.hostdir, "DRIFT.md")))

    def test_compare_includes_measurement_drift(self):
        diff = self.drift["measure_diff"]
        self.assertIsNotNone(diff, self.drift["warnings"])
        self.assertEqual((diff["from"], diff["to"]), (D1, D2))
        # 최신 회차끼리 비교한다 — 기준선 0/4, 배포 후 2/4. 기준선이 분모에 재유입되지 않는다.
        for when, cited, rate in (("before", 0, 0.0), ("after", 2, 0.5)):
            slot = diff["totals"][when]["nonbrand"]
            self.assertEqual((slot["cited"], slot["runs"], slot["rate"], slot["errors"]),
                             (cited, 4, rate, 0))
            self.assertEqual(len(slot["wilson95"]), 2)
        # 엔진별로도 같은 드리프트가 잡힌다
        chatgpt = [e for e in diff["engines"] if e["engine"] == "chatgpt"][0]
        self.assertEqual(chatgpt["nonbrand"]["before"]["rate"], 0.0)
        self.assertGreater(chatgpt["nonbrand"]["points"], 0)
        # 인용 URL 표는 비어 있다 — measure.py의 URL 검증기가 IP 호스트를 도메인으로 치지 않는다.
        # 실제 도메인에서는 채워지고, 그 경로는 tests/test_measure.py가 따로 본다.
        self.assertEqual(diff["ours"], [])

    def test_no_stale_baseline_warning_for_a_14_day_gap(self):
        self.assertEqual(self.drift["baseline_age_days"], 14)
        self.assertEqual([w for w in self.drift["warnings"] if "낡았다" in w], [])


class TestFixtureIntegrity(unittest.TestCase):
    """픽스처가 조용히 망가지면 E2E 판정이 의미를 잃는다 — 결함이 그대로 있는지 본다."""

    def test_faq_answers_match_the_site_json_verbatim(self):
        html = read_text(os.path.join(FIXTURES, "site", "faq.html"))
        site = json.loads(read_text(os.path.join(FIXTURES, "site.template.json")))
        for entry in site["faqs"]:
            self.assertIn(entry["q"], html)
            self.assertIn(entry["a"], html)

    def test_planted_defects_are_still_planted(self):
        root = os.path.join(FIXTURES, "site")
        beta = read_text(os.path.join(root, "guide", "beta.html"))
        gamma = read_text(os.path.join(root, "service", "gamma.html"))
        self.assertIn("<title>예시 렌탈 이용 안내 페이지</title>", beta)
        self.assertIn("<title>예시 렌탈 이용 안내 페이지</title>", gamma)
        self.assertNotIn('rel="canonical"', read_text(os.path.join(root, "guide", "alpha.html")))
        self.assertIn('name="robots" content="noindex',
                      read_text(os.path.join(root, "draft.html")))
        self.assertIn("Disallow: /private/", read_text(os.path.join(root, "robots.txt")))
        self.assertFalse(glob.glob(os.path.join(root, "sitemap*.xml")))
        self.assertFalse(glob.glob(os.path.join(root, "llms*.txt")))
        for path in glob.glob(os.path.join(root, "**", "*.html"), recursive=True):
            self.assertNotIn("ld+json", read_text(path), path)


if __name__ == "__main__":
    unittest.main()
