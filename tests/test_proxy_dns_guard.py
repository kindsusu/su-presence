# -*- coding: utf-8 -*-
"""비공개 주소 차단의 남은 두 틈 — DNS 조회 시간 제한과 프록시 경로.

외부에 접속하지 않는다. 합성 이름(*.test)은 socket.getaddrinfo 패치로 풀고, "공개 주소"로 풀린
직접 연결만 127.0.0.1 로컬 원 서버로 돌린다. 프록시는 127.0.0.1의 가짜 HTTP 프록시다.
"""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import report  # noqa: E402
import seo_geo  # noqa: E402
import verify  # noqa: E402

PUBLIC_IP = "93.184.216.34"     # 공개 주소로 분류되는 값. 실제로는 로컬 원 서버로 돌린다.
SECRET = "s3cr3t-proxy-pass"
PROXY_USER = "pxuser9"
_real_connect_rows = crawl._connect_rows
HOME = ('<html><head><title>합성 공개 대상 페이지</title></head><body><h1>홈</h1>'
        + "본문 " * 200 + "</body></html>").encode("utf-8")


def _row(address, port):
    if ":" in address:
        return (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


def _fake_dns(table):
    def lookup(host, port, *args, **kwargs):
        name = (host.decode() if isinstance(host, bytes) else str(host)).lower().rstrip(".")
        if name in table:
            return [_row(a, port) for a in table[name]]
        if name in ("127.0.0.1", "localhost"):
            return [_row("127.0.0.1", port)]
        raise socket.gaierror(socket.EAI_NONAME, "synthetic name not found")
    return lookup


def _routing_connect(rows, timeout, source_address):
    routed = [_row("127.0.0.1", r[4][1]) if r[4][0] == PUBLIC_IP else r for r in rows]
    for r in routed:
        if r[4][0] != "127.0.0.1":
            raise AssertionError("test tried to connect to %s" % (r[4][0],))
    return _real_connect_rows(routed, timeout, source_address)


def _site_handler(hits):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # 프록시면 path가 절대 URL이다. 원 서버면 Host 헤더로 이름을 본다.
            parts = urllib.parse.urlsplit(self.path)
            host = (parts.hostname or (self.headers.get("Host") or "").split(":")[0]).lower()
            path = parts.path or "/"
            hits.append((host, path))
            if host != "site.test":
                return self._send(404, b"not here")
            if path == "/robots.txt":
                return self._send(200, b"User-agent: *\nAllow: /\n")
            if path == "/":
                return self._send(200, HOME, "text/html; charset=utf-8")
            return self._send(404, b"missing")

        def _send(self, status, body, ctype="text/plain"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def _serve(test, hits):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _site_handler(hits))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    test.addCleanup(thread.join, 2)
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server.server_port


class DnsTimeout(unittest.TestCase):
    """getaddrinfo에는 시간 제한이 없다 — 조회가 멈춰도 fetch는 요청 시간 제한 안에 돌아온다."""

    def setUp(self):
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def blocking(self, *_args, **_kwargs):
        self.release.wait(10)
        raise socket.gaierror(socket.EAI_AGAIN, "released")

    def test_blocked_lookup_returns_dns_timeout_within_the_deadline(self):
        with patch.object(crawl, "TIMEOUT", 0.3), patch.object(socket, "getaddrinfo", self.blocking), \
                patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"}):
            started = time.monotonic()
            res = crawl.fetch("http://slow.test/")
            elapsed = time.monotonic() - started
        self.assertEqual(res["error"], "dns_timeout")
        self.assertIsNone(res["status"])
        self.assertNotIn("address_class", res)
        self.assertLess(elapsed, 1.5)
        self.assertEqual(crawl.robots_fetch_state(res), "unreachable")

    def test_resolve_raises_dns_timeout(self):
        with patch.object(socket, "getaddrinfo", self.blocking):
            started = time.monotonic()
            with self.assertRaises(crawl.DNSTimeout):
                crawl._resolve("slow.test", 80, 0.05)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_scope_timeout_is_not_cached(self):
        scope = crawl._TargetScope("https://slow.test/")
        with patch.object(socket, "getaddrinfo", self.blocking):
            with self.assertRaises(crawl.DNSTimeout):
                scope.allowed(0.05)
        self.assertIsNone(scope._allowed)
        with patch.object(socket, "getaddrinfo", _fake_dns({"slow.test": ["10.0.0.3"]})):
            self.assertEqual(scope.allowed(1), frozenset({"public", "private"}))

    def test_dns_failure_is_still_dns_fail(self):
        with patch.object(socket, "getaddrinfo", _fake_dns({})), \
                patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"}):
            res = crawl.fetch("http://missing.test/")
        self.assertEqual(res["error"], "dns_fail")
        self.assertIsNone(res["status"])


class DnsTimeoutIsNotObserved(unittest.TestCase):
    """dns_timeout 페이지·변형 주소는 깨진 링크·접속 불가가 아니라 미관측이다."""

    def test_page_and_alt_host_timeouts_are_coverage_gaps(self):
        base = "https://example.com"
        home = ('<html><head><title>Example company</title></head><body><h1>Example</h1>'
                '<a href="/a">a</a>' + "words " * 80 + "</body></html>")

        def fetch(url, **_kwargs):
            out = {"status": 200, "body": "", "error": None, "headers": {}, "final_url": url,
                   "ms": 1, "redirects": 0, "content_type": "text/html"}
            if url.rstrip("/") == base:
                out["body"] = home
            elif url == base + "/a" or url.startswith("https://www.example.com"):
                out.update(status=None, error="dns_timeout")
            elif url.endswith("/robots.txt"):
                out.update(body="User-agent: *\nAllow: /\n", content_type="text/plain")
            else:
                out.update(status=404)
            return out

        with patch.object(crawl, "fetch", side_effect=fetch), contextlib.redirect_stderr(io.StringIO()):
            audit = crawl.build_report(base, 10, 0)
        codes = {f["code"] for f in audit["findings"]}
        self.assertNotIn("HTTP_ERROR", codes)
        self.assertNotIn("ALT_HOST_UNREACHABLE", codes)
        self.assertEqual(audit["site"]["hygiene"]["alt_host"]["result"], "dns_timeout")
        self.assertIn("dns_timeout", audit["coverage"]["reasons"])
        self.assertFalse(audit["coverage"]["complete"])
        self.assertNotIn("network", audit["coverage"])


class _ProxyCase(unittest.TestCase):
    """원 서버와 가짜 프록시를 띄우고, 프록시 환경 변수에 자격 증명을 싣는다."""

    def setUp(self):
        self.origin_hits, self.proxy_hits = [], []
        self.origin_port = _serve(self, self.origin_hits)
        self.proxy_port = _serve(self, self.proxy_hits)
        self.proxy = "http://%s:%s@127.0.0.1:%d" % (PROXY_USER, SECRET, self.proxy_port)
        for patcher in (patch.object(socket, "getaddrinfo", _fake_dns({"site.test": [PUBLIC_IP]})),
                        patch.object(crawl, "_connect_rows", _routing_connect),
                        patch.dict(os.environ, {"http_proxy": self.proxy, "HTTP_PROXY": self.proxy,
                                                "no_proxy": "", "NO_PROXY": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.base = "http://site.test:%d" % self.origin_port

    def audit(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return crawl.build_report(self.base, 2, 0, **kwargs)

    def assertNoProxySecrets(self, text):
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PROXY_USER, text)
        self.assertNotIn("127.0.0.1:%d" % self.proxy_port, text)


class ProxyIsRecorded(_ProxyCase):

    def test_proxy_use_is_recorded_without_credentials(self):
        audit = self.audit()
        self.assertTrue(self.proxy_hits, "요청이 프록시를 거쳐야 한다")
        self.assertEqual(self.origin_hits, [])
        self.assertEqual(audit["coverage"]["network"],
                         {"proxy": True, "address_check": "local_dns_only"})
        found = [f for f in audit["findings"] if f["code"] == "PROXY_ADDRESS_UNVERIFIED"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "info")
        self.assertIn("--direct", found[0]["message"])
        self.assertNoProxySecrets(json.dumps(audit, ensure_ascii=False))

    def test_cli_output_has_no_proxy_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                crawl.main([self.base, "--max-pages", "2", "--delay", "0", "--out", tmp])
            written = ""
            for root, _dirs, files in os.walk(tmp):
                for name in files:
                    with open(os.path.join(root, name), encoding="utf-8") as fh:
                        written += fh.read()
        self.assertIn("--direct", out.getvalue())
        self.assertIn('"PROXY_ADDRESS_UNVERIFIED"', written)
        self.assertNoProxySecrets(out.getvalue() + err.getvalue() + written)

    def test_verify_records_the_same_note(self):
        audit = {"target": {"base": self.base, "host": "site.test:%d" % self.origin_port},
                 "pages": [], "site": {}}
        with tempfile.TemporaryDirectory() as deploy:
            result = verify.verify_deploy(audit, deploy, delay=0)
        network = result["target"]["network"]
        self.assertEqual((network["proxy"], network["address_check"], network["code"]),
                         (True, "local_dns_only", "PROXY_ADDRESS_UNVERIFIED"))
        self.assertIn("--direct", network["note"])
        self.assertIn("PROXY_ADDRESS_UNVERIFIED", verify.render_md(result))
        self.assertNoProxySecrets(json.dumps(result, ensure_ascii=False) + verify.render_md(result))


class DirectIgnoresProxy(_ProxyCase):

    def test_direct_audit_never_reaches_the_proxy(self):
        audit = self.audit(direct=True)
        self.assertEqual(self.proxy_hits, [])
        self.assertIn(("site.test", "/"), self.origin_hits)
        self.assertNotIn("PROXY_ADDRESS_UNVERIFIED", {f["code"] for f in audit["findings"]})
        self.assertEqual(audit["coverage"]["network"],
                         {"proxy": False, "direct": True, "address_check": "connected_address"})
        self.assertFalse(crawl._DIRECT.get())

    def test_direct_still_refuses_nonpublic_addresses(self):
        with patch.object(socket, "getaddrinfo", _fake_dns({"site.test": [PUBLIC_IP],
                                                            "a.site.test": ["10.0.0.5"]})):
            with crawl.direct_connection(), crawl.target_scope(self.base):
                res = crawl.fetch("http://a.site.test:%d/" % self.origin_port)
        self.assertEqual(res["error"], "nonpublic_address")
        self.assertEqual(self.proxy_hits, [])

    def test_verify_direct(self):
        audit = {"target": {"base": self.base, "host": "site.test:%d" % self.origin_port},
                 "pages": [], "site": {}}
        with tempfile.TemporaryDirectory() as deploy:
            result = verify.verify_deploy(audit, deploy, delay=0, direct=True)
        self.assertEqual(self.proxy_hits, [])
        self.assertTrue(self.origin_hits)
        self.assertFalse(result["target"]["network"]["proxy"])
        self.assertNotIn("PROXY_ADDRESS_UNVERIFIED", verify.render_md(result))

    def test_cli_flags_reach_the_crawl(self):
        data = {"target": {"host": "example.com"}, "coverage": {"complete": True}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(crawl, "build_report", return_value=data) as build, \
                patch("report.render", return_value="<html></html>"), \
                patch.object(crawl, "print_summary"), contextlib.redirect_stdout(io.StringIO()):
            seo_geo.main(["audit", "example.com", "--out", tmp, "--direct"])
            self.assertIs(build.call_args.kwargs.get("direct"), True)
            seo_geo.main(["audit", "example.com", "--out", tmp])
            self.assertIs(build.call_args.kwargs.get("direct"), False)
            crawl.main(["example.com", "--out", tmp, "--direct"])
            self.assertIs(build.call_args.kwargs.get("direct"), True)
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = os.path.join(tmp, "audit.json")
            with open(audit_path, "w", encoding="utf-8") as fh:
                json.dump({"schema": "su-presence/audit/1",
                           "target": {"base": "https://example.com", "host": "example.com"}}, fh)
            os.makedirs(os.path.join(tmp, "deploy"))
            stub = {"target": {}, "summary": {}, "exit_code": 0}
            with patch.object(verify, "verify_deploy", return_value=stub) as run, \
                    patch.object(verify, "render_md", return_value=""), \
                    patch.object(verify, "print_summary"), contextlib.redirect_stdout(io.StringIO()):
                verify.main(["deploy", audit_path, "--direct"])
            self.assertIs(run.call_args.kwargs.get("direct"), True)


class NoProxyBypass(_ProxyCase):

    def test_no_proxy_covering_the_host_means_no_note(self):
        with patch.dict(os.environ, {"no_proxy": "site.test", "NO_PROXY": "site.test"}):
            audit = self.audit()
        self.assertEqual(self.proxy_hits, [])
        self.assertIn(("site.test", "/"), self.origin_hits)
        self.assertNotIn("PROXY_ADDRESS_UNVERIFIED", {f["code"] for f in audit["findings"]})
        self.assertNotIn("network", audit["coverage"])



class EnglishReport(unittest.TestCase):
    def test_proxy_note_has_an_english_message(self):
        msg = report.finding_message({"code": "PROXY_ADDRESS_UNVERIFIED", "message": "국문 원문",
                                      "data": {"address_check": "local_dns_only"}, "urls": []}, "en")
        self.assertIn("--direct", msg)
        self.assertIn("local DNS", msg)
        self.assertNotIn("국문", msg)

if __name__ == "__main__":
    unittest.main()
