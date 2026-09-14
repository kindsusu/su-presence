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
import gzip
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, deque
from datetime import datetime, timezone
from html.parser import HTMLParser

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

def parse_robots(raw: str):
    """robots.txt를 (agents, rules) 그룹 목록으로 쪼갠다.

    규칙이 나온 뒤 User-agent 줄이 다시 나오면 새 그룹으로 본다. 주석은
    제거하고 User-agent만 대소문자 비구분으로 처리한다.
    """
    groups = []
    agents: list[str] = []
    rules: list[tuple[str, bool]] = []
    saw_rule = False
    for raw_line in (raw or "").split("\n"):
        line = raw_line.rstrip("\r").split("#", 1)[0].strip()
        lower = line.lower()
        if lower.startswith("user-agent:"):
            if saw_rule:
                groups.append((agents, rules))
                agents, rules, saw_rule = [], [], False
            agents.append(line[len("user-agent:"):].strip().lower())
        elif re.match(r"^(dis)?allow:", lower):
            saw_rule = True
            is_allow = lower.startswith("allow:")
            path = re.sub(r"^(dis)?allow:\s*", "", line, flags=re.I).strip()
            rules.append((path, is_allow))
    if agents or rules:
        groups.append((agents, rules))
    return groups


def robots_policy(raw: str, ua: str) -> str:
    """UA의 실효 정책 판정.

    반환: explicit-allow|explicit-block|explicit-partial
          |star-allow|star-block|star-partial|none
    """
    target = ua.lower()
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
    """우리 UA에 적용되는 Disallow/Allow 규칙 (없으면 User-agent:* 규칙)."""
    target = ua.lower()
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

class _CountingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin_host, rules=None):
        self.count = 0
        self.origin_host = origin_host
        self.rules = rules

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        parts = urllib.parse.urlsplit(newurl)
        if self.count > 10 or parts.scheme not in ("http", "https") or parts.netloc.lower() != self.origin_host:
            return None
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        if self.rules is not None and not crawl_allowed(self.rules, target):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


def fetch(url: str, method: str = "GET", rules=None):
    """한 번 받아온다. 예외를 던지지 않고 dict로 돌려준다."""
    out = {
        "status": None, "final_url": url, "headers": {}, "body": "",
        "ms": 0, "redirects": 0, "error": None, "content_type": "",
    }
    redirector = _CountingRedirect(host_of(url), rules)
    opener = urllib.request.build_opener(redirector)
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko,en;q=0.8",
    })
    started = time.monotonic()
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
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
            redirected = urllib.parse.urljoin(url, location)
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


def _bounded_gunzip(body: bytes, limit: int) -> tuple[bytes, bool]:
    """압축 폭탄이 메모리를 무제한 소비하지 않도록 limit+1 바이트만 푼다."""
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        expanded = stream.read(limit + 1)
    return expanded[:limit], len(expanded) > limit


def _classify_error(exc) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return "tls_fail"
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

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta_description = None
        self.meta_robots = None
        self.meta_googlebot = None
        self.canonical = None
        self.lang = None
        self.naver_site_verification = False
        self.og = {}
        self.h1 = []
        self.jsonld_raw = []
        self.links = []
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
        elif name in ("googlebot", "googlebot-news"):
            self.meta_googlebot = ", ".join(filter(None, (self.meta_googlebot, content))) or None
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
        if tag == "title":
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


def probe_mirrors(host: str) -> dict:
    """공개된 개발·스테이징 미러를 찾는다. 존재만 확인하고 크롤하지 않는다.

    본 도메인만 훑어서는 절대 보이지 않는 표면이다 — 실측에서 생성엔진이 우리 개발서버를
    "회사 홈페이지"라며 인용한 일이 있었고, 크롤 쪽은 그 호스트를 볼 일이 없어 놓쳤다.
    """
    bare = host[4:] if host.startswith("www.") else host
    bare = bare.split(":")[0]
    if (not bare) or bare == "localhost" or bare.startswith("[") or "." not in bare:
        return {"checked": [], "found": [], "wildcard_suspect": False}
    if all(c.isdigit() or c == "." for c in bare):
        return {"checked": [], "found": [], "wildcard_suspect": False}

    checked, found = [], []
    for prefix in MIRROR_PREFIXES:
        cand = "%s.%s" % (prefix, bare)
        if cand == host:
            continue
        checked.append(cand)
        res = fetch("https://%s/" % cand)
        if res["error"] in ("dns_fail", "tls_fail") or not res["status"]:
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
    wildcard = len(found) >= max(4, len(checked) - 1)
    return {"checked": checked, "found": [] if wildcard else found,
            "wildcard_suspect": wildcard}


def script_of(text: str) -> str:
    """길이 기준을 정할 문자 종류: 한글 비중이 높으면 ko."""
    if not text:
        return "en"
    return "ko" if len(HANGUL.findall(text)) >= max(1, len(text) * 0.15) else "en"


# ─────────────────────────────────────────────────────────── 크롤

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
        if host_of(seed) != host or not is_page(seed) or not crawl_allowed(rules, target):
            blocked_count += 1
            blocked_seeds += 1
            continue
        if seed not in seen:
            seen.add(seed)
            queue.append(seed)
    pages = []
    while queue and len(pages) < max_pages:
        url = queue.popleft()
        res = fetch(url, rules=rules)
        page = page_record(url, res)
        pages.append(page)
        sys.stderr.write("  · %-4s %s\n" % (page["status"] or "ERR", url))
        sys.stderr.flush()
        links = page.pop("_links", []) if page["status"] == 200 and host_of(res["final_url"] or url) == host else []
        for href in links:
            try:
                nxt = normalize(urllib.parse.urljoin(res["final_url"] or url, href))
            except ValueError:
                continue
            if not nxt.startswith(("http://", "https://")):
                continue
            if host_of(nxt) != host or nxt in seen or not is_page(nxt):
                continue
            parts = urllib.parse.urlsplit(nxt)
            target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            if not crawl_allowed(rules, target):
                blocked_count += 1
                continue
            seen.add(nxt)
            if len(seen) <= max_pages * 4:
                queue.append(nxt)
            else:
                dropped_discoveries += 1
        if delay:
            time.sleep(delay)
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
        if http_errors:
            reasons.append("http_errors")
        if not pages:
            reasons.append("no_pages_fetched")
        coverage.update({"complete": not reasons, "max_pages": max_pages,
                         "pages_fetched": len(pages), "queued_remaining": len(queue),
                         "blocked_count": blocked_count, "reasons": reasons})
    return pages


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
        "title": parser.title,
        "meta_description": parser.meta_description,
        "meta_robots": parser.meta_robots,
        "meta_googlebot": parser.meta_googlebot,
        "canonical": parser.canonical,
        "h1": parser.h1,
        "jsonld_count": count,
        "jsonld_types": sorted(set(types)),
        "text_chars": parser.text_chars,
        "og": parser.og,
        "naver_site_verification": parser.naver_site_verification,
        "lang": parser.lang,
        "_links": parser.links,
    })
    return page


# ─────────────────────────────────────────────────────────── 사이트 수준

def probe_site(base: str, robots_raw: str, robots_status, crawled: list, sitemap_data=None) -> dict:
    host = host_of(base)
    declared = [
        m.strip() for m in
        re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_raw or "")
    ]
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
    for name in ("llms.txt", "llms-full.txt"):
        llms[name] = allowed_fetch("%s/%s" % (base, name))["status"]

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
        elif alt["error"]:
            alt_result = alt["error"] if alt["error"] in ("tls_fail", "dns_fail") else "error"
        elif alt["redirects"]:
            alt_result = "redirect"
        else:
            alt_result = "ok"

    return {
        "robots": {
            "status": robots_status,
            "present": bool(robots_raw),
            "raw": robots_raw or "",
            "policies": {ua: robots_policy(robots_raw or "", ua) for ua in ALL_UAS},
            "sitemap_declared": declared,
        },
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
            },
        },
    }


def sitemap_candidates(base: str, host: str, declared: list) -> list:
    """robots.txt가 준 값을 그대로 믿지 않는다 — http(s) + 진단 대상 호스트만 남긴다(SSRF 방지)."""
    candidates = []
    for url in list(declared) + ["%s/sitemap.xml" % base, "%s/sitemap_index.xml" % base]:
        if not url.startswith(("http://", "https://")) or host_of(url) != host:
            continue
        if url not in candidates:
            candidates.append(url)
    return candidates


def read_sitemaps(base: str, host: str, declared: list):
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
        res = fetch(url)
        locs, is_index, parsed, parse_error = [], False, False, None
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
        results.append({
            "url": url, "status": res["status"],
            "is_index": is_index, "url_count": len(locs), "parsed": parsed,
            "truncated": bool(res.get("truncated")) or (is_index and len(locs) > 100),
            "error": res.get("error") or parse_error,
        })
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


def analyze(base: str, site: dict, pages: list) -> tuple:
    findings: list = []
    ok = [p for p in pages if p["status"] == 200]
    total = max(1, len(ok))

    _check_indexability(findings, ok)
    _check_meta(findings, ok, total)
    _check_structure(findings, ok, total)
    _check_site(findings, base, site, ok)
    _check_crawler_policy(findings, site)

    stats = {
        "pages_crawled": len(pages),
        "unique_titles": len({p["title"] for p in ok if p["title"]}),
        "unique_descriptions": len({p["meta_description"] for p in ok if p["meta_description"]}),
        "pages_with_jsonld": sum(1 for p in ok if p["jsonld_count"]),
        "pages_noindex": sum(1 for p in ok if _noindex(p)),
    }
    return findings, stats, scorecard(findings)


def _noindex(page) -> bool:
    return "noindex" in _robots_directives(page)


def _robots_directives(page, ua="googlebot") -> set[str]:
    """페이지 수준 지시를 토큰화한다. generic과 해당 UA 지시는 합산한다."""
    values = [page.get("meta_robots") or ""]
    if ua.startswith("googlebot"):
        values.append(page.get("meta_googlebot") or "")
    header = page.get("x_robots_tag") or ""
    scope_pattern = re.compile(r"^\s*([\w-]+(?:bot|user))\s*:\s*(.*)$", re.I)
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
    no_ld = [p["url"] for p in ok if not p["jsonld_count"]]
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


def _check_site(findings, base, site, ok):
    pages_all_status = site.get("_all_pages_status", [])
    broken = [u for u, s in pages_all_status if s is None or s >= 400]
    if broken:
        add(findings, "SEO", "warn", "HTTP_ERROR",
            "내부 링크를 따라간 URL 중 %d개가 오류를 냈다." % len(broken), broken,
            {"count": len(broken)})

    if not site["robots"]["present"]:
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

    coverage = site.get("_coverage")
    if coverage and not coverage.get("complete"):
        add(findings, "SEO", "critical", "CRAWL_INCOMPLETE",
            "크롤 범위가 완전하지 않다 (%s). 결과를 전수 진단으로 사용하면 안 된다."
            % ", ".join(coverage.get("reasons") or ["unknown"]), [], coverage)

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


def _check_crawler_policy(findings, site):
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


def scorecard(findings) -> dict:
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
        board[lane] = {"status": status, "evidence": [f["code"] for f in mine]}
    return board


# ─────────────────────────────────────────────────────────── 콘솔 요약

STATUS_MARK = {"ok": "✅", "warn": "⚠️", "bad": "❌", "na": "—"}
SEV_MARK = {"critical": "🚨", "warn": "⚠️ ", "info": "· "}


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
    print("   robots.txt      : %s (HTTP %s)"
          % ("있음" if site["robots"]["present"] else "❌ 없음/비정상",
             site["robots"]["status"]))
    print("   Sitemap 선언    : %s"
          % ("✅ %d건" % len(site["robots"]["sitemap_declared"])
             if site["robots"]["sitemap_declared"] else "❌ robots.txt에 없음"))
    for sm in site["sitemaps"]:
        tag = "인덱스 · 하위 %d" % sm["url_count"] if sm["is_index"] else "URL %d개" % sm["url_count"]
        print("   %-42s HTTP %s  (%s)" % (sm["url"], sm["status"], tag))
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
    hygiene = site["hygiene"]
    print("   404 동작        : HTTP %s  (404여야 정상)" % hygiene["probe_404"])
    print("   리다이렉트 홉   : %s" % hygiene["redirect_hops"])
    print("   홈 응답 시간    : %sms" % hygiene["home_response_ms"])
    alt = hygiene["alt_host"]
    print("   도메인 변형     : %s" % ("해당 없음 (IP·localhost 대상)" if alt["result"] == "na"
                                       else "https://%s → %s" % (alt["host"], alt["result"])))

    print("")
    print("── 5. 레인 점수표 ──")
    for lane in LANES:
        cell = report["scorecard"].get(lane) or {"status": "na", "evidence": []}
        codes = [f["code"] for f in report["findings"]
                 if f["lane"] == lane and f["severity"] != "info"]
        print("   %-11s %s  %s" % (lane, STATUS_MARK[cell["status"]],
                                   ", ".join(codes[:4]) or cell.get("note", "")))

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
    }.get(policy, "미설정 → 기본 허용 (명시 권장)")


# ─────────────────────────────────────────────────────────── main

def build_report(target: str, max_pages: int, delay: float, allow_noindex=None) -> dict:
    base = target if target.startswith(("http://", "https://")) else "https://%s" % target
    base = base.rstrip("/")
    host = host_of(base)

    robots = fetch("%s/robots.txt" % base)
    raw = robots["body"]
    # 404를 200처럼 꾸민 HTML 오류 페이지를 robots.txt로 오인하지 않는다
    if robots["status"] != 200 or raw.lstrip().startswith("<"):
        raw = ""
    rules = crawl_rules(raw, UA.split("/")[0])
    declared = [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", raw or "")]
    sitemap_data = read_sitemaps(base, host, declared)
    coverage = {}

    sys.stderr.write("크롤 시작: %s (최대 %d페이지, 간격 %.1fs)\n" % (base, max_pages, delay))
    pages = crawl_site(base, max_pages, delay, rules, seeds=sitemap_data[1], coverage=coverage)
    allowed_paths = set(allow_noindex or [])
    for page in pages:
        if urllib.parse.urlsplit(page["url"]).path in allowed_paths:
            page["intended_indexable"] = False

    if robots["status"] != 200 or robots.get("error"):
        coverage["complete"] = False
        coverage["reasons"] = list(dict.fromkeys(coverage["reasons"] + ["robots_unavailable"]))
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

    site = probe_site(base, raw, robots["status"], pages, sitemap_data=sitemap_data)
    site["_coverage"] = coverage
    site["_all_pages_status"] = [(p["url"], p["status"]) for p in pages]
    findings, stats, board = analyze(base, site, pages)
    site.pop("_all_pages_status", None)
    site.pop("_coverage", None)

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
    args = ap.parse_args(argv)

    report = None
    try:
        if args.max_pages < 1:
            ap.error("--max-pages는 1 이상이어야 한다")
        if args.delay < 0:
            ap.error("--delay는 0 이상이어야 한다")
        report = build_report(args.target, args.max_pages, args.delay, args.allow_noindex)
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
    print("보고서 생성    : python tools/report.py %s" % path)
    return 0 if report.get("coverage", {}).get("complete") is True else 2


if __name__ == "__main__":
    sys.exit(main())
