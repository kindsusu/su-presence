#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-presence M3 — 배포 후 검증. "고쳤다"를 크롤러의 눈으로 증명한다.

사용:
    python tools/verify.py deploy out/<host>/audit.json [--deploy out/<host>/deploy] [--facts facts.json]
    python tools/verify.py diff   before/audit.json after/audit.json

출력:
    <out>/verify.json (스키마: su-multi-geo/verify/1) + VERIFY.md + 콘솔 요약
    exit code: fail이 하나라도 있으면 1, fail은 없지만 검사 범위 불완전(deploy)이나
               결론 보류(diff — after 진단이 판정하지 못한 레인·findings)면 2, 그 밖엔 0

원칙
  · 라이브 사이트에서 받은 것만 근거다. 패키지에 있다는 것은 근거가 아니다.
  · 진단 대상 호스트 외에는 요청하지 않는다. 리다이렉트 목적지도 다시 검사한다.
  · 파서·정책 판정·정규화는 crawl.py 것을 그대로 쓴다 (복제 금지).
  · 표준 라이브러리만 쓴다 (pip 의존 0).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawl  # noqa: E402   (fetch·PageParser·robots 정책·normalize를 재사용)
import generate  # noqa: E402  (slug_of — 패키지 파일명 ↔ URL 역매핑)

SCHEMA = "su-presence/verify/1"
AUDIT_SCHEMA_PREFIX = ("su-presence/audit/", "su-multi-geo/audit/")
JSONLD_MANIFEST_SCHEMAS = ("su-presence/jsonld-manifest/1",
                           "su-multi-geo/jsonld-manifest/1")

ORDER = {"fail": 0, "warn": 1, "pass": 2, "skip": 3}
MARK = {"fail": "❌", "warn": "⚠️", "pass": "✅", "skip": "—"}

# 실패했을 때 사람이 다음에 할 일 — VERIFY.md에 한 줄씩 붙는다
NEXT = {
    "noindex": "noindex를 먼저 걷어내라. 이게 남아 있으면 나머지 최적화는 전부 무효다.",
    "robots.status": "robots.txt가 200으로 서빙되는지 확인하라 (CDN·SEO 플러그인이 가로채는 경우가 많다).",
    "robots.preserved": "기존 robots.txt 원문이 지워졌다. 백업본을 되돌리고 추가 블록만 다시 얹어라.",
    "robots.policy": "추가한 UA 블록이 실제로 서빙되지 않는다. 배포 파일과 서버 파일을 대조하라.",
    "robots.sitemap": "robots.txt에 `Sitemap:` 줄을 넣어라.",
    "sitemap.reachable": "사이트맵이 200으로 응답하고 XML로 파싱되는지 확인하라.",
    "sitemap.locs": "사이트맵에 실린 URL이 200이 아니다 — 죽은 URL을 빼거나 페이지를 되살려라.",
    "sitemap.noindex": "noindex 페이지가 사이트맵에 들어 있다 — 둘 중 하나를 고쳐라.",
    "sitemap.canonical": "사이트맵 URL과 canonical이 어긋난다 — 사이트맵에 canonical URL을 실어라.",
    "llms.status": "llms.txt를 웹루트에 올려라.",
    "llms.todo": "llms.txt에 `<<TODO` 표식이 남아 있다 — 미완성 배포다. 채우고 다시 올려라.",
    "llms.format": "선택 사항이다. llms.txt를 쓰는 서비스를 위해 다듬을 때 `verify.json`의 메모(줄 번호)를 "
                   "참고하라. Google 검색에는 필요 없고 순위에 영향이 없다 (lanes/geo.md 1번).",
    "jsonld.present": "해당 페이지 `<head>`에 LD 스니펫이 들어가지 않았다.",
    "jsonld.type": "LD가 들어갔지만 @type이 다르다 — 스니펫을 다시 붙여라.",
    "jsonld.visible": "LD가 화면에 없는 말을 한다 — 스팸으로 분류될 수 있다. "
                      "본문에 그 문구를 노출하거나 LD에서 빼라.",
    "jsonld.org_id": "Organization @id가 페이지마다 다르다 — 엔티티가 쪼개진다. 전역 1개로 통일하라.",
    "jsonld.rating_scale": "평점이 숫자가 아니거나 선언한 척도(worstRating~bestRating, 기본 1~5) 밖이다 — "
                           "조작된 평점으로 읽혀 리치 결과를 잃는다. 실제 평점과 척도를 다시 확인해 고쳐라.",
    "jsonld.rating_count": "ratingCount·reviewCount는 0 이상의 정수여야 한다 — 실제 리뷰 수를 세어 정수로 적어라.",
    "facts.match": "사실 원장의 기대값과 라이브 LD가 다르다 — 근거(evidence) 문서를 다시 보고 "
                   "LD나 원장 중 틀린 쪽을 고쳐라. 추정으로 맞추지 마라.",
    "facts.visible": "LD 값은 맞지만 화면에서 그 값을 찾지 못했다 — 본문에 노출하거나 표기를 확인하라.",
    "meta.applied": "meta 초안이 반영되지 않았다 — meta-draft.csv를 검토해 적용하라.",
    "meta.duplicate": "중복 title이 남아 있다 — 페이지 고유 문구를 사람이 붙여야 한다.",
    "diff.new": "새로 생긴 findings다 — 배포가 만든 회귀인지 먼저 확인하라.",
    "diff.persisting": "critical이 그대로 남았다 — 다음 사이클 최우선 과제다.",
    "diff.scorecard": "레인 점수가 나빠졌거나 after 진단에서 판정하지 못했다 — 악화면 findings를 "
                      "대조하고, 미확인이면 원인(robots.txt 응답 등)을 고친 뒤 다시 진단하라.",
    "diff.unverified": "after 진단이 robots.txt를 읽지 못해 크롤러 정책 findings를 확인하지 못했다 — "
                       "robots.txt가 200으로 응답하게 고친 뒤 다시 진단하고 비교하라.",
    "diff.pages": "이전 크롤에 있던 URL이 사라졌다 — 404인지 리다이렉트인지 확인하라.",
}


# ─────────────────────────────────────────────────────────── 체크 목록

def chk(checks, cid, status, message, evidence=None):
    checks.append({"id": cid, "status": status, "message": message,
                   "evidence": evidence or {}})


def summarize(checks) -> dict:
    counts = Counter(c["status"] for c in checks)
    return {k: counts.get(k, 0) for k in ("pass", "fail", "warn", "skip")}


# ─────────────────────────────────────────────────────────── 세션 (네트워크)

class Session:
    """대상 호스트에만 요청하는 캐시 세션. fetch는 주입 가능하다(테스트용)."""

    def __init__(self, host: str, fetch=None, delay: float = 0.5):
        self.host = host
        self.fetch = fetch or crawl.fetch
        self.delay = delay
        self.cache: dict = {}
        self.fetches = 0

    def get(self, url: str) -> dict:
        if url not in self.cache:
            self.cache[url] = self._load(url)
        return self.cache[url]

    def _load(self, url: str) -> dict:
        rec = {
            "url": url, "final_url": url, "status": None, "error": None,
            "off_host": False, "body": "", "text": "", "title": None,
            "meta_description": None, "meta_robots": None, "x_robots_tag": None,
            "canonical": None, "ld_objs": [], "ld_types": [], "ld_broken": 0,
            "html": False,
        }
        if not url.startswith(("http://", "https://")) or crawl.host_of(url) != self.host:
            rec["off_host"] = True
            rec["error"] = "off_host"
            return rec
        if self.fetches and self.delay:
            time.sleep(self.delay)
        self.fetches += 1
        res = self.fetch(url) or {}
        rec["status"] = res.get("status")
        rec["final_url"] = res.get("final_url") or url
        rec["error"] = res.get("error")
        rec["truncated"] = bool(res.get("truncated"))
        headers = res.get("headers") or {}
        rec["x_robots_tag"] = headers.get("x-robots-tag")
        # 리다이렉트 목적지도 대상 호스트여야 한다 — 밖으로 나갔으면 본문을 쓰지 않는다
        if crawl.host_of(rec["final_url"]) != self.host:
            rec["off_host"] = True
            rec["error"] = rec["error"] or "off_host_redirect"
            return rec
        rec["body"] = res.get("body") or ""
        ctype = (res.get("content_type") or "").lower()
        if rec["body"] and ("html" in ctype or not ctype):
            rec["html"] = True
            self._parse(rec)
        return rec

    @staticmethod
    def _parse(rec: dict) -> None:
        parser = crawl.PageParser()
        try:
            parser.feed(rec["body"])
            parser.close()
        except Exception:  # 깨진 HTML에서도 여기까지 모은 값은 살린다
            pass
        rec["title"] = parser.title
        rec["meta_description"] = parser.meta_description
        rec["meta_robots"] = parser.meta_robots
        rec["canonical"] = parser.canonical
        rec["text"] = parser.text
        ok, types = crawl.jsonld_types(parser.jsonld_raw)
        rec["ld_types"] = sorted(set(types))
        rec["ld_broken"] = len(parser.jsonld_raw) - ok
        for block in parser.jsonld_raw:
            try:
                rec["ld_objs"].append(json.loads(block))
            except (ValueError, TypeError):
                continue


def is_noindex(rec: dict) -> bool:
    return crawl._noindex({"meta_robots": rec.get("meta_robots"),
                           "x_robots_tag": rec.get("x_robots_tag")})


def nodes(obj):
    """JSON-LD 트리의 dict 노드를 전부 훑는다 (@graph·중첩 포함)."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from nodes(item)


def norm_text(value) -> str:
    """공백만 정규화한 문자열. None만 빈 값이다 — 0·False를 ""로 접지 않는다."""
    return crawl.WS.sub(" ", "" if value is None else str(value)).strip()


def visible(text: str, needle) -> bool:
    """LD 값이 가시 텍스트에 글자 그대로 있는가 (공백만 정규화)."""
    needle = norm_text(needle)
    return bool(needle) and needle in text


def price_visible(text: str, price) -> bool:
    """가격은 천단위 쉼표 표기도 같은 값으로 본다."""
    raw = norm_text(price)
    if not raw:
        return False
    if raw in text:
        return True
    try:
        return format(int(float(raw)), ",") in text
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────── 필드 단위 비교 (JSON pointer)

_MISSING = object()
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
# 값이 URL·식별자라 화면 글자와 비교할 대상이 아닌 키
URL_KEYS = frozenset(("@id", "url", "item", "logo", "image", "sameAs", "contentUrl",
                      "thumbnailUrl", "embedUrl", "mainEntityOfPage"))
NON_VISIBLE_KEYS = URL_KEYS | frozenset(("@type", "@context", "priceCurrency", "availability",
                                         "itemCondition", "inLanguage", "position",
                                         "worstRating", "bestRating"))
# 앞자리 0까지 의미가 있는 식별자 — 숫자로 바꿔 비교하지 않는다
ID_KEYS = frozenset(("sku", "mpn", "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "isbn",
                     "productID", "identifier", "telephone", "faxNumber", "postalCode",
                     "vatID", "taxID", "duns", "leiCode"))
NUMERIC_KEYS = frozenset(("price", "lowPrice", "highPrice", "minPrice", "maxPrice",
                          "ratingValue", "bestRating", "worstRating", "ratingCount",
                          "reviewCount", "minValue", "maxValue"))
# `value`는 담은 노드에 따라 뜻이 다르다 — 수량·금액이면 수, PropertyValue(식별자 등)면 글자 그대로.
NUMERIC_VALUE_TYPES = frozenset(("QuantitativeValue", "MonetaryAmount"))
_LEADING_ZERO = re.compile(r"^\s*[+-]?0\d")


def numeric_key(key: str, container_types=None) -> bool:
    """이 키의 값을 수로 비교하는가. `value`는 QuantitativeValue·MonetaryAmount 안에서만 수다."""
    if key in ID_KEYS:
        return False
    if key == "value":
        return bool(NUMERIC_VALUE_TYPES.intersection(container_types or ()))
    return key in NUMERIC_KEYS


def _leading_zero(value) -> bool:
    """'007'처럼 앞자리 0이 의미를 갖는 문자열인가 (0.5 같은 소수는 아니다)."""
    return isinstance(value, str) and bool(_LEADING_ZERO.match(value))


def pointer_escape(token) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer_tokens(pointer: str) -> list:
    """RFC 6901 JSON pointer를 토큰으로 나눈다. 빈 문자열은 루트."""
    if pointer == "":
        return []
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON pointer는 '/'로 시작해야 한다: %r" % (pointer,))
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def resolve_pointer(obj, pointer: str):
    """pointer가 가리키는 값. 없으면 _MISSING (배열 첨자는 엄격하게 본다)."""
    current = obj
    for token in pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not re.fullmatch(r"0|[1-9]\d*", token) or int(token) >= len(current):
                return _MISSING
            current = current[int(token)]
        else:
            return _MISSING
    return current


def node_types(node) -> list:
    t = node.get("@type") if isinstance(node, dict) else None
    return [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]


def typed_nodes(obj, path: str = ""):
    """nodes()와 같은 순서로 dict 노드를 훑되 JSON pointer를 함께 낸다."""
    if isinstance(obj, dict):
        yield path, obj
        for key, value in obj.items():
            yield from typed_nodes(value, "%s/%s" % (path, pointer_escape(key)))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from typed_nodes(item, "%s/%d" % (path, i))


def ld_leaves(node, path: str = ""):
    """패키지 객체의 말단 값 (pointer, key, value). @context는 비교하지 않는다."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "@context":
                continue
            sub = "%s/%s" % (path, pointer_escape(key))
            if key == "@type":
                yield sub, key, value
            else:
                yield from ld_leaves(value, sub)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from ld_leaves(item, "%s/%d" % (path, i))
    else:
        key = path.rsplit("/", 1)[-1] if path else ""
        if key.isdigit():  # 배열 원소면 배열의 키를 쓴다 (sameAs/0 → sameAs)
            key = path.rsplit("/", 2)[-2] if path.count("/") >= 2 else ""
        yield path, key, node


def as_decimal(value):
    """JSON 수 또는 숫자 문자열 → Decimal. 숫자가 아니면 None (bool·NaN·무한대 포함)."""
    from decimal import Decimal, InvalidOperation
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = repr(value)
    elif isinstance(value, str) and _NUMBER.match(value.strip()):
        text = value.strip()
    else:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _identifier(value) -> str:
    if not value:
        return ""
    try:
        parts = urllib.parse.urlsplit(str(value))
        base = crawl.normalize(urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, parts.query, "")))
    except (TypeError, ValueError):
        return norm_text(value)
    return base + (("#" + parts.fragment) if parts.fragment else "")


def same_value(a, b, key: str = "", container_types=None) -> bool:
    """패키지/기대값 a와 라이브 값 b가 같은 사실인가. 추론 없이 표기 차이만 접는다.

    container_types: 값을 담은 노드의 @type 목록 (`value` 키의 뜻을 가른다).
    """
    if key == "@type":
        return sorted(node_types({"@type": a})) == sorted(node_types({"@type": b}))
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return a == b
    if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if key in URL_KEYS:
        return _identifier(a) == _identifier(b)
    if numeric_key(key, container_types) or (
            key not in ID_KEYS and (isinstance(a, (int, float)) or isinstance(b, (int, float)))
            and not (_leading_zero(a) or _leading_zero(b))):
        da, db = as_decimal(a), as_decimal(b)
        if da is not None and db is not None:
            return da == db
        if da is not None or db is not None:
            return False   # 한쪽만 수다 — 0과 ""(빈 값)·글자를 같은 값으로 보지 않는다
    return norm_text(a) == norm_text(b)


def number_visible(text: str, number, raw: str) -> bool:
    """숫자가 화면에 있는가 — 1,234 표기를 같게 보고, 더 긴 숫자의 일부는 인정하지 않는다."""
    candidates = {raw} if raw else set()
    if number == number.to_integral_value():
        whole = int(number)
        candidates |= {str(whole), format(whole, ",")}
    else:
        plain = format(number.normalize(), "f")
        candidates |= {plain, format(number.normalize(), ",f")}
    for cand in sorted(candidates):
        if re.search(r"(?<![\d.,])%s(?![\d]|[.,]\d)" % re.escape(cand), text):
            return True
    return False


def value_visible(text: str, value, key: str = "", container_types=None):
    """값이 가시 텍스트에 있는가. URL·코드값처럼 화면 비교 대상이 아니면 None."""
    if value is None or isinstance(value, (bool, dict, list, tuple)) or key in NON_VISIBLE_KEYS:
        return None
    if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
        return None
    if numeric_key(key, container_types) or (key not in ID_KEYS
                                             and isinstance(value, (int, float))):
        number = as_decimal(value)
        if number is not None:
            return number_visible(text, number, norm_text(value))
    return visible(text, value)


def _clip(value, limit: int = 200):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def container_types(root, pointer: str) -> list:
    """pointer가 가리키는 말단 값을 담은 가장 가까운 dict 노드의 @type 목록."""
    tokens = pointer_tokens(pointer)
    while tokens:
        tokens = tokens[:-1]
        node = resolve_pointer(root, "".join("/" + pointer_escape(t) for t in tokens))
        if isinstance(node, dict):
            return node_types(node)
    return []


def ld_field_diffs(pkg_root, live_root, text: str) -> list:
    """패키지 객체의 말단 값마다 라이브 객체의 같은 pointer 값과 비교한 차이 목록."""
    out = []
    for pointer, key, value in ld_leaves(pkg_root):
        live_value = resolve_pointer(live_root, pointer) if live_root is not None else _MISSING
        present = live_value is not _MISSING
        types = container_types(pkg_root, pointer)
        if present and same_value(value, live_value, key, types):
            continue
        out.append({
            "path": pointer,
            "package": _clip(value),
            "live": _clip(live_value) if present else None,
            "live_present": present,
            "visible": value_visible(text, value, key, types),
            "live_visible": value_visible(text, live_value, key, types) if present else None,
        })
    return out


def ld_match(pkg_root, live_roots: list) -> tuple:
    """패키지 루트 객체에 대응하는 라이브 객체. (객체 또는 None, 핵심 필드 일치 여부)"""
    identity = ld_identity(pkg_root)
    for root in live_roots:
        if ld_identity(root) == identity:
            return root, True
    same_kind = [r for r in live_roots if ld_identity(r)[0] == identity[0]]
    if not same_kind:
        return None, False
    # 차이가 가장 적은 객체 — 동률이면 페이지에 먼저 나온 것 (결정적)
    best = min(same_kind, key=lambda r: len(ld_field_diffs(pkg_root, r, "")))
    return best, False


# ─────────────────────────────────────────────────────────── A. deploy 검증

def load_package(deploy_dir: str) -> dict:
    """배포 패키지에서 검증할 파일만 읽는다."""
    pkg = {"dir": deploy_dir, "robots": None, "llms": None, "sitemaps": [],
           "jsonld": OrderedDict(), "meta": None, "meta_present": False}
    if not os.path.isdir(deploy_dir):
        return pkg
    robots = os.path.join(deploy_dir, "robots.txt")
    if os.path.exists(robots):
        pkg["robots"] = read_text(robots)
    llms = os.path.join(deploy_dir, "llms.txt")
    if os.path.exists(llms):
        pkg["llms"] = read_text(llms)
    for name in sorted(os.listdir(deploy_dir)):
        if name.startswith("sitemap") and name.endswith(".xml"):
            pkg["sitemaps"].append(name)
    manifest_path = os.path.join(deploy_dir, generate.JSONLD_MANIFEST)
    pkg["jsonld_manifest"] = None
    if os.path.exists(manifest_path):
        try:
            pkg["jsonld_manifest"] = json.loads(read_text(manifest_path))
        except ValueError:
            pkg["jsonld_manifest"] = {}
    for path in sorted(glob.glob(os.path.join(deploy_dir, "jsonld", "*.json"))):
        if os.path.basename(path) == "manifest.json":
            continue
        try:
            pkg["jsonld"][os.path.basename(path)] = json.loads(read_text(path))
        except ValueError:
            pkg["jsonld"][os.path.basename(path)] = None
    meta = os.path.join(deploy_dir, "meta-draft.json")
    if os.path.exists(meta):
        pkg["meta_present"] = True
        try:
            pkg["meta"] = json.loads(read_text(meta))
        except ValueError:
            pkg["meta"] = None
    return pkg


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def check_robots(checks, session: Session, base: str, audit: dict, pkg: dict) -> str:
    live = session.get("%s/robots.txt" % base)
    raw = live["body"] if live["status"] == 200 else ""
    if live["status"] != 200 or raw.lstrip().startswith("<"):
        chk(checks, "robots.status", "fail",
            "robots.txt가 정상 응답하지 않는다 (HTTP %s)" % live["status"],
            {"status": live["status"], "error": live["error"]})
        raw = ""
    else:
        chk(checks, "robots.status", "pass", "robots.txt HTTP 200", {"bytes": len(raw)})

    before = ((audit.get("site") or {}).get("robots") or {}).get("raw") or ""
    old_lines = [l.strip() for l in before.split("\n") if l.strip()]
    if len(before) >= 8000 and old_lines:
        old_lines.pop()  # audit.json의 robots 원문은 8000자에서 잘린다 — 마지막 줄은 못 믿는다
    if not old_lines:
        chk(checks, "robots.preserved", "skip",
            "배포 전 robots.txt 원문이 없어 보존 여부를 비교할 수 없다")
    else:
        live_lines = {l.strip() for l in raw.split("\n")}
        lost = [l for l in old_lines if l not in live_lines]
        if lost:
            chk(checks, "robots.preserved", "fail",
                "기존 robots.txt에서 %d줄이 사라졌다" % len(lost),
                {"lost": lost[:20], "lost_count": len(lost)})
        else:
            chk(checks, "robots.preserved", "pass",
                "기존 robots.txt %d줄이 전부 살아 있다" % len(old_lines))

    if pkg["robots"] is None:
        chk(checks, "robots.policy", "skip", "패키지에 robots.txt가 없다")
    else:
        mismatch = []
        for ua in crawl.ALL_UAS:
            want = crawl.robots_policy(pkg["robots"], ua)
            got = crawl.robots_policy(raw, ua)
            if want != got:
                mismatch.append({"ua": ua, "package": want, "live": got})
        if mismatch:
            chk(checks, "robots.policy", "fail",
                "UA %d종의 실효 정책이 배포 패키지와 다르다 — 블록이 서빙되지 않는다"
                % len(mismatch), {"mismatch": mismatch})
        else:
            chk(checks, "robots.policy", "pass",
                "UA %d종의 실효 정책이 패키지와 일치한다" % len(crawl.ALL_UAS))

    declared = [m.strip() for m in
                re.findall(r"(?im)^\s*sitemap:\s*(\S+)", raw)]
    if declared:
        chk(checks, "robots.sitemap", "pass",
            "robots.txt에 Sitemap 선언 %d건" % len(declared), {"declared": declared})
    else:
        chk(checks, "robots.sitemap", "fail", "robots.txt에 `Sitemap:` 줄이 없다")
    return raw


def sitemap_locs(session: Session, base: str, raw_robots: str, pkg: dict,
                 host: str) -> tuple:
    """선언·패키지 기준 사이트맵을 열고 <loc>을 모은다. (결과, loc 목록)"""
    declared = [m.strip() for m in
                re.findall(r"(?im)^\s*sitemap:\s*(\S+)", raw_robots)]
    candidates = []
    for url in declared + ["%s/%s" % (base, n) for n in pkg["sitemaps"]]:
        if not url.startswith(("http://", "https://")) or crawl.host_of(url) != host:
            continue
        if url not in candidates:
            candidates.append(url)
    results, locs, seen = [], [], set()
    queue = list(candidates)
    max_sitemaps = 500
    while queue and len(results) < max_sitemaps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        rec = session.get(url)
        entry = {"url": url, "status": rec["status"], "parsed": False,
                 "is_index": False, "loc_count": 0, "fetch_error": rec["error"]}
        if rec["status"] == 200:
            try:
                root = ElementTree.fromstring(rec["body"].strip())
                entry["parsed"] = True
            except ElementTree.ParseError as exc:
                entry["error"] = "XML 파싱 실패: %s" % exc
                root = None
            if root is not None:
                entry["is_index"] = root.tag.endswith("sitemapindex")
                found = [el.text.strip() for el in root.iter()
                         if el.tag.endswith("}loc") or el.tag == "loc"
                         if (el.text or "").strip()]
                entry["loc_count"] = len(found)
                if entry["is_index"]:
                    queue.extend(u for u in found if crawl.host_of(u) == host)
                else:
                    locs.extend(found)
        results.append(entry)
    remaining = len(set(queue) - seen)
    return results, locs, {"discovered": len(seen) + remaining,
                           "checked": len(results), "remaining": remaining,
                           "limit": max_sitemaps}


def check_sitemap(checks, session: Session, base: str, raw_robots: str, pkg: dict,
                  host: str, max_urls: int) -> list:
    results, locs, coverage = sitemap_locs(session, base, raw_robots, pkg, host)
    if not results:
        chk(checks, "sitemap.reachable", "skip", "확인할 사이트맵 주소가 없다")
        return []
    broken = [r for r in results
              if r["status"] != 200 or not r["parsed"] or r.get("fetch_error")]
    if broken:
        chk(checks, "sitemap.reachable", "fail",
            "사이트맵 %d개가 200이 아니거나 XML로 파싱되지 않는다" % len(broken),
            {"sitemaps": results})
    elif coverage["remaining"]:
        chk(checks, "sitemap.reachable", "warn",
            "사이트맵 %d개를 확인했지만 %d개가 상한 밖에 남았다"
            % (coverage["checked"], coverage["remaining"]),
            {"sitemaps": results, "coverage": coverage})
    else:
        chk(checks, "sitemap.reachable", "pass",
            "사이트맵 %d개 200·XML 파싱 OK (URL %d개)"
            % (len(results), len(locs)), {"sitemaps": results, "coverage": coverage})

    off_host = [u for u in locs if crawl.host_of(u) != host]
    targets = [u for u in locs if crawl.host_of(u) == host]
    capped = len(targets) > max_urls
    targets = targets[:max_urls]
    if off_host:
        chk(checks, "sitemap.host", "warn",
            "사이트맵에 다른 호스트 URL이 %d개 있다 — 확인하지 않았다" % len(off_host),
            {"urls": off_host[:20]})

    recs = [session.get(u) for u in targets]
    dead = [{"url": r["url"], "status": r["status"], "error": r["error"]}
            for r in recs if r["status"] != 200]
    if not targets:
        chk(checks, "sitemap.locs", "skip", "확인할 <loc> URL이 없다")
    elif dead:
        chk(checks, "sitemap.locs", "fail",
            "사이트맵 URL %d개 중 %d개가 200이 아니다" % (len(targets), len(dead)),
            {"dead": dead[:20], "checked": len(targets), "capped": capped})
    elif capped:
        chk(checks, "sitemap.locs", "warn",
            "사이트맵 URL %d개는 200이지만 %d개를 상한 때문에 확인하지 못했다"
            % (len(targets), len(locs) - len(targets)),
            {"checked": len(targets), "capped": True,
             "unverified": len(locs) - len(targets)})
    else:
        chk(checks, "sitemap.locs", "pass",
            "사이트맵 URL %d개 전부 200" % len(targets),
            {"checked": len(targets), "capped": False, "unverified": 0})

    noindexed = [r["url"] for r in recs if r["status"] == 200 and is_noindex(r)]
    if noindexed:
        chk(checks, "sitemap.noindex", "fail",
            "noindex 페이지 %d개가 사이트맵에 실려 있다" % len(noindexed),
            {"urls": noindexed[:20]})
    elif targets:
        chk(checks, "sitemap.noindex", "warn" if capped else "pass",
            ("확인한 사이트맵 URL에는 noindex가 없지만 일부 URL은 미검사다" if capped
             else "사이트맵에 noindex 페이지가 없다"),
            {"checked": len(targets), "unverified": len(locs) - len(targets)})

    mismatch = []
    for rec in recs:
        if rec["status"] != 200:
            continue
        requested = crawl.normalize(rec["url"])
        final = crawl.normalize(rec["final_url"])
        canonical = (crawl.normalize(urllib.parse.urljoin(rec["final_url"], rec["canonical"]))
                     if rec["canonical"] else None)
        if requested != final or (canonical is not None and canonical != final):
            mismatch.append({"url": rec["url"], "final_url": final,
                             "canonical": canonical})
    if mismatch:
        chk(checks, "sitemap.canonical", "fail",
            "사이트맵 URL %d개가 자기 자신이 아닌 canonical을 가리킨다" % len(mismatch),
            {"mismatch": mismatch[:20]})
    elif targets:
        chk(checks, "sitemap.canonical", "warn" if capped else "pass",
            ("확인한 URL의 최종 주소·canonical은 일치하지만 일부 URL은 미검사다" if capped
             else "사이트맵 URL과 최종 주소·canonical이 일치한다"),
            {"checked": len(targets), "unverified": len(locs) - len(targets)})
    return recs


def check_llms(checks, session: Session, base: str, pkg: dict) -> None:
    if pkg["llms"] is None:
        chk(checks, "llms.status", "skip", "패키지에 llms.txt가 없다")
        return
    rec = session.get("%s/llms.txt" % base)
    if rec["status"] != 200:
        chk(checks, "llms.status", "fail",
            "llms.txt가 서빙되지 않는다 (HTTP %s)" % rec["status"],
            {"status": rec["status"]})
        return
    chk(checks, "llms.status", "pass", "llms.txt HTTP 200", {"bytes": len(rec["body"])})
    todos = [l.strip() for l in rec["body"].split("\n") if "<<TODO" in l]
    if todos:
        chk(checks, "llms.todo", "fail",
            "llms.txt에 `<<TODO` 표식이 %d줄 남아 있다 — 미완성 배포다" % len(todos),
            {"lines": todos[:20]})
    else:
        chk(checks, "llms.todo", "pass", "llms.txt에 남은 TODO 표식이 없다")
    # 형식 메모는 참고용이다 — llmstxt.org 제안 형식과 다른 곳을 알려 줄 뿐 순위·인용 요인이 아니어서
    # fail로 올리지 않는다(종료 코드에 영향 없음). 받은 본문을 읽지 못했으면 미측정으로 남긴다.
    record = crawl.llms_format_record(rec)
    if record is None:
        chk(checks, "llms.format", "skip",
            "llms.txt 본문을 온전히 받지 못해 형식을 확인하지 않았다 (%s)" % (rec.get("error") or "unknown"))
    elif any(n.get("level", "warn") == "warn" for n in record["notes"]):
        chk(checks, "llms.format", "warn",
            "llms.txt 형식 메모 %d건 — llmstxt.org 제안 형식 기준의 참고 사항이며 순위·인용 요인이 아니다: %s"
            % (len(record["notes"]), ", ".join(n["code"] for n in record["notes"])), record)
    elif record["notes"]:
        # 권장 항목(info)만 빠졌다 — 형식 차이는 없으므로 pass로 두고 메모만 남긴다.
        chk(checks, "llms.format", "pass",
            "llms.txt가 제안 형식과 맞는다. 권장 항목 참고 %d건: %s (순위·인용 요인이 아니다)"
            % (len(record["notes"]), ", ".join(n["code"] for n in record["notes"])), record)
    else:
        chk(checks, "llms.format", "pass",
            "llms.txt가 llmstxt.org 제안 형식과 맞는다 (참고 사항 — 순위·인용 요인이 아니다)", record)


def jsonld_targets(pkg: dict, audit: dict, base: str) -> list:
    """패키지의 jsonld/*.json ↔ 대상 페이지 URL 매핑. (파일명, @type, url, kind)"""
    home = base + "/"
    by_slug = {}
    for page in audit.get("pages") or []:
        if page.get("status") == 200:
            by_slug.setdefault(generate.slug_of(page["url"]), page["url"])
            path = urllib.parse.urlsplit(page["url"]).path.strip("/")
            legacy = (re.sub(r"[^A-Za-z0-9._-]+", "-", urllib.parse.unquote(path)).strip("-")
                      or "home")[:80]
            by_slug.setdefault(legacy, page["url"])
    manifest_obj = pkg.get("jsonld_manifest")
    manifest_present = manifest_obj is not None
    manifest_valid = (isinstance(manifest_obj, dict)
                      and manifest_obj.get("schema") in JSONLD_MANIFEST_SCHEMAS
                      and isinstance(manifest_obj.get("files"), dict))
    manifest = manifest_obj["files"] if manifest_valid else ({} if manifest_present else None)
    out = []
    for name, obj in pkg["jsonld"].items():
        stem = name[:-5]
        if isinstance(manifest, dict):
            url = manifest.get(name)
            if not isinstance(url, str) or crawl.host_of(url) != crawl.host_of(base):
                url = None
            kind = stem.rsplit(".", 1)[-1]
        elif stem in ("organization", "website"):
            kind, url = stem, home
        elif "." in stem:
            slug, kind = stem.rsplit(".", 1)
            url = by_slug.get(slug)
        else:
            kind, url = stem, None
        types = sorted({t for node in nodes(obj) for t in
                        ([node["@type"]] if isinstance(node.get("@type"), str)
                         else [x for x in (node.get("@type") or []) if isinstance(x, str)])})
        out.append({"file": "jsonld/%s" % name, "kind": kind, "url": url,
                    "obj": obj, "types": types})
    return out


def ld_identity(node: dict):
    """타입별 핵심 사실을 정규화한 비교 키."""
    types = node.get("@type")
    types = [types] if isinstance(types, str) else list(types or [])
    kind = next((t for t in types if isinstance(t, str)), "")
    n = norm_text

    def u(value):
        try:
            return crawl.normalize(value) if value else ""
        except (TypeError, ValueError):
            return n(value)

    def identifier(value):
        if not value:
            return ""
        parts = urllib.parse.urlsplit(str(value))
        base = u(urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                         parts.query, "")))
        return base + (("#" + parts.fragment) if parts.fragment else "")
    if kind == "FAQPage":
        pairs = []
        for q in node.get("mainEntity") or []:
            if not isinstance(q, dict):
                continue
            answer = q.get("acceptedAnswer") or {}
            pairs.append((n(q.get("name")), n(answer.get("text") if isinstance(answer, dict) else "")))
        return kind, tuple(sorted(pairs))
    if kind == "Product":
        offer = node.get("offers") or {}
        if isinstance(offer, list):
            offers = tuple(sorted((n(x.get("price")), n(x.get("priceCurrency")), u(x.get("url")))
                                  for x in offer if isinstance(x, dict)))
        elif isinstance(offer, dict):
            offers = ((n(offer.get("price")), n(offer.get("priceCurrency")), u(offer.get("url"))),)
        else:
            offers = ()
        return kind, n(node.get("name")), u(node.get("url")), offers
    if kind == "Organization":
        return kind, identifier(node.get("@id")), n(node.get("name")), u(node.get("url"))
    if kind == "WebSite":
        return kind, n(node.get("name")), u(node.get("url"))
    if kind == "BreadcrumbList":
        items = tuple((n(x.get("position")), n(x.get("name")), u(x.get("item")))
                      for x in node.get("itemListElement") or [] if isinstance(x, dict))
        return kind, items
    return kind, json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ld_roots(obj) -> list:
    if isinstance(obj, list):
        return [x for item in obj for x in ld_roots(item)]
    if not isinstance(obj, dict):
        return []
    graph = obj.get("@graph")
    return [x for x in graph if isinstance(x, dict)] if isinstance(graph, list) else [obj]


def _visible_claims(obj, text: str):
    """가시 텍스트와 대조하는 LD 값 (종류, pointer, 원래 값, 가시 여부). pointer는 obj 기준."""
    for path, node in typed_nodes(obj):
        types = node_types(node)
        if "Question" in types:
            yield "question", path + "/name", node.get("name"), visible(text, node.get("name"))
            answer = node.get("acceptedAnswer") or {}
            if isinstance(answer, dict):
                yield ("answer", path + "/acceptedAnswer/text", answer.get("text"),
                       visible(text, answer.get("text")))
        elif "Organization" in types and node.get("name"):
            yield "organization name", path + "/name", node["name"], visible(text, node["name"])
        elif "Product" in types:
            if node.get("name"):
                yield "product name", path + "/name", node["name"], visible(text, node["name"])
            offers = node.get("offers") or {}
            if isinstance(offers, dict) and offers.get("price") is not None:
                yield ("price", path + "/offers/price", offers["price"],
                       price_visible(text, offers["price"]))


def ld_visible_fields(obj, text: str, live=None) -> list:
    """필드마다 pointer·패키지 값·라이브 값·가시 여부. live가 있으면 같은 pointer 값을 싣는다."""
    out = []
    for kind, pointer, value, ok in _visible_claims(obj, text):
        live_value = resolve_pointer(live, pointer) if live is not None else _MISSING
        out.append({"field": kind, "path": pointer, "package": _clip(value),
                    "live": _clip(live_value) if live_value is not _MISSING else None,
                    "visible": ok})
    return out


def ld_visible_misses(obj, text: str) -> list:
    """LD가 화면에 없는 말을 하는지 — 빠진 (종류, 문자열) 목록."""
    return [(kind, norm_text(value)[:60])
            for kind, _pointer, value, ok in _visible_claims(obj, text) if not ok]


def check_jsonld(checks, session: Session, base: str, audit: dict, pkg: dict) -> None:
    targets = jsonld_targets(pkg, audit, base)
    if not targets:
        chk(checks, "jsonld.present", "skip", "패키지에 JSON-LD 파일이 없다")
        return

    missing, wrong_type, invisible, unmapped, drift = [], [], [], [], []
    for target in targets:
        if target["obj"] is None:
            wrong_type.append({"file": target["file"], "reason": "패키지 파일이 JSON으로 파싱되지 않는다"})
            continue
        if not target["url"]:
            unmapped.append(target["file"])
            continue
        rec = session.get(target["url"])
        if rec["status"] != 200 or not rec["html"]:
            missing.append({"file": target["file"], "url": target["url"],
                            "reason": "페이지 HTTP %s" % rec["status"]})
            continue
        if not rec["ld_objs"]:
            missing.append({"file": target["file"], "url": target["url"],
                            "reason": "페이지에 ld+json 블록이 없다"})
            continue
        if rec["ld_broken"]:
            wrong_type.append({"file": target["file"], "url": target["url"],
                               "reason": "ld+json 블록 %d개가 JSON으로 파싱되지 않는다" % rec["ld_broken"]})
        live_roots = [x for obj in rec["ld_objs"] for x in ld_roots(obj)]
        matches = [(root, ld_match(root, live_roots)) for root in ld_roots(target["obj"])]
        # 핵심 필드가 어긋난 객체의 필드 단위 차이 (pointer는 그 객체 기준)
        fields = [dict(node=ld_identity(root)[0], **diff)
                  for root, (live_root, exact) in matches if not exact
                  for diff in ld_field_diffs(root, live_root, rec["text"])]
        for wanted in target["types"]:
            if wanted not in rec["ld_types"]:
                wrong_type.append({"file": target["file"], "url": target["url"],
                                   "reason": "@type %s가 페이지 LD에 없다" % wanted,
                                   "fields": fields[:20]})
                break
        else:
            wanted_ids = [ld_identity(x) for x in ld_roots(target["obj"])]
            live_ids = {ld_identity(x) for x in live_roots}
            absent = [identity for identity in wanted_ids if identity not in live_ids]
            if absent:
                wrong_type.append({"file": target["file"], "url": target["url"],
                                   "reason": "같은 @type은 있지만 패키지의 핵심 필드가 일치하지 않는다",
                                   "fields": fields[:20]})
                continue
            for root, (live_root, _exact) in matches:
                for diff in ld_field_diffs(root, live_root, rec["text"]):
                    drift.append(dict(file=target["file"], url=target["url"],
                                      node=ld_identity(root)[0], **diff))
            misses = ld_visible_misses(target["obj"], rec["text"])
            if misses:
                fields = [dict(node=ld_identity(root)[0], **field)
                          for root, (live_root, _exact) in matches
                          for field in ld_visible_fields(root, rec["text"], live=live_root)
                          if not field["visible"]]
                invisible.append({"file": target["file"], "url": target["url"],
                                  "missing": ["%s: %s" % (k, v) for k, v in misses][:10],
                                  "fields": fields[:20]})

    if missing:
        chk(checks, "jsonld.present", "fail",
            "LD가 %d개 페이지에서 확인되지 않는다" % len(missing), {"pages": missing[:20]})
    elif unmapped:
        chk(checks, "jsonld.present", "fail",
            "manifest에서 대상 페이지를 확인할 수 없는 LD가 %d건이다" % len(unmapped),
            {"files": unmapped[:20]})
    else:
        chk(checks, "jsonld.present", "pass",
            "패키지 LD %d건의 대상 페이지에서 ld+json 블록을 확인했다" % len(targets))
    if unmapped:
        chk(checks, "jsonld.mapping", "fail" if pkg.get("jsonld_manifest") is not None else "warn",
            "대상 URL을 찾지 못한 LD 파일이 %d개다 (audit.json의 크롤 목록에 없다)" % len(unmapped),
            {"files": unmapped[:20]})
    elif pkg.get("jsonld_manifest") is not None:
        chk(checks, "jsonld.mapping", "pass", "JSON-LD manifest의 파일→URL 매핑이 완전하다")
    # 핵심 필드는 일치하지만 그 밖의 필드가 패키지와 다른 경우 — 판정은 바꾸지 않고 증적에만 싣는다
    drift_evidence = {"field_drift": drift[:20]} if drift else {}
    if wrong_type:
        chk(checks, "jsonld.type", "fail",
            "@type이 어긋나거나 깨진 LD가 %d건이다" % len(wrong_type),
            dict({"items": wrong_type[:20]}, **drift_evidence))
    elif not missing:
        chk(checks, "jsonld.type", "pass", "패키지와 라이브 페이지의 @type이 일치한다",
            drift_evidence)
    if invisible:
        chk(checks, "jsonld.visible", "fail",
            "LD가 화면에 없는 말을 한다 — %d개 페이지 (스팸 리스크)" % len(invisible),
            {"pages": invisible[:20]})
    elif wrong_type or missing or unmapped:
        chk(checks, "jsonld.visible", "skip",
            "패키지 LD와 라이브 객체가 일치하지 않아 가시 내용 검증을 완료할 수 없다")
    else:
        chk(checks, "jsonld.visible", "pass",
            "LD의 문답·이름·가격이 페이지 가시 텍스트에 글자 그대로 있다")

    org_ids = OrderedDict()
    for rec in session.cache.values():
        for obj in rec.get("ld_objs") or []:
            for node in nodes(obj):
                t = node.get("@type")
                types = [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]
                if "Organization" in types and node.get("@id"):
                    org_ids.setdefault(node["@id"], []).append(rec["url"])
    if len(org_ids) > 1:
        chk(checks, "jsonld.org_id", "fail",
            "Organization @id가 %d종으로 갈렸다 — 엔티티가 쪼개진다" % len(org_ids),
            {"ids": {k: v[:5] for k, v in org_ids.items()}})
    elif org_ids:
        chk(checks, "jsonld.org_id", "pass",
            "Organization @id가 전 페이지에서 하나다 (%s)" % list(org_ids)[0])


RATING_TYPES = frozenset(("Rating", "AggregateRating", "EmployerAggregateRating"))
_NONNEG_INT = re.compile(r"^\d+$")
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_RATING_PERCENT = re.compile(r"^\s*(%s)\s*%%\s*$" % _NUM)
_RATING_FRACTION = re.compile(r"^\s*(%s)\s*/\s*(%s)\s*$" % (_NUM, _NUM))


def parse_rating(value):
    """ratingValue → (값, 함축 worst, 함축 best, 형식). 해석할 수 없으면 None.

    Google 리뷰 스니펫 문서는 수(4), 백분율("60%"), 분수("6 / 10")를 받는다. 백분율은 0~100,
    분수는 분모가 최고점이라는 척도를 함축한다. 수는 척도를 함축하지 않는다(기본 1~5).
    """
    number = as_decimal(value)
    if number is not None:
        return number, None, None, "number"
    if not isinstance(value, str):
        return None
    from decimal import Decimal
    m = _RATING_PERCENT.match(value)
    if m:
        return Decimal(m.group(1)), Decimal(0), Decimal(100), "percent"
    m = _RATING_FRACTION.match(value)
    if m:
        return Decimal(m.group(1)), Decimal(0), Decimal(m.group(2)), "fraction"
    return None


def rating_issues(obj) -> tuple:
    """평점 노드 무결성. (평점 노드 수, 척도 문제, 개수 문제) — pointer는 obj 기준."""
    total, scale, counts = 0, [], []
    for path, node in typed_nodes(obj):
        types = node_types(node)
        if not RATING_TYPES.intersection(types):
            continue
        total += 1
        kind = next(t for t in types if t in RATING_TYPES)
        value = node.get("ratingValue", _MISSING)
        worst_raw = node.get("worstRating", _MISSING)
        best_raw = node.get("bestRating", _MISSING)
        parsed = parse_rating(value) if value is not _MISSING else None
        rating, implied_worst, implied_best, form = parsed or (None, None, None, None)
        worst = ((1 if implied_worst is None else implied_worst) if worst_raw is _MISSING
                 else as_decimal(worst_raw))
        best = ((5 if implied_best is None else implied_best) if best_raw is _MISSING
                else as_decimal(best_raw))
        item = {"path": path or "/", "node": kind,
                "ratingValue": None if value is _MISSING else value,
                "worstRating": None if worst_raw is _MISSING else worst_raw,
                "bestRating": None if best_raw is _MISSING else best_raw}
        reason = None
        if value is _MISSING:
            reason = "ratingValue가 없다"
        elif rating is None:
            reason = "ratingValue %r는 숫자가 아니다" % (value,)
        elif worst is None:
            reason = "worstRating %r는 숫자가 아니다" % (worst_raw,)
        elif best is None:
            reason = "bestRating %r는 숫자가 아니다" % (best_raw,)
        elif form == "fraction" and best_raw is not _MISSING and best != implied_best:
            reason = ("ratingValue %r의 분모(%s)가 bestRating(%s)과 다르다"
                      % (value, implied_best, best_raw))
        elif worst >= best:
            reason = "worstRating(%s)이 bestRating(%s)보다 작지 않다" % (worst, best)
        elif not worst <= rating <= best:
            reason = "ratingValue %s가 척도 [%s, %s] 밖이다" % (value, worst, best)
        if reason:
            scale.append(dict(item, reason=reason))
        for key in ("ratingCount", "reviewCount"):
            if key not in node:
                continue
            count = node[key]
            ok = ((isinstance(count, int) and not isinstance(count, bool) and count >= 0)
                  or (isinstance(count, float) and count.is_integer() and count >= 0)
                  or (isinstance(count, str) and bool(_NONNEG_INT.match(count.strip()))))
            if not ok:
                counts.append({"path": "%s/%s" % (path, key), "node": kind, "field": key,
                               "value": count,
                               "reason": "%s %r는 0 이상의 정수가 아니다" % (key, count)})
    return total, scale, counts


def check_ratings(checks, session: Session, pkg: dict, target_urls=None) -> None:
    """패키지와 라이브에서 받은 모든 LD의 평점 노드가 선언한 척도·개수 규칙을 지키는가.

    판정 범위를 나눈다. 패키지 파일과 패키지가 배포되는 대상 페이지의 결함은 이번 배포의
    결함이라 fail이다. 그 밖에 세션이 받은 기존 페이지(사이트맵·noindex 확인용)의 결함은
    배포와 무관하게 원래 있던 마크업이므로 warn으로 알리고 배포 실패로 세지 않는다.
    target_urls가 None이면(옛 호출) 모두 배포 범위로 본다.
    """
    targets = (None if target_urls is None
               else {crawl.normalize(u) for u in target_urls if u})

    def in_scope(where) -> bool:
        if where.get("source") == "package" or targets is None:
            return True
        return crawl.normalize(where.get("url") or "") in targets

    scanned = []
    for name, obj in pkg["jsonld"].items():
        if obj is not None:
            scanned.append(({"source": "package", "file": "jsonld/%s" % name}, obj))
    for rec in session.cache.values():
        if rec.get("status") == 200 and rec.get("html") and not rec.get("off_host"):
            for i, obj in enumerate(rec.get("ld_objs") or []):
                scanned.append(({"source": "live", "url": rec["url"], "block": i}, obj))
    total, scale, counts, sources = 0, [], [], 0
    for where, obj in scanned:
        n, bad_scale, bad_count = rating_issues(obj)
        sources += 1 if n else 0
        total += n
        scale.extend(dict(where, **item) for item in bad_scale)
        counts.extend(dict(where, **item) for item in bad_count)
    if not total:
        return
    count_fields = sum(1 for _w, obj in scanned for _p, node in typed_nodes(obj)
                       if RATING_TYPES.intersection(node_types(node))
                       for key in ("ratingCount", "reviewCount") if key in node)
    evidence = {"rating_nodes": total, "sources": sources}
    for item in scale + counts:
        item["scope"] = "deploy" if in_scope(item) else "existing"

    def verdict(items):
        return "fail" if any(i["scope"] == "deploy" for i in items) else "warn"

    def scope_note(items):
        existing = sum(1 for i in items if i["scope"] == "existing")
        return (" (배포 대상 밖 기존 페이지 %d건 포함 — 원래 있던 마크업)" % existing
                if existing else "")

    if scale:
        chk(checks, "jsonld.rating_scale", verdict(scale),
            "평점 %d건이 숫자가 아니거나 선언한 척도 밖이다 — 조작된 평점으로 읽힌다%s"
            % (len(scale), scope_note(scale)),
            dict(evidence, items=scale[:20]))
    else:
        chk(checks, "jsonld.rating_scale", "pass",
            "평점 노드 %d개가 모두 선언한 척도(기본 1~5) 안의 숫자다" % total, evidence)
    if counts:
        chk(checks, "jsonld.rating_count", verdict(counts),
            "ratingCount/reviewCount %d건이 0 이상의 정수가 아니다%s"
            % (len(counts), scope_note(counts)),
            dict(evidence, items=counts[:20]))
    elif count_fields:
        chk(checks, "jsonld.rating_count", "pass",
            "ratingCount/reviewCount %d개가 모두 0 이상의 정수다" % count_fields, evidence)


# ─────────────────────────────────────────────────────────── 사실 원장 (선택)

FACTS_SCHEMA = "su-presence/facts/1"


class FactsError(ValueError):
    """사실 원장 파일이 계약을 어겼다 — 조용히 건너뛰지 않고 실행을 멈춘다."""


def validate_facts(data) -> list:
    """원장 항목을 검증해 정규화한 목록을 돌려준다. 하나라도 어긋나면 FactsError."""
    if isinstance(data, dict):
        if data.get("schema") not in (None, FACTS_SCHEMA):
            raise FactsError("facts 스키마가 아니다: %r (기대: %s)"
                             % (data.get("schema"), FACTS_SCHEMA))
        data = data.get("facts")
    if not isinstance(data, list):
        raise FactsError('facts 파일은 항목 배열이거나 {"facts": [...]} 형식이어야 한다')
    if not data:
        raise FactsError("facts 항목이 하나도 없다")
    errors, out = [], []
    for i, entry in enumerate(data):
        where = "facts[%d]" % i
        if not isinstance(entry, dict):
            errors.append("%s: 객체가 아니다" % where)
            continue
        problems = []
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
            problems.append("url이 http(s) 주소가 아니다")
        pointer = entry.get("pointer")
        if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
            problems.append("pointer는 '/'로 시작하는 JSON pointer여야 한다 (예: /offers/price)")
        if "expected" not in entry:
            problems.append("expected 값이 없다")
        else:
            expected = entry["expected"]
            if expected is None or isinstance(expected, (dict, list)):
                problems.append("expected는 문자열·수·참거짓 값이어야 한다")
            elif isinstance(expected, str) and not expected.strip():
                problems.append("expected가 빈 문자열이다")
            elif isinstance(expected, float) and as_decimal(expected) is None:
                problems.append("expected가 유한한 수가 아니다")
        evidence = entry.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            problems.append("evidence(이 사실의 출처 위치)가 비어 있다")
        node_type = entry.get("type")
        if node_type is not None and (not isinstance(node_type, str) or not node_type.strip()):
            problems.append("type은 비어 있지 않은 문자열이어야 한다")
        if problems:
            errors.append("%s: %s" % (where, "; ".join(problems)))
            continue
        fact = OrderedDict([("url", url.strip()), ("pointer", pointer),
                            ("expected", entry["expected"]), ("evidence", evidence.strip())])
        if node_type:
            fact["type"] = node_type.strip()
        out.append(fact)
    if errors:
        raise FactsError("facts 파일을 받을 수 없다 — %d건:\n  %s"
                         % (len(errors), "\n  ".join(errors)))
    return out


def load_facts(path: str) -> list:
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise FactsError("facts 파일이 JSON이 아니다: %s" % exc) from exc
    return validate_facts(data)


def _same_page(a: str, b: str) -> bool:
    try:
        return crawl.normalize(a) == crawl.normalize(b)
    except (TypeError, ValueError):
        return a == b


def _fact_values(objs, pointer: str, node_type) -> list:
    """LD 루트 객체마다 pointer를 풀어 찾은 값 목록 (type이 있으면 그 @type 객체만)."""
    found = []
    for obj in objs:
        for root in ld_roots(obj):
            if node_type and node_type not in node_types(root):
                continue
            value = resolve_pointer(root, pointer)
            if value is not _MISSING:
                # _types는 비교용 내부 값이다 — 기록 전에 뺀다
                found.append({"node": ld_identity(root)[0], "value": value,
                              "_types": container_types(root, pointer)})
    return found


def _pointer_key(pointer: str) -> str:
    tokens = [t for t in pointer_tokens(pointer) if not t.isdigit()]
    return tokens[-1] if tokens else ""


def check_facts(checks, session: Session, base: str, audit: dict, pkg: dict,
                facts: list, source=None) -> dict:
    """사실 원장 항목마다 라이브 LD 값과 대조한다. 받지 못한 페이지는 확인으로 치지 않는다."""
    targets = [t for t in jsonld_targets(pkg, audit, base) if t["obj"] is not None and t["url"]]
    results = []
    for fact in facts:
        key = _pointer_key(fact["pointer"])
        node_type = fact.get("type")
        package = _fact_values([t["obj"] for t in targets if _same_page(t["url"], fact["url"])],
                               fact["pointer"], node_type)
        item = OrderedDict(fact)
        item["package_claimed"] = bool(package)
        if package:
            item["package_values"] = [{"node": v["node"], "value": _clip(v["value"])}
                                      for v in package]
        rec = session.get(fact["url"])
        if rec["off_host"] or rec["status"] != 200 or not rec["html"]:
            item.update(status="unverified", outcome="unverified", live_values=[], visible=None,
                        reason="페이지를 받지 못했다 (HTTP %s%s)"
                        % (rec["status"], ", %s" % rec["error"] if rec["error"] else ""))
            results.append(item)
            continue
        live_values = _fact_values(rec["ld_objs"], fact["pointer"], node_type)
        item["live_values"] = live_values
        types = next((v["_types"] for v in live_values + package), [])
        item["visible"] = value_visible(rec["text"], fact["expected"], key, types)
        if not live_values:
            item["status"] = "missing"
            if package:
                item["outcome"] = "fail"
                item["reason"] = "패키지가 선언한 값인데 라이브 LD에 이 pointer가 없다"
            else:
                item["outcome"] = "unverified"
                item["reason"] = ("라이브 LD에 이 pointer가 없다" if rec["ld_objs"]
                                  else "페이지에 ld+json 블록이 없다")
        elif all(not isinstance(v["value"], (dict, list))
                 and same_value(fact["expected"], v["value"], key, v["_types"])
                 for v in live_values):
            item["status"], item["outcome"] = "agree", "pass"
        else:
            item["status"], item["outcome"] = "mismatch", "fail"
            item["reason"] = "라이브 LD 값이 기대값과 다르다"
        for found in live_values:  # 비교는 원래 값으로, 기록은 잘라서
            found["value"] = _clip(found["value"])
            found.pop("_types", None)
        results.append(item)

    summary = OrderedDict((k, sum(1 for r in results if r["status"] == k))
                          for k in ("agree", "mismatch", "missing", "unverified"))
    claimed_missing = sum(1 for r in results
                          if r["status"] == "missing" and r["outcome"] == "fail")
    unresolved = sum(1 for r in results if r["outcome"] == "unverified")
    evidence = {"source": source, "summary": dict(summary), "unverified": unresolved,
                "items": [r for r in results if r["status"] != "agree"][:20]}
    if summary["mismatch"] or claimed_missing:
        chk(checks, "facts.match", "fail",
            "사실 원장 %d건 중 불일치 %d건 · 패키지가 선언했는데 라이브에 없는 값 %d건"
            % (len(results), summary["mismatch"], claimed_missing), evidence)
    elif unresolved:
        chk(checks, "facts.match", "warn",
            "사실 원장 %d건 중 %d건을 확인하지 못했다 (페이지 미수신 %d · LD 노드 없음 %d)"
            % (len(results), unresolved, summary["unverified"], summary["missing"]), evidence)
    else:
        chk(checks, "facts.match", "pass",
            "사실 원장 %d건이 모두 라이브 LD 값과 일치한다" % len(results), evidence)

    hidden = [r for r in results if r["status"] == "agree" and r["visible"] is False]
    judged = [r for r in results if r["status"] == "agree" and r["visible"] is not None]
    if hidden:
        chk(checks, "facts.visible", "warn",
            "LD와 일치하지만 화면 텍스트에서 기대값을 찾지 못한 사실이 %d건이다" % len(hidden),
            {"items": [OrderedDict((k, r[k]) for k in ("url", "pointer", "expected", "evidence"))
                       for r in hidden[:20]]})
    elif judged:
        chk(checks, "facts.visible", "pass",
            "LD와 일치한 사실 %d건의 기대값이 화면 텍스트에도 있다" % len(judged))
    return OrderedDict([("schema", FACTS_SCHEMA), ("source", source),
                        ("entries", len(results)), ("summary", summary),
                        ("results", results)])


def check_meta(checks, session: Session, audit: dict, pkg: dict, max_urls: int) -> None:
    rows = pkg["meta"]
    if pkg.get("meta_present") and rows is None:
        chk(checks, "meta.applied", "fail", "meta-draft.json을 JSON으로 파싱할 수 없다")
        return
    if not rows:
        chk(checks, "meta.applied", "skip", "패키지에 meta-draft.json이 없다")
        return
    changed = unchanged = applied = desc_changed = 0
    misses, failures, unchanged_urls = [], [], []
    fields_expected = fields_matched = 0
    requested_rows = rows[:max_urls]
    live_titles = []
    for row in requested_rows:
        rec = session.get(row.get("url") or "")
        if rec["status"] != 200 or not rec["html"]:
            failures.append({"url": row.get("url"), "status": rec["status"],
                             "error": rec["error"]})
            continue
        title = norm_text(rec["title"])
        live_titles.append(title)
        if title == norm_text(row.get("current_title")):
            unchanged += 1
            unchanged_urls.append(row.get("url"))
        else:
            changed += 1
        draft_title = norm_text(row.get("draft_title"))
        if draft_title and not draft_title.startswith("<<todo"):
            fields_expected += 1
        if draft_title and title == draft_title:
            applied += 1
            fields_matched += 1
        elif draft_title and not draft_title.startswith("<<todo"):
            misses.append({"url": row.get("url"), "field": "title",
                           "expected": draft_title, "actual": title})
        live_desc = norm_text(rec["meta_description"])
        draft_desc = norm_text(row.get("draft_description"))
        if draft_desc and not draft_desc.startswith("<<todo"):
            fields_expected += 1
            if live_desc == draft_desc:
                fields_matched += 1
            else:
                misses.append({"url": row.get("url"), "field": "description",
                               "expected": draft_desc, "actual": live_desc})
        if live_desc != norm_text(row.get("current_description")):
            desc_changed += 1
    evidence = {"rows": len(rows), "changed": changed, "unchanged": unchanged,
                "draft_applied": applied, "description_changed": desc_changed,
                "expected_fields": fields_expected, "matched_fields": fields_matched,
                "mismatches": misses[:20], "fetch_failures": failures[:20],
                "unchanged_urls": unchanged_urls[:20],
                "checked_rows": len(requested_rows) - len(failures),
                "unverified_rows": len(rows) - len(requested_rows) + len(failures)}
    if fields_expected and fields_matched == fields_expected and not failures \
            and len(requested_rows) == len(rows):
        chk(checks, "meta.applied", "pass",
            "검토 가능한 meta 초안 %d개 필드가 라이브 값과 모두 일치한다" % fields_expected,
            evidence)
    elif misses:
        chk(checks, "meta.applied", "fail",
            "meta 초안과 다른 라이브 title/description이 %d개 필드다" % len(misses),
            evidence)
    else:
        chk(checks, "meta.applied", "warn",
            "meta 초안 %d/%d 필드만 일치하거나 %d행을 확인하지 못했다"
            % (fields_matched, fields_expected, evidence["unverified_rows"]),
            evidence)

    dups = {t: n for t, n in Counter(t for t in live_titles if t).items() if n > 1}
    if dups:
        chk(checks, "meta.duplicate", "fail",
            "같은 title을 쓰는 페이지가 아직 %d개다 (%d그룹)"
            % (sum(dups.values()), len(dups)), {"titles": list(dups)[:20]})
    elif live_titles:
        chk(checks, "meta.duplicate", "pass", "중복 title이 남아 있지 않다")


def check_noindex(session: Session, audit: dict, max_urls: int) -> dict:
    """배포로 noindex가 새로 생기지 않았는가 — 실패면 최우선."""
    pages = audit.get("pages") or []
    before = {crawl.normalize(p["url"]) for p in pages if crawl._noindex(p)}
    expected = [p["url"] for p in pages
                if p.get("status") == 200 and not crawl._noindex(p)]
    targets = expected[:max_urls]
    recs = [session.get(url) for url in targets]
    now = [rec for rec in recs if rec.get("html") and rec.get("status") == 200
           and is_noindex(rec)]
    new = [rec["url"] for rec in now
           if crawl.normalize(rec["url"]) not in before]
    checked = sum(1 for r in recs if r.get("html") and r.get("status") == 200)
    unavailable = [{"url": r["url"], "status": r["status"], "error": r["error"]}
                   for r in recs if r.get("status") != 200 or not r.get("html")]
    unverified = len(expected) - len(targets) + len(unavailable)
    evidence = {"new": new[:20], "pages_checked": checked,
                "was_noindex": len(before), "expected": len(expected),
                "unverified": unverified, "unavailable": unavailable[:20]}
    if new:
        return {"id": "noindex", "status": "fail",
                "message": "배포 후 새로 noindex가 걸린 페이지가 %d개다 — 다른 모든 검증보다 우선한다"
                           % len(new),
                "evidence": evidence}
    if checked == 0:
        return {"id": "noindex", "status": "fail",
                "message": "배포 전 indexable 페이지를 한 건도 재확인하지 못했다",
                "evidence": evidence}
    if unverified:
        return {"id": "noindex", "status": "warn",
                "message": "새 noindex는 없지만 %d개 indexable 페이지를 확인하지 못했다" % unverified,
                "evidence": evidence}
    return {"id": "noindex", "status": "pass",
            "message": "새로 생긴 noindex 없음 (%d페이지 확인)" % checked,
            "evidence": evidence}


def verify_deploy(audit: dict, deploy_dir: str, fetch=None, delay: float = 0.5,
                  max_urls: int = 500, facts=None, facts_source=None,
                  direct: bool = False) -> dict:
    """facts: validate_facts()를 통과한 사실 원장 (선택). 주면 verify.json에 `facts` 키가 생긴다.

    모든 요청은 진단 대상 하나의 주소 범위(crawl.target_scope) 안에서 나간다 — 검증 도중 대상
    이름이 사설·루프백 주소로 바뀌어 풀려도(DNS 재바인딩) 접속하지 않고 nonpublic_address로 남긴다.
    direct=True(--direct)면 프록시 환경 변수를 무시하고 직접 연결한다. 프록시를 거쳤으면
    target.network에 그 사실(주소 검사가 로컬 DNS뿐)을 남긴다 — 프록시 주소는 적지 않는다.
    """
    base = ((audit.get("target") or {}).get("base") or "").rstrip("/")
    host = (audit.get("target") or {}).get("host") or crawl.host_of(base)
    with crawl.direct_connection(direct), crawl.target_scope(base or "https://%s" % host):
        return _verify_deploy(audit, deploy_dir, fetch, delay, max_urls, facts, facts_source,
                              base, host)


def _verify_deploy(audit, deploy_dir, fetch, delay, max_urls, facts, facts_source, base, host):
    pkg = load_package(deploy_dir)
    session = Session(host, fetch=fetch, delay=delay)
    checks: list = []

    raw_robots = check_robots(checks, session, base, audit, pkg)
    check_sitemap(checks, session, base, raw_robots, pkg, host, max_urls)
    check_llms(checks, session, base, pkg)
    check_jsonld(checks, session, base, audit, pkg)
    ledger = (check_facts(checks, session, base, audit, pkg, facts, facts_source)
              if facts is not None else None)
    check_meta(checks, session, audit, pkg, max_urls)
    noindex = check_noindex(session, audit, max_urls)
    check_ratings(checks, session, pkg,  # 받은 페이지 전부를 본다 — 모든 요청이 끝난 뒤
                  [t["url"] for t in jsonld_targets(pkg, audit, base) if t["url"]])
    checks.insert(0, noindex)  # 최우선 — 목록 맨 앞에 둔다

    incomplete = verification_incomplete(checks)
    failed = any(c["status"] == "fail" for c in checks)
    result = {
        "schema": SCHEMA,
        "mode": "deploy",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"base": base, "host": host, "deploy": deploy_dir,
                   "audit_generated_at": audit.get("generated_at"),
                   "requests": session.fetches},
        "checks": checks,
        "summary": summarize(checks),
        "completion": {"complete": not incomplete, "reasons": incomplete},
        "exit_code": 1 if failed else (2 if incomplete else 0),
    }
    if ledger is not None:
        result["facts"] = ledger
    network = crawl.network_record()
    if network is not None:
        # 선택 필드 — 프록시를 거쳤거나 --direct였을 때만. audit.json의 PROXY_ADDRESS_UNVERIFIED와 같은 메모다.
        if network.get("proxy"):
            network = dict(network, code="PROXY_ADDRESS_UNVERIFIED", note=crawl.PROXY_NOTE_KO)
        result["target"]["network"] = network
    return result


def verification_incomplete(checks: list) -> list:
    """실패와 별개로, 검사하지 못한 범위가 남았는지 구조적으로 표시한다."""
    reasons = []
    for check in checks:
        evidence = check.get("evidence") or {}
        cid = check.get("id")
        remaining = ((evidence.get("coverage") or {}).get("remaining")
                     if isinstance(evidence.get("coverage"), dict) else 0)
        unverified = evidence.get("unverified") or evidence.get("unverified_rows") or 0
        if remaining:
            reasons.append({"check": cid, "kind": "remaining_sitemaps", "count": remaining})
        if unverified:
            reasons.append({"check": cid, "kind": "unverified", "count": unverified})
    unique = OrderedDict()
    for reason in reasons:
        unique[(reason["check"], reason["kind"])] = reason
    return list(unique.values())


# ─────────────────────────────────────────────────────────── B. diff 검증

def findings_by_code(report: dict) -> dict:
    out: dict = OrderedDict()
    for finding in report.get("findings") or []:
        code = finding.get("code")
        current = out.get(code)
        if current is None:
            out[code] = {"code": code, "lane": finding.get("lane"),
                         "severity": finding.get("severity"),
                         "message": finding.get("message"),
                         "urls": len(finding.get("urls") or [])}
        else:
            current["urls"] += len(finding.get("urls") or [])
    return out


SCORE_RANK = {"ok": 0, "warn": 1, "bad": 2, "na": -1}
ASSESSED = ("ok", "warn", "bad")


def verify_diff(before: dict, after: dict) -> dict:
    checks: list = []
    b, a = findings_by_code(before), findings_by_code(after)

    # after 진단이 robots.txt를 읽지 못했으면 robots 원문에서만 나오는 findings는 "사라짐"이
    # 아니라 "확인 못 함"이다 — 해소로 세지 않는다.
    after_robots = (after.get("site") or {}).get("robots") or {}
    robots_blind = crawl.robots_unknown(after.get("site") or {})
    gone = [b[c] for c in b if c not in a]
    unverified = [f for f in gone if robots_blind and f["code"] in crawl.ROBOTS_DERIVED_CODES]
    resolved = [f for f in gone if f not in unverified]
    new = [a[c] for c in a if c not in b]
    persisting = [{"code": c, "lane": a[c]["lane"], "severity": a[c]["severity"],
                   "urls_before": b[c]["urls"], "urls_after": a[c]["urls"]}
                  for c in a if c in b]

    chk(checks, "diff.resolved", "pass",
        "해소된 findings %d건" % len(resolved),
        {"items": [{"code": f["code"], "lane": f["lane"], "severity": f["severity"]}
                   for f in resolved]})
    if unverified:
        chk(checks, "diff.unverified", "warn",
            "사라졌지만 확인하지 못한 findings %d건 — after 진단이 robots.txt를 읽지 못했다 "
            "(HTTP %s). 해소로 세지 않는다" % (len(unverified), after_robots.get("status")),
            {"reason": "robots_unreachable_after", "robots_status": after_robots.get("status"),
             "items": [{"code": f["code"], "lane": f["lane"], "severity": f["severity"]}
                       for f in unverified]})

    if any(f["severity"] == "critical" for f in new):
        status = "fail"
    elif new:
        status = "warn"
    else:
        status = "pass"
    chk(checks, "diff.new", status,
        "새로 생긴 findings %d건%s" % (len(new), " (critical 포함)" if status == "fail" else ""),
        {"items": [{"code": f["code"], "lane": f["lane"], "severity": f["severity"],
                    "urls": f["urls"], "message": f["message"]} for f in new]})

    worse = [p for p in persisting if p["urls_after"] > p["urls_before"]]
    crit = [p for p in persisting if p["severity"] == "critical"]
    chk(checks, "diff.persisting", "warn" if (crit or worse) else "pass",
        "그대로 남은 findings %d건 (critical %d · 영향 URL 증가 %d)"
        % (len(persisting), len(crit), len(worse)), {"items": persisting})

    lanes, dropped, unconcluded = OrderedDict(), [], []
    for lane in crawl.LANES:
        bcell = (before.get("scorecard") or {}).get(lane) or {}
        acell = (after.get("scorecard") or {}).get(lane) or {}
        bs, as_ = bcell.get("status", "na"), acell.get("status", "na")
        lanes[lane] = {"before": bs, "after": as_}
        if acell.get("unassessed"):
            lanes[lane]["after_unassessed"] = list(acell["unassessed"])
        if bs not in ASSESSED:
            continue   # 비교 기준이 없다 — 악화도 개선도 아니다
        if as_ not in ASSESSED:
            # 판정했던 레인을 이번엔 판정하지 못했다 — 통과·개선이 아니라 미결론이다
            unconcluded.append("%s %s→%s" % (lane, bs, as_))
        elif SCORE_RANK[as_] > SCORE_RANK[bs]:
            dropped.append("%s %s→%s" % (lane, bs, as_))
        elif acell.get("unassessed") and not bcell.get("unassessed"):
            # 일부 항목을 못 잰 판정 — 같거나 나아 보여도 결론으로 쓰지 않는다
            unconcluded.append("%s %s→%s(일부 미확인)" % (lane, bs, as_))
    message = "레인 점수 악화 %d건%s" % (len(dropped), (": " + ", ".join(dropped)) if dropped else "")
    if unconcluded:
        message += " · after에서 판정하지 못한 레인 %d건: %s" % (len(unconcluded),
                                                         ", ".join(unconcluded))
    chk(checks, "diff.scorecard", "fail" if dropped else ("warn" if unconcluded else "pass"),
        message, {"lanes": lanes, "unconcluded": unconcluded})

    keys = sorted(set(before.get("stats") or {}) | set(after.get("stats") or {}))
    chk(checks, "diff.stats", "pass", "stats 전후 비교",
        {"stats": {k: {"before": (before.get("stats") or {}).get(k),
                       "after": (after.get("stats") or {}).get(k)} for k in keys}})

    bu = {p["url"] for p in (before.get("pages") or []) if p.get("status") == 200}
    au = {p["url"] for p in (after.get("pages") or []) if p.get("status") == 200}
    gone, fresh = sorted(bu - au), sorted(au - bu)
    chk(checks, "diff.pages", "warn" if gone else "pass",
        "사라진 URL %d개 · 새 URL %d개" % (len(gone), len(fresh)),
        {"gone": gone[:50], "new": fresh[:50]})

    incomplete = []
    if unverified:
        incomplete.append({"check": "diff.unverified", "kind": "robots_unreachable_after",
                           "count": len(unverified)})
    if unconcluded:
        incomplete.append({"check": "diff.scorecard", "kind": "lane_unassessed_after",
                           "count": len(unconcluded)})
    failed = any(c["status"] == "fail" for c in checks)
    return {
        "schema": SCHEMA,
        "mode": "diff",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"base": ((after.get("target") or {}).get("base") or ""),
                   "host": ((after.get("target") or {}).get("host") or ""),
                   "before_at": before.get("generated_at"),
                   "after_at": after.get("generated_at")},
        "checks": checks,
        "summary": summarize(checks),
        "completion": {"complete": not incomplete, "reasons": incomplete},
        "exit_code": 1 if failed else (2 if incomplete else 0),
    }


# ─────────────────────────────────────────────────────────── 출력

def render_md(result: dict) -> str:
    target = result["target"]
    head = "배포 검증" if result["mode"] == "deploy" else "전/후 진단 비교"
    out = ["# %s — %s" % (head, target.get("host") or target.get("base") or "?"), "",
           "생성: %s · su-presence verify.py" % result["generated_at"].replace("T", " "),
           ""]
    s = result["summary"]
    out += ["| 결과 | 수 |", "|---|---|",
            "| ❌ fail | %d |" % s["fail"], "| ⚠️ warn | %d |" % s["warn"],
            "| ✅ pass | %d |" % s["pass"], "| — skip | %d |" % s["skip"], ""]
    completion = result.get("completion")
    if isinstance(completion, dict) and not completion.get("complete", True):
        if result["mode"] == "diff":
            out += ["**결론 보류:** after 진단이 일부 레인·findings를 판정하지 못했다(robots.txt 미확인 등). "
                    "사라진 항목을 해소로 세지 않았고 성공으로 종료하지 않았다(exit 2). "
                    "상세는 `verify.json`의 `completion`에 있다.", ""]
        else:
            out += ["**검사 범위 불완전:** 확인하지 못한 URL·사이트맵이 남아 있어 성공으로 "
                    "종료하지 않았다(exit 2). 상세 범위는 `verify.json`의 `completion`에 있다.", ""]
    network = target.get("network") if isinstance(target.get("network"), dict) else {}
    if network.get("proxy"):
        out += ["**네트워크:** %s (`PROXY_ADDRESS_UNVERIFIED`)"
                % (network.get("note") or crawl.PROXY_NOTE_KO), ""]

    for status, title in (("fail", "❌ 실패 — 이것부터 고친다"),
                          ("warn", "⚠️ 경고"),
                          ("pass", "✅ 통과"),
                          ("skip", "— 확인하지 않음")):
        items = [c for c in result["checks"] if c["status"] == status]
        if not items:
            continue
        out += ["## %s" % title, ""]
        for c in items:
            out.append("- **`%s`** — %s" % (c["id"], c["message"]))
            if status in ("fail", "warn") and NEXT.get(c["id"]):
                out.append("  - 다음 조치: %s" % NEXT[c["id"]])
        out.append("")

    ledger = result.get("facts")
    if ledger:
        s = ledger["summary"]
        out += ["## 사실 원장", "",
                "일치 %d · 불일치 %d · LD 노드 없음 %d · 페이지 미수신 %d (총 %d건)"
                % (s["agree"], s["mismatch"], s["missing"], s["unverified"], ledger["entries"]),
                ""]
        for r in [r for r in ledger["results"] if r["status"] != "agree"][:20]:
            live_vals = ", ".join(json.dumps(v["value"], ensure_ascii=False)
                                  for v in r.get("live_values") or []) or "없음"
            out.append("- `%s` %s `%s` — 기대 %s · 라이브 %s · 근거: %s"
                       % (r["status"], r["url"], r["pointer"],
                          json.dumps(r["expected"], ensure_ascii=False), live_vals,
                          r["evidence"]))
        out.append("")

    out += ["---", "",
            "증빙 원본은 `verify.json`의 `checks[].evidence`에 있다. "
            "이 문서는 그 요약이다.", ""]
    return "\n".join(out)


def print_summary(result: dict) -> None:
    s = result["summary"]
    print("")
    print("════════════════════════════════════════════")
    print(" %s — %s" % ("배포 검증" if result["mode"] == "deploy" else "전/후 비교",
                        result["target"].get("host") or result["target"].get("base")))
    print("════════════════════════════════════════════")
    for c in sorted(result["checks"], key=lambda c: ORDER[c["status"]]):
        print(" %s %-20s %s" % (MARK[c["status"]], c["id"], c["message"]))
    print("")
    print(" 결과: ❌ %d · ⚠️ %d · ✅ %d · — %d" % (s["fail"], s["warn"], s["pass"], s["skip"]))
    if s["fail"]:
        print(" 실패가 있다 — 배포는 아직 끝나지 않았다.")
    elif not (result.get("completion") or {}).get("complete", True):
        print(" 검사 범위가 불완전하다 — 확인하지 못한 항목이 남아 있다 (exit 2).")
    if ((result.get("target") or {}).get("network") or {}).get("proxy"):
        print(" ℹ️ %s" % crawl.PROXY_NOTE_KO)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="배포 후 검증 / 전후 진단 비교")
    sub = ap.add_subparsers(dest="mode", required=True)

    d = sub.add_parser("deploy", help="배포 패키지가 실제로 서빙되는지 라이브에서 확인")
    d.add_argument("audit", help="배포 전 audit.json")
    d.add_argument("--deploy", default=None, help="배포 패키지 폴더 (기본: audit.json 옆의 deploy/)")
    d.add_argument("--delay", type=float, default=0.5, help="요청 간격(초)")
    d.add_argument("--max-urls", type=int, default=500, help="사이트맵 URL 확인 상한")
    d.add_argument("--out", default=None, help="verify.json 경로")
    d.add_argument("--facts", default=None,
                   help="사실 원장 JSON (항목마다 url·pointer·expected·evidence 필수). "
                        "선택 — 주면 라이브 LD 값과 항목별로 대조한다")
    d.add_argument("--direct", action="store_true",
                   help="프록시 환경 변수를 무시하고 직접 연결한다 — 비공개 주소 차단이 실제 연결을 덮는다")

    f = sub.add_parser("diff", help="배포 전/후 audit.json 비교")
    f.add_argument("before")
    f.add_argument("after")
    f.add_argument("--out", default=None)

    args = ap.parse_args(argv)

    if args.mode == "deploy":
        audit = load_json(args.audit)
        if not str(audit.get("schema", "")).startswith(AUDIT_SCHEMA_PREFIX):
            sys.stderr.write("audit.json 스키마가 아니다: %s\n" % audit.get("schema"))
            return 2
        here = os.path.dirname(os.path.abspath(args.audit))
        deploy_dir = args.deploy or os.path.join(here, "deploy")
        if not os.path.isdir(deploy_dir):
            sys.stderr.write("배포 패키지 폴더가 없다: %s\n" % deploy_dir)
            return 2
        facts = None
        if args.facts:
            try:
                facts = load_facts(args.facts)
            except (FactsError, OSError) as exc:
                sys.stderr.write("사실 원장을 읽을 수 없다 (%s): %s\n" % (args.facts, exc))
                return 2
        result = verify_deploy(audit, deploy_dir, delay=args.delay,
                               max_urls=args.max_urls, facts=facts,
                               facts_source=args.facts, direct=args.direct)
        default_out = os.path.join(here, "verify.json")
    else:
        before, after = load_json(args.before), load_json(args.after)
        for name, report in (("before", before), ("after", after)):
            if not str(report.get("schema", "")).startswith(AUDIT_SCHEMA_PREFIX):
                sys.stderr.write("%s가 audit.json 스키마가 아니다: %s\n"
                                 % (name, report.get("schema")))
                return 2
        result = verify_diff(before, after)
        default_out = os.path.join(os.path.dirname(os.path.abspath(args.after)),
                                   "verify.json")

    path = args.out or default_out
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    md_path = os.path.join(os.path.dirname(os.path.abspath(path)), "VERIFY.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(result))

    print_summary(result)
    print("")
    print("verify.json: %s" % path)
    print("VERIFY.md  : %s" % md_path)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
