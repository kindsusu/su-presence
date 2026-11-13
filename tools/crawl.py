#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-presence Phase 0 — 사이트 전수 진단 (크롤러의 눈).

사용:
    python tools/crawl.py <도메인|URL> [--max-pages 300] [--delay 0.5] [--out out/]

출력:
    <out>/<host>/audit.json   (스키마: su-presence/audit/1)
    콘솔 요약 (audit.sh와 같은 톤)

원칙
  · 자바스크립트 없이 받은 HTML만 본다. "코드에 있다"는 근거가 아니다.
  · 창작하지 않는다. 측정한 값만 audit.json에 담는다.
  · robots.txt의 Disallow는 크롤할 때 존중한다.
  · 표준 라이브러리만 쓴다 (pip 의존 0).
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import functools
import gzip
import http.client
import io
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, deque
from datetime import datetime, timezone
from html.parser import HTMLParser

import browser_review

# 신분은 도구 이름을 따른다. audit.sh 도 같은 토큰을 쓴다.
UA = "su-presence-audit/2.1"
SCHEMA = "su-presence/audit/1"
TIMEOUT = 15
MAX_BODY = 4_000_000

# GEO 레인 — 생성 엔진 크롤러. Google-Extended는 UA가 아니라 robots 토큰이다.
TRAINING_UAS = ["GPTBot", "ClaudeBot", "Google-Extended"]
SEARCH_UAS = ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot", "Googlebot", "Bingbot"]
USER_FETCH_UAS = ["ChatGPT-User", "Claude-User", "Perplexity-User"]
AI_UAS = TRAINING_UAS + SEARCH_UAS + USER_FETCH_UAS
# NEO 레인 — 네이버 검색 크롤러.
NEO_UAS = ["Yeti"]
# KEO 레인 — 다음·카카오 검색 크롤러. 다음은 UA 토큰이 둘이고 **둘 다 허용해야** 한다.
KEO_UAS = ["Daumoa", "DAUM"]
ALL_UAS = AI_UAS + NEO_UAS + KEO_UAS

# 요청 제한·일시 불가 — 깨진 링크가 아니라 그 시점에 응답을 못 받은 것(미확인)이다.
THROTTLE_STATUSES = (429, 503)

ASSET_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".txt", ".pdf", ".zip",
    ".mp4", ".webm", ".mp3", ".wav", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".rss", ".atom", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)

HANGUL = re.compile(r"[가-힣]")
WS = re.compile(r"\s+")

# 한글은 검색결과에서 영문의 약 두 배 폭을 먹는다 — 그래서 권장 길이가 다르다.
LEN_TITLE = {"ko": (25, 30), "en": (50, 60)}
LEN_DESC = {"ko": (70, 80), "en": (150, 160)}


# ─────────────────────────────────────────────────────────── robots.txt

def ua_token(value: str) -> str:
    """User-agent 값에서 제품 토큰만 뽑아 소문자로 돌려준다.

    RFC 9309·Google은 제품 토큰을 대소문자 구분 없이 맞춘다 — 첫 '/'나 공백 앞까지다.
    `GPTBot/1.1` → `gptbot`. 접두 일치가 아니므로 `GPTBot`은 `GPTBot-Image`와,
    `Daumoa`는 `DAUM`과 서로 맞지 않는다.
    """
    value = (value or "").strip()
    return re.split(r"[/\s]", value, maxsplit=1)[0].lower() if value else ""


def _robots_groups(raw: str):
    """robots.txt를 그룹 dict 목록으로 쪼갠다 — parse_robots·Content-Signal·관리 블록 판정이 공유한다.

    규칙(Allow/Disallow)이 나온 뒤 User-agent 줄이 다시 나오면 새 그룹으로 본다. 주석은 제거한다.
    Content-Signal 줄은 그룹에 붙지만 그룹을 나누지 않는다(규칙이 아니다).
    """
    groups = []
    current = {"agents": [], "tokens": [], "rules": [], "signals": []}
    saw_rule = False
    for raw_line in (raw or "").split("\n"):
        line = raw_line.rstrip("\r").split("#", 1)[0].strip()
        lower = line.lower()
        if lower.startswith("user-agent:"):
            if saw_rule:
                groups.append(current)
                current = {"agents": [], "tokens": [], "rules": [], "signals": []}
                saw_rule = False
            value = line[len("user-agent:"):].strip()
            current["agents"].append(value)
            current["tokens"].append(ua_token(value))
        elif re.match(r"^(dis)?allow:", lower):
            saw_rule = True
            is_allow = lower.startswith("allow:")
            path = re.sub(r"^(dis)?allow:\s*", "", line, flags=re.I).strip()
            current["rules"].append((path, is_allow))
        elif lower.startswith("content-signal:"):
            current["signals"].append(line.split(":", 1)[1].strip())
    if current["agents"] or current["rules"] or current["signals"]:
        groups.append(current)
    return groups


def parse_robots(raw: str):
    """robots.txt를 (agents, rules) 그룹 목록으로 쪼갠다.

    agents는 제품 토큰(소문자)이다 — `User-agent: GPTBot/1.1`도 `gptbot`으로 맞춘다.
    """
    return [(g["tokens"], g["rules"]) for g in _robots_groups(raw)
            if g["agents"] or g["rules"]]


def robots_policy(raw: str, ua: str) -> str:
    """UA의 실효 정책 판정.

    반환: explicit-allow|explicit-block|explicit-partial
          |star-allow|star-block|star-partial|none
    robots.txt를 읽지 못한 경우의 `unknown`은 호출하는 쪽(probe_site)이 붙인다 —
    이 함수는 받은 원문만 판정한다.
    """
    target = ua_token(ua)
    groups = parse_robots(raw)
    exact = [rules for agents, rules in groups if target in agents]
    selected = exact or [rules for agents, rules in groups if "*" in agents]
    if not selected:
        return "none"
    prefix = "explicit-" if exact else "star-"
    rules = [rule for group in selected for rule in group]
    root_allowed = crawl_allowed(rules, "/")
    meaningful = [(p, a) for p, a in rules if p]
    if not root_allowed:
        if any(is_allow and path for path, is_allow in meaningful):
            return prefix + "partial"
        return prefix + "block"
    if any(p not in ("/", "") for p, _ in meaningful):
        return prefix + "partial"
    return prefix + "allow"


def crawl_rules(raw: str, ua: str):
    """우리 UA에 적용되는 Disallow/Allow 규칙 (없으면 User-agent:* 규칙).

    정책 판정(robots_policy)과 같은 제품 토큰 일치를 쓴다.
    """
    target = ua_token(ua)
    exact: list[tuple[str, bool]] = []
    star: list[tuple[str, bool]] = []
    has_exact = False
    for agents, rules in parse_robots(raw):
        if target in agents:
            has_exact = True
            exact.extend(rules)
        if "*" in agents:
            star.extend(rules)
    return exact if has_exact else star


def _token_as_written(value: str) -> str:
    return re.split(r"[/\s]", (value or "").strip(), maxsplit=1)[0]


def robots_nonstandard_agents(raw: str) -> list:
    """제품 토큰만이 아닌 User-agent 값(버전·전체 브라우저 UA 문자열)을 모은다.

    판정은 토큰으로 하므로 이 도구의 결과는 맞지만, 값을 통째로 비교하는 파서도 있다 — 정보로 남긴다.
    """
    out, seen = [], set()
    for group in _robots_groups(raw):
        for value in group["agents"]:
            if value and re.search(r"[/\s]", value) and value not in seen:
                seen.add(value)
                out.append({"value": value, "token": _token_as_written(value)})
    return out


def _parse_content_signal(value: str) -> dict:
    parsed = {}
    for part in (value or "").split(","):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key.strip():
            parsed[key.strip().lower()] = val.strip().lower()
    return parsed


def robots_content_signals(raw: str) -> list:
    """그룹별 Content-Signal 줄 (Cloudflare 문서 표기 `Content-signal:`, 대소문자 비구분).

    선호 표현일 뿐 접근 제어가 아니다 — 허용·차단 판정에 절대 쓰지 않는다.
    """
    out = []
    for group in _robots_groups(raw):
        if not group["signals"]:
            continue
        values = {}
        for line in group["signals"]:
            values.update(_parse_content_signal(line))
        out.append({"agents": list(group["agents"]), "signals": values,
                    "raw": list(group["signals"])})
    return out


CF_MANAGED_BEGIN = re.compile(r"^[ \t]*#[ \t]*BEGIN Cloudflare Managed content\b.*$", re.I | re.M)
CF_MANAGED_END = re.compile(r"^[ \t]*#[ \t]*END Cloudflare Managed Content\b.*$", re.I | re.M)


def cdn_managed_span(raw: str):
    """Cloudflare 관리 블록의 (시작, 끝, 끝표식 있음) — 없으면 None. 끝 표식이 없으면 파일 끝까지."""
    begin = CF_MANAGED_BEGIN.search(raw or "")
    if not begin:
        return None
    end = CF_MANAGED_END.search(raw, begin.end())
    return begin.start(), (end.end() if end else len(raw)), bool(end)


def cdn_managed_info(raw: str):
    """Cloudflare가 엣지에서 붙인 관리 블록 정보. 없으면 None.

    blocked_uas는 관리 블록 **안의** 그룹만 보고 루트(/)가 막힌 UA다 — 원본 쪽 그룹과 합친
    실효 정책은 policies가 따로 말한다.
    """
    span = cdn_managed_span(raw)
    if not span:
        return None
    start, end, closed = span
    blocked = []
    for group in _robots_groups(raw[start:end]):
        if group["rules"] and not crawl_allowed(group["rules"], "/"):
            for value in group["agents"]:
                token = _token_as_written(value)
                if token and token != "*" and token not in blocked:
                    blocked.append(token)
    return {"provider": "cloudflare", "blocked_uas": blocked, "end_marker": closed}


def robots_fetch_state(res: dict) -> str:
    """robots.txt 응답 분류 (RFC 9309 2.3.1).

    ok          — 2xx 텍스트. 원문을 판정한다.
    absent      — 404·410 등 4xx(429 제외), 또는 200으로 꾸민 HTML 오류 페이지 → 제한 없음으로 본다.
    unreachable — 429·5xx·시간 초과·접속 실패·상태 없음·따라가지 못한 리다이렉트 → 정책 미확인.
                  RFC 9309는 서버 오류·접속 불가일 때 전체 차단으로 간주하라고 하고,
                  Google도 429를 5xx처럼 다룬다. 0이나 "허용"으로 바꿔 적지 않는다.
    """
    status = res.get("status")
    error = res.get("error")
    if status is None or status == 429 or status >= 500:
        return "unreachable"
    if 200 <= status < 300:
        if error and error != "body_truncated":
            return "unreachable"
        if (res.get("body") or "").lstrip().startswith("<"):
            return "absent"
        return "ok"
    if 400 <= status < 500:
        return "absent"
    return "unreachable"


def crawl_allowed(rules, path: str) -> bool:
    """가장 구체적인 robots 규칙을 적용한다. 같은 길이면 Allow가 이긴다."""
    best = None  # (len, is_allow)
    for rule_path, is_allow in rules:
        if not rule_path:
            continue
        anchored = rule_path.endswith("$")
        source = rule_path[:-1] if anchored else rule_path
        pattern = "^" + re.escape(source).replace(r"\*", ".*") + ("$" if anchored else "")
        if not re.search(pattern, path):
            continue
        n = len(source.replace("*", ""))
        if best is None or n > best[0] or (n == best[0] and is_allow):
            best = (n, is_allow)
    return best[1] if best else True


# ─────────────────────────────────────────────────────────── HTTP

# ── 주소 범위 보호 (SSRF·DNS 재바인딩) ───────────────────────────────────
#
# fetch()는 연결 직전에 호스트를 풀고, 풀린 주소가 하나라도 "허용 범위" 밖이면 접속하지 않는다
# (error=nonpublic_address — HTTP 상태가 아니라 미관측이다). 통과하면 검사한 그 주소로 직접
# 연결한다(pin) — Host 헤더와 TLS SNI·인증서 검사는 원래 호스트 이름을 그대로 쓴다. 그래서
# "검사할 때는 공개 주소, 연결할 때는 내부 주소"로 바꿔 치는 DNS 재바인딩이 끼어들 틈이 없다.
#
# 허용 범위는 진단 대상(target_scope)이 정한다. 공개 주소는 늘 허용이다. 진단 대상 자신이
# 비공개 주소(127.0.0.1 테스트 서버·사내망 이름)면 그 대상이 속한 분류(loopback·private …)만
# 추가로 허용한다 — 공개 대상에서 사설·루프백 주소로 넘어가는 요청은 늘 거부된다.
#
# DNS 조회(socket.getaddrinfo)에는 시간 제한이 없다. 그래서 조회마다 별도 데몬 스레드에서 돌리고
# 요청 시간 제한(TIMEOUT)만큼만 기다린다. 넘기면 error=dns_timeout — dns_fail처럼 미관측이다.
#
# HTTP(S) 프록시를 거치면(urllib가 따르는 *_proxy 환경 변수, NO_PROXY로 빠지지 않는 호스트) 대상
# 주소는 프록시가 푼다. 로컬 DNS로 검사는 하지만 실제로 닿는 주소를 증명하지 못하므로, 프록시를
# 쓴 사실을 범위(_TargetScope.proxied)에 남긴다. --direct(direct_connection)는 프록시 환경 변수를
# 무시하고 직접 연결해 검사한 주소로 고정한다.

_SHARED_NET = ipaddress.ip_network("100.64.0.0/10")        # CGNAT (RFC 6598)
_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")          # NAT64 well-known prefix (RFC 6052)
_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
_SCHEME_PORTS = {"http": 80, "https": 443}


def address_class(address) -> str:
    """주소 분류: public · loopback · private · link_local · shared · multicast · unspecified ·
    reserved. IP가 아니면 invalid. IPv4-mapped(::ffff:a.b.c.d)·6to4·NAT64는 안에 든 IPv4로 판정한다.
    """
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0].strip("[]"))
    except ValueError:
        return "invalid"
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in _NAT64_NET:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return address_class(embedded)
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.version == 4 and ip in _SHARED_NET:
        return "shared"
    if any(ip in net for net in _PRIVATE_NETS if net.version == ip.version):
        return "private"
    if ip.is_reserved or ip.is_private or not ip.is_global:
        return "reserved"
    return "public"


class NonPublicAddress(OSError):
    """허용 범위 밖 주소로 풀렸다 — 접속하지 않았다."""

    def __init__(self, host, klass):
        super().__init__("%s resolves to a %s address outside the audit scope" % (host, klass))
        self.host = host
        self.address_class = klass


class DNSTimeout(OSError):
    """DNS 조회가 요청 시간 제한 안에 끝나지 않았다 — 접속하지 않았다(미관측)."""

    def __init__(self, host, timeout):
        super().__init__("DNS lookup for %s exceeded %.1fs" % (host, timeout))
        self.host = host


# 끝나지 않은 조회 스레드가 무한히 쌓이지 않게 동시 조회 수를 묶는다.
_DNS_SLOTS = threading.BoundedSemaphore(16)


def _dns_timeout(timeout) -> float:
    """연결 함수가 받은 timeout을 조회 제한으로 쓴다. 숫자가 아니면(기본값 객체 등) TIMEOUT."""
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        return float(timeout)
    return float(TIMEOUT)


def _resolve(host: str, port: int, timeout=None) -> list:
    """getaddrinfo를 데몬 스레드에서 돌리고 timeout(기본 TIMEOUT)초만 기다린다.

    늦은 리졸버가 크롤 전체를 붙잡지 못하게 한다. 시간을 넘기면 DNSTimeout. 버려진 스레드는
    데몬이라 종료를 막지 않고, 세마포어가 그 수를 묶는다.
    """
    limit = _dns_timeout(timeout)
    started = time.monotonic()
    if not _DNS_SLOTS.acquire(timeout=limit):
        raise DNSTimeout(host, limit)
    remaining = limit - (time.monotonic() - started)
    if remaining <= 0:
        _DNS_SLOTS.release()
        raise DNSTimeout(host, limit)
    results = queue.Queue(maxsize=1)

    def run():
        try:
            results.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except BaseException as exc:   # 호출한 스레드에서 그대로 다시 올린다
            results.put((False, exc))
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=run, name="su-presence-dns", daemon=True).start()
    try:
        ok, value = results.get(timeout=remaining)
    except queue.Empty:
        raise DNSTimeout(host, limit) from None
    if not ok:
        raise value
    if not value:
        raise socket.gaierror(socket.EAI_NONAME, "no address for %s" % host)
    return value


class _TargetScope:
    """진단 대상이 허용하는 주소 분류. DNS 조회는 실제 연결이 처음 필요할 때 한 번만 한다."""

    def __init__(self, url: str):
        try:
            parts = urllib.parse.urlsplit(url if "//" in url else "//" + url)
            self.host = (parts.hostname or "").rstrip(".").lower()
            self.port = parts.port or _SCHEME_PORTS.get(parts.scheme.lower(), 443)
        except ValueError:
            self.host, self.port = "", 443
        self._allowed = None
        # 이 범위 안의 요청 중 하나라도 HTTP(S) 프록시를 거쳤나 — 주소 검사가 로컬 DNS뿐이었다는 표시.
        self.proxied = False

    def allowed(self, timeout=None) -> frozenset:
        """허용 분류. 대상 이름 조회가 시간 제한을 넘기면 DNSTimeout을 그대로 올린다 — 이때는
        결과를 캐시하지 않는다(일시적으로 늦은 응답이 감사 전체의 허용 범위를 줄이지 않게)."""
        if self._allowed is None:
            classes = {"public"}
            literal = address_class(self.host)
            if literal != "invalid":
                classes.add(literal)
            elif self.host:
                if self.host == "localhost" or self.host.endswith(".localhost"):
                    classes.add("loopback")
                try:
                    classes.update(address_class(row[4][0])
                                   for row in _resolve(self.host, self.port, timeout))
                except DNSTimeout:
                    raise
                except (OSError, UnicodeError):
                    pass   # 대상이 안 풀리면 공개 주소만 허용한다
            classes.discard("invalid")
            self._allowed = frozenset(classes)
        return self._allowed


_SCOPE: contextvars.ContextVar = contextvars.ContextVar("su_presence_target_scope", default=None)


@contextlib.contextmanager
def target_scope(url: str):
    """이 블록 안의 fetch()는 url의 호스트를 진단 대상으로 보고 주소 범위를 정한다.

    이미 범위가 잡혀 있으면(바깥 build_report 등) 그 범위를 그대로 쓴다 — 미러·www↔apex 변형이
    스스로 범위를 넓히지 못하게 한다.
    """
    current = _SCOPE.get()
    if current is not None:
        yield current
        return
    scope = _TargetScope(url)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def _scoped(url_of):
    """함수 전체를 진단 대상(url_of가 인자에서 고른다)의 target_scope 안에서 실행한다."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with target_scope(url_of(*args, **kwargs)):
                return fn(*args, **kwargs)
        return wrapper
    return decorate


def _checked_rows(host: str, port: int, timeout=None) -> list:
    """host를 풀고, 주소가 하나라도 허용 범위 밖이면 NonPublicAddress. 통과한 행을 돌려준다.

    두 조회(host, 범위가 처음 정해질 때 진단 대상)를 합쳐 timeout(기본 TIMEOUT) 하나로 묶는다.
    넘기면 DNSTimeout. 범위는 첫 연결에서 바로 정한다 — 공인 주소로 시작한 대상이 나중에 비공개
    주소로 바뀌어 풀려도(재바인딩) 범위가 그 주소를 따라 넓어지지 않게 한다.
    """
    limit = _dns_timeout(timeout)
    deadline = time.monotonic() + limit
    rows = _resolve(host, port, limit)
    scope = _SCOPE.get() or _TargetScope("//%s:%d" % ("[%s]" % host if ":" in host else host, port))
    remaining = deadline - time.monotonic()
    if remaining <= 0 and scope._allowed is None:
        raise DNSTimeout(scope.host or host, limit)
    allowed = scope.allowed(max(remaining, 0.001))
    for row in rows:
        klass = address_class(row[4][0])
        if klass not in allowed:
            raise NonPublicAddress(host, klass)
    return rows


def _connect_rows(rows, timeout, source_address):
    """검사를 통과한 주소로만 연결한다 (socket.create_connection과 같은 순서·예외)."""
    error = None
    for family, socktype, proto, _canon, sockaddr in rows:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is None or isinstance(timeout, (int, float)):
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            if sock is not None:
                sock.close()
    raise error if error is not None else OSError("no address to connect")


def _guarded_connection(base_cls, req):
    """http.client 연결 클래스를 감싼다 — 연결 직전에 대상 주소를 검사하고 고정한다.

    http.client는 Host 헤더·SNI에 원래 호스트 이름을 쓰고, 소켓만 _create_connection으로 만든다.
    그 자리에 검사한 주소로 연결하는 함수를 넣는다. urllib의 리다이렉트도 홉마다 새 연결이라
    같은 검사를 다시 거친다.

    프록시를 거치면 실제 대상 주소는 프록시가 푼다. 그때는 로컬 DNS로 대상 이름을 검사만 하고
    (로컬에서 안 풀리거나 시간 안에 안 풀리면 검사할 수 없으니 통과) 프록시에 연결한다. 프록시를
    거쳤다는 사실은 범위(_TargetScope.proxied)에 남긴다 — audit.json·verify.json이 이를 밝힌다.
    """
    target = urllib.parse.urlsplit(req.full_url)
    target_host = target.hostname or ""
    target_port = target.port or _SCHEME_PORTS.get(target.scheme.lower(), 80)
    via_proxy = req.has_proxy() or bool(getattr(req, "_tunnel_host", None))

    def make(host, **kwargs):
        conn = base_cls(host, **kwargs)

        def create(address, timeout=None, source_address=None):
            if via_proxy:
                scope = _SCOPE.get()
                if scope is not None:
                    scope.proxied = True
                try:
                    _checked_rows(target_host, target_port, timeout)
                except (socket.gaierror, DNSTimeout):
                    pass
                return socket.create_connection(address, timeout, source_address)
            return _connect_rows(_checked_rows(address[0], address[1], timeout), timeout, source_address)

        conn._create_connection = create
        return conn
    return make


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_guarded_connection(http.client.HTTPConnection, req), req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_guarded_connection(http.client.HTTPSConnection, req), req,
                            context=self._context)


class _CountingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin_host, rules=None, max_redirects=10):
        self.count = 0
        self.origin_host = origin_host
        self.rules = rules
        self.max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        parts = urllib.parse.urlsplit(newurl)
        if self.count > self.max_redirects or parts.scheme not in ("http", "https") or parts.netloc.lower() != self.origin_host:
            return None
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        if self.rules is not None and not crawl_allowed(self.rules, target):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_DIRECT: contextvars.ContextVar = contextvars.ContextVar("su_presence_direct", default=False)


@contextlib.contextmanager
def direct_connection(enabled: bool = True):
    """이 블록 안의 fetch()는 프록시 환경 변수(http_proxy·https_proxy 등)를 무시하고 직접 연결한다.

    프록시를 거치면 대상 주소는 프록시가 풀어 주소 검사가 로컬 DNS에 그친다. 직접 연결하면
    검사한 그 주소로 고정해 연결하므로 비공개 주소 차단이 실제 연결을 덮는다(--direct).
    """
    token = _DIRECT.set(bool(enabled))
    try:
        yield
    finally:
        _DIRECT.reset(token)


def network_record(scope=None, direct=None):
    """audit.json coverage.network / verify.json target.network 선택 필드.

    프록시를 거쳤으면 {"proxy": true, "address_check": "local_dns_only"}, --direct면
    {"proxy": false, "direct": true, "address_check": "connected_address"}. 그 밖에는 None(필드 없음).
    프록시 주소·자격 증명은 남기지 않는다.
    """
    scope = scope if scope is not None else _SCOPE.get()
    direct = _DIRECT.get() if direct is None else direct
    if scope is not None and getattr(scope, "proxied", False):
        return {"proxy": True, "address_check": "local_dns_only"}
    if direct:
        return {"proxy": False, "direct": True, "address_check": "connected_address"}
    return None


PROXY_NOTE_KO = ("HTTP(S) 프록시를 거쳐 요청했다 — 대상 주소는 프록시가 풀었으므로 비공개 주소 차단은 "
                 "로컬 DNS 검사에 그쳤고 실제로 연결된 주소는 확인하지 못했다. 주소 검사가 실제 연결을 "
                 "덮게 하려면 --direct로 프록시 없이 다시 실행하라.")


def _response_headers(message) -> dict:
    """HTTPMessage를 소문자 dict로 바꾸되 반복 X-Robots-Tag를 모두 보존한다."""
    if message is None:
        return {}
    out = {}
    seen = set()
    for original in message.keys():
        key = original.lower()
        if key in seen:
            continue
        seen.add(key)
        values = message.get_all(original) or []
        values = [str(value).strip() for value in values if value is not None]
        if not values:
            continue
        # 줄마다 UA scope가 새로 시작할 수 있으므로 X-Robots-Tag의 필드 경계를 남긴다.
        out[key] = "\n".join(values) if key == "x-robots-tag" else ", ".join(values)
    return out


def fetch(url: str, method: str = "GET", rules=None, max_redirects: int = 10):
    """한 번 받아온다. 예외를 던지지 않고 dict로 돌려준다.

    리다이렉트는 같은 호스트 안에서만 max_redirects번까지 따라간다. 다른 호스트로 가는
    리다이렉트는 따라가지 않고 external_redirect_blocked로 남긴다.
    """
    out = {
        "status": None, "final_url": url, "headers": {}, "body": "",
        "ms": 0, "redirects": 0, "error": None, "content_type": "",
    }
    redirector = _CountingRedirect(host_of(url), rules, max_redirects)
    handlers = [redirector, _GuardedHTTPHandler(), _GuardedHTTPSHandler()]
    if _DIRECT.get():
        # 빈 ProxyHandler가 기본(환경 변수) ProxyHandler를 대신한다 — 프록시를 쓰지 않는다.
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko,en;q=0.8",
    })
    started = time.monotonic()
    try:
        with target_scope(url), opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_BODY + 1)
            out["status"] = resp.status
            out["final_url"] = resp.geturl()
            out["headers"] = _response_headers(resp.headers)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(1_000_000)
        except Exception:
            pass
        out["status"] = exc.code
        out["final_url"] = exc.url or url
        out["headers"] = _response_headers(exc.headers)
        location = out["headers"].get("location")
        if exc.code in (301, 302, 303, 307, 308) and location:
            redirected = urllib.parse.urljoin(exc.url or url, location)
            parts = urllib.parse.urlsplit(redirected)
            target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            if host_of(redirected) != host_of(url):
                out["final_url"] = redirected
                out["error"] = "external_redirect_blocked"
            elif rules is not None and not crawl_allowed(rules, target):
                out["final_url"] = redirected
                out["error"] = "redirect_blocked_by_robots"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as exc:
        out["ms"] = int((time.monotonic() - started) * 1000)
        out["error"] = _classify_error(exc)
        refused = getattr(exc, "reason", exc)
        if isinstance(refused, NonPublicAddress):
            # 선택 필드 — 접속하지 않은 주소의 분류만 남긴다(내부 IP 자체는 적지 않는다).
            out["address_class"] = refused.address_class
        return out
    out["ms"] = int((time.monotonic() - started) * 1000)
    out["redirects"] = redirector.count
    input_truncated = len(body) > MAX_BODY
    if input_truncated:
        body = body[:MAX_BODY]
        out["error"] = out["error"] or "body_truncated"
    if not input_truncated and (out["headers"].get("content-encoding", "").lower() == "gzip" or
            urllib.parse.urlsplit(url).path.lower().endswith(".gz")):
        try:
            body, expanded_truncated = _bounded_gunzip(body, MAX_BODY)
            if expanded_truncated:
                out["error"] = out["error"] or "body_truncated"
        except (OSError, EOFError):
            out["error"] = "invalid_gzip"
            body = b""
    out["content_type"] = out["headers"].get("content-type", "")
    out["truncated"] = input_truncated or len(body) > MAX_BODY or out["error"] == "body_truncated"
    body = body[:MAX_BODY]
    out["body"] = _decode(body, out["content_type"])
    return out


# RFC 9309 2.3.1.2: robots.txt 리다이렉트는 연속 5번 이상 따라가야 한다(Google도 5홉).
ROBOTS_MAX_REDIRECTS = 5
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_key(url: str):
    """비교용 호스트[:포트]. 스킴 기본 포트는 떼어 낸다. 읽을 수 없으면 None."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if ":" in host:
        host = "[%s]" % host
    if port is not None and port != _DEFAULT_PORTS.get(parts.scheme.lower()):
        host = "%s:%d" % (host, port)
    return host


@_scoped(lambda base, *_a, **_k: base)
def fetch_robots(base: str) -> dict:
    """robots.txt를 받는다. 진단 대상 호스트와 그 www↔apex 변형 사이의 리다이렉트만 따라간다.

    fetch()는 다른 호스트로 가는 리다이렉트를 따라가지 않는다(SSRF 방지). robots.txt만 예외로,
    http(s)이고 대상 호스트 또는 www↔apex 변형(probe_site가 이미 직접 요청하는 호스트)일 때만
    한 홉씩 다시 fetch()로 받는다 — 각 홉은 fetch()의 같은 보호를 그대로 거친다.
    같은 호스트 안의 홉과 www↔apex 홉을 합친 체인 전체가 ROBOTS_MAX_REDIRECTS홉까지다.

    돌려주는 dict는 fetch()와 같고, 다음 키가 붙는다.
      robots_url        — 처음 요청한 robots.txt 주소
      redirect_hosts    — www↔apex로 넘어간 홉 수 (0이면 같은 호스트 안에서 끝났다)
      redirect_reason   — 따라가지 않은 이유: other_host · too_many_redirects (따라갔으면 없음)
    """
    url = urllib.parse.urljoin(base, "/robots.txt")
    origin = _host_key(url)
    alt = alt_host_of(origin) if origin else None
    allowed = {h for h in (origin, alt) if h}
    # 같은 호스트 안의 리다이렉트도 체인 전체 한도(5홉)에 넣는다 — fetch() 기본값(10)을 쓰면
    # 6~10홉 체인을 따라가 판정해 버린다(크롤러와 audit.sh는 5홉에서 멈춘다).
    res = fetch(url, max_redirects=ROBOTS_MAX_REDIRECTS)
    total = res.get("redirects") or 0
    crossed = 0
    reason = None
    while res.get("error") == "external_redirect_blocked" and res.get("status") in REDIRECT_STATUSES:
        target = res.get("final_url") or ""
        scheme = urllib.parse.urlsplit(target).scheme.lower()
        if scheme not in ("http", "https") or _host_key(target) not in allowed:
            reason = "other_host"
            break
        if total > ROBOTS_MAX_REDIRECTS:
            reason = "too_many_redirects"
            break
        crossed += 1
        res = fetch(target, max_redirects=ROBOTS_MAX_REDIRECTS - total)
        total += res.get("redirects") or 0
    if reason is None and (crossed or total > ROBOTS_MAX_REDIRECTS) and (
            total > ROBOTS_MAX_REDIRECTS or res.get("status") in REDIRECT_STATUSES):
        # 남은 홉 수를 다 쓰고도 3xx가 남았다 — 같은 호스트 안이든 www↔apex를 섞었든 연속 5홉을 넘겼다.
        reason = "too_many_redirects"
    if reason == "too_many_redirects":
        res["error"] = "too_many_redirects"
    res["redirects"] = total
    res["robots_url"] = url
    res["redirect_hosts"] = crossed
    res["redirect_reason"] = reason
    return res


def _bounded_gunzip(body: bytes, limit: int) -> tuple[bytes, bool]:
    """압축 폭탄이 메모리를 무제한 소비하지 않도록 limit+1 바이트만 푼다."""
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        expanded = stream.read(limit + 1)
    return expanded[:limit], len(expanded) > limit


def _classify_error(exc) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, NonPublicAddress) or isinstance(exc, NonPublicAddress):
        return "nonpublic_address"
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return "tls_fail"
    if isinstance(reason, DNSTimeout) or isinstance(exc, DNSTimeout):
        return "dns_timeout"
    if isinstance(reason, socket.gaierror):
        return "dns_fail"
    if isinstance(reason, socket.timeout) or isinstance(exc, socket.timeout):
        return "timeout"
    return "error: %s" % (reason,)


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    if not charset:
        match = re.search(rb'charset=["\']?([\w-]+)', body[:4096], re.I)
        if match:
            charset = match.group(1).decode("ascii", "replace")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────── HTML 파싱

# <head> 안에 올 수 있는 요소. 이 밖의 시작 태그가 나오면 head가 끝난 것으로 본다(HTML 파싱 규칙).
_HEAD_TAGS = frozenset(("html", "head", "title", "meta", "link", "base", "script", "style",
                        "noscript", "template"))


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta_description = None
        self.meta_robots = None
        self.meta_googlebot = None
        self.meta_googlebot_news = None
        self.canonical = None
        self.lang = None
        self.naver_site_verification = False
        self.og = {}
        self.h1 = []
        self.jsonld_raw = []
        self.links = []
        # <head>의 <link rel="alternate" hreflang href> — (hreflang 원문, href 원문) 순서대로
        self.hreflang_links = []
        self._head_closed = False
        self._title_buf = []
        self._h1_buf = []
        self._ld_buf = []
        self._in_title = False
        self._in_h1 = 0
        self._in_ld = False
        self._skip = 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag not in _HEAD_TAGS:
            self._head_closed = True   # <body>나 본문 요소가 나오면 </head>가 생략돼도 head는 끝났다
        if tag == "html" and not self.lang:
            self.lang = a.get("lang") or None
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            self._meta(a)
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "canonical" in rel and not self.canonical:
                self.canonical = a.get("href") or None
            # hreflang은 <head>의 link만 유효하다. rel은 공백으로 나뉜 토큰 목록이다.
            if (not self._head_closed and "alternate" in rel.split()
                    and a.get("hreflang", "").strip() and a.get("href", "").strip()):
                self.hreflang_links.append((a["hreflang"], a["href"]))
        elif tag == "a":
            href = a.get("href")
            if href:
                self.links.append(href)
        elif tag == "h1":
            self._in_h1 += 1
            self._h1_buf = []
        elif tag == "script":
            if "ld+json" in a.get("type", "").lower():
                self._in_ld = True
                self._ld_buf = []
            else:
                self._skip += 1
        elif tag in ("style", "noscript", "template"):
            self._skip += 1

    def _meta(self, a):
        name = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
        content = a.get("content") or ""
        if name == "description" and self.meta_description is None:
            self.meta_description = content
        elif name == "robots":
            self.meta_robots = ", ".join(filter(None, (self.meta_robots, content))) or None
        elif name == "googlebot":
            self.meta_googlebot = ", ".join(filter(None, (self.meta_googlebot, content))) or None
        elif name == "googlebot-news":
            self.meta_googlebot_news = ", ".join(filter(None, (self.meta_googlebot_news, content))) or None
        elif name == "naver-site-verification":
            self.naver_site_verification = True
        elif name.startswith("og:"):
            key = name[3:]
            if key in ("title", "description", "image", "url") and key not in self.og:
                self.og[key] = content

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
            self._in_ld = False

    def handle_endtag(self, tag):
        if tag == "head":
            self._head_closed = True
        elif tag == "title":
            self._in_title = False
            self.title = WS.sub(" ", "".join(self._title_buf)).strip() or None
        elif tag == "h1" and self._in_h1:
            self._in_h1 -= 1
            text = WS.sub(" ", "".join(self._h1_buf)).strip()
            self.h1.append(text)
            self._h1_buf = []
        elif tag == "script":
            if self._in_ld:
                self._in_ld = False
                self.jsonld_raw.append("".join(self._ld_buf))
            else:
                self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
            return
        if self._in_title:
            self._title_buf.append(data)
        if self._skip:
            return
        if self._in_h1:
            self._h1_buf.append(data)
        self._text.append(data)

    @property
    def text(self) -> str:
        """가시 텍스트 — 태그·script·style 제거 후 공백 정규화."""
        return WS.sub(" ", "".join(self._text)).strip()

    @property
    def text_chars(self) -> int:
        return len(self.text)


def jsonld_types(blocks) -> list:
    """JSON-LD 블록에서 @type을 전부 긁는다 (@graph·중첩 포함)."""
    types = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                types.append(t)
            elif isinstance(t, list):
                types.extend([x for x in t if isinstance(x, str)])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    ok = 0
    for block in blocks:
        try:
            walk(json.loads(block))
            ok += 1
        except (ValueError, TypeError):
            continue
    return ok, types


# ─────────────────────────────────────────────────────────── URL 정규화

def normalize(url: str) -> str:
    """프래그먼트만 떼고 스킴·호스트를 소문자로. query는 보존한다."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    return urllib.parse.urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "",
    ))


def is_page(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return not path.endswith(ASSET_EXT)


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def safe_host(host: str) -> str:
    """호스트를 폴더 이름으로 — Windows는 ':'를 파일명에 못 쓴다 (127.0.0.1:8000 같은 대상)."""
    return re.sub(r'[:*?"<>|\\/]', "_", host) or "site"


def alt_host_of(host: str):
    """www↔apex 변형 주소. IP·localhost에는 변형이 없다 → None (엉뚱한 DNS 조회를 막는다)."""
    if host.startswith("www."):
        return host[4:]
    bare = host.split(":")[0]
    if bare in ("localhost", "") or re.fullmatch(r"[\d.]+|\[[0-9a-f:]+\]", bare):
        return None
    return "www.%s" % host


# 흔한 개발·스테이징 접두. 미러가 색인되면 본진 대신 그쪽이 인용된다.
MIRROR_PREFIXES = ("dev", "staging", "stage", "test", "beta", "preview", "new", "old")


@_scoped(lambda host, *_a, **_k: "https://%s/" % host)
def probe_mirrors(host: str) -> dict:
    """공개된 개발·스테이징 미러를 찾는다. 존재만 확인하고 크롤하지 않는다.

    본 도메인만 훑어서는 절대 보이지 않는 표면이다 — 실측에서 생성엔진이 우리 개발서버를
    "회사 홈페이지"라며 인용한 일이 있었고, 크롤 쪽은 그 호스트를 볼 일이 없어 놓쳤다.

    후보 이름이 진단 대상의 주소 범위 밖(공개 대상인데 사설·루프백 등)으로 풀리면 접속하지 않고
    nonpublic에 {"host", "address_class"}로 남긴다 — 공개 DNS에 내부 주소가 실린 것 자체가 메모거리다.
    """
    bare = host[4:] if host.startswith("www.") else host
    bare = bare.split(":")[0]
    if (not bare) or bare == "localhost" or bare.startswith("[") or "." not in bare:
        return {"checked": [], "found": [], "wildcard_suspect": False}
    if all(c.isdigit() or c == "." for c in bare):
        return {"checked": [], "found": [], "wildcard_suspect": False}

    checked, found, nonpublic = [], [], []
    for prefix in MIRROR_PREFIXES:
        cand = "%s.%s" % (prefix, bare)
        if cand == host:
            continue
        checked.append(cand)
        res = fetch("https://%s/" % cand)
        if res["error"] == "nonpublic_address":
            nonpublic.append({"host": cand, "address_class": res.get("address_class") or "unknown"})
            continue
        if res["error"] in ("dns_fail", "dns_timeout", "tls_fail") or not res["status"]:
            continue
        if res["status"] >= 400:
            continue
        # 본진으로 넘기는 미러는 위험하지 않다 — 외부 리다이렉트로 걸러진다
        if res["error"] == "external_redirect_blocked":
            continue
        rb = fetch("https://%s/robots.txt" % cand)
        raw = (rb.get("body") or "") if rb["status"] == 200 and not rb.get("error") else ""
        robots_known = (rb["status"] in (404, 410) and not rb.get("error")) or (
            rb["status"] == 200 and not rb.get("error") and not raw.lstrip().startswith("<"))
        if not robots_known:
            raw = ""
        blocked = robots_policy(raw, "Googlebot").endswith("block") if raw else False
        page = page_record("https://%s/" % cand, res)
        directives = sorted(_robots_directives(page))
        if res["status"] == 200 and "noindex" in directives:
            indexability = "noindex"
        elif blocked:
            indexability = "robots_blocked"
        elif (robots_known and res["status"] == 200 and not res.get("error")
              and res.get("body") and "html" in (res.get("content_type") or "").lower()):
            indexability = "unrestricted"
        else:
            indexability = "unknown"
        found.append({"host": cand, "status": res["status"],
                      "final_url": res["final_url"], "robots_blocks_all": blocked,
                      "indexability": indexability, "robots_directives": directives,
                      "checked_url": "https://%s/" % cand})
    # 와일드카드 DNS면 아무 접두나 응답한다. 전부 떴다면 발견이 아니라 설정이다.
    # 내부 주소로 풀린 후보도 이름이 풀린 것이므로 같이 센다.
    wildcard = len(found) + len(nonpublic) >= max(4, len(checked) - 1)
    out = {"checked": checked, "found": [] if wildcard else found,
           "wildcard_suspect": wildcard}
    if nonpublic and not wildcard:
        out["nonpublic"] = nonpublic   # 선택 필드 — 옛 audit에는 없다
    return out


def script_of(text: str) -> str:
    """길이 기준을 정할 문자 종류: 한글 비중이 높으면 ko."""
    if not text:
        return "en"
    return "ko" if len(HANGUL.findall(text)) >= max(1, len(text) * 0.15) else "en"


# ─────────────────────────────────────────────────────────── 크롤

@_scoped(lambda base, *_a, **_k: base)
def crawl_site(base: str, max_pages: int, delay: float, rules, seeds=None, coverage=None) -> list:
    host = host_of(base)
    seen = set()
    queue = deque()
    blocked_count = 0
    blocked_seeds = 0
    dropped_discoveries = 0
    seed_list = list(dict.fromkeys([normalize(base)] + [normalize(u) for u in (seeds or [])]))
    for seed in seed_list:
        parts = urllib.parse.urlsplit(seed)
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        eligible = host_of(seed) == host and is_page(seed)
        if not eligible or not crawl_allowed(rules, target):
            if eligible and coverage is not None:
                browser_review.offer(coverage, base, seed, "disallow")
            blocked_count += 1
            blocked_seeds += 1
            continue
        if seed not in seen:
            seen.add(seed)
            queue.append(seed)
    pages = []
    linked = set()   # 크롤한 페이지의 원시 HTML 링크로 닿은 URL — 사이트맵 시드와 구별한다
    start = normalize(base)
    while queue and len(pages) < max_pages:
        url = queue.popleft()
        res = fetch(url, rules=rules)
        if res.get("error") == "redirect_blocked_by_robots" and coverage is not None:
            browser_review.offer(coverage, base, res["final_url"], "disallow")
        page = page_record(url, res)
        pages.append(page)
        sys.stderr.write("  · %-4s %s\n" % (page["status"] or "ERR", url))
        sys.stderr.flush()
        same_host = page["status"] == 200 and host_of(res["final_url"] or url) == host
        links = page.pop("_links", []) if same_host else []
        page.pop("_links", None)
        is_html = (not res["error"] and bool(res["body"])
                   and "html" in (res["content_type"] or "text/html").lower())
        # 원시 HTML의 <a href>로 닿는 내부 페이지 수. 스크립트로만 그리는 메뉴는 0이 된다.
        # 판정할 수 없는 응답(오류·외부 호스트)은 None으로 둔다 — 0과 구별한다.
        page_links = set()
        for href in links:
            try:
                nxt = normalize(urllib.parse.urljoin(res["final_url"] or url, href))
            except ValueError:
                continue
            if not nxt.startswith(("http://", "https://")):
                continue
            if host_of(nxt) != host or not is_page(nxt):
                continue
            if nxt not in (url, normalize(res["final_url"] or url)):
                page_links.add(nxt)
                linked.add(nxt)
            if nxt in seen:
                continue
            parts = urllib.parse.urlsplit(nxt)
            target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            if not crawl_allowed(rules, target):
                if coverage is not None:
                    browser_review.offer(coverage, base, nxt, "disallow")
                blocked_count += 1
                continue
            seen.add(nxt)
            if len(seen) <= max_pages * 4:
                queue.append(nxt)
            else:
                dropped_discoveries += 1
        page["internal_links"] = len(page_links) if same_host and is_html else None
        if delay:
            time.sleep(delay)
    linked_keys = {_link_key(u) for u in linked}
    # 시작 페이지가 /ko/ 같은 곳으로 리다이렉트되면 그 도착지도 사이트맵 시드로 따로 잡힌다 —
    # 시작점의 도착지는 링크로 닿은 것으로 본다.
    for page in pages:
        if page["url"] == start and page.get("final_url"):
            linked_keys.add(_link_key(page["final_url"]))
    for page in pages:
        page["linked_from_html"] = (page["url"] == start or _link_key(page["url"]) in linked_keys
                                    or _link_key(page.get("final_url") or "") in linked_keys)
    if coverage is not None:
        network_errors = sum(1 for p in pages if p.get("error") or p.get("status") is None)
        http_errors = sum(1 for p in pages if p.get("status") is not None and p.get("status", 0) >= 400)
        reasons = []
        if queue:
            reasons.append("max_pages_reached")
        if dropped_discoveries:
            reasons.append("discovery_queue_truncated")
        # 내부 링크의 의도적 제외는 정상 robots 정책이다. 시작 URL 또는
        # sitemap의 색인 후보를 검사하지 못했을 때만 범위 불완전으로 본다.
        if blocked_seeds:
            reasons.append("seed_blocked_by_robots")
        if network_errors:
            reasons.append("network_errors")
        if any(p.get("error") == "nonpublic_address" for p in pages):
            # 대상 범위 밖 주소로 풀려 받지 않은 페이지 — 깨진 링크가 아니라 미관측이다.
            reasons.append("nonpublic_address")
        if any(p.get("error") == "dns_timeout" for p in pages):
            # DNS 조회가 시간 안에 끝나지 않아 받지 않은 페이지 — 역시 깨진 링크가 아니라 미관측이다.
            reasons.append("dns_timeout")
        if http_errors:
            reasons.append("http_errors")
        if not pages:
            reasons.append("no_pages_fetched")
        coverage.update({"complete": not reasons, "max_pages": max_pages,
                         "pages_fetched": len(pages), "queued_remaining": len(queue),
                         "blocked_count": blocked_count, "reasons": reasons})
    return pages


def _link_key(url: str) -> str:
    """링크 도달 판정용 비교 키 — 스킴과 끝 슬래시 차이로 고아 페이지를 만들지 않는다."""
    parts = urllib.parse.urlsplit(url or "")
    path = (parts.path or "/").rstrip("/") or "/"
    return "%s%s%s" % (parts.netloc.lower(), path, ("?" + parts.query) if parts.query else "")


def page_record(url: str, res: dict) -> dict:
    page = {
        "url": url,
        "status": res["status"],
        "final_url": res["final_url"],
        "title": None, "meta_description": None, "meta_robots": None, "meta_googlebot": None,
        "x_robots_tag": res["headers"].get("x-robots-tag"),
        "canonical": None, "h1": [], "jsonld_count": 0, "jsonld_types": [],
        "text_chars": 0, "og": {}, "naver_site_verification": False,
        "lang": None, "response_ms": res["ms"], "error": res["error"],
        "_links": [],
    }
    if res["error"] or not res["body"]:
        return page
    if "html" not in (res["content_type"] or "text/html").lower():
        return page
    parser = PageParser()
    try:
        parser.feed(res["body"])
        parser.close()
    except Exception:  # 깨진 HTML에서도 여기까지 모은 값은 살린다
        pass
    count, types = jsonld_types(parser.jsonld_raw)
    page.update({
        # JSON 문법 오류로 읽히지 않은 블록 수. "블록이 없다"와 "있지만 깨졌다"를 가른다.
        "jsonld_invalid": len(parser.jsonld_raw) - count,
        "title": parser.title,
        "meta_description": parser.meta_description,
        "meta_robots": parser.meta_robots,
        "meta_googlebot": parser.meta_googlebot,
        "meta_googlebot_news": parser.meta_googlebot_news,
        "canonical": parser.canonical,
        "h1": parser.h1,
        "jsonld_count": count,
        "jsonld_types": sorted(set(types)),
        "text_chars": parser.text_chars,
        "og": parser.og,
        "naver_site_verification": parser.naver_site_verification,
        "lang": parser.lang,
        # 선택 필드 — HTML을 파싱한 페이지에만 있다(없으면 빈 목록). 옛 audit·미파싱 페이지는 키가 없다.
        "hreflang": hreflang_entries(res["final_url"] or url, parser.hreflang_links),
        "_links": parser.links,
    })
    return page


def hreflang_entries(page_url: str, links) -> list:
    """<link rel="alternate" hreflang href> 목록을 정렬된 {"hreflang", "href"}로.

    hreflang 값은 소문자로(x-default 포함), href는 페이지 URL 기준 절대 주소로 바꾼다.
    """
    rows = set()
    for lang, href in links:
        lang = (lang or "").strip().lower()
        href = (href or "").strip()
        if not lang or not href:
            continue
        try:
            absolute = urllib.parse.urljoin(page_url, href)
        except ValueError:
            continue
        rows.add((lang, absolute))
    return [{"hreflang": lang, "href": href} for lang, href in sorted(rows)]


# ─────────────────────────────────────────────────────────── llms.txt 형식 메모

# llms.txt는 공식 표준이 아니라 llmstxt.org의 제안 형식이다. Google은 검색에 필요 없고 순위에
# 영향이 없다고 밝혔다(lanes/geo.md 1번). 그래서 아래 메모는 순위·인용 요인이 아니라 "제안 형식과
# 다른 곳"을 알려 주는 참고 사항이다. 메모가 없다고 인용이 오는 것도, 있다고 감점되는 것도 아니다.
# 아이디어: AgriciDaniel/claude-seo(MIT) agentic_check.py의 llms_format_notes — 코드는 새로 썼다.
LLMS_MAX_BYTES = 100 * 1024
_LLMS_LIST_ITEM = re.compile(r"^[-*+]\s+")
_LLMS_LINK_ITEM = re.compile(r"^[-*+]\s+\[([^\[\]\n]+)\]\(\s*([^()\s]+)\s*\)(?:\s*:\s*(.*))?$")
_LLMS_H1 = re.compile(r"^#\s+\S")
_LLMS_H2 = re.compile(r"^##\s+\S")
# 메모 등급 — 권장 항목은 info, 제안 형식과 다른 곳은 warn. 어느 것도 fail이 아니다.
LLMS_NOTE_LEVEL = {"summary_missing": "info"}
_LLMS_HTML = re.compile(r"^\s*(?:<!doctype\s+html|<html[\s>])", re.I)


def _llms_clip(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def llms_format_notes(text: str, size_bytes=None) -> list:
    """llms.txt 본문을 llmstxt.org 제안 형식과 대조한 메모 목록. 빈 목록이면 관측된 차이가 없다.

    각 메모는 {"code", "level", "message", "lines"}다. level은 info(권장 항목) 또는 warn(형식 차이)이고
    fail은 없다. lines는 "줄번호: 원문" 형태로 최대 10개다.
    size_bytes를 주지 않으면 UTF-8로 다시 인코딩한 크기를 쓴다.
    """
    notes = []

    def note(code, message, lines=None):
        notes.append({"code": code, "level": LLMS_NOTE_LEVEL.get(code, "warn"),
                      "message": message, "lines": list(lines or [])[:10]})

    text = text or ""
    if text.startswith("\ufeff"):
        text = text[1:]
    size = size_bytes if size_bytes is not None else len(text.encode("utf-8"))
    if size > LLMS_MAX_BYTES:
        note("too_large",
             "파일이 %d KB다 — 제안 형식은 짧은 안내서라 100 KB를 넘으면 전문은 llms-full.txt 같은 "
             "별도 파일로 나누는 편이 낫다." % (size // 1024))
    if _LLMS_HTML.match(text):
        note("html_document",
             "마크다운이 아니라 HTML 문서가 돌아왔다 — 앱의 기본 페이지(SPA 폴백)가 대신 응답했을 수 있다. "
             "형식 검사는 하지 않았다.")
        return notes

    content = []   # (줄번호, 앞뒤 공백을 뗀 줄) — 빈 줄과 코드 블록 안은 뺀다
    fenced = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not line:
            continue
        content.append((number, line))
    if not content:
        note("empty", "내용이 없다 — 제목(# 이름) 한 줄도 없다.")
        return notes

    h1_at = next((i for i, (_, line) in enumerate(content) if _LLMS_H1.match(line)), None)
    first_no, first = content[0]
    if h1_at is None:
        note("h1_missing", "H1 제목(`# 이름`)이 없다 — 제안 형식에서 유일한 필수 항목이다.",
             ["%d: %s" % (first_no, _llms_clip(first))])
    elif h1_at != 0:
        note("h1_not_first", "첫 줄이 H1 제목(`# 이름`)이 아니다 — 제안 형식은 H1으로 시작한다.",
             ["%d: %s" % (first_no, _llms_clip(first))])
    if h1_at is not None:
        following = content[h1_at + 1] if h1_at + 1 < len(content) else None
        if following is None or not following[1].startswith(">"):
            note("summary_missing",
                 "H1 바로 다음에 인용 블록 요약(`> 한 줄 소개`)이 없다 — 필수는 아니지만 권장 항목이다.",
                 ["%d: %s" % (following[0], _llms_clip(following[1]))] if following else [])

    in_section = False
    links, relative, non_http, malformed = 0, [], [], []
    for number, line in content:
        if _LLMS_H2.match(line):
            in_section = True
            continue
        if _LLMS_H1.match(line):
            in_section = False
            continue
        # 링크 목록은 H2 절 안의 목록 항목이다. H1 아래 소개부의 목록은 자유 형식이라 보지 않는다.
        if not in_section or not _LLMS_LIST_ITEM.match(line):
            continue
        match = _LLMS_LINK_ITEM.match(line)
        if match:
            links += 1
            target = match.group(2)
            scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", target)
            if scheme is None:
                relative.append("%d: %s" % (number, _llms_clip(target)))
            elif scheme.group(1).lower() not in ("http", "https"):
                non_http.append("%d: %s" % (number, _llms_clip(target)))
        elif "[" in line or "](" in line:
            malformed.append("%d: %s" % (number, _llms_clip(line)))
    if relative:
        note("relative_links",
             "상대 경로 링크가 %d개다 — 파일만 따로 읽는 도구는 상대 경로를 풀지 못할 수 있다. "
             "절대 https:// 주소로 적는다." % len(relative), relative)
    if non_http:
        note("non_http_links",
             "http(s)가 아닌 링크가 %d개다 (mailto:·ftp: 등) — 링크 목록은 읽을 수 있는 문서 주소를 "
             "가리킨다." % len(non_http), non_http)
    if malformed:
        note("malformed_links",
             "`- [제목](https://…): 설명` 형식에서 벗어난 링크 줄이 %d개다." % len(malformed), malformed)
    if not links:
        note("no_links",
             "H2 절 아래 `- [제목](https://…)` 형식의 링크 목록이 하나도 없다 — 안내할 문서가 없는 셈이다.")
    return notes


def llms_format_record(res: dict):
    """crawl·verify 공용. 200으로 받은 llms.txt 응답이면 형식 메모 기록을, 아니면 None(미측정)을 준다."""
    if res.get("status") != 200 or res.get("error") not in (None, "body_truncated"):
        return None
    body = res.get("body") or ""
    size = len(body.encode("utf-8"))
    return {"bytes": size, "truncated": bool(res.get("truncated")),
            "notes": llms_format_notes(body, size_bytes=size)}


# ─────────────────────────────────────────────────────────── 사이트 수준

@_scoped(lambda base, *_a, **_k: base)
def probe_site(base: str, robots_raw: str, robots_status, crawled: list, sitemap_data=None,
               robots_state=None, robots_error=None, robots_final_url=None, robots_redirects=None) -> dict:
    host = host_of(base)
    declared = [
        m.strip() for m in
        re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_raw or "")
    ]
    robots = {
        "status": robots_status,
        "present": bool(robots_raw),
        "raw": robots_raw or "",
        # 읽지 못한 robots.txt는 "미설정(none)"이 아니라 "미확인(unknown)"이다.
        "policies": {ua: ("unknown" if robots_state == "unreachable"
                          else robots_policy(robots_raw or "", ua)) for ua in ALL_UAS},
        "sitemap_declared": declared,
        # 이하 선택 필드 — 옛 audit에는 없다. 키가 있으면 측정했다는 뜻이다.
        "nonstandard_agents": robots_nonstandard_agents(robots_raw or ""),
        "cdn_managed": cdn_managed_info(robots_raw or ""),
        "content_signals": robots_content_signals(robots_raw or ""),
    }
    if robots_state:
        robots["fetch_state"] = robots_state
        robots["fetch_error"] = robots_error
    if robots_final_url is not None:
        # 선택 필드 — 마지막으로 받은(또는 따라가지 않은) robots.txt 주소와 리다이렉트 홉 수.
        robots["final_url"] = robots_final_url
        robots["redirects"] = robots_redirects or 0
    if robots_state == "unreachable":
        # No cached policy: return the observed policy response without sending probes.
        return {"probes_skipped": "robots_unavailable", "robots": robots, "sitemaps": [], "sitemap_urls": [],
                "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": [],
                                     "excluded_noindex": [], "excluded_noncanonical": []},
                "llms": {}, "hygiene": {"probe_404": None, "redirect_hops": None,
                                        "home_response_ms": None, "mirrors": {},
                                        "alt_host": {"host": alt_host_of(host), "result": "unmeasured",
                                                     "status": None, "location": None}}}
    sitemaps, sitemap_urls = sitemap_data or read_sitemaps(base, host, declared)

    crawl_set = set()
    excluded_noindex = []
    excluded_noncanonical = []
    for page in crawled:
        if page["status"] != 200:
            continue
        effective = normalize(page.get("final_url") or page["url"])
        if host_of(effective) != host:
            continue
        if _noindex(page) or not page.get("intended_indexable", True):
            excluded_noindex.append(effective)
            continue
        canonical = page.get("canonical")
        if canonical:
            resolved = normalize(urllib.parse.urljoin(effective, canonical))
            if resolved.rstrip("/") != effective.rstrip("/"):
                excluded_noncanonical.append(effective)
                continue
        crawl_set.add(effective)
    sm_set = {normalize(u) for u in sitemap_urls if host_of(u) == host}
    mismatch = {
        "only_in_sitemap": sorted(sm_set - crawl_set),
        "only_in_crawl": sorted(crawl_set - sm_set) if sm_set else [],
        "excluded_noindex": sorted(set(excluded_noindex)),
        "excluded_noncanonical": sorted(set(excluded_noncanonical)),
    }

    audit_rules = crawl_rules(robots_raw or "", UA.split("/")[0])

    def allowed_fetch(url):
        parts = urllib.parse.urlsplit(url)
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        if not crawl_allowed(audit_rules, target):
            return {"status": None, "final_url": url, "headers": {}, "body": "", "ms": 0,
                    "redirects": 0, "error": "robots_blocked", "content_type": ""}
        return fetch(url, rules=audit_rules)

    llms = {}
    llms_format = None
    for name in ("llms.txt", "llms-full.txt"):
        res = allowed_fetch("%s/%s" % (base, name))
        llms[name] = res["status"]
        if name == "llms.txt":
            llms_format = llms_format_record(res)

    home = allowed_fetch(base)
    probe = allowed_fetch("%s/__multi_geo_404_probe__" % base)
    alt_host = alt_host_of(host)
    if alt_host is None:
        alt = {"error": None, "status": None, "final_url": None, "redirects": 0}
        alt_result = "na"
    else:
        alt = fetch("https://%s/" % alt_host)
        destination = urllib.parse.urlsplit(alt.get("final_url") or "")
        if (alt["error"] == "external_redirect_blocked"
                and alt["status"] in (301, 302, 303, 307, 308)
                and destination.scheme in ("http", "https")
                and destination.netloc.lower() == host):
            # fetch deliberately does not follow cross-host redirects. A redirect
            # from www/apex to the audited host is still an observed valid handoff.
            alt_result = "redirect"
        elif alt["error"] == "nonpublic_address":
            # 진단 대상 범위 밖 주소로 풀려 접속하지 않았다 — 접속 불가가 아니라 미관측이다.
            alt_result = "nonpublic_address"
        elif alt["error"] == "dns_timeout":
            # DNS가 시간 안에 답하지 않았다 — 접속 불가 판정이 아니라 미관측이다(finding 없음).
            alt_result = "dns_timeout"
        elif alt["error"]:
            alt_result = alt["error"] if alt["error"] in ("tls_fail", "dns_fail") else "error"
        elif alt["redirects"]:
            alt_result = "redirect"
        else:
            alt_result = "ok"

    site = {
        "robots": robots,
        "sitemaps": sitemaps,
        "sitemap_urls": sorted(sm_set),
        "sitemap_vs_crawl": mismatch,
        "llms": llms,
        "hygiene": {
            "probe_404": probe["status"],
            "redirect_hops": home["redirects"],
            "home_response_ms": home["ms"],
            "mirrors": probe_mirrors(host),
            "alt_host": {
                "host": alt_host,
                "result": alt_result,
                "status": alt["status"],
                "location": alt["final_url"] if alt_result == "redirect" else None,
                **({"address_class": alt.get("address_class") or "unknown"}
                   if alt_result == "nonpublic_address" else {}),
            },
        },
    }
    # 선택 필드 — llms.txt를 HTTP 200으로 받았을 때만 생긴다. 키가 없으면 미측정이지 "문제 없음"이 아니다.
    # 순위·인용 요인이 아니라 참고 메모라 findings·scorecard에는 넣지 않는다.
    if llms_format is not None:
        site["llms_format"] = llms_format
    return site


def sitemap_candidates(base: str, host: str, declared: list) -> list:
    """robots.txt가 준 값을 그대로 믿지 않는다 — http(s) + 진단 대상 호스트만 남긴다(SSRF 방지)."""
    candidates = []
    for url in list(declared) + ["%s/sitemap.xml" % base, "%s/sitemap_index.xml" % base]:
        if not url.startswith(("http://", "https://")) or host_of(url) != host:
            continue
        if url not in candidates:
            candidates.append(url)
    return candidates


# ─────────────────────────────────────────────────────────── 사이트맵 위생

# 사이트맵 프로토콜(sitemaps.org)의 파일당 한도. 넘으면 검색엔진이 파일을 읽지 않을 수 있다.
# 아래 지표는 순위·인용 요인이 아니라 사이트맵 관리 상태다. 크롤 완전성 판정에는 쓰지 않는다.
SITEMAP_MAX_URLS = 50_000
SITEMAP_MAX_BYTES = 52_428_800          # 50 MB, 압축 해제 기준
SITEMAP_UNIFORM_MIN = 10                # 같은 lastmod 비율을 따질 최소 항목 수
SITEMAP_UNIFORM_SHARE = 0.9
_W3C_DATETIME = re.compile(
    r"^(\d{4})(?:-(\d{2})(?:-(\d{2})"
    r"(?:T([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d)(?:\.\d+)?)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?)?)?$")


def w3c_datetime(value: str) -> bool:
    """W3C Datetime(YYYY · YYYY-MM · YYYY-MM-DD · 시각은 시간대 필수) 형식인가. 없는 날짜는 거부한다."""
    match = _W3C_DATETIME.match(value or "")
    if not match:
        return False
    year, month, day = match.group(1), match.group(2), match.group(3)
    try:
        datetime(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return False
    return True


def _xml_local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _normalize_safe(url: str) -> str:
    try:
        return normalize(url)
    except ValueError:   # 잘못된 IPv6 표기 등 — 원문 그대로 비교한다
        return url


def _declared_size(res: dict, url: str):
    """압축되지 않은 응답의 Content-Length. gzip이면 압축 해제 크기를 알 수 없어 None."""
    headers = res.get("headers") or {}
    if headers.get("content-encoding") or urllib.parse.urlsplit(url).path.lower().endswith(".gz"):
        return None
    try:
        return int(headers.get("content-length") or "")
    except ValueError:
        return None


def sitemap_quality(root, body: str, host: str, declared_size=None) -> dict:
    """사이트맵 한 파일의 위생 지표 (선택 필드 sitemaps[].quality).

    항목은 루트 바로 아래 <url>·<sitemap>이고, 그 항목의 직계 <loc>·<lastmod>만 본다
    (image:loc 같은 확장 요소는 세지 않는다). 예시 목록은 최대 5개다.
    """
    entries = [node for node in root if _xml_local(node.tag) in ("url", "sitemap")]
    locs, valid, invalid, missing = [], [], [], 0
    for entry in entries:
        loc = next(((child.text or "").strip() for child in entry
                    if _xml_local(child.tag) == "loc"), "")
        if loc:
            locs.append(loc)
        lastmod = next(((child.text or "").strip() for child in entry
                        if _xml_local(child.tag) == "lastmod"), "")
        if not lastmod:
            missing += 1
        elif w3c_datetime(lastmod):
            valid.append(lastmod)
        else:
            invalid.append(lastmod)
    top_value, top_count = (Counter(valid).most_common(1) or [(None, 0)])[0]
    foreign = [u for u in locs if u.lower().startswith(("http://", "https://")) and host_of(u) != host]
    seen = Counter(_normalize_safe(u) for u in locs)
    duplicates = [u for u, n in seen.items() if n > 1]
    size = len((body or "").encode("utf-8"))
    if declared_size and declared_size > size:
        size = declared_size
    count = len(entries)
    return {
        "entries": count,
        "bytes": size,
        "lastmod_missing": missing,
        "lastmod_invalid": len(invalid),
        "lastmod_invalid_examples": list(dict.fromkeys(invalid))[:5],
        "lastmod_top_value": top_value,
        "lastmod_top_count": top_count,
        "lastmod_top_share": round(top_count / count, 3) if count else 0.0,
        "foreign_host": len(foreign),
        "foreign_host_examples": list(dict.fromkeys(foreign))[:5],
        "duplicate_urls": sum(n - 1 for n in seen.values() if n > 1),
        "duplicate_examples": duplicates[:5],
        "over_url_limit": count > SITEMAP_MAX_URLS,
        "over_size_limit": size > SITEMAP_MAX_BYTES,
    }


@_scoped(lambda base, *_a, **_k: base)
def read_sitemaps(base: str, host: str, declared: list, *, rules=None):
    """선언된 사이트맵 + 표준 경로 2종을 읽는다."""
    candidates = sitemap_candidates(base, host, declared)
    required = set(declared) & set(candidates)
    results = []
    urls = []
    queue = deque(candidates)
    seen = set()
    limit = 100
    while queue and len(results) < limit:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        parts = urllib.parse.urlsplit(url)
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        if rules and not crawl_allowed(rules, target):
            res = {"status": None, "error": "robots_blocked"}
        else:
            res = fetch(url, rules=rules) if rules else fetch(url)
        locs, is_index, parsed, parse_error, quality = [], False, False, None, None
        if res["status"] == 200 and not res.get("error"):
            try:
                root = ET.fromstring(res["body"] or "")
                kind = root.tag.rsplit("}", 1)[-1].lower()
                if kind not in ("urlset", "sitemapindex"):
                    raise ValueError("root element is %s" % kind)
                is_index = kind == "sitemapindex"
                locs = [((node.text or "").strip()) for node in root.iter()
                        if node.tag.rsplit("}", 1)[-1].lower() == "loc" and (node.text or "").strip()]
                parsed = True
            except (ET.ParseError, ValueError) as exc:
                parse_error = str(exc)
            if parsed:
                # 위생 지표는 참고용이다 — 계산이 실패해도 파싱·완전성 판정은 바꾸지 않는다.
                try:
                    quality = sitemap_quality(root, res["body"] or "", host,
                                              _declared_size(res, url))
                except ValueError:
                    quality = None
        elif res["status"] == 200 and (_declared_size(res, url) or 0) > SITEMAP_MAX_BYTES:
            # 본문을 다 받지 못해 파싱은 못 했지만 선언된 크기만으로 한도 초과는 확인된다.
            size = _declared_size(res, url)
            quality = {"bytes": size, "over_size_limit": True, "partial": True}
        result = {
            "url": url, "status": res["status"],
            "is_index": is_index, "url_count": len(locs), "parsed": parsed,
            "truncated": bool(res.get("truncated")) or (is_index and len(locs) > 100),
            "error": res.get("error") or parse_error,
        }
        if quality is not None:
            result["quality"] = quality   # 선택 필드 — 옛 audit에는 없다
        results.append(result)
        if res["status"] != 200 or not parsed:
            continue
        if is_index:
            for child in locs[:100]:
                if child.startswith(("http://", "https://")) and host_of(child) == host:
                    required.add(child)
                    queue.append(child)
        else:
            urls.extend(locs)
    if queue:
        pending = []
        while queue:
            url = queue.popleft()
            if url not in seen and url not in pending:
                pending.append(url)
        results.extend({"url": url, "status": None, "is_index": False, "url_count": 0,
                        "parsed": False, "truncated": True, "error": "inspection_limit"}
                       for url in pending)
    # A default path may already have been fetched before an index references it.
    # Assign provenance after traversal so that its failure is not lost.
    for result in results:
        result["required"] = result["url"] in required
    return results, urls


# ─────────────────────────────────────────────────────────── findings

def add(findings, lane, severity, code, message, urls=None, data=None):
    findings.append({
        "lane": lane, "severity": severity, "code": code, "message": message,
        "urls": (urls or [])[:20], "data": data or {},
    })


def analyze(base: str, site: dict, pages: list, *, start=None) -> tuple:
    findings: list = []
    ok = [p for p in pages if p["status"] == 200]
    total = max(1, len(ok))

    _check_indexability(findings, ok)
    _check_meta(findings, ok, total)
    if ok:
        _check_structure(findings, ok, total)
    _check_site(findings, base, site, ok)
    _check_sitemap_quality(findings, site)
    _check_link_discovery(findings, start or base, site, ok)
    _check_robots_file(findings, base, site)
    _check_crawler_policy(findings, site)

    stats = {
        "pages_crawled": len(pages),
        "unique_titles": len({p["title"] for p in ok if p["title"]}),
        "unique_descriptions": len({p["meta_description"] for p in ok if p["meta_description"]}),
        "pages_with_jsonld": sum(1 for p in ok if p["jsonld_count"]),
        "pages_noindex": sum(1 for p in ok if _noindex(p)),
    }
    unassessed = {}
    if robots_unknown(site):
        for lane in ROBOTS_POLICY_LANES:
            unassessed[lane] = ["crawler_policy"]
    if site["robots"].get("fetch_state") == "unreachable":
        for lane in ("AEO", "LLMO"):
            unassessed[lane] = ["page_content"]
    return findings, stats, scorecard(findings, unassessed)


def _noindex(page) -> bool:
    return "noindex" in _robots_directives(page)


def _robots_directives(page, ua="googlebot") -> set[str]:
    """페이지 수준 지시를 토큰화한다. generic과 해당 UA 지시는 합산한다."""
    values = [page.get("meta_robots") or ""]
    if ua in ("googlebot", "googlebot-news"):
        values.append(page.get("meta_googlebot") or "")
    if ua == "googlebot-news":
        values.append(page.get("meta_googlebot_news") or "")
    header = page.get("x_robots_tag") or ""
    scope_pattern = re.compile(r"^\s*(googlebot-news|[\w-]+(?:bot|user))\s*:\s*(.*)$", re.I)
    for line in header.splitlines():
        active_scope = None
        for segment in line.split(","):
            scoped = scope_pattern.match(segment)
            if scoped:
                active_scope = scoped.group(1).lower()
                directive = scoped.group(2)
            else:
                directive = segment
            if active_scope is None or active_scope == ua.lower():
                values.append(directive)
    tokens = set()
    for value in values:
        normalized = re.sub(r"\s*:\s*", ":", value.lower())
        for token in re.split(r"[,;\s]+", normalized):
            if token:
                tokens.add(token)
    if "none" in tokens:
        tokens.update(("noindex", "nofollow"))
    return tokens


def _check_indexability(findings, ok):
    noindex = [p["url"] for p in ok if _noindex(p) and p.get("intended_indexable", True)]
    intentional = [p["url"] for p in ok if _noindex(p) and not p.get("intended_indexable", True)]
    if noindex:
        add(findings, "SEO", "critical", "NOINDEX",
            "noindex가 %d개 페이지에 있다 — 해당 페이지가 의도한 비색인 대상인지 확인한다." % len(noindex),
            noindex, {"count": len(noindex)})
    if intentional:
        add(findings, "SEO", "info", "INTENTIONAL_NOINDEX",
            "의도적으로 비색인 처리한 페이지가 %d개다." % len(intentional), intentional,
            {"count": len(intentional)})
    restricted = []
    for page in ok:
        directives = _robots_directives(page)
        if "nosnippet" in directives or "max-snippet:0" in directives:
            restricted.append(page["url"])
    if restricted:
        add(findings, "AEO", "warn", "SNIPPET_RESTRICTED",
            "검색·AI 답변용 snippet을 막은 페이지가 %d개다." % len(restricted), restricted,
            {"count": len(restricted)})


def _check_meta(findings, ok, total):
    titles = Counter(p["title"] for p in ok if p["title"])
    dup_titles = {t: n for t, n in titles.items() if n > 1}
    if dup_titles:
        affected = sum(dup_titles.values())
        urls = [p["url"] for p in ok if p["title"] in dup_titles]
        add(findings, "SEO", "warn", "TITLE_DUPLICATE",
            "같은 title을 쓰는 페이지가 %d개다 (%d%%) — 검색결과에서 페이지를 구분하기 어려울 수 있다."
            % (affected, round(affected * 100 / total)),
            urls, {"groups": len(dup_titles), "pages": affected,
                   "ratio": round(affected / total, 3)})

    missing_title = [p["url"] for p in ok if not p["title"]]
    if missing_title:
        add(findings, "SEO", "critical", "TITLE_MISSING",
            "title이 없는 페이지가 %d개다." % len(missing_title), missing_title,
            {"count": len(missing_title)})

    long_title, short_title = [], []
    for page in ok:
        if not page["title"]:
            continue
        lo, hi = LEN_TITLE[script_of(page["title"])]
        n = len(page["title"])
        if n > hi:
            long_title.append(page["url"])
        elif n < lo * 0.4:
            short_title.append(page["url"])
    if long_title:
        add(findings, "SEO", "info", "TITLE_TOO_LONG",
            "권장 길이(한글 25~30 · 영문 50~60)를 넘는 title이 %d개다 — 기기와 표시 폭에 따라 잘릴 수 있다."
            % len(long_title), long_title, {"count": len(long_title)})
    if short_title:
        add(findings, "SEO", "warn", "TITLE_TOO_SHORT",
            "title이 지나치게 짧은 페이지가 %d개다." % len(short_title), short_title,
            {"count": len(short_title)})

    descs = Counter(p["meta_description"] for p in ok if p["meta_description"])
    dup_desc = {d: n for d, n in descs.items() if n > 1}
    if dup_desc:
        affected = sum(dup_desc.values())
        urls = [p["url"] for p in ok if p["meta_description"] in dup_desc]
        add(findings, "SEO", "warn", "DESC_DUPLICATE",
            "같은 meta description을 쓰는 페이지가 %d개다 (%d%%) — 템플릿 하나로 찍은 흔적이다."
            % (affected, round(affected * 100 / total)),
            urls, {"groups": len(dup_desc), "pages": affected,
                   "ratio": round(affected / total, 3)})

    missing_desc = [p["url"] for p in ok if not p["meta_description"]]
    if missing_desc:
        add(findings, "SEO", "warn", "DESC_MISSING",
            "meta description이 없는 페이지가 %d개다 (%d%%)."
            % (len(missing_desc), round(len(missing_desc) * 100 / total)),
            missing_desc, {"count": len(missing_desc)})

    long_desc, short_desc = [], []
    for page in ok:
        text = page["meta_description"]
        if not text:
            continue
        lo, hi = LEN_DESC[script_of(text)]
        n = len(text)
        if n > hi:
            long_desc.append(page["url"])
        elif n < lo * 0.5:
            short_desc.append(page["url"])
    if long_desc:
        add(findings, "SEO", "info", "DESC_TOO_LONG",
            "권장 길이(한글 70~80 · 영문 150~160)를 넘는 설명이 %d개다." % len(long_desc),
            long_desc, {"count": len(long_desc)})
    if short_desc:
        add(findings, "SEO", "info", "DESC_TOO_SHORT",
            "설명이 지나치게 짧은 페이지가 %d개다." % len(short_desc), short_desc,
            {"count": len(short_desc)})


def _check_structure(findings, ok, total):
    # 깨진 블록만 있는 페이지는 "없음"이 아니라 "깨짐"이다. 필드가 없는 옛 audit은 0으로 읽는다.
    invalid = [p for p in ok if (p.get("jsonld_invalid") or 0) > 0]
    if invalid:
        blocks = sum(p["jsonld_invalid"] for p in invalid)
        add(findings, "AEO", "warn", "JSONLD_INVALID",
            "JSON-LD 블록이 JSON 문법 오류로 읽히지 않는 페이지가 %d개다 (깨진 블록 %d개) — "
            "검색엔진도 그 블록을 읽지 못한다. 문법부터 고친다." % (len(invalid), blocks),
            [p["url"] for p in invalid], {"count": len(invalid), "blocks": blocks})
    no_ld = [p["url"] for p in ok
             if not p["jsonld_count"] and not (p.get("jsonld_invalid") or 0)]
    if no_ld:
        ratio = len(no_ld) / total
        add(findings, "AEO", "info", "JSONLD_MISSING",
            "JSON-LD가 없는 페이지가 %d개다 (%d%%) — 페이지 성격에 맞으면 추가를 검토한다."
            % (len(no_ld), round(ratio * 100)), no_ld,
            {"count": len(no_ld), "ratio": round(ratio, 3)})

    all_types = Counter()
    for page in ok:
        all_types.update(page["jsonld_types"])
    if not any(t in all_types for t in ("FAQPage", "QAPage")):
        add(findings, "AEO", "info", "FAQ_MISSING",
            "FAQPage/QAPage JSON-LD가 없다 — 실제 문답 콘텐츠가 있는 경우에만 추가를 검토한다.",
            [], {"types_found": dict(all_types)})

    org_pages = [p["url"] for p in ok
                 if any(t in ("Organization", "LocalBusiness", "Corporation")
                        for t in p["jsonld_types"])]
    if not org_pages:
        add(findings, "LLMO", "info", "ORG_JSONLD_MISSING",
            "Organization/LocalBusiness JSON-LD가 없다 — 조직 사이트라면 명확한 엔티티 정보 추가를 검토한다.",
            [], {})
    elif len(org_pages) > 1:
        add(findings, "LLMO", "info", "ORG_JSONLD_SCATTERED",
            "Organization을 %d개 페이지가 각자 선언한다 — 전역 @id 하나로 모아야 엔티티가 안 쪼개진다."
            % len(org_pages), org_pages, {"count": len(org_pages)})

    no_canon = [p["url"] for p in ok if not p["canonical"]]
    if no_canon:
        add(findings, "SEO", "warn", "CANONICAL_MISSING",
            "canonical이 없는 페이지가 %d개다 (%d%%)."
            % (len(no_canon), round(len(no_canon) * 100 / total)), no_canon,
            {"count": len(no_canon)})

    not_self = []
    for page in ok:
        if not page["canonical"]:
            continue
        try:
            resolved = normalize(urllib.parse.urljoin(page["url"], page["canonical"]))
        except ValueError:
            continue
        if resolved.rstrip("/") != page["url"].rstrip("/"):
            not_self.append(page["url"])
    if not_self:
        add(findings, "SEO", "info", "CANONICAL_NOT_SELF",
            "canonical이 자기 자신을 가리키지 않는 페이지가 %d개다 — 의도한 통합인지 확인하라."
            % len(not_self), not_self, {"count": len(not_self)})

    no_h1 = [p["url"] for p in ok if not p["h1"]]
    if no_h1:
        add(findings, "SEO", "warn", "H1_MISSING",
            "h1이 없는 페이지가 %d개다." % len(no_h1), no_h1, {"count": len(no_h1)})
    many_h1 = [p["url"] for p in ok if len(p["h1"]) > 1]
    if many_h1:
        add(findings, "SEO", "info", "H1_MULTIPLE",
            "h1이 여러 개인 페이지가 %d개다." % len(many_h1), many_h1,
            {"count": len(many_h1)})

    thin = [p["url"] for p in ok if p["text_chars"] < 300]
    if thin:
        add(findings, "SEO", "critical" if len(thin) / total > 0.5 else "warn", "THIN_TEXT",
            "원시 HTML의 본문 텍스트가 300자 미만인 페이지가 %d개다 — 페이지 목적과 렌더링 결과를 별도 확인한다."
            % len(thin), thin, {"count": len(thin)})


def _check_link_discovery(findings, base, site, ok):
    """원시 HTML 링크만 따라가는 방문자(스크립트를 실행하지 않는 AI 크롤러·에이전트)의 시점.

    사이트맵을 시드로 넣으면 크롤 자체는 전 페이지에 닿으므로 메뉴가 스크립트 뒤에 숨어 있어도
    드러나지 않는다. 링크 필드가 없는 옛 audit은 판정하지 않는다.
    """
    start = normalize(base)
    home = next((p for p in ok if p["url"] == start), None)
    if home is not None and home.get("internal_links") == 0:
        add(findings, "GEO", "warn", "LINKS_NOT_IN_HTML",
            "시작 페이지의 원시 HTML에 내부 링크(<a href>)가 없다 — 메뉴·목록이 스크립트로만 그려지면 "
            "스크립트를 실행하지 않는 크롤러·에이전트는 첫 화면에서 더 나아가지 못한다. 렌더링 결과는 별도 확인한다.",
            [start], {})

    coverage = site.get("_coverage")
    if not coverage or not coverage.get("complete"):
        return   # 크롤하지 못한 페이지가 링크를 걸고 있을 수 있다 — 불완전 크롤에서는 판정하지 않는다
    orphans = [p["url"] for p in ok if p.get("linked_from_html") is False]
    if orphans:
        share = len(orphans) / max(1, len(ok))
        add(findings, "GEO", "warn" if share >= 0.5 else "info", "SITEMAP_ONLY_PAGES",
            "사이트맵으로만 닿고 원시 HTML 링크로는 닿지 않는 페이지가 %d개다 (%d%%) — "
            "링크를 따라가는 방문자에게는 없는 페이지다." % (len(orphans), round(share * 100)),
            orphans, {"count": len(orphans)})


def _check_site(findings, base, site, ok):
    pages_all_status = site.get("_all_pages_status", [])
    # 429·503은 "깨진 링크"가 아니라 "그 시점에 응답을 못 받음(미확인)"이다 — 따로 센다.
    throttled = [(u, s) for u, s in pages_all_status if s in THROTTLE_STATUSES]
    broken = [u for u, s in pages_all_status
              if s is None or (s >= 400 and s not in THROTTLE_STATUSES)]
    if broken:
        add(findings, "SEO", "warn", "HTTP_ERROR",
            "내부 링크를 따라간 URL 중 %d개가 오류를 냈다." % len(broken), broken,
            {"count": len(broken)})
    if throttled:
        statuses = Counter(str(s) for _, s in throttled)
        add(findings, "SEO", "info", "HTTP_THROTTLED",
            "URL %d개가 HTTP 429·503(요청 제한·일시 불가)을 냈다 (%s) — 깨진 링크가 아니라 "
            "미확인이다. 이 페이지들은 검사하지 못했으므로 크롤 범위 불완전(http_errors)에 "
            "포함된다. --delay를 늘려 다시 잰다."
            % (len(throttled), ", ".join("%s×%d" % kv for kv in sorted(statuses.items()))),
            [u for u, _ in throttled], {"count": len(throttled), "statuses": dict(statuses)})

    coverage = site.get("_coverage")
    if coverage and not coverage.get("complete"):
        add(findings, "SEO", "critical", "CRAWL_INCOMPLETE",
            "크롤 범위가 완전하지 않다 (%s). 결과를 전수 진단으로 사용하면 안 된다."
            % ", ".join(coverage.get("reasons") or ["unknown"]), [], coverage)

    robots = site["robots"]
    if robots.get("fetch_state") == "unreachable":
        status = robots.get("status")
        detail = ", " + robots["fetch_error"] if robots.get("fetch_error") else ""
        if status is not None and 300 <= status < 400:
            why = "리다이렉트를 따라가지 못해 내용을 확인하지 못했다 (HTTP %s%s)." % (status, detail)
            target = robots.get("final_url")
            if robots.get("fetch_error") == "external_redirect_blocked":
                why += (" 다른 도메인(%s)으로 리다이렉트된다 — 서버 장애가 아니다. 이 도구는 진단 대상과 "
                        "www↔apex 변형 밖의 호스트를 따라가지 않으므로 그쪽 내용은 확인하지 않았다. "
                        "Google 등 크롤러는 리다이렉트를 따라가 도착한 파일을 적용할 수 있으니 의도한 "
                        "설정인지 확인하라." % (host_of(target) if target else "알 수 없음"))
            elif robots.get("fetch_error") == "too_many_redirects":
                why += (" 리다이렉트가 연속 %d번을 넘는다 — RFC 9309와 Google은 5홉까지만 따라가므로 "
                        "크롤러도 이 파일을 읽지 못할 수 있다. robots.txt를 한 번에 최종 주소로 보내라."
                        % ROBOTS_MAX_REDIRECTS)
        elif robots.get("fetch_error") == "nonpublic_address":
            why = ("주소가 진단 대상의 주소 범위 밖(사설·루프백 등 비공개 주소)으로 풀려 접속하지 않았다 — "
                   "SSRF 방지를 위한 것이며 서버 장애로 판정하지 않는다. 외부 크롤러도 그 주소에는 닿지 "
                   "못하므로 robots.txt(또는 www↔apex 변형)의 DNS 기록을 확인하라.")
        else:
            why = ("응답을 받지 못했다 (HTTP %s%s). RFC 9309는 서버 오류·접속 불가일 때 크롤러가 "
                   "사이트 전체를 차단으로 간주하라고 하고, Google도 429·5xx를 일시적으로 전체 "
                   "차단처럼 다룬다 — 일시 장애가 아니면 검색·AI 크롤러가 사이트를 수집하지 않을 수 있다."
                   % (status if status is not None else "없음", detail))
        add(findings, "SEO", "warn", "ROBOTS_UNREACHABLE",
            "robots.txt를 읽지 못했다 — " + why + " UA별 크롤러 정책은 미확인이다(허용으로 보지 않는다).",
            ["%s/robots.txt" % base],
            {"status": status, "error": robots.get("fetch_error"),
             "final_url": robots.get("final_url"), "redirects": robots.get("redirects")})
        return  # All remaining checks need observations withheld by the policy gate.
    elif not robots["present"]:
        add(findings, "SEO", "warn", "ROBOTS_MISSING",
            "robots.txt가 없거나 비정상이다 (HTTP %s)." % site["robots"]["status"],
            ["%s/robots.txt" % base], {})
    elif not site["robots"]["sitemap_declared"]:
        add(findings, "SEO", "warn", "SITEMAP_NOT_DECLARED",
            "robots.txt에 Sitemap: 선언이 없다.", ["%s/robots.txt" % base], {})

    live = [s for s in site["sitemaps"] if s["status"] == 200 and s.get("parsed", True)]
    if not live:
        add(findings, "SEO", "warn", "SITEMAP_MISSING",
            "접근 가능한 사이트맵이 없다 — 필수 색인 조건은 아니지만 URL 발견과 갱신 전달에 유용하다.",
            [s["url"] for s in site["sitemaps"]], {})
    else:
        only_sm = site["sitemap_vs_crawl"]["only_in_sitemap"]
        only_cr = site["sitemap_vs_crawl"]["only_in_crawl"]
        if only_sm or only_cr:
            add(findings, "SEO", "warn", "SITEMAP_CRAWL_MISMATCH",
                "사이트맵과 실제 크롤 결과가 어긋난다 — 사이트맵에만 %d개, 크롤에만 %d개."
                % (len(only_sm), len(only_cr)), (only_cr or only_sm),
                {"only_in_sitemap": len(only_sm), "only_in_crawl": len(only_cr)})

    if site["llms"].get("llms.txt") != 200:
        add(findings, "GEO", "info", "LLMS_TXT_MISSING",
            "/llms.txt가 없다 (HTTP %s) — 모델에 줄 요약 지도를 아직 안 만들었다."
            % site["llms"].get("llms.txt"), ["%s/llms.txt" % base], {})

    hygiene = site["hygiene"]
    if hygiene["probe_404"] != 404:
        add(findings, "SEO", "warn", "SOFT_404",
            "없는 주소가 404가 아니라 HTTP %s를 낸다 — soft 404는 색인 예산을 태운다."
            % hygiene["probe_404"], [], {"status": hygiene["probe_404"]})
    if hygiene["redirect_hops"] > 1:
        add(findings, "SEO", "info", "REDIRECT_HOPS",
            "홈 접속에 리다이렉트가 %d홉이다 — 1홉으로 줄여라." % hygiene["redirect_hops"],
            [], {"hops": hygiene["redirect_hops"]})
    alt = hygiene["alt_host"]
    if alt["result"] in ("tls_fail", "dns_fail", "error"):
        add(findings, "SEO", "warn", "ALT_HOST_UNREACHABLE",
            "www↔apex 변형 주소 %s에 접속되지 않는다 (%s) — 그쪽으로 온 사용자·크롤러를 잃는다."
            % (alt["host"], alt["result"]), ["https://%s/" % alt["host"]], alt)
    elif alt["result"] == "nonpublic_address":
        add(findings, "SEO", "info", "ALT_HOST_NONPUBLIC",
            "www↔apex 변형 주소 %s가 비공개 주소(%s)로 풀려 접속하지 않았다 — 응답 여부는 미확인이다. "
            "공개 사이트의 변형 이름이 내부 주소를 가리키면 외부 사용자·크롤러는 닿지 못한다. "
            "DNS 기록이 의도한 것인지 확인하라."
            % (alt["host"], alt.get("address_class") or "unknown"), ["https://%s/" % alt["host"]], alt)

    mirrors = hygiene.get("mirrors") or {}
    for m in mirrors.get("found") or []:
        indexability = m.get("indexability", "unknown")
        if indexability == "noindex":
            add(findings, "SEO", "info", "MIRROR_NOINDEX",
                "미러 %s의 루트 페이지는 공개 접근 가능하지만 noindex가 관측됐다. "
                "이 결과로 다른 경로의 색인 상태까지 판단하지 않는다."
                % m["host"], ["https://%s/" % m["host"]], m)
        elif m["robots_blocks_all"]:
            add(findings, "SEO", "warn", "MIRROR_PRESENT",
                "개발·스테이징 미러 %s 가 공개돼 있다 (HTTP %s). robots로 막혀 있지만 "
                "링크·인용으로는 새어 나간다 — 인증이나 IP 제한으로 닫아라."
                % (m["host"], m["status"]), ["https://%s/" % m["host"]], m)
        elif indexability == "unrestricted":
            add(findings, "SEO", "critical", "MIRROR_PUBLIC",
                "개발·스테이징 미러 %s의 루트 페이지가 공개돼 있고 색인 제한이 "
                "관측되지 않았다 (HTTP %s). 다른 경로와 실제 색인 여부는 별도 확인한다."
                % (m["host"], m["status"]), ["https://%s/" % m["host"]], m)
        else:
            add(findings, "SEO", "info", "MIRROR_INDEXABILITY_UNKNOWN",
                "미러 %s가 응답하지만 루트 페이지의 색인 제한 여부는 미확인이다 "
                "(HTTP %s). 응답 본문·헤더와 robots.txt를 확인한다."
                % (m["host"], m["status"]), ["https://%s/" % m["host"]], m)
    hidden = mirrors.get("nonpublic") or []
    if hidden:
        add(findings, "SEO", "info", "MIRROR_NONPUBLIC_DNS",
            "개발·스테이징 후보 이름 %d개가 공개 DNS에서 비공개 주소로 풀린다 (%s) — 접속하지 않았다"
            "(SSRF 방지). 외부에서는 열리지 않겠지만 내부 호스트 이름과 주소 체계가 공개 DNS로 드러난다. "
            "의도한 기록인지 확인하라."
            % (len(hidden), ", ".join("%s→%s" % (m["host"], m.get("address_class") or "unknown")
                                       for m in hidden[:5])),
            ["https://%s/" % m["host"] for m in hidden], {"count": len(hidden), "hosts": hidden})
    if mirrors.get("wildcard_suspect"):
        add(findings, "SEO", "info", "MIRROR_WILDCARD_DNS",
            "하위 도메인이 무엇이든 응답한다 — 와일드카드 DNS로 보인다. "
            "미러 판정을 신뢰할 수 없으니 DNS 설정을 사람이 확인하라.", [],
            {"checked": mirrors.get("checked") or []})

    if not any(p["naver_site_verification"] for p in ok):
        add(findings, "NEO", "warn", "NAVER_VERIFY_MISSING",
            "naver-site-verification 메타가 없다 — 서치어드바이저 미연결 가능성.", [], {})

    # 다음 웹마스터도구는 네이버와 달리 메타 태그를 쓰지 않는다(URL+PIN·robots 주석).
    # 크롤로는 "등록됨"도 "미등록"도 증명할 수 없다 — 없는 것을 finding 으로 만들면
    # "관측 안 됨"을 "문제 있음"으로 바꿔 적는 셈이다. 사람 확인 항목으로 남긴다
    # (ops/coverage.md · KEO 다음 웹마스터도구).


def _check_sitemap_quality(findings, site):
    """사이트맵 위생 (선택 필드 sitemaps[].quality — 옛 audit에는 없고, 없으면 아무것도 내지 않는다).

    순위·인용 요인이 아니라 프로토콜 준수·관리 상태다. 프로토콜 위반으로 항목이나 파일이 무시될 수
    있는 것(한도 초과·다른 호스트)만 warn, 나머지는 info다. 크롤 완전성 판정은 바꾸지 않는다.
    """
    measured = [s for s in site.get("sitemaps") or [] if isinstance(s.get("quality"), dict)]
    if not measured:
        return
    note = " 순위·인용 요인이 아니라 사이트맵 관리 상태다."

    def rows(key, *extra):
        return [dict({"url": s["url"], key: s["quality"].get(key)},
                     **{k: s["quality"].get(k) for k in extra})
                for s in measured if s["quality"].get(key)]

    over = [s for s in measured
            if s["quality"].get("over_url_limit") or s["quality"].get("over_size_limit")]
    if over:
        add(findings, "SEO", "info", "SITEMAP_LIMIT_EXCEEDED",
            "사이트맵 %d개가 파일당 한도(URL %s개 · 압축 해제 50 MB)를 넘는다 — 검색엔진이 파일을 "
            "읽지 않을 수 있다. 여러 파일로 나누고 sitemap index로 묶는다." % (len(over), format(SITEMAP_MAX_URLS, ","))
            + note,
            [s["url"] for s in over],
            {"count": len(over), "max_urls": SITEMAP_MAX_URLS, "max_bytes": SITEMAP_MAX_BYTES,
             "sitemaps": [{"url": s["url"], "entries": s["quality"].get("entries"),
                           "bytes": s["quality"].get("bytes"),
                           "over_url_limit": bool(s["quality"].get("over_url_limit")),
                           "over_size_limit": bool(s["quality"].get("over_size_limit"))}
                          for s in over]})

    foreign = rows("foreign_host", "foreign_host_examples")
    if foreign:
        total = sum(r["foreign_host"] for r in foreign)
        add(findings, "SEO", "info", "SITEMAP_FOREIGN_HOST",
            "사이트맵 <loc> %d개가 진단 대상과 다른 호스트를 가리킨다 — 프로토콜상 사이트맵에는 같은 "
            "호스트의 URL만 싣는다(www↔apex 혼용부터 확인). 이 URL은 사이트맵·크롤 비교에서도 빠졌다."
            % total + note,
            [u for r in foreign for u in r["foreign_host_examples"] or []],
            {"count": total, "sitemaps": foreign})

    duplicates = rows("duplicate_urls", "duplicate_examples")
    if duplicates:
        total = sum(r["duplicate_urls"] for r in duplicates)
        add(findings, "SEO", "info", "SITEMAP_DUPLICATE_URLS",
            "사이트맵 안에서 같은 URL이 %d번 더 실렸다 — 생성 설정을 확인한다." % total + note,
            [u for r in duplicates for u in r["duplicate_examples"] or []],
            {"count": total, "sitemaps": duplicates})

    invalid = rows("lastmod_invalid", "lastmod_invalid_examples")
    if invalid:
        total = sum(r["lastmod_invalid"] for r in invalid)
        add(findings, "SEO", "info", "SITEMAP_LASTMOD_INVALID",
            "W3C Datetime 형식이 아닌 lastmod가 %d개다 (예: %s) — 그 값은 무시될 수 있다. "
            "2026-11-12 또는 2026-11-12T09:00:00+09:00처럼 적는다."
            % (total, ", ".join(v for r in invalid for v in r["lastmod_invalid_examples"] or [])[:120])
            + note,
            [r["url"] for r in invalid], {"count": total, "sitemaps": invalid})

    entries = sum(s["quality"].get("entries") or 0 for s in measured)
    missing = rows("lastmod_missing", "entries")
    if missing:
        total = sum(r["lastmod_missing"] for r in missing)
        add(findings, "SEO", "info", "SITEMAP_LASTMOD_MISSING",
            "사이트맵 항목 %d/%d개에 lastmod가 없다 — 필수 항목은 아니다. 넣는다면 실제 내용이 바뀐 "
            "날짜만 적는다." % (total, entries) + note,
            [r["url"] for r in missing], {"count": total, "entries": entries, "sitemaps": missing})

    uniform = [s for s in measured
               if (s["quality"].get("lastmod_top_count") or 0) >= SITEMAP_UNIFORM_MIN
               and (s["quality"].get("lastmod_top_share") or 0) >= SITEMAP_UNIFORM_SHARE]
    if uniform:
        first = uniform[0]["quality"]
        add(findings, "SEO", "info", "SITEMAP_LASTMOD_UNIFORM",
            "사이트맵 %d개에서 항목의 %d%% 이상이 같은 lastmod(%s)다 — 생성 시각을 일괄로 찍는 설정일 수 "
            "있다. 실제 변경일이 아니면 lastmod가 신뢰받지 못할 수 있다."
            % (len(uniform), round(SITEMAP_UNIFORM_SHARE * 100), first.get("lastmod_top_value")) + note,
            [s["url"] for s in uniform],
            {"count": len(uniform), "min_share": SITEMAP_UNIFORM_SHARE,
             "sitemaps": [{"url": s["url"], "value": s["quality"].get("lastmod_top_value"),
                           "share": s["quality"].get("lastmod_top_share"),
                           "entries": s["quality"].get("entries")} for s in uniform]})


def robots_unknown(site) -> bool:
    """robots.txt를 읽지 못해 UA별 정책이 미확인인가 (옛 audit에는 fetch_state가 없다)."""
    robots = site.get("robots") or {}
    return (robots.get("fetch_state") == "unreachable"
            or "unknown" in (robots.get("policies") or {}).values())


def _check_robots_file(findings, base, site):
    """robots.txt 원문에서 읽히는 정보성 신호. 점수에 넣지 않는다(전부 info)."""
    robots = site["robots"]
    url = ["%s/robots.txt" % base]
    nonstandard = robots.get("nonstandard_agents") or []
    if nonstandard:
        add(findings, "SEO", "info", "ROBOTS_NONSTANDARD_UA",
            "robots.txt의 User-agent 값 %d개가 제품 토큰만이 아니다 (%s) — 표준(RFC 9309)은 "
            "`User-agent: %s`처럼 토큰만 적는다. 이 도구는 첫 '/'·공백 앞 토큰으로 맞췄지만, "
            "값을 통째로 비교하는 크롤러에는 안 맞을 수 있다."
            % (len(nonstandard), ", ".join(n["value"] for n in nonstandard[:5]),
               nonstandard[0]["token"] or "<토큰>"),
            url, {"agents": nonstandard})

    cdn = robots.get("cdn_managed")
    if cdn:
        blocked = cdn.get("blocked_uas") or []
        add(findings, "GEO", "info", "ROBOTS_CDN_MANAGED",
            "robots.txt에 Cloudflare 관리 블록(# BEGIN Cloudflare Managed content)이 있다 — "
            "Cloudflare가 엣지에서 붙이는 부분(managed robots.txt·봇 설정)이라 원본 서버 파일을 "
            "고쳐도 바뀌지 않는다. 바꾸려면 Cloudflare 대시보드에서 바꾼다.%s"
            % (" 이 블록이 차단하는 UA: %s." % ", ".join(blocked) if blocked else ""),
            url, dict(cdn))

    signals = robots.get("content_signals") or []
    if signals:
        star_only = all("*" in [ua_token(a) for a in g.get("agents") or []] for g in signals)
        named_ai = []
        if star_only:
            tokens = {ua.lower(): ua for ua in AI_UAS}
            signalled = {ua_token(a) for g in signals for a in g.get("agents") or []}
            for agents, _ in parse_robots(robots.get("raw") or ""):
                for token in agents:
                    if (token in tokens and token not in signalled
                            and tokens[token] not in named_ai):
                        named_ai.append(tokens[token])
        note = (" `*` 그룹에만 있는데 AI 크롤러 이름 그룹(%s)이 따로 있다 — 이름 그룹은 `*` 그룹의 "
                "줄을 물려받지 않으므로 그 크롤러에는 이 신호가 적용되지 않는다." % ", ".join(named_ai)
                if named_ai else "")
        add(findings, "GEO", "info", "CONTENT_SIGNAL_PRESENT",
            "robots.txt에 Content-Signal 선언이 %d개 그룹에 있다 (%s) — 콘텐츠 사용 선호 표현일 뿐 "
            "접근 제어가 아니며 준수는 크롤러 운영자 재량이다. 허용·차단 판정에는 쓰지 않았다.%s"
            % (len(signals), "; ".join(", ".join(g.get("raw") or []) for g in signals[:3]), note),
            url, {"groups": signals, "named_groups_without_signal": named_ai})


def _check_crawler_policy(findings, site):
    if robots_unknown(site):
        return   # robots.txt 미확인 — 차단·미명시 판정을 지어내지 않는다 (ROBOTS_UNREACHABLE가 대신 말한다)
    policies = site["robots"]["policies"]
    blocked = [ua for ua in SEARCH_UAS + USER_FETCH_UAS
               if policies.get(ua, "none").endswith("block")]
    partial = [ua for ua in SEARCH_UAS + USER_FETCH_UAS
               if policies.get(ua, "none").endswith("partial")]
    if blocked:
        add(findings, "GEO", "critical", "AI_CRAWLER_BLOCKED",
            "AI 크롤러 %d종이 차단돼 있다 (%s) — 해당 엔진 인용을 포기한 상태다."
            % (len(blocked), ", ".join(blocked)), [],
            {"blocked": blocked, "policies": {ua: policies[ua] for ua in blocked}})
    if partial:
        add(findings, "GEO", "warn", "AI_CRAWLER_PARTIAL",
            "AI 크롤러 %d종에 부분 제한이 걸려 있다 (%s) — 규칙을 직접 읽어 확인하라."
            % (len(partial), ", ".join(partial)), [], {"partial": partial})

    training_blocked = [ua for ua in TRAINING_UAS
                        if policies.get(ua, "none").endswith("block")]
    if training_blocked:
        add(findings, "GEO", "info", "AI_TRAINING_BLOCKED",
            "학습용 크롤러/토큰을 차단한 정책이 있다 (%s). 검색 접근 차단과는 구분한다."
            % ", ".join(training_blocked), [], {"blocked": training_blocked})

    undeclared = [ua for ua in AI_UAS if policies.get(ua) == "none"]
    if undeclared:
        add(findings, "GEO", "info", "AI_CRAWLER_UNDECLARED",
            "AI 크롤러 %d종이 robots.txt에 명시돼 있지 않다 — 기본 허용이지만 우연에 맡긴 상태다."
            % len(undeclared), [], {"undeclared": undeclared})

    neo_blocked = [ua for ua in NEO_UAS if policies.get(ua, "none").endswith("block")]
    if neo_blocked:
        add(findings, "NEO", "critical", "NAVER_CRAWLER_BLOCKED",
            "네이버 검색 크롤러가 차단돼 있다 (%s) — NEO 레인 전체가 닫힌다."
            % ", ".join(neo_blocked), [], {"blocked": neo_blocked})

    keo_blocked = [ua for ua in KEO_UAS if policies.get(ua, "none").endswith("block")]
    if keo_blocked:
        add(findings, "KEO", "critical", "DAUM_CRAWLER_BLOCKED",
            "다음 검색 크롤러가 차단돼 있다 (%s) — KEO 레인 전체가 닫힌다."
            % ", ".join(keo_blocked), [], {"blocked": keo_blocked})
    keo_undeclared = [ua for ua in KEO_UAS if policies.get(ua) == "none"]
    if keo_undeclared and not keo_blocked:
        add(findings, "KEO", "info", "DAUM_CRAWLER_UNDECLARED",
            "다음 크롤러 UA(%s)가 robots.txt에 명시돼 있지 않다 — 기본 허용이지만 "
            "Daumoa 와 DAUM 은 별개 토큰이라 한쪽만 적으면 다른 쪽이 샌다."
            % ", ".join(keo_undeclared), [], {"undeclared": keo_undeclared})


LANES = ["SEO", "AEO", "GEO", "LLMO", "NEO", "KEO", "reputation"]
# robots.txt 크롤러 정책이 판정 근거인 레인 — robots를 못 읽으면 "양호"라고 말할 수 없다.
ROBOTS_POLICY_LANES = ("GEO", "NEO", "KEO")
# robots.txt 원문을 읽어야만 나오는 findings — robots.txt를 못 읽은 진단에서 사라졌다고 "해소"는 아니다
# (verify.py diff가 미확인으로 분류한다). _check_crawler_policy·_check_robots_file·_check_site와 맞춘다.
ROBOTS_DERIVED_CODES = frozenset((
    "AI_CRAWLER_BLOCKED", "AI_CRAWLER_PARTIAL", "AI_TRAINING_BLOCKED", "AI_CRAWLER_UNDECLARED",
    "NAVER_CRAWLER_BLOCKED", "DAUM_CRAWLER_BLOCKED", "DAUM_CRAWLER_UNDECLARED",
    "ROBOTS_NONSTANDARD_UA", "ROBOTS_CDN_MANAGED", "CONTENT_SIGNAL_PRESENT",
    "ROBOTS_MISSING", "SITEMAP_NOT_DECLARED"))
UNASSESSED_NOTE = {"page_content": "robots.txt 미확인으로 본문 수집을 보류했다",
                   "crawler_policy": "robots.txt 미확인 — 크롤러 정책을 판정하지 못했다"}


def scorecard(findings, unassessed=None) -> dict:
    """레인 판정. unassessed={레인: [못 잰 항목]}이면 그 레인은 "양호" 대신 na(미확인)로 둔다.

    이미 warn·bad 근거가 있으면 그 판정은 유지하되(최소한 그만큼 나쁘다) 미확인 항목을 남긴다.
    """
    unassessed = unassessed or {}
    board = {}
    for lane in LANES:
        if lane == "reputation":
            # 사이트 밖 표면이라 크롤로는 잴 수 없다. 사람이 점검한다.
            board[lane] = {"status": "na", "evidence": [],
                           "note": "사이트 밖 표면 — 점검 대상 (lanes/reputation.md)"}
            continue
        mine = [f for f in findings if f["lane"] == lane]
        if any(f["severity"] == "critical" for f in mine):
            status = "bad"
        elif any(f["severity"] == "warn" for f in mine):
            status = "warn"
        else:
            status = "ok"
        cell = {"status": status, "evidence": [f["code"] for f in mine]}
        missing = unassessed.get(lane)
        if missing:
            if status == "ok":
                cell["status"] = "na"
            cell["unassessed"] = list(missing)
            cell["note"] = "; ".join(UNASSESSED_NOTE.get(m, m) for m in missing)
        board[lane] = cell
    return board


def lane_coverage(board, coverage=None) -> dict:
    """평가 범위 — 판정한 레인 수/전체, 미평가 레인, 일부 미확인 레인.

    conclusive는 크롤이 완전하고, 크롤로 잴 수 있는 레인이 전부 판정됐을 때만 참이다.
    reputation은 설계상 사이트 밖(사람 점검)이라 목록에는 넣되 conclusive 계산에서는 뺀다.
    옛 audit(unassessed 필드 없음)도 status만으로 계산된다.
    """
    board = board or {}
    unassessed = [lane for lane in LANES
                  if (board.get(lane) or {}).get("status", "na") not in ("ok", "warn", "bad")]
    partial = [lane for lane in LANES
               if lane not in unassessed and (board.get(lane) or {}).get("unassessed")]
    crawl_complete = (coverage or {}).get("complete") is True
    return {
        "assessed": len(LANES) - len(unassessed), "total": len(LANES),
        "unassessed": unassessed, "partial": partial, "crawl_complete": crawl_complete,
        "conclusive": (crawl_complete and not partial
                       and not [lane for lane in unassessed if lane != "reputation"]),
    }


# ─────────────────────────────────────────────────────────── 콘솔 요약

STATUS_MARK = {"ok": "✅", "warn": "⚠️", "bad": "❌", "na": "—"}
SEV_MARK = {"critical": "🚨", "warn": "⚠️ ", "info": "· "}


def _print_hygiene(hygiene):
    print("   404 동작        : HTTP %s  (404여야 정상)" % hygiene["probe_404"])
    print("   리다이렉트 홉   : %s" % hygiene["redirect_hops"])
    print("   홈 응답 시간    : %sms" % hygiene["home_response_ms"])
    alt = hygiene["alt_host"]
    print("   도메인 변형     : %s" % ("해당 없음 (IP·localhost 대상)" if alt["result"] == "na"
                                       else "https://%s → %s" % (alt["host"], alt["result"])))



def print_summary(report: dict) -> None:
    site = report["site"]
    stats = report["stats"]
    print("")
    print("════════════════════════════════════════════")
    print(" Phase 0 범위 진단 — %s" % report["target"]["base"])
    print(" %s · %d페이지" % (report["generated_at"][:16].replace("T", " "),
                             stats["pages_crawled"]))
    print("════════════════════════════════════════════")

    print("")
    print("── 0. noindex 사고 점검 (최우선) ──")
    if not stats["pages_crawled"]:
        print("⚠️  확인한 페이지가 없어 noindex 여부를 판정할 수 없다")
    elif stats["pages_noindex"]:
        print("🚨 noindex %d개 페이지 — 의도한 비색인 대상인지 확인하라"
              % stats["pages_noindex"])
    else:
        print("✅ noindex 없음")

    print("")
    print("── 1. 크롤 통계 ──")
    print("   크롤 페이지     : %d" % stats["pages_crawled"])
    print("   고유 title      : %d" % stats["unique_titles"])
    print("   고유 설명       : %d" % stats["unique_descriptions"])
    print("   JSON-LD 보유    : %d" % stats["pages_with_jsonld"])
    coverage = report.get("coverage")
    if coverage:
        print("   크롤 범위       : %s%s" %
              ("완료" if coverage.get("complete") else "불완전",
               "" if coverage.get("complete") else " (" + ", ".join(coverage.get("reasons", [])) + ")"))

    print("")
    print("── 2. robots / sitemap ──")
    state = site["robots"].get("fetch_state")
    print("   robots.txt      : %s (HTTP %s)"
          % ("❓ 읽지 못함 — 크롤러 정책 미확인" if state == "unreachable"
             else "있음" if site["robots"]["present"] else "❌ 없음/비정상",
             site["robots"]["status"]))
    print("   Sitemap 선언    : %s"
          % ("✅ %d건" % len(site["robots"]["sitemap_declared"])
             if site["robots"]["sitemap_declared"] else "미확인" if state == "unreachable" else "❌ robots.txt에 없음"))
    for sm in site["sitemaps"]:
        tag = "인덱스 · 하위 %d" % sm["url_count"] if sm["is_index"] else "URL %d개" % sm["url_count"]
        print("   %-42s HTTP %s  (%s)" % (sm["url"], sm["status"], tag))
        q = sm.get("quality")
        if q and not q.get("partial"):
            print("     위생: lastmod 없음 %d · 형식 오류 %d · 최다 동일값 %d%% · 다른 호스트 %d · 중복 %d%s"
                  % (q.get("lastmod_missing") or 0, q.get("lastmod_invalid") or 0,
                     round((q.get("lastmod_top_share") or 0) * 100), q.get("foreign_host") or 0,
                     q.get("duplicate_urls") or 0,
                     " · ⚠️ 파일 한도 초과" if q.get("over_url_limit") or q.get("over_size_limit") else ""))
        elif q:
            print("     위생: ⚠️ 선언 크기 %s바이트 — 파일 한도(50 MB) 초과" % q.get("bytes"))
    mismatch = site["sitemap_vs_crawl"]
    print("   사이트맵 vs 크롤: 사이트맵에만 %d · 크롤에만 %d"
          % (len(mismatch["only_in_sitemap"]), len(mismatch["only_in_crawl"])))

    print("")
    print("── 3. AI·국내 크롤러 정책 (robots.txt 실효 판정) ──")
    for ua in ALL_UAS:
        print("   %-18s %s" % (ua, _policy_label(site["robots"]["policies"].get(ua, "none"), ua)))
    print("   ※ Google-Extended는 UA가 아니라 robots 토큰이다 — 서버 로그에 안 잡힌다")
    print("   ※ Yeti는 네이버 검색 크롤러다 — 차단이면 NEO 레인 전체가 닫힌다")

    print("")
    print("── 4. llms.txt / 응답 위생 ──")
    for name, status in site["llms"].items():
        print("   /%-14s HTTP %s" % (name, status))
    llms_format = site.get("llms_format")
    if llms_format is not None:
        notes = llms_format.get("notes") or []
        print("   llms.txt 형식 메모: %s (llmstxt.org 제안 형식 기준 — 순위·인용 요인이 아니다)"
              % ("%d건" % len(notes) if notes else "없음"))
        for item in notes:
            print("     · %s" % item["message"])
    hygiene = site["hygiene"]
    if state == "unreachable":
        print("   robots.txt를 확인하지 못해 본문·사이트 검사를 보류했다. 응답 복구 후 재진단하라.")
    else:
        _print_hygiene(hygiene)

    print("")
    print("── 5. 레인 점수표 ──")
    for lane in LANES:
        cell = report["scorecard"].get(lane) or {"status": "na", "evidence": []}
        codes = [f["code"] for f in report["findings"]
                 if f["lane"] == lane and f["severity"] != "info"]
        detail = ", ".join(codes[:4]) or cell.get("note", "")
        if codes and cell.get("unassessed"):
            detail += " · " + cell.get("note", "일부 미확인")
        print("   %-11s %s  %s" % (lane, STATUS_MARK.get(cell["status"], STATUS_MARK["na"]), detail))
    reach = lane_coverage(report["scorecard"], report.get("coverage"))
    print("   평가 범위 %d/%d 레인%s" % (
        reach["assessed"], reach["total"],
        " · 미평가: " + ", ".join(reach["unassessed"]) if reach["unassessed"] else ""))
    if reach["partial"]:
        print("   일부 미확인 레인: %s" % ", ".join(reach["partial"]))
    if not reach["conclusive"]:
        print("   ※ 종합 판정 없음 — %s. 위 표를 '전체 양호'로 읽지 않는다."
              % ("크롤 범위가 불완전하다" if not reach["crawl_complete"]
                 else "판정하지 못한 레인이 있다"))

    print("")
    print("── 6. findings ──")
    for f in sorted(report["findings"],
                    key=lambda x: {"critical": 0, "warn": 1, "info": 2}[x["severity"]]):
        print("   %s[%s] %s" % (SEV_MARK[f["severity"]], f["lane"], f["message"]))

    print("")
    print("════════════════════════════════════════════")
    print(" 스크립트로 안 되는 것 (사람이 확인):")
    print("  · GSC / Bing WMT / 네이버 서치어드바이저 색인 수")
    print("  · 각 엔진에 직접 질의한 AI 인용 O/X (특히 Gemini)")
    print("  · 제3자 평판 표면 (lanes/reputation.md)")
    print("════════════════════════════════════════════")


def _policy_label(policy: str, ua=None) -> str:
    if ua in TRAINING_UAS and policy == "explicit-block":
        return "학습 사용 차단 (검색·사용자 요청 접근과 별도)"
    if ua in TRAINING_UAS and policy == "explicit-partial":
        return "학습 사용 일부 제한"
    return {
        "explicit-allow": "✅ 명시 허용",
        "explicit-block": "🚫 명시 차단 — 이 엔진 인용을 포기한 상태다",
        "explicit-partial": "⚠️  부분 제한 (규칙 수동 확인 필요)",
        "star-allow": "허용 (User-agent:* 적용)",
        "star-block": "🚫 차단 (User-agent:* 전체 차단에 걸림)",
        "star-partial": "⚠️  부분 제한 (* 규칙 적용, 수동 확인)",
        "unknown": "❓ 미확인 — robots.txt를 읽지 못했다 (허용으로 보지 않는다)",
    }.get(policy, "미설정 → 기본 허용 (명시 권장)")


# ─────────────────────────────────────────────────────────── main

def build_report(target: str, max_pages: int, delay: float, allow_noindex=None,
                 direct: bool = False) -> dict:
    """진단 하나. direct=True면 프록시 환경 변수를 무시하고 직접 연결한다(--direct)."""
    with direct_connection(direct):
        return _build_report(target, max_pages, delay, allow_noindex)


@_scoped(lambda target, *_a, **_k: target if target.startswith(("http://", "https://"))
         else "https://%s" % target)
def _build_report(target: str, max_pages: int, delay: float, allow_noindex=None) -> dict:
    start = target if target.startswith(("http://", "https://")) else "https://%s" % target
    parts = urllib.parse.urlsplit(start)
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    host = host_of(base)

    # www↔apex 리다이렉트까지만 따라간다(fetch_robots). 원문·규칙은 도착한 응답에서 읽는다.
    robots = fetch_robots(base)
    # ok만 원문을 판정한다. 404를 200처럼 꾸민 HTML 오류 페이지는 absent,
    # 429·5xx·접속 실패는 unreachable(정책 미확인)이다 — 둘 다 원문은 비운다.
    robots_state = robots_fetch_state(robots)
    raw = robots["body"] if robots_state == "ok" else ""
    rules = crawl_rules(raw, UA.split("/")[0])
    declared = [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", raw or "")]
    if robots_state == "unreachable":
        sitemap_data, pages = ([], []), []
        coverage = {"complete": False, "max_pages": max_pages, "pages_fetched": 0,
                    "queued_remaining": 0, "blocked_count": 0, "reasons": ["robots_unavailable"]}
        if robots.get("error") not in ("external_redirect_blocked", "nonpublic_address"):
            browser_review.offer(coverage, base, normalize(start), "robots_unavailable")
    else:
        sitemap_data = read_sitemaps(base, host, declared, rules=rules)
        coverage = {}
        sys.stderr.write("크롤 시작: %s (최대 %d페이지, 간격 %.1fs)\n" % (start, max_pages, delay))
        pages = crawl_site(start, max_pages, delay, rules, seeds=sitemap_data[1], coverage=coverage)
    allowed_paths = set(allow_noindex or [])
    for page in pages:
        if urllib.parse.urlsplit(page["url"]).path in allowed_paths:
            page["intended_indexable"] = False

    if robots.get("error") == "body_truncated":
        coverage["complete"] = False
        coverage["reasons"].append("robots_truncated")
    if any(s.get("status") != 200 and
           (s.get("required") or s.get("status") not in (404, 410) or s.get("error"))
           for s in sitemap_data[0]):
        coverage["complete"] = False
        coverage["reasons"] = list(dict.fromkeys(coverage["reasons"] + ["sitemap_unavailable"]))
    if any(s.get("status") == 200 and not s.get("parsed", True) for s in sitemap_data[0]):
        coverage["complete"] = False
        coverage["reasons"] = list(dict.fromkeys(coverage["reasons"] + ["sitemap_invalid"]))
    if any(s.get("truncated") for s in sitemap_data[0]):
        coverage["complete"] = False
        coverage["reasons"] = list(dict.fromkeys(coverage["reasons"] + ["sitemap_truncated"]))

    site = probe_site(base, raw, robots["status"], pages, sitemap_data=sitemap_data,
                      robots_state=robots_state, robots_error=robots.get("error"),
                      robots_final_url=robots.get("final_url"), robots_redirects=robots.get("redirects"))
    site["_coverage"] = coverage
    # 범위 밖 주소로 풀렸거나 DNS가 시간 안에 답하지 않아 받지 않은 페이지는 깨진 링크가 아니다 —
    # coverage(nonpublic_address·dns_timeout)가 말한다.
    site["_all_pages_status"] = [(p["url"], p["status"]) for p in pages
                                 if p.get("error") not in ("nonpublic_address", "dns_timeout")]
    findings, stats, board = analyze(base, site, pages, start=start)
    site.pop("_all_pages_status", None)
    site.pop("_coverage", None)

    network = network_record()
    if network is not None:
        # 선택 필드 — 프록시를 거쳤거나 --direct로 실행했을 때만 생긴다. 프록시 주소는 적지 않는다.
        coverage["network"] = network
    if network is not None and network.get("proxy"):
        # 점수표(analyze)가 끝난 뒤에 붙인다 — 실행 환경 메모이지 사이트 결함이 아니다.
        add(findings, "SEO", "info", "PROXY_ADDRESS_UNVERIFIED", PROXY_NOTE_KO, [],
            {"address_check": "local_dns_only"})

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"input": target, "base": base, "host": host},
        "coverage": coverage,
        "site": site,
        "pages": pages,
        "stats": stats,
        "findings": findings,
        "scorecard": board,
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="su-presence 전수 진단")
    ap.add_argument("target", help="도메인 또는 URL (예: example.com)")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--allow-noindex", action="append", default=[], metavar="PATH",
                    help="의도한 비색인 경로(반복 가능, 예: /search)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--direct", action="store_true",
                    help="프록시 환경 변수를 무시하고 직접 연결한다 — 비공개 주소 차단이 실제 연결을 덮는다")
    browser_review.add_option(ap)
    args = ap.parse_args(argv)

    report = None
    try:
        if args.max_pages < 1:
            ap.error("--max-pages는 1 이상이어야 한다")
        if args.delay < 0:
            ap.error("--delay는 0 이상이어야 한다")
        report = build_report(args.target, args.max_pages, args.delay, args.allow_noindex,
                              direct=args.direct)
    except KeyboardInterrupt:
        sys.stderr.write("\n중단됨 — 여기까지의 결과를 저장한다.\n")
    except Exception as exc:  # 부분 결과라도 남긴다
        sys.stderr.write("진단 중 오류: %s\n" % exc)
    if report is None:
        return 1

    host = report["target"]["host"] or "site"
    outdir = os.path.join(args.out, safe_host(host))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "audit.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print_summary(report)
    print("")
    print("audit.json 저장: %s" % path)
    browser_review.after_audit(report, path, args.browser_fallback)
    print("보고서 생성    : python tools/report.py %s" % path)
    return 0 if report.get("coverage", {}).get("complete") is True else 2


if __name__ == "__main__":
    sys.exit(main())
