#!/usr/bin/env python3
"""Conservative, standard-library evidence collection for rebuild briefs."""

from __future__ import annotations

import http.client
import ipaddress
import math
import queue
import re
import socket
import ssl
import threading
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

SCHEMA = "su-presence/rebuild-brief/1"
UA = "su-presence-research/1"
MAX_REDIRECTS = 5
MAX_ROBOTS_BYTES = 100_000
_MAX_ACTIVE_REQUESTS = 32
_request_slots = threading.BoundedSemaphore(_MAX_ACTIVE_REQUESTS)


def public_url(url: str) -> bool:
    """True for a syntactically valid public HTTP URL (DNS is checked at fetch)."""
    if not isinstance(url, str) or not url or any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if parts.scheme.lower() not in ("http", "https") or not host or parts.username is not None or parts.password is not None:
            return False
        if port is not None and not 1 <= port <= 65535:
            return False
        if parts.fragment or "\\" in url or "%" in host:
            return False
        host = host.rstrip(".").lower()
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            pass
        # DNS names must be ordinary names; DNS resolution checks every address.
        ascii_host = host.encode("idna").decode("ascii")
        labels = ascii_host.split(".")
        return (len(labels) >= 2 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                and not labels[-1].isdigit())
    except (ValueError, UnicodeError):
        return False


def _strings(value, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{label} must be a list of nonempty strings")


def _host_key(url: str) -> str:
    return urlsplit(url).hostname.rstrip(".").encode("idna").decode("ascii").lower()


def validate_brief(brief: dict) -> dict:
    """Validate the complete input shape and return a shallow independent copy."""
    if not isinstance(brief, dict) or brief.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    if not public_url(brief.get("site_url")):
        raise ValueError("site_url must be a public HTTP URL")
    queries = brief.get("queries")
    if not isinstance(queries, list):
        raise ValueError("queries must be a list")
    query_ids = set()
    intent_ids = set()
    for index, item in enumerate(queries):
        if not isinstance(item, dict):
            raise ValueError(f"queries[{index}] must be an object")
        for name in ("id", "text", "intent_id", "page_type"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ValueError(f"queries[{index}].{name} must be a nonempty string")
        for name in ("terms", "required_sections"):
            _strings(item.get(name), f"queries[{index}].{name}")
        if item["id"] in query_ids:
            raise ValueError("query ids must be unique")
        query_ids.add(item["id"])
        intent_ids.add(item["intent_id"])
        if "priority" in item and (type(item["priority"]) is not int or item["priority"] not in (1, 2, 3)):
            raise ValueError(f"queries[{index}].priority must be 1, 2, or 3")
        if "type" in item and item["type"] not in ("brand", "nonbrand"):
            raise ValueError(f"queries[{index}].type must be brand or nonbrand")
    competitors = brief.get("competitors")
    if not isinstance(competitors, list) or any(not public_url(x) for x in competitors):
        raise ValueError("competitors must be a list of public HTTP URLs")
    _strings(brief.get("channels"), "channels")
    facts = brief.get("facts")
    if not isinstance(facts, list):
        raise ValueError("facts must be a list")
    fact_ids = set()
    for index, item in enumerate(facts):
        if not isinstance(item, dict):
            raise ValueError(f"facts[{index}] must be an object")
        for name in ("id", "text"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ValueError(f"facts[{index}].{name} must be a nonempty string")
        if item["id"] in fact_ids:
            raise ValueError("fact ids must be unique")
        fact_ids.add(item["id"])
        if not public_url(item.get("source_url")) or type(item.get("verified")) is not bool:
            raise ValueError(f"facts[{index}] needs public source_url and boolean verified")
        if "intent_ids" in item:
            _strings(item["intent_ids"], f"facts[{index}].intent_ids")
            if any(intent not in intent_ids for intent in item["intent_ids"]):
                raise ValueError(f"facts[{index}].intent_ids must reference known intents")
    pages = brief.get("pages", [])
    if not isinstance(pages, list):
        raise ValueError("pages must be a list")
    for index, item in enumerate(pages):
        if not isinstance(item, dict) or not isinstance(item.get("intent_id"), str) or not item["intent_id"].strip() or not public_url(item.get("url")):
            raise ValueError(f"pages[{index}] needs intent_id and public url")
        if item["intent_id"] not in intent_ids or _host_key(item["url"]) != _host_key(brief["site_url"]):
            raise ValueError(f"pages[{index}] must reference a known intent on the site host")
    return dict(brief)


def _resolve_host(host: str, port: int) -> list[str]:
    addresses = {row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("DNS returned a nonpublic address")
    return sorted(addresses)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _request_once(url: str, limit: int, timeout: float) -> tuple[int, dict, bytes]:
    """Bound the entire DNS, connection, headers, and body request by timeout.

    A DNS resolver or drip response may ignore a socket's per-operation timeout.
    Daemon workers cannot delay process shutdown; the semaphore bounds stragglers.
    """
    started = time.monotonic()
    if not _request_slots.acquire(timeout=timeout):
        raise TimeoutError("request worker limit exceeded")
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        _request_slots.release()
        raise TimeoutError("fetch deadline exceeded")
    results = queue.Queue(maxsize=1)

    def run():
        try:
            results.put((True, _request_once_worker(url, limit, remaining)))
        except Exception as exc:
            results.put((False, exc))
        finally:
            _request_slots.release()

    worker = threading.Thread(target=run, name="su-presence-fetch", daemon=True)
    worker.start()
    try:
        ok, value = results.get(timeout=remaining)
    except queue.Empty as exc:
        raise TimeoutError("fetch deadline exceeded") from exc
    if not ok:
        raise value
    return value


def _request_once_worker(url: str, limit: int, timeout: float) -> tuple[int, dict, bytes]:
    """Resolve and pin a public IP; no proxy or implicit redirect is used."""
    deadline = time.monotonic() + timeout
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = _resolve_host(host, port)[0]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("fetch deadline exceeded")
    connection = (_PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP)(host, port, address, remaining)
    path = urlunsplit(("", "", quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                       quote(parts.query, safe="/%:@!$&'()*+,;=?-._~"), ""))
    try:
        connection.request("GET", path, headers={"User-Agent": UA, "Accept": "text/html,text/plain;q=0.8", "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response exceeds byte limit")
        return response.status, headers, body
    finally:
        connection.close()


def _fetch_raw(url: str, limit: int, deadline: float) -> tuple[str, int, dict, bytes]:
    current = url
    origin = urlsplit(url).netloc.lower()
    for hop in range(MAX_REDIRECTS + 1):
        if not public_url(current):
            raise ValueError("redirect to nonpublic URL")
        if urlsplit(current).netloc.lower() != origin:
            raise ValueError("robots redirected to another origin")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("fetch deadline exceeded")
        status, headers, body = _request_once(current, limit, remaining)
        if status in (301, 302, 303, 307, 308):
            if hop >= MAX_REDIRECTS:
                raise ValueError("redirect limit exceeded")
            location = headers.get("location")
            if not location:
                raise ValueError("redirect has no location")
            current = urljoin(current, location)
            continue
        return current, status, headers, body
    raise ValueError("redirect limit exceeded")


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.stack = []
        self.hidden_flags = []
        self.title = []
        self.headings = []
        self.h1 = []
        self.active_headings = []
        self.text = []
        self.table = False
        self.faq = False
        self.sources = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden_tag = tag in ("script", "style", "noscript", "template", "nav", "footer", "aside", "svg", "iframe")
        hidden_attr = ("hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
                       or bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)(?:\s*;|\s*$)", str(attrs.get("style", "")), re.I)))
        is_hidden = hidden_tag or hidden_attr
        self.stack.append(tag)
        self.hidden_flags.append(is_hidden)
        if is_hidden:
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.active_headings.append((tag, []))
        self.table |= tag == "table"
        label = " ".join(str(attrs.get(key) or "") for key in ("id", "class", "aria-label")).lower()
        self.faq |= bool(re.search(r"\bfaq\b|자주\s*묻는", label))
        self.sources |= bool(re.search(r"\bsources?\b|\breferences?\b|출처|참고\s*자료", label))

    def handle_endtag(self, tag):
        if self.active_headings and self.active_headings[-1][0] == tag:
            heading_tag, pieces = self.active_headings.pop()
            value = " ".join(pieces).strip()
            if value:
                self.headings.append(value)
                if heading_tag == "h1":
                    self.h1.append(value)
                self.faq |= bool(re.search(r"\bfaq\b|자주\s*묻는\s*질문", value, re.I))
                self.sources |= bool(re.search(r"\bsources?\b|\breferences?\b|출처|참고\s*자료", value, re.I))
        if tag in self.stack:
            while self.stack:
                popped = self.stack.pop()
                if self.hidden_flags.pop():
                    self.hidden -= 1
                if popped == tag:
                    break

    def handle_data(self, data):
        if self.hidden or not data.strip():
            return
        value = " ".join(data.split())
        if "title" in self.stack:
            self.title.append(value)
        if self.active_headings:
            self.active_headings[-1][1].append(value)
        if "head" not in self.stack:
            self.text.append(value)


def extract_page(html: str, url: str) -> dict:
    """Extract only text and structural hints present in supplied HTML."""
    parser = _VisibleHTML()
    parser.feed(html)
    headings = parser.headings[:100]
    return {"url": url, "title": " ".join(parser.title)[:500],
            "h1": " ".join(parser.h1)[:500],
            "headings": headings, "text_excerpt": " ".join(parser.text)[:5000],
            "features": {"table": parser.table, "faq": parser.faq, "sources": parser.sources}}


def fetch_page(url: str, max_bytes: int = 1_000_000, timeout: float = 15) -> dict:
    """Fetch one public HTML page under robots policy; failures remain explicit."""
    result = {"url": url, "final_url": url, "status": None, "outcome": "error", "title": "", "h1": "",
              "headings": [], "text_excerpt": "", "features": {"table": False, "faq": False, "sources": False},
              "fetched_at": datetime.now(timezone.utc).isoformat(), "error": None}
    if not public_url(url) or type(max_bytes) is not int or max_bytes < 1 or max_bytes > 10_000_000 or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        result.update(outcome="blocked", error="invalid URL or fetch limits")
        return result
    deadline = time.monotonic() + timeout
    try:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            robots_final, robots_status, _, robots_body = _fetch_raw(robots_url, MAX_ROBOTS_BYTES, deadline)
        except (OSError, ValueError, TimeoutError, http.client.HTTPException) as exc:
            raise ValueError(f"robots unavailable: {exc}") from exc
        if urlsplit(robots_final).netloc.lower() != parts.netloc.lower():
            raise ValueError("robots redirected to another origin")
        if robots_status in (404, 410):
            allowed = True
        elif robots_status == 200:
            parser = RobotFileParser()
            parser.parse(robots_body.decode("utf-8", errors="replace").splitlines())
            allowed = parser.can_fetch(UA, url)
        else:
            raise ValueError(f"robots unavailable: HTTP {robots_status}")
        if not allowed:
            result.update(outcome="blocked", error="robots disallow")
            return result
        # Check robots separately for every redirect destination.
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            if not public_url(current):
                raise ValueError("redirect to nonpublic URL")
            if hop:
                target = urlsplit(current)
                target_robots = urlunsplit((target.scheme, target.netloc, "/robots.txt", "", ""))
                try:
                    robot_final, robot_status, _, robot_body = _fetch_raw(target_robots, MAX_ROBOTS_BYTES, deadline)
                except (OSError, ValueError, TimeoutError, http.client.HTTPException) as exc:
                    raise ValueError(f"redirect target robots unavailable: {exc}") from exc
                if urlsplit(robot_final).netloc.lower() != target.netloc.lower() or robot_status not in (200, 404, 410):
                    raise ValueError("redirect target robots unavailable")
                if robot_status == 200:
                    parser = RobotFileParser()
                    parser.parse(robot_body.decode("utf-8", errors="replace").splitlines())
                    if not parser.can_fetch(UA, current):
                        raise ValueError("redirect target robots disallow")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("fetch deadline exceeded")
            status, headers, body = _request_once(current, max_bytes, remaining)
            result.update(final_url=current, status=status)
            if status in (301, 302, 303, 307, 308):
                if hop >= MAX_REDIRECTS or not headers.get("location"):
                    raise ValueError("redirect limit or missing location")
                current = urljoin(current, headers["location"])
                continue
            if status != 200:
                raise ValueError(f"HTTP {status}")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("non-HTML response")
            charset_match = re.search(r"charset\s*=\s*([^;\s]+)", headers.get("content-type", ""), re.I)
            charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
            html = body.decode(charset, errors="replace")
            result.update(extract_page(html, current))
            result.update(url=url, final_url=current, status=status, outcome="observed")
            return result
    except (OSError, ValueError, TimeoutError, UnicodeError, LookupError, ssl.SSLError, http.client.HTTPException) as exc:
        message = str(exc) or type(exc).__name__
        outcome = "blocked" if any(word in message for word in ("nonpublic", "robots", "redirect", "DNS")) else "error"
        result.update(outcome=outcome, error=message)
    return result
