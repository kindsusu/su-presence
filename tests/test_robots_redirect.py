# -*- coding: utf-8 -*-
"""robots.txt 리다이렉트 회귀 테스트 — www↔apex만 따라가고 다른 호스트·5홉 초과는 미확인으로 남긴다.

외부 사이트에 접속하지 않는다. 127.0.0.1 로컬 HTTP 서버 하나가 Host 헤더로 합성 호스트
(apex.test · www.apex.test · other.test)를 흉내 내고, 테스트 동안만 이 이름들을 127.0.0.1로
풀어 준다. 그 밖의 이름은 DNS 실패로 돌려 실제 조회가 나가지 않게 한다.
"""

import io
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import report  # noqa: E402

LOCAL_NAMES = ("apex.test", "www.apex.test", "other.test")
ROBOTS = b"User-agent: *\nDisallow: /private\n\nUser-agent: GPTBot\nDisallow: /\n"
HOME = ('<!doctype html><html lang="ko"><head><title>합성 시험 페이지 제목입니다</title>'
        '<link rel="canonical" href="/"></head><body><h1>홈</h1>'
        '<a href="/public">공개</a><a href="/private">비공개</a>'
        + "본문 " * 200 + "</body></html>").encode("utf-8")
_real_getaddrinfo = socket.getaddrinfo


def _fake_getaddrinfo(host, port, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else str(host)
    name = name.lower().rstrip(".")
    if name in LOCAL_NAMES:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
    if name.endswith(".test"):
        raise socket.gaierror(socket.EAI_NONAME, "synthetic name not found")
    return _real_getaddrinfo(host, port, *args, **kwargs)


class SiteHandler(BaseHTTPRequestHandler):
    scenario = "www"
    loopback_target = ""
    hits = []

    def _send(self, status, body=b"", ctype="text/plain", location=None):
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        port = self.server.server_port
        path = self.path
        self.hits.append((host, path))
        scenario = self.scenario
        if host == "apex.test" and path == "/robots.txt" and scenario.startswith("same"):
            return self._send(302, location="/same/1")
        if host == "apex.test" and path.startswith("/same/"):
            hop, limit = int(path.rsplit("/", 1)[1]), int(scenario[len("same"):])
            if hop < limit:
                return self._send(302, location="/same/%d" % (hop + 1))
            return self._send(200, ROBOTS)
        if host == "apex.test" and path == "/robots.txt":
            target = {
                "www": "http://www.apex.test:%d/robots.txt" % port,
                "other": "http://other.test:%d/robots.txt" % port,
                "loopback": self.loopback_target,
                "file": "file:///etc/passwd",
            }.get(scenario, "http://www.apex.test:%d/hop/1" % port)
            return self._send(301, location=target)
        if host == "www.apex.test" and path.startswith("/hop/"):
            hop, limit = int(path.rsplit("/", 1)[1]), int(scenario[len("hops"):])
            if hop < limit:
                return self._send(302, location="/hop/%d" % (hop + 1))
            return self._send(200, ROBOTS)
        if host in ("www.apex.test", "other.test") and path == "/robots.txt":
            return self._send(200, ROBOTS)
        if host == "apex.test" and path == "/":
            return self._send(200, HOME, "text/html; charset=utf-8")
        if host == "apex.test" and path in ("/public", "/private"):
            return self._send(200, HOME, "text/html; charset=utf-8")
        return self._send(404, b"not found")

    def log_message(self, *_args):
        pass


class CountingHandler(BaseHTTPRequestHandler):
    """리다이렉트로 가면 안 되는 루프백 서버 — 요청이 오면 기록만 한다."""
    hits = []

    def do_GET(self):
        self.hits.append(self.path)
        body = ROBOTS
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _RedirectCase(unittest.TestCase):
    scenario = "www"

    @classmethod
    def setUpClass(cls):
        cls.handler = type("H", (SiteHandler,), {"scenario": cls.scenario, "hits": []})
        cls.server, cls.thread = _serve(cls.handler)
        cls.port = cls.server.server_port
        cls.base = "http://apex.test:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.handler.hits.clear()
        for patcher in (patch.object(socket, "getaddrinfo", _fake_getaddrinfo),
                        patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self):
        with patch.object(sys, "stderr", io.StringIO()):
            return crawl.build_report(self.base, 5, 0)

    @staticmethod
    def finding(audit, code):
        return next((f for f in audit["findings"] if f["code"] == code), None)


class ApexToWwwRedirect(_RedirectCase):
    scenario = "www"

    def test_followed_and_policies_evaluated_from_redirected_body(self):
        audit = self.build()
        robots = audit["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "ok")
        self.assertIsNone(robots["fetch_error"])
        self.assertEqual(robots["status"], 200)
        self.assertEqual(robots["final_url"], "http://www.apex.test:%d/robots.txt" % self.port)
        self.assertEqual(robots["redirects"], 1)
        self.assertEqual(robots["raw"], ROBOTS.decode())
        self.assertEqual(robots["policies"]["GPTBot"], "explicit-block")
        self.assertEqual(robots["policies"]["ClaudeBot"], "star-partial")
        self.assertNotIn("unknown", robots["policies"].values())
        codes = {f["code"] for f in audit["findings"]}
        self.assertNotIn("ROBOTS_UNREACHABLE", codes)
        self.assertNotIn("ROBOTS_MISSING", codes)
        self.assertIn("AI_TRAINING_BLOCKED", codes)
        self.assertNotIn("robots_unavailable", audit["coverage"]["reasons"])

    def test_own_crawl_obeys_redirected_rules(self):
        audit = self.build()
        crawled = {p["url"] for p in audit["pages"]}
        self.assertIn("%s/public" % self.base, crawled)
        self.assertNotIn("%s/private" % self.base, crawled)
        self.assertNotIn(("apex.test", "/private"), self.handler.hits)

    def test_fetch_robots_records_hop_details(self):
        res = crawl.fetch_robots(self.base)
        self.assertEqual(res["status"], 200)
        self.assertEqual(res["redirect_hosts"], 1)
        self.assertIsNone(res["redirect_reason"])
        self.assertEqual(res["robots_url"], "%s/robots.txt" % self.base)


class RedirectToUnrelatedHost(_RedirectCase):
    scenario = "other"

    def test_stays_unreachable_and_says_it_is_another_domain(self):
        audit = self.build()
        robots = audit["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "unreachable")
        self.assertEqual(robots["fetch_error"], "external_redirect_blocked")
        self.assertEqual(robots["status"], 301)
        self.assertEqual(robots["final_url"], "http://other.test:%d/robots.txt" % self.port)
        self.assertEqual(set(robots["policies"].values()), {"unknown"})
        self.assertNotIn(("other.test", "/robots.txt"), self.handler.hits)
        found = self.finding(audit, "ROBOTS_UNREACHABLE")
        self.assertIsNotNone(found)
        self.assertIn("리다이렉트를 따라가지 못해", found["message"])
        self.assertIn("다른 도메인(other.test:%d)" % self.port, found["message"])
        self.assertIn("서버 장애가 아니다", found["message"])
        self.assertNotIn("응답을 받지 못했다", found["message"])
        english = report.finding_message(found, "en")
        self.assertIn("different domain", english)
        self.assertNotIn("server error", english)

    def test_non_http_redirect_is_refused(self):
        self.handler.scenario = "file"
        self.addCleanup(setattr, self.handler, "scenario", self.scenario)
        res = crawl.fetch_robots(self.base)
        self.assertEqual(crawl.robots_fetch_state(res), "unreachable")
        self.assertEqual(res["redirect_reason"], "other_host")
        self.assertEqual(res["redirect_hosts"], 0)


class TooManyRedirects(_RedirectCase):
    scenario = "hops6"

    def test_more_than_five_hops_stays_unreachable(self):
        audit = self.build()
        robots = audit["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "unreachable")
        self.assertEqual(robots["fetch_error"], "too_many_redirects")
        self.assertEqual(set(robots["policies"].values()), {"unknown"})
        self.assertNotIn(("www.apex.test", "/hop/6"), self.handler.hits)
        found = self.finding(audit, "ROBOTS_UNREACHABLE")
        self.assertIn("연속 5번을 넘는다", found["message"])
        self.assertIn("more than five", report.finding_message(found, "en"))

    def test_exactly_five_hops_is_followed(self):
        self.handler.scenario = "hops5"
        self.addCleanup(setattr, self.handler, "scenario", self.scenario)
        res = crawl.fetch_robots(self.base)
        self.assertEqual(crawl.robots_fetch_state(res), "ok")
        self.assertEqual(res["redirects"], 5)
        self.assertEqual(res["final_url"], "http://www.apex.test:%d/hop/5" % self.port)


class SameHostRedirectChain(_RedirectCase):
    """같은 호스트 안의 robots.txt 체인도 5홉에서 멈춘다 (fetch() 기본 10홉을 쓰지 않는다)."""
    scenario = "same6"

    def test_six_same_host_hops_stay_unreachable(self):
        res = crawl.fetch_robots(self.base)
        self.assertEqual(crawl.robots_fetch_state(res), "unreachable")
        self.assertEqual(res["error"], "too_many_redirects")
        self.assertEqual(res["redirect_reason"], "too_many_redirects")
        self.assertEqual(res["redirect_hosts"], 0)
        self.assertNotIn(("apex.test", "/same/6"), self.handler.hits)
        audit = self.build()
        robots = audit["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "unreachable")
        self.assertEqual(robots["fetch_error"], "too_many_redirects")
        self.assertEqual(set(robots["policies"].values()), {"unknown"})
        self.assertIn("robots_unavailable", audit["coverage"]["reasons"])

    def test_exactly_five_same_host_hops_is_followed(self):
        self.handler.scenario = "same5"
        self.addCleanup(setattr, self.handler, "scenario", self.scenario)
        res = crawl.fetch_robots(self.base)
        self.assertEqual(crawl.robots_fetch_state(res), "ok")
        self.assertEqual(res["redirects"], 5)
        self.assertIsNone(res["redirect_reason"])
        self.assertEqual(res["final_url"], "%s/same/5" % self.base)


class RedirectToLoopbackAddress(_RedirectCase):
    """대상과 www↔apex 변형이 아닌 주소(사설·루프백 IP 포함)로는 요청을 보내지 않는다."""
    scenario = "loopback"

    def setUp(self):
        super().setUp()
        self.trap_handler = type("T", (CountingHandler,), {"hits": []})
        self.trap, trap_thread = _serve(self.trap_handler)
        self.addCleanup(trap_thread.join, 2)
        self.addCleanup(self.trap.server_close)
        self.addCleanup(self.trap.shutdown)

    def test_redirect_to_loopback_is_refused(self):
        for target in ("http://127.0.0.1:%d/robots.txt" % self.trap.server_port,
                       "http://localhost:%d/robots.txt" % self.trap.server_port,
                       "http://10.0.0.1/robots.txt"):
            with self.subTest(target=target):
                self.handler.loopback_target = target
                res = crawl.fetch_robots(self.base)
                self.assertEqual(crawl.robots_fetch_state(res), "unreachable")
                self.assertEqual(res["error"], "external_redirect_blocked")
                self.assertEqual(res["redirect_reason"], "other_host")
                self.assertEqual(res["final_url"], target)
        self.assertEqual(self.trap_handler.hits, [])


class HostKeyTests(unittest.TestCase):
    def test_default_ports_are_ignored_and_variants_match(self):
        self.assertEqual(crawl._host_key("https://Example.com:443/robots.txt"), "example.com")
        self.assertEqual(crawl._host_key("http://example.com:8080/robots.txt"), "example.com:8080")
        self.assertEqual(crawl.alt_host_of(crawl._host_key("https://example.com/")), "www.example.com")
        self.assertIsNone(crawl._host_key("file:///etc/passwd"))
        self.assertIsNone(crawl._host_key("http://[bad/robots.txt"))


class LegacyRobotsFieldsTests(unittest.TestCase):
    def test_old_unreachable_finding_without_new_fields_renders(self):
        old = {"code": "ROBOTS_UNREACHABLE", "lane": "SEO", "severity": "warn",
               "message": "robots.txt를 읽지 못했다", "urls": [],
               "data": {"status": 301, "error": "external_redirect_blocked"}}
        self.assertIn("different domain", report.finding_message(old, "en"))
        older = dict(old, data={"status": 503, "error": None})
        self.assertIn("could not be read", report.finding_message(older, "en"))


if __name__ == "__main__":
    unittest.main()
