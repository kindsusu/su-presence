# -*- coding: utf-8 -*-
"""fetch() 주소 범위 보호 회귀 테스트 — 공개 대상에서 사설·루프백 주소로 넘어가는 요청을 막는다.

외부에 접속하지 않는다. 합성 이름(*.test)을 socket.getaddrinfo 패치로 풀고, "공개 주소"로 풀린
연결만 127.0.0.1 로컬 서버로 돌린다(_connect_rows 패치). 검사(_checked_rows)는 실제 코드를 탄다.
거부돼야 하는 주소(10.x·169.254.x·::1·::ffff:10.x)에는 소켓 연결 자체가 일어나지 않는다.
"""

import contextlib
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
import verify  # noqa: E402

PUBLIC_IP = "93.184.216.34"     # 공개 주소로 분류되는 값. 실제로는 로컬 서버로 돌린다.
INTRANET_IPS = ("10.0.0.9", "10.0.0.10")   # 사내망 대상 시나리오에서 로컬 서버로 돌리는 값
_real_getaddrinfo = socket.getaddrinfo
_real_connect_rows = crawl._connect_rows


def _row(address, port):
    if ":" in address:
        return (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


class FakeDNS:
    """이름 → 주소 목록. 없는 .test 이름은 DNS 실패로, 나머지는 실제 조회로 넘긴다."""

    def __init__(self, table):
        self.table = dict(table)
        self.lookups = []

    def __call__(self, host, port, *args, **kwargs):
        name = (host.decode() if isinstance(host, bytes) else str(host)).lower().rstrip(".")
        self.lookups.append(name)
        if name in self.table:
            return [_row(a, port) for a in self.table[name]]
        if name.endswith(".test"):
            raise socket.gaierror(socket.EAI_NONAME, "synthetic name not found")
        return _real_getaddrinfo(host, port, *args, **kwargs)


class Handler(BaseHTTPRequestHandler):
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
        self.hits.append((host, self.path))
        if host == "site.test" and self.path == "/robots.txt":
            if self.server.robots_hop:
                return self._send(301, location="http://www.site.test:%d/robots.txt" % port)
            return self._send(200, b"User-agent: *\nAllow: /\n")
        if host == "site.test" and self.path == "/":
            if self.server.on_home:
                self.server.on_home()
            return self._send(302, location="/next") if self.server.home_redirects else self._send(
                200, ('<html><head><title>합성 공개 대상 페이지</title></head><body><h1>홈</h1>'
                      + "본문 " * 200 + "</body></html>").encode("utf-8"), "text/html; charset=utf-8")
        return self._send(200, b"ok")

    def log_message(self, *_args):
        pass


def _routing_connect(rows, timeout, source_address):
    """공개·사내망 시나리오 주소만 로컬 서버로 돌린다. 그 밖의 비루프백 주소는 실패시킨다."""
    routed = []
    for family, socktype, proto, canon, sockaddr in rows:
        if sockaddr[0] == PUBLIC_IP or sockaddr[0] in INTRANET_IPS:
            routed.append(_row("127.0.0.1", sockaddr[1]))
        elif sockaddr[0] in ("127.0.0.1",):
            routed.append((family, socktype, proto, canon, sockaddr))
        else:
            raise AssertionError("test tried to connect to %s" % (sockaddr[0],))
    return _real_connect_rows(routed, timeout, source_address)


class _GuardCase(unittest.TestCase):
    dns_table = {}

    def setUp(self):
        self.handler = type("H", (Handler,), {"hits": []})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.server.robots_hop = False
        self.server.home_redirects = False
        self.server.on_home = None
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.dns = FakeDNS(self.dns_table)
        self.connects = []

        def connect(rows, timeout, source_address):
            self.connects.append([r[4][0] for r in rows])
            return _routing_connect(rows, timeout, source_address)

        for patcher in (patch.object(socket, "getaddrinfo", self.dns),
                        patch.object(crawl, "_connect_rows", connect),
                        patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def url(self, host, path="/"):
        return "http://%s:%d%s" % (host, self.port, path)

    def hosts_hit(self):
        return {h for h, _ in self.handler.hits}


class AddressClassTests(unittest.TestCase):
    def test_classes(self):
        cases = {
            "8.8.8.8": "public", "2606:4700::1": "public",
            "10.1.2.3": "private", "172.16.0.1": "private", "192.168.1.1": "private", "fd00::1": "private",
            "127.0.0.1": "loopback", "::1": "loopback",
            "169.254.169.254": "link_local", "fe80::1": "link_local", "fe80::1%3": "link_local",
            "100.64.0.1": "shared", "224.0.0.1": "multicast", "ff02::1": "multicast",
            "0.0.0.0": "unspecified", "::": "unspecified",
            "240.0.0.1": "reserved", "192.0.2.1": "reserved", "::7f00:1": "reserved",
            "example.com": "invalid",
        }
        for address, expected in cases.items():
            with self.subTest(address=address):
                self.assertEqual(crawl.address_class(address), expected)

    def test_ipv4_embedded_in_ipv6_is_judged_by_the_ipv4(self):
        self.assertEqual(crawl.address_class("::ffff:10.0.0.1"), "private")
        self.assertEqual(crawl.address_class("::ffff:127.0.0.1"), "loopback")
        self.assertEqual(crawl.address_class("::ffff:169.254.169.254"), "link_local")
        self.assertEqual(crawl.address_class("::ffff:8.8.8.8"), "public")
        self.assertEqual(crawl.address_class("64:ff9b::a00:1"), "private")     # NAT64 → 10.0.0.1
        self.assertEqual(crawl.address_class("2002:a00:1::1"), "private")      # 6to4 → 10.0.0.1


class PublicTargetRefusesNonPublicHosts(_GuardCase):
    """공개 대상의 범위에서 비공개 주소로 풀리는 이름은 접속 없이 nonpublic_address."""
    dns_table = {
        "site.test": [PUBLIC_IP],
        "a.site.test": ["10.0.0.5"],
        "b.site.test": ["127.0.0.1"],
        "c.site.test": ["169.254.169.254"],
        "d.site.test": ["::1"],
        "e.site.test": ["::ffff:10.0.0.1"],
        "f.site.test": [PUBLIC_IP, "10.0.0.6"],     # 하나라도 비공개면 거부
    }

    def test_each_nonpublic_resolution_is_refused_without_a_request(self):
        expected = {"a": "private", "b": "loopback", "c": "link_local", "d": "loopback",
                    "e": "private", "f": "private"}
        with crawl.target_scope(self.url("site.test")):
            for label, klass in expected.items():
                with self.subTest(host=label):
                    res = crawl.fetch(self.url("%s.site.test" % label))
                    self.assertEqual(res["error"], "nonpublic_address")
                    self.assertEqual(res["address_class"], klass)
                    self.assertIsNone(res["status"])
        self.assertEqual(self.handler.hits, [])
        self.assertEqual(self.connects, [])

    def test_public_target_itself_is_fetched_through_the_checked_address(self):
        with patch.object(socket, "create_connection",
                          side_effect=AssertionError("unpinned connection")):
            res = crawl.fetch(self.url("site.test"))
        self.assertEqual(res["status"], 200)
        self.assertIsNone(res["error"])
        self.assertEqual(self.connects, [[PUBLIC_IP]])
        self.assertEqual(self.handler.hits, [("site.test", "/")])   # Host 헤더는 원래 이름

    def test_mirror_candidates_resolving_inside_are_reported_not_fetched(self):
        dns = dict(self.dns_table, **{"dev.site.test": ["127.0.0.1"],
                                      "staging.site.test": ["::ffff:10.0.0.1"],
                                      "beta.site.test": ["169.254.10.1"]})
        with patch.object(socket, "getaddrinfo", FakeDNS(dns)):
            out = crawl.probe_mirrors("site.test")
        self.assertEqual(out["found"], [])
        self.assertFalse(out["wildcard_suspect"])
        self.assertEqual(out["nonpublic"], [
            {"host": "dev.site.test", "address_class": "loopback"},
            {"host": "staging.site.test", "address_class": "private"},
            {"host": "beta.site.test", "address_class": "link_local"},
        ])
        self.assertEqual(self.connects, [])
        self.assertEqual(self.handler.hits, [])

    def test_mirror_probe_on_its_own_still_uses_the_target_scope(self):
        """probe_mirrors를 단독으로 불러도 후보가 스스로 범위를 정하지 못한다."""
        dns = dict(self.dns_table, **{"dev.site.test": ["10.0.0.8"]})
        with patch.object(socket, "getaddrinfo", FakeDNS(dns)):
            out = crawl.probe_mirrors("site.test")
        self.assertEqual(out["nonpublic"], [{"host": "dev.site.test", "address_class": "private"}])


class RobotsVariantHop(_GuardCase):
    """robots.txt가 www 변형으로 넘어가는데 그 이름이 내부 주소를 가리킨다."""

    def check(self, address, klass):
        self.dns.table["www.site.test"] = [address]
        self.server.robots_hop = True
        res = crawl.fetch_robots(self.url("site.test", ""))
        self.assertEqual(res["error"], "nonpublic_address")
        self.assertEqual(res["address_class"], klass)
        self.assertEqual(res["redirect_hosts"], 1)
        self.assertEqual(crawl.robots_fetch_state(res), "unreachable")
        self.assertNotIn("www.site.test", self.hosts_hit())

    def test_private(self):
        self.check("10.0.0.7", "private")

    def test_loopback_reaching_the_same_server_is_refused(self):
        # 같은 로컬 서버가 www.site.test도 받는다 — 거부되지 않으면 Host 기록이 남는다.
        self.dns.table["site.test"] = [PUBLIC_IP]
        self.check("127.0.0.1", "loopback")

    def test_link_local_and_ipv6_loopback(self):
        self.check("169.254.169.254", "link_local")
        self.check("::1", "loopback")

    dns_table = {"site.test": [PUBLIC_IP]}


class RebindingOnRedirect(_GuardCase):
    """같은 호스트 안의 리다이렉트라도 연결마다 다시 풀고 검사한다 (DNS 재바인딩)."""
    dns_table = {"site.test": [PUBLIC_IP]}

    def test_second_hop_resolving_to_loopback_is_refused(self):
        self.server.home_redirects = True
        self.server.on_home = lambda: self.dns.table.__setitem__("site.test", ["127.0.0.1"])
        res = crawl.fetch(self.url("site.test"))
        self.assertEqual(res["error"], "nonpublic_address")
        self.assertEqual(res["address_class"], "loopback")
        self.assertEqual(self.handler.hits, [("site.test", "/")])
        self.assertNotIn(("site.test", "/next"), self.handler.hits)


class FullAuditOfPublicTarget(_GuardCase):
    dns_table = {
        "site.test": [PUBLIC_IP],
        "www.site.test": ["169.254.169.254"],
        "dev.site.test": ["::1"],
        "staging.site.test": ["::ffff:10.0.0.1"],
    }

    def test_variant_and_mirrors_are_unknown_not_broken(self):
        with contextlib.redirect_stderr(io.StringIO()):
            audit = crawl.build_report(self.url("site.test", ""), 3, 0)
        alt = audit["site"]["hygiene"]["alt_host"]
        self.assertEqual(alt["result"], "nonpublic_address")
        self.assertEqual(alt["address_class"], "link_local")
        self.assertIsNone(alt["status"])
        codes = {f["code"]: f for f in audit["findings"]}
        self.assertNotIn("ALT_HOST_UNREACHABLE", codes)
        self.assertEqual(codes["ALT_HOST_NONPUBLIC"]["severity"], "info")
        mirrors = audit["site"]["hygiene"]["mirrors"]
        self.assertEqual(mirrors["found"], [])
        self.assertEqual({m["host"] for m in mirrors["nonpublic"]}, {"dev.site.test", "staging.site.test"})
        self.assertEqual(codes["MIRROR_NONPUBLIC_DNS"]["severity"], "info")
        self.assertEqual(codes["MIRROR_NONPUBLIC_DNS"]["data"]["count"], 2)
        self.assertNotIn("MIRROR_PUBLIC", codes)
        self.assertEqual(self.hosts_hit(), {"site.test"})
        for addresses in self.connects:
            self.assertEqual(addresses, [PUBLIC_IP])


class NonpublicPageIsNotABrokenLink(unittest.TestCase):
    def test_page_refused_by_scope_is_coverage_gap_not_http_error(self):
        base = "https://example.com"
        home = ('<html><head><title>Example company</title></head><body><h1>Example</h1>'
                '<a href="/a">a</a>' + "words " * 80 + "</body></html>")

        def fetch(url, **_kwargs):
            out = {"status": 200, "body": "", "error": None, "headers": {}, "final_url": url,
                   "ms": 1, "redirects": 0, "content_type": "text/html"}
            if url.rstrip("/") == base:
                out["body"] = home
            elif url == base + "/a":
                out.update(status=None, error="nonpublic_address", address_class="loopback")
            elif url.endswith("/robots.txt"):
                out.update(body="User-agent: *\nAllow: /\n", content_type="text/plain")
            else:
                out.update(status=404)
            return out

        with patch.object(crawl, "fetch", side_effect=fetch), contextlib.redirect_stderr(io.StringIO()):
            audit = crawl.build_report(base, 10, 0)
        codes = {f["code"] for f in audit["findings"]}
        self.assertNotIn("HTTP_ERROR", codes)
        self.assertIn("nonpublic_address", audit["coverage"]["reasons"])
        self.assertFalse(audit["coverage"]["complete"])
        self.assertIn("CRAWL_INCOMPLETE", codes)


class NonPublicTargetScope(_GuardCase):
    """비공개 대상(루프백·사내망)은 같은 분류만 추가로 허용한다."""
    dns_table = {
        "intranet.test": ["10.0.0.9"],
        "dev.intranet.test": ["10.0.0.10"],
        "lo.test": ["127.0.0.1"],
        "meta.test": ["169.254.169.254"],
    }

    def test_loopback_literal_target_works(self):
        res = crawl.fetch("http://127.0.0.1:%d/" % self.port)
        self.assertEqual(res["status"], 200)
        self.assertIsNone(res["error"])

    def test_localhost_name_target_works(self):
        self.dns.table["localhost"] = ["127.0.0.1"]
        res = crawl.fetch(self.url("localhost"))
        self.assertEqual(res["status"], 200)

    def test_intranet_target_allows_private_but_not_loopback_or_link_local(self):
        with crawl.target_scope(self.url("intranet.test")):
            ok = crawl.fetch(self.url("dev.intranet.test"))
            lo = crawl.fetch(self.url("lo.test"))
            meta = crawl.fetch(self.url("meta.test"))
        self.assertEqual(ok["status"], 200)
        self.assertEqual((lo["error"], lo["address_class"]), ("nonpublic_address", "loopback"))
        self.assertEqual((meta["error"], meta["address_class"]), ("nonpublic_address", "link_local"))
        self.assertEqual(self.hosts_hit(), {"dev.intranet.test"})

    def test_loopback_target_does_not_open_private_or_link_local(self):
        with crawl.target_scope("http://127.0.0.1:%d" % self.port):
            res = crawl.fetch(self.url("intranet.test"))
            meta = crawl.fetch(self.url("meta.test"))
        self.assertEqual(res["address_class"], "private")
        self.assertEqual(meta["address_class"], "link_local")
        self.assertEqual(self.handler.hits, [])

    def test_inner_scope_cannot_widen_the_outer_one(self):
        with crawl.target_scope("https://public.example/"):
            self.dns.table["public.example"] = [PUBLIC_IP]
            with crawl.target_scope(self.url("lo.test")):
                res = crawl.fetch(self.url("lo.test"))
        self.assertEqual(res["error"], "nonpublic_address")
        self.assertEqual(self.handler.hits, [])


class ProxyPath(_GuardCase):
    """프록시를 거쳐도 대상 이름이 비공개 주소로 풀리면 프록시에 요청을 보내지 않는다."""
    dns_table = {"site.test": [PUBLIC_IP], "a.site.test": ["10.0.0.5"]}

    def test_refused_before_contacting_proxy(self):
        proxy = "http://127.0.0.1:%d" % self.port
        with patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy,
                                     "no_proxy": "", "NO_PROXY": ""}):
            with crawl.target_scope(self.url("site.test")):
                res = crawl.fetch("http://a.site.test/")
        self.assertEqual(res["error"], "nonpublic_address")
        self.assertEqual(self.handler.hits, [])


class VerifyUsesOneScope(unittest.TestCase):
    def test_verify_deploy_runs_inside_the_audit_target_scope(self):
        seen = []

        def fetch(url, **_kwargs):
            seen.append(crawl._SCOPE.get())
            return {"status": 404, "body": "", "error": None, "headers": {}, "final_url": url,
                    "ms": 1, "redirects": 0, "content_type": "text/plain"}

        audit = {"target": {"base": "https://example.com", "host": "example.com"},
                 "pages": [], "site": {}}
        import tempfile
        with tempfile.TemporaryDirectory() as deploy:
            verify.verify_deploy(audit, deploy, fetch=fetch, delay=0)
        self.assertTrue(seen)
        self.assertTrue(all(s is not None and s.host == "example.com" for s in seen))
        self.assertIsNone(crawl._SCOPE.get())


if __name__ == "__main__":
    unittest.main()
