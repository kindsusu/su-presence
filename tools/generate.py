#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-multi-geo M2 — 진단 결과를 배포 가능한 산출물 초안으로 만든다.

사용:
    python tools/generate.py all     out/<host>/audit.json [--site out/<host>/site.json]
    python tools/generate.py sitemap out/<host>/audit.json
    python tools/generate.py robots  out/<host>/audit.json
    python tools/generate.py llms    out/<host>/audit.json --site out/<host>/site.json
    python tools/generate.py jsonld  out/<host>/audit.json --site out/<host>/site.json
    python tools/generate.py meta    out/<host>/audit.json --site out/<host>/site.json

출력:
    out/<host>/deploy/ 패키지 + DEPLOY.md 배포 지시서

원칙
  · 창작하지 않는다. 값은 audit.json(실측)과 site.json(사용자가 준 사실)에서만 온다.
  · 없는 값은 지어내지 않고 `<<TODO: ...>>` 표식으로 남긴다.
  · 기존 robots.txt의 Disallow는 제거·완화하지 않는다.
  · 생성물은 전부 **초안**이다. 사람이 검토하고 사람이 배포한다.
  · 표준 라이브러리만 쓴다 (pip 의존 0).
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sys
import urllib.parse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawl  # noqa: E402  (normalize·robots 파서·길이 기준을 그대로 쓴다)

SCHEMA_PREFIX = "su-multi-geo/audit/"
TODO = "<<TODO: %s>>"
OWNERSHIP_MANIFEST = ".su-multi-geo-generated.json"
JSONLD_MANIFEST = "jsonld/manifest.json"

# 사이트맵 한도(5만 URL·50MB)에 여유를 둔 분할 기준
MAX_URLS_PER_FILE = 45000
MAX_BYTES_PER_FILE = 45 * 1024 * 1024

# robots.txt에 명시할 크롤러 — ops/crawlers.md 완본 순서
UA_GROUPS = [
    ("OpenAI", ["GPTBot", "OAI-SearchBot", "ChatGPT-User"]),
    ("Anthropic", ["ClaudeBot", "Claude-SearchBot", "Claude-User"]),
    ("Perplexity", ["PerplexityBot", "Perplexity-User"]),
    ("Google (Gemini 그라운딩·학습 스위치 — UA가 아니라 robots 토큰)", ["Google-Extended"]),
    ("네이버 (NEO 레인)", ["Yeti"]),
    # 다음은 UA 토큰이 둘이다. Daumoa 만 적으면 DAUM 으로 오는 요청이 규칙 밖에 남는다.
    ("다음·카카오 (KEO 레인)", ["Daumoa", "DAUM"]),
]

TITLE_SEPS = ["|", "—", "–", "-", "·", ":"]


# ─────────────────────────────────────────────────────────── 컨텍스트

class Ctx:
    """생성 한 번의 상태 — 쓴 파일, 남은 TODO, DEPLOY.md에 실을 메모."""

    def __init__(self, audit: dict, site: dict, outdir: str):
        self.audit = audit
        self.site = site
        self.outdir = outdir
        self.files: list[str] = []
        self.todos: list[str] = []
        self.notes: dict = {}
        self.base = (audit.get("target") or {}).get("base") or ""
        self.host = (audit.get("target") or {}).get("host") or ""
        self.pages = audit.get("pages") or []
        self.ok_pages = [p for p in self.pages
                         if p.get("status") == 200 and not crawl._noindex(p)]
        self.crawled = {p["url"] for p in self.pages if p.get("status") == 200}

    def write(self, relpath: str, text: str) -> str:
        path = os.path.join(self.outdir, relpath)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.files.append(relpath.replace("\\", "/"))
        return path

    def write_json(self, relpath: str, obj) -> str:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
        json.loads(text)  # 저장 전 유효성 검사 — 깨진 LD는 내보내지 않는다
        return self.write(relpath, text + "\n")

    def todo(self, message: str) -> None:
        if message not in self.todos:
            self.todos.append(message)


# ─────────────────────────────────────────────────────────── site.json 접근

def sget(site: dict, *path, default=""):
    """site.json에서 값을 꺼낸다. 비어 있으면 default — 절대 지어내지 않는다."""
    node = site
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    if node is None or (isinstance(node, str) and not node.strip()):
        return default
    return node


def slug_of(url: str) -> str:
    """사람이 읽을 수 있으면서 정규 URL마다 고유한 파일 키."""
    normalized = crawl.normalize(url)
    path = urllib.parse.urlsplit(url).path.strip("/")
    slug = "home" if not path else re.sub(
        r"[^A-Za-z0-9._-]+", "-", urllib.parse.unquote(path)).strip("-") or "page"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return "%s-%s" % (slug[:67], digest)


def _coverage(ctx: Ctx) -> dict:
    return ctx.audit.get("coverage") or (ctx.audit.get("audit") or {}).get("coverage") or {}


def _known_sitemap_urls(ctx: Ctx) -> list:
    site = ctx.audit.get("site") or {}
    urls = site.get("sitemap_urls")
    candidates = (urls if isinstance(urls, list) else
                  ((site.get("sitemap_vs_crawl") or {}).get("only_in_sitemap") or []))
    out = set()
    for url in candidates:
        try:
            if isinstance(url, str) and crawl.host_of(url) == ctx.host:
                out.add(crawl.normalize(url))
        except ValueError:
            continue
    return sorted(out)


def sitemap_safety(ctx: Ctx, urls: list | None = None) -> dict:
    """교체용 sitemap을 만들 만큼 크롤 범위가 충분한지 판정한다."""
    urls = eligible_urls(ctx) if urls is None else urls
    coverage = _coverage(ctx)
    known = _known_sitemap_urls(ctx)
    missing = sorted(set(known) - set(urls))
    if coverage and coverage.get("complete") is not True:
        reasons = coverage.get("reasons") or ["크롤 범위가 완전하지 않다"]
        return {"safe": False, "verified": True, "missing": missing,
                "reason": "; ".join(str(x) for x in reasons)}
    if not coverage and missing:
        return {"safe": False, "verified": False, "missing": missing,
                "reason": "완성도 정보가 없는 구형 audit에서 기존 sitemap URL 누락이 확인됐다"}
    return {"safe": True, "verified": bool(coverage), "missing": missing,
            "reason": "" if coverage else "구형 audit이라 전수 크롤 여부를 확인할 수 없다"}


def seg_label(segment: str) -> str:
    return re.sub(r"[-_+]+", " ", urllib.parse.unquote(segment)).strip()


def brand_suffix(titles) -> tuple:
    """페이지 절반 이상이 공유하는 꼬리표를 브랜드 접미로 본다."""
    tails = Counter()
    for title in titles:
        for sep in TITLE_SEPS:
            if sep in title:
                tail = title.rsplit(sep, 1)[1].strip()
                if tail:
                    tails[(sep, tail)] += 1
                break
    if not tails:
        return None
    (sep, tail), n = tails.most_common(1)[0]
    return (sep, tail) if n >= max(2, len(titles) * 0.5) else None


def title_core(title: str, suffix) -> str:
    """제목에서 사이트 공통 꼬리표를 뗀 고유부."""
    if not title:
        return ""
    if suffix and title.endswith(suffix[1]) and suffix[0] in title:
        return title.rsplit(suffix[0], 1)[0].strip() or title
    return title


def label_index(ctx) -> tuple:
    """h1·title 고유부가 사이트에서 몇 번 쓰이는지 — 통짜 로고 h1을 걸러내려고 센다."""
    titles = [p["title"] for p in ctx.ok_pages if p.get("title")]
    suffix = brand_suffix(titles)
    h1s, cores = Counter(), Counter()
    for page in ctx.ok_pages:
        first = (page.get("h1") or [None])[0]
        if first:
            h1s[first] += 1
        core = title_core(page.get("title") or "", suffix)
        if core:
            cores[core] += 1
    return suffix, h1s, cores


def page_label(page: dict, suffix, h1s: Counter, cores: Counter) -> tuple:
    """이 페이지만의 이름과 그 출처. 지어내지 않고 실측값 중에서만 고른다."""
    first = (page.get("h1") or [None])[0]
    if first and h1s[first] == 1:
        return first, "h1"
    core = title_core(page.get("title") or "", suffix)
    if core and cores[core] == 1:
        return core, "기존 title의 고유부"
    if first:
        return first, "h1 (여러 페이지가 공유한다)"
    if core:
        return core, "기존 title (여러 페이지가 공유한다)"
    return "", "없음"


# ─────────────────────────────────────────────────────────── 1. sitemap

def eligible_urls(ctx: Ctx) -> list:
    """사이트맵에 실을 URL — 200 · noindex 아님 · canonical이 자기 자신이거나 없음."""
    out = []
    for page in ctx.ok_pages:
        try:
            requested = crawl.normalize(page["url"])
            final = crawl.normalize(page.get("final_url") or page["url"])
        except ValueError:
            continue
        if crawl.host_of(final) != ctx.host or requested != final:
            continue
        canonical = page.get("canonical")
        if canonical:
            try:
                resolved = crawl.normalize(urllib.parse.urljoin(page["url"], canonical))
            except ValueError:
                continue
            if resolved.rstrip("/") != final.rstrip("/"):
                continue
        out.append(final)
    return sorted(set(out))


def _urlset(urls) -> str:
    body = "".join("  <url><loc>%s</loc></url>\n" % xml_escape(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "%s</urlset>\n" % body)


def _chunk(urls) -> list:
    """5만 URL·50MB 한도 안에서 쪼갠다. 한도를 넘는 순간 통째로 무시되기 때문."""
    chunks, current, size = [], [], 0
    for url in urls:
        entry = len(url.encode("utf-8")) + 40
        if current and (len(current) >= MAX_URLS_PER_FILE or size + entry > MAX_BYTES_PER_FILE):
            chunks.append(current)
            current, size = [], 0
        current.append(url)
        size += entry
    if current:
        chunks.append(current)
    return chunks or [[]]


def gen_sitemap(ctx: Ctx) -> None:
    urls = eligible_urls(ctx)
    safety = sitemap_safety(ctx, urls)
    ctx.notes["sitemap_safety"] = safety
    ctx.notes["sitemap_count"] = len(urls)
    ctx.notes["sitemap_existing_count"] = len(_known_sitemap_urls(ctx))
    ctx.notes["sitemap_unconfirmed_or_removed"] = safety["missing"]
    if not safety["safe"]:
        ctx.notes["sitemap_files"] = []
        ctx.notes["sitemap_blocked"] = True
        ctx.todo("교체용 사이트맵을 만들지 않았다 — %s. audit을 전수 크롤한 뒤 다시 생성하라."
                 % safety["reason"])
        return
    chunks = _chunk(urls)
    if len(chunks) == 1:
        ctx.write("sitemap.xml", _urlset(chunks[0]))
        ctx.notes["sitemap_files"] = ["sitemap.xml"]
    else:
        parts = []
        for n, chunk in enumerate(chunks, 1):
            name = "sitemap-%d.xml" % n
            ctx.write(name, _urlset(chunk))
            parts.append(name)
        index = "".join("  <sitemap><loc>%s/%s</loc></sitemap>\n"
                        % (xml_escape(ctx.base), xml_escape(name)) for name in parts)
        ctx.write("sitemap_index.xml",
                  '<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                  "%s</sitemapindex>\n" % index)
        ctx.notes["sitemap_files"] = ["sitemap_index.xml"] + parts

    # lastmod는 audit.json에 없다 — 가짜 날짜를 넣느니 태그를 빼는 쪽이 맞다
    ctx.notes["sitemap_excluded"] = sorted(
        {p["url"] for p in ctx.pages if p.get("status") == 200} - set(urls))
    only_in_crawl = ((ctx.audit.get("site") or {}).get("sitemap_vs_crawl") or {}).get(
        "only_in_crawl") or []
    ctx.notes["sitemap_new"] = [u for u in only_in_crawl if u in set(urls)]
    if not safety["verified"]:
        ctx.todo("구형 audit에는 coverage가 없어 전수 크롤을 증명할 수 없다. 배포 전에 기존 "
                 "사이트맵 전체와 생성 파일을 대조하라.")
    ctx.todo("사이트맵에 lastmod가 없다 — CMS·저장소에서 실제 수정일을 뽑을 수 있으면 채운다 "
             "(모르는 날짜를 지어 넣지 말 것).")
    if not urls:
        ctx.todo("사이트맵에 실을 수 있는 URL이 0개다 — 크롤 결과부터 확인하라.")


# ─────────────────────────────────────────────────────────── 2. robots

def robots_plan(raw: str, policies: dict) -> tuple:
    """UA별로 '추가할 규칙'과 '건드리지 않을 이유'를 가른다.

    기존 Disallow는 절대 제거·완화하지 않는다. 이미 차단이거나 이미 명시된 UA는
    그대로 두고 DEPLOY.md에 사유를 남긴다.
    """
    star_rules = crawl.crawl_rules(raw, "__su_multi_geo_no_such_ua__")
    add, keep = OrderedDict(), []
    groups = list(UA_GROUPS)
    covered = {ua for _, uas in groups for ua in uas}
    extra = [ua for ua in crawl.ALL_UAS if ua not in covered]
    if extra:
        groups.append(("기타 검색 크롤러", extra))
    for _, uas in groups:
        for ua in uas:
            policy = policies.get(ua, "none")
            if policy in ("explicit-block", "star-block"):
                keep.append((ua, policy, "차단 유지 — 의도 확인 필요"))
            elif policy in ("explicit-allow", "explicit-partial"):
                keep.append((ua, policy, "이미 robots.txt에 명시돼 있다 — 그대로 둔다"))
            elif policy == "star-partial":
                # `*` 그룹의 제한을 글자 그대로 복사한다 — 현재 실효 정책과 동일하다
                add[ua] = [("Allow" if is_allow else "Disallow", path)
                           for path, is_allow in star_rules] or [("Allow", "/")]
            else:  # star-allow · none
                add[ua] = [("Allow", "/")]
    return add, keep


def gen_robots(ctx: Ctx) -> None:
    robots = (ctx.audit.get("site") or {}).get("robots") or {}
    before = robots.get("raw") or ""
    policies = robots.get("policies") or {}
    add, keep = robots_plan(before, policies)

    lines = []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append("# ── su-multi-geo: AI·검색 크롤러 명시 (%s 생성) ──" % stamp)
    lines.append("# 위쪽 기존 규칙은 한 줄도 건드리지 않았다. 아래는 추가분이다.")
    for label, uas in UA_GROUPS:
        block = [ua for ua in uas if ua in add]
        if not block:
            continue
        lines.append("")
        lines.append("# ── %s ──" % label)
        for ua in block:
            lines.append("User-agent: %s" % ua)
            for verb, path in add[ua]:
                lines.append("%s: %s" % (verb, path))
            lines.append("")
        lines.pop()  # 그룹 끝의 빈 줄 하나만 남긴다
    covered = {ua for _, uas in UA_GROUPS for ua in uas}
    extras = [ua for ua in add if ua not in covered]
    if extras:
        lines += ["", "# ── 기타 검색 크롤러 ──"]
        for ua in extras:
            lines.append("User-agent: %s" % ua)
            for verb, path in add[ua]:
                lines.append("%s: %s" % (verb, path))
            lines.append("")
        lines.pop()

    declared = [d for d in (robots.get("sitemap_declared") or [])]
    sitemap_files = (ctx.notes.get("sitemap_files")
                     if "sitemap_files" in ctx.notes else ["sitemap.xml"])
    new_declarations = []
    for name in sitemap_files:
        if name.startswith("sitemap-"):
            continue  # 인덱스만 선언한다
        url = "%s/%s" % (ctx.base, name)
        if url not in declared:
            new_declarations.append(url)
    if new_declarations:
        lines.append("")
        for url in new_declarations:
            lines.append("Sitemap: %s" % url)

    added = "\n".join(lines).rstrip() + "\n"
    after = (before.rstrip("\n") + "\n\n" + added) if before.strip() else added
    ctx.write("robots.txt", after)

    ctx.notes["robots_before"] = before
    ctx.notes["robots_after"] = after
    ctx.notes["robots_keep"] = keep
    ctx.notes["robots_added_uas"] = list(add)
    ctx.notes["robots_diff"] = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        "robots.txt (현재)", "robots.txt (제안)", lineterm="", n=2))
    if not before.strip():
        ctx.todo("기존 robots.txt를 읽지 못했다(없음 또는 비정상) — 배포 전에 서버의 실제 "
                 "robots.txt를 직접 확인하고, 있으면 그 원문 위에 이 블록을 얹어라.")
    for ua, policy, reason in keep:
        if reason.startswith("차단 유지"):
            ctx.todo("robots.txt에서 %s가 차단(%s) 상태다 — 의도한 차단인지 확인하라. "
                     "생성기는 차단을 뒤집지 않았다." % (ua, policy))


# ─────────────────────────────────────────────────────────── 3. llms.txt

def section_pages(ctx: Ctx, limit: int = 20) -> list:
    """경로 패턴별 대표 페이지 — 각 섹션에서 가장 얕은 URL 하나."""
    groups: dict = {}
    for page in ctx.ok_pages:
        parts = [p for p in urllib.parse.urlsplit(page["url"]).path.split("/") if p]
        key = parts[0] if parts else ""
        current = groups.get(key)
        if current is None or len(page["url"]) < len(current["url"]):
            groups[key] = page
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "", kv[0]))
    return [page for _, page in ordered][:limit]


def gen_llms(ctx: Ctx) -> None:
    name = sget(ctx.site, "name") or ctx.host
    if not sget(ctx.site, "name"):
        ctx.todo("site.json의 name이 비어 있어 llms.txt 제목에 호스트명을 썼다 — 정식 명칭으로 바꿔라.")
    description = sget(ctx.site, "description")
    if not description:
        description = TODO % "우산 메시지 한 문장 — SKILL.md Phase 2에서 확정한 문장을 그대로"
        ctx.todo("llms.txt 한 줄 설명이 비어 있다 — site.json의 description을 채워라.")

    lines = ["# %s" % name, "", "> %s" % description, "", "## 주요 페이지"]
    reps = section_pages(ctx)
    index = label_index(ctx)
    for page in reps:
        label, source = page_label(page, *index)
        if not label or "공유" in source:
            # 페이지마다 같은 h1(로고)을 쓰는 사이트가 흔하다 — 그럴 땐 URL 경로가 더 정확하다
            parts = [p for p in urllib.parse.urlsplit(page["url"]).path.split("/") if p]
            label = seg_label(parts[-1]) if parts else (label or ctx.host)
        lines.append("- [%s](%s): %s" % (label, page["url"], TODO % "한 줄 설명"))
    if not reps:
        lines.append("- %s" % (TODO % "크롤된 페이지가 없다 — Phase 0을 다시 돌려라"))
    lines += [
        "",
        "## 데이터 출처와 이용",
        "- 출처: %s" % (TODO % "무엇의 원출처인지 — 자체 운영 데이터·집계 방식"),
        "- 갱신 주기: %s" % (TODO % "예: 매일 / 매월 1일"),
        "- 인용 시 출처 표기: %s" % ctx.host,
        "",
    ]
    ctx.write("llms.txt", "\n".join(lines))
    ctx.todo("llms.txt의 페이지별 한 줄 설명과 데이터 정책 절을 사람이 채워야 한다 — "
             "크롤 데이터로는 만들 수 없다.")
    ctx.notes["llms_pages"] = len(reps)


# ─────────────────────────────────────────────────────────── 4. JSON-LD

def build_organization(ctx: Ctx):
    site = ctx.site
    org = {"@context": "https://schema.org", "@type": "Organization",
           "@id": "%s#organization" % ctx.base}
    mapping = [
        ("name", ("name",)), ("legalName", ("legal_name",)),
        ("url", ("url",)), ("logo", ("logo",)), ("description", ("description",)),
        ("telephone", ("contact", "phone")), ("email", ("contact", "email")),
    ]
    for key, path in mapping:
        value = sget(site, *path)
        if value:
            org[key] = value
    same_as = [s for s in (sget(site, "same_as", default=[]) or []) if isinstance(s, str) and s.strip()]
    if same_as:
        org["sameAs"] = same_as
    founding = sget(site, "founding_year")
    if founding:
        org["foundingDate"] = str(founding)
    address = sget(site, "address", default={}) or {}
    addr = {k: v for k, v in {
        "streetAddress": sget(address, "street"),
        "addressLocality": sget(address, "locality"),
        "addressRegion": sget(address, "region"),
        "postalCode": sget(address, "postal_code"),
        "addressCountry": sget(address, "country"),
    }.items() if v}
    if addr:
        addr["@type"] = "PostalAddress"
        org["address"] = addr
    # @context·@type·@id 말고 실제 사실이 하나도 없으면 LD를 만들지 않는다
    return org if len(org) > 3 else None


def build_breadcrumb(ctx: Ctx, page: dict):
    parts = [p for p in urllib.parse.urlsplit(page["url"]).path.split("/") if p]
    if not parts:
        return None
    by_url = {p["url"]: p for p in ctx.pages}
    home_name = sget(ctx.site, "name") or ctx.host
    items = [{"@type": "ListItem", "position": 1, "name": home_name,
              "item": ctx.base + "/"}]
    trail = ctx.base
    for n, segment in enumerate(parts, 2):
        trail = "%s/%s" % (trail, segment)
        known = by_url.get(trail) or by_url.get(trail + "/")
        name = None
        if known:
            name = (known.get("h1") or [None])[0] or known.get("title")
        items.append({"@type": "ListItem", "position": n,
                      "name": name or seg_label(segment), "item": trail})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": items}


def build_faqs(ctx: Ctx):
    """site.faqs 중 크롤된 페이지에 붙은, q·a가 모두 있는 것만."""
    by_page: dict = OrderedDict()
    skipped = []
    for entry in (sget(ctx.site, "faqs", default=[]) or []):
        if not isinstance(entry, dict):
            continue
        question = sget(entry, "q")
        answer = sget(entry, "a")
        page_url = sget(entry, "page_url")
        if not (question and answer and page_url):
            skipped.append((page_url or "(page_url 없음)", "q·a·page_url 중 빈 값이 있다"))
            continue
        normalized = crawl.normalize(page_url)
        if normalized not in ctx.crawled:
            skipped.append((page_url, "크롤되지 않은 URL이다 (200 응답 목록에 없다)"))
            continue
        by_page.setdefault(normalized, []).append((question, answer))
    out = OrderedDict()
    for url, pairs in by_page.items():
        out[url] = {
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs],
        }
    return out, skipped


def build_products(ctx: Ctx):
    out, skipped = OrderedDict(), []
    for entry in (sget(ctx.site, "products", default=[]) or []):
        if not isinstance(entry, dict):
            continue
        page_url = sget(entry, "page_url")
        name = sget(entry, "name")
        if not (page_url and name):
            skipped.append((page_url or "(page_url 없음)", "page_url·name 중 빈 값이 있다"))
            continue
        product = {"@context": "https://schema.org", "@type": "Product",
                   "name": name, "url": page_url}
        description = sget(entry, "description")
        if description:
            product["description"] = description
        offers = sget(entry, "offers", default={}) or {}
        price, currency = sget(offers, "price"), sget(offers, "currency")
        if price != "" and currency:
            offer = {"@type": "Offer", "price": str(price), "priceCurrency": currency,
                     "url": page_url}
            unit = sget(offers, "unit")
            if unit:
                offer["description"] = unit
            product["offers"] = offer
        else:
            skipped.append((page_url, "price·currency가 없어 Offer를 뺐다 — 가격은 지어내지 않는다"))
        if crawl.normalize(page_url) not in ctx.crawled:
            skipped.append((page_url, "크롤되지 않은 URL이다 — 이 페이지가 실재하는지 확인하라"))
        out.setdefault(crawl.normalize(page_url), []).append(product)
    return out, skipped


def snippet(objs) -> str:
    def html_safe_json(obj):
        # HTML raw-text의 종료 토큰과 엔티티 해석을 막되 JSON 유효성은 유지한다.
        return (json.dumps(obj, ensure_ascii=False, indent=2)
                .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))
    return "\n".join(
        '<script type="application/ld+json">\n%s\n</script>'
        % html_safe_json(obj) for obj in objs) + "\n"


def gen_jsonld(ctx: Ctx) -> None:
    per_page: dict = OrderedDict()
    manifest = OrderedDict()
    made = []

    org = build_organization(ctx)
    if org:
        ctx.write_json("jsonld/organization.json", org)
        ctx.write("jsonld/organization.snippet.html", snippet([org]))
        manifest["organization.json"] = ctx.base + "/"
        manifest["organization.snippet.html"] = ctx.base + "/"
        made.append("Organization (전역 1회, @id=%s#organization)" % ctx.base)
    else:
        ctx.todo("site.json이 비어 Organization JSON-LD를 만들지 않았다 — name·url·logo· "
                 "same_as 등을 채우고 다시 돌려라. 빈 값을 넣은 LD는 만들지 않는다.")

    name = sget(ctx.site, "name")
    if name:
        website = {"@context": "https://schema.org", "@type": "WebSite",
                   "name": name, "url": sget(ctx.site, "url") or (ctx.base + "/")}
        ctx.write_json("jsonld/website.json", website)
        manifest["website.json"] = ctx.base + "/"
        per_page.setdefault(ctx.base + "/", []).append(website)
        made.append("WebSite (홈)")
    else:
        ctx.todo("site.json의 name이 없어 WebSite JSON-LD를 만들지 않았다.")

    faqs, faq_skipped = build_faqs(ctx)
    for url, obj in faqs.items():
        name = "%s.faq.json" % slug_of(url)
        ctx.write_json("jsonld/%s" % name, obj)
        manifest[name] = url
        per_page.setdefault(url, []).append(obj)
    if faqs:
        made.append("FAQPage %d페이지" % len(faqs))
    else:
        ctx.todo("FAQPage를 만들지 않았다 — site.json의 faqs가 비었거나 page_url이 "
                 "크롤 결과에 없다. 문답은 lanes/aeo.md 기준으로 실제 문의에서 뽑아 채운다.")

    products, product_skipped = build_products(ctx)
    for url, objs in products.items():
        name = "%s.product.json" % slug_of(url)
        ctx.write_json("jsonld/%s" % name,
                       objs[0] if len(objs) == 1 else objs)
        manifest[name] = url
        per_page.setdefault(url, []).extend(objs)
    if products:
        made.append("Product %d페이지" % len(products))

    crumbs = 0
    for page in ctx.ok_pages:
        obj = build_breadcrumb(ctx, page)
        if not obj:
            continue
        name = "%s.breadcrumb.json" % slug_of(page["url"])
        ctx.write_json("jsonld/%s" % name, obj)
        manifest[name] = page["url"]
        per_page.setdefault(page["url"], []).append(obj)
        crumbs += 1
    if crumbs:
        made.append("BreadcrumbList %d페이지 (크롤한 경로 구조에서 생성)" % crumbs)

    for url, objs in per_page.items():
        name = "%s.snippet.html" % slug_of(url)
        ctx.write("jsonld/%s" % name, snippet(objs))
        manifest[name] = url

    ctx.write_json(JSONLD_MANIFEST, {
        "schema": "su-multi-geo/jsonld-manifest/1", "files": manifest})

    ctx.notes["jsonld_made"] = made
    ctx.notes["jsonld_pages"] = {url: len(objs) for url, objs in per_page.items()}
    ctx.notes["faq_pages"] = list(faqs)
    ctx.notes["jsonld_skipped"] = faq_skipped + product_skipped
    if faqs:
        ctx.todo("FAQPage의 질문·답변이 해당 페이지 **가시 텍스트에 글자 그대로** 있어야 한다. "
                 "화면에 없는 문답을 LD에만 넣으면 스팸으로 분류된다 — 배포 전 대조하라.")


# ─────────────────────────────────────────────────────────── 5. meta 초안

def verdict(text: str, table: dict) -> tuple:
    if not text or text.startswith("<<TODO"):
        return 0, "없음"
    lo, hi = table[crawl.script_of(text)]
    n = len(text)
    return n, "짧음" if n < lo else ("김" if n > hi else "적정")


def gen_meta(ctx: Ctx) -> None:
    index = label_index(ctx)
    name = sget(ctx.site, "name")
    rows = []
    for page in ctx.ok_pages:
        current = page.get("title") or ""
        core, source = page_label(page, *index)
        if core:
            draft_title = "%s | %s" % (core, name) if name and name not in core else core
        else:
            draft_title = TODO % "제목 — h1도 title도 없다"
            source = "없음"
            ctx.todo("h1도 title도 없는 페이지가 있다 (%s) — 제목은 지어낼 수 없다." % page["url"])

        # audit.json에는 본문 텍스트가 없다 → 페이지에 실재하는 문장(기존 설명·og)만 후보로 쓴다
        draft_desc = page.get("meta_description") or (page.get("og") or {}).get("description") or ""
        desc_source = ("기존 meta description" if page.get("meta_description")
                       else ("og:description" if draft_desc else "없음"))
        if not draft_desc:
            draft_desc = TODO % "본문 첫 문장에서 골라 붙일 것 — 문장을 새로 짓지 말 것"

        tlen, tverdict = verdict(draft_title, crawl.LEN_TITLE)
        dlen, dverdict = verdict(draft_desc, crawl.LEN_DESC)
        rows.append(OrderedDict([
            ("url", page["url"]),
            ("current_title", current),
            ("draft_title", draft_title),
            ("title_source", source),
            ("title_len", tlen),
            ("title_verdict", tverdict),
            ("current_description", page.get("meta_description") or ""),
            ("draft_description", draft_desc),
            ("description_source", desc_source),
            ("description_len", dlen),
            ("description_verdict", dverdict),
        ]))

    drafted = Counter(r["draft_title"] for r in rows if not r["draft_title"].startswith("<<TODO"))
    dup = 0
    for row in rows:
        collides = drafted[row["draft_title"]] > 1
        row["title_duplicate"] = "중복" if collides else ""
        dup += 1 if collides else 0
    if dup:
        ctx.todo("초안 title %d개가 서로 겹친다 — 페이지마다 같은 h1·title을 쓰고 있다는 뜻이다. "
                 "페이지 고유의 문구는 지어낼 수 없으니 사람이 직접 붙여야 한다 "
                 "(meta-draft.csv의 title_duplicate 열)." % dup)

    ctx.write_json("meta-draft.json", rows)
    path = os.path.join(ctx.outdir, "meta-draft.csv")
    os.makedirs(ctx.outdir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["url"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    ctx.files.append("meta-draft.csv")

    ctx.notes["meta_rows"] = len(rows)
    ctx.notes["meta_todo"] = sum(1 for r in rows if r["draft_description"].startswith("<<TODO"))
    if ctx.notes["meta_todo"]:
        ctx.todo("설명 초안 %d개가 TODO다 — audit.json에는 본문 텍스트가 없어 첫 문장을 뽑을 수 "
                 "없다. 페이지 본문에 **이미 있는 문장**을 골라 넣어라 (새로 짓지 말 것)."
                 % ctx.notes["meta_todo"])
    ctx.todo("meta 초안은 자동 적용 대상이 아니다 — 사람이 한 줄씩 검토한 뒤 반영한다.")


# ─────────────────────────────────────────────────────────── 6. DEPLOY.md

STACK_HINTS = [
    ("정적 호스팅 / Nginx·Apache", "웹루트에 파일 그대로 업로드. `<head>` 스니펫은 공통 레이아웃 파일에 붙인다."),
    ("Laravel", "`public/`에 파일을 두면 그대로 서빙된다. LD 스니펫은 `resources/views/layouts/app.blade.php`의 `<head>`에."),
    ("WordPress", "robots·sitemap은 SEO 플러그인이 가로챈다 — 플러그인 설정에서 넣거나 끄고 파일로 올린다. LD는 자식 테마 `header.php` 또는 `wp_head` 훅."),
    ("Next.js / Nuxt", "`public/`에 정적 파일. LD는 페이지 컴포넌트의 `<script type=\"application/ld+json\">`으로 SSR 출력."),
    ("Cafe24·아임웹 등 임대몰", "robots·sitemap은 관리자 설정 화면에만 열려 있는 경우가 많다. 파일 업로드가 막혀 있으면 설정 화면에 같은 내용을 붙여넣는다."),
]


def gen_deploy(ctx: Ctx) -> None:
    n = ctx.notes
    out = ["# 배포 지시서 — %s" % ctx.host, "",
           "생성: %s · su-multi-geo generate.py" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "",
           "이 패키지의 모든 파일은 **초안**이다. 값은 진단 크롤(`audit.json`)에서 실측한 것과 "
           "`site.json`에 사람이 적어 준 사실뿐이며, 그 밖의 것은 지어내지 않고 "
           "`<<TODO: ...>>` 표식으로 남겼다. **사람이 검토하고 사람이 배포한다.**", ""]

    out += ["## 0. 파일과 놓을 위치", "", "| 파일 | 놓을 곳 | 방법 |", "|---|---|---|"]
    snippets = [f for f in ctx.files if f.startswith("jsonld/") and f.endswith(".snippet.html")]
    sources = [f for f in ctx.files if f.startswith("jsonld/") and f.endswith(".json")]
    for rel in sorted(f for f in ctx.files if not f.startswith("jsonld/")):
        if rel in ("meta-draft.csv", "meta-draft.json"):
            where, how = "(검토용)", "업로드 대상이 아니다. 검토 후 CMS·템플릿에 손으로 반영"
        else:
            where, how = "웹루트 (`https://%s/%s`)" % (ctx.host, rel), "파일 그대로 업로드"
        out.append("| `%s` | %s | %s |" % (rel, where, how))
    if "jsonld/organization.snippet.html" in snippets:
        out.append("| `jsonld/organization.snippet.html` | **전역 1회** — 공통 레이아웃의 "
                   "`<head>` | 내용을 그대로 붙여넣는다. 페이지마다 다시 선언하지 마라 |")
    others = [f for f in snippets if f != "jsonld/organization.snippet.html"]
    if others:
        out.append("| `jsonld/<슬러그>.snippet.html` (%d개) | 각 페이지 `<head>` | "
                   "슬러그와 같은 URL의 페이지에 그 파일 내용을 붙여넣는다 |" % len(others))
    if sources:
        out.append("| `jsonld/*.json` (%d개) | (참고용 원본) | 스니펫의 원본이다. "
                   "서버에 올릴 필요는 없다 |" % len(sources))
    out.append("")
    if others:
        out += ["<details><summary>스니펫 ↔ 페이지 대응 (%d개)</summary>" % len(others), ""]
        out += ["- `%s` → %s" % ("jsonld/%s.snippet.html" % slug_of(url), url)
                for url in n.get("jsonld_pages", {}) ]
        out += ["", "</details>", ""]

    out += ["### 스택별 한 줄 힌트", ""]
    for stack, hint in STACK_HINTS:
        out.append("- **%s** — %s" % (stack, hint))
    out.append("")

    if "sitemap_files" in n:
        out += ["## 1. 사이트맵", ""]
        if n.get("sitemap_blocked"):
            safety = n.get("sitemap_safety") or {}
            out += ["**교체 금지: 크롤 범위가 불완전해 sitemap XML을 생성하지 않았다.**",
                    "", "- 이유: %s" % safety.get("reason", "확인 불가"),
                    "- 기존 sitemap에서 누락 위험이 확인된 URL: %d개"
                    % len(safety.get("missing") or []), ""]
        else:
            out += [
                "- 실은 URL: **%d개** (HTTP 200 · noindex 아님 · canonical이 자기 자신이거나 없음)"
                % n.get("sitemap_count", 0),
                "- 제외한 URL: %d개 (noindex이거나 canonical이 다른 곳을 가리킨다)"
                % len(n.get("sitemap_excluded") or []),
                "- `lastmod`는 넣지 않았다 — 진단에서 실제 수정일을 알 수 없었다. "
                "가짜 날짜보다 없는 편이 낫다.", ""]
            removed = n.get("sitemap_unconfirmed_or_removed") or []
            if removed:
                out += ["### 기존 sitemap에서 새 파일에 넣지 않은 URL (%d개)" % len(removed), "",
                        "전수 크롤 결과에서 200·indexable·self-canonical 조건을 통과하지 못한 URL이다. "
                        "삭제 사유를 확인한 뒤 교체하라.", ""]
                out += ["- `%s`" % u for u in removed[:200]]
                if len(removed) > 200:
                    out.append("- … 외 %d개" % (len(removed) - 200))
                out.append("")
        new = n.get("sitemap_new") or []
        out += ["### 크롤엔 있는데 기존 사이트맵에 없던 URL (%d개)" % len(new), ""]
        if new:
            out.append("이 URL들은 지금까지 검색엔진에 신고되지 않았다. 새 사이트맵에는 들어 있다.")
            out.append("")
            out += ["- `%s`" % u for u in new[:200]]
            if len(new) > 200:
                out.append("- … 외 %d개 (`sitemap.xml` 참조)" % (len(new) - 200))
        else:
            out.append("없음 — 또는 기존 사이트맵을 읽지 못했다(`audit.json`의 `sitemap_vs_crawl` 확인).")
        out.append("")

    if "robots_after" in n:
        out += ["## 2. robots.txt", "",
                "**기존 원문은 한 줄도 지우거나 완화하지 않았다.** 아래 블록을 뒤에 덧붙인 형태다.", ""]
        keep = n.get("robots_keep") or []
        if keep:
            out += ["| User-agent | 현재 정책 | 처리 |", "|---|---|---|"]
            out += ["| `%s` | %s | %s |" % (ua, policy, reason) for ua, policy, reason in keep]
            out.append("")
        out += ["명시 허용을 추가한 UA: %s" % (", ".join("`%s`" % u for u in n.get("robots_added_uas") or []) or "없음"), ""]
        out += ["### 전/후 diff", "", "```diff"] + (n.get("robots_diff") or ["(변경 없음)"]) + ["```", ""]

    if "llms_pages" in n:
        out += ["## 3. llms.txt", "",
                "- 핵심 페이지 %d개를 크롤 결과(경로 패턴별 대표 URL)에서 뽑았다." % n["llms_pages"],
                "- 페이지별 한 줄 설명과 데이터 정책 절은 `<<TODO>>`로 비워 뒀다 — 사람이 채운다.",
                "- `llms-full.txt`는 만들지 않았다. 전문을 낼지는 사업 판단이다 (`lanes/geo.md` 1번).", ""]

    if "jsonld_made" in n:
        out += ["## 4. JSON-LD", ""]
        out += ["- %s" % item for item in (n["jsonld_made"] or ["생성된 LD 없음"])]
        out += ["",
                "**검증 의무 — FAQPage.** LD에 넣은 질문·답변은 해당 페이지 화면에 "
                "**글자 그대로** 있어야 한다. 화면에 없는 문답을 LD에만 넣으면 스팸으로 분류된다. "
                "배포 전에 페이지를 열어 한 문장씩 대조하라.", ""]
        if n.get("faq_pages"):
            out += ["FAQ LD가 붙는 페이지:", ""] + ["- `%s`" % u for u in n["faq_pages"]] + [""]
        skipped = n.get("jsonld_skipped") or []
        if skipped:
            out += ["### 뺀 항목과 이유", "", "| 대상 | 이유 |", "|---|---|"]
            out += ["| `%s` | %s |" % (url, reason) for url, reason in skipped]
            out.append("")
        out += ["삽입 위치: 각 `jsonld/<슬러그>.snippet.html`의 내용을 그 페이지의 `<head>` 안에 "
                "그대로 넣는다. `organization.snippet.html`은 **전역 1회만** — 페이지마다 새로 "
                "선언하면 엔티티가 쪼개진다.", ""]

    if "meta_rows" in n:
        out += ["## 5. title·description 초안", "",
                "- 대상 %d페이지, 설명이 TODO인 행 %d개." % (n["meta_rows"], n.get("meta_todo", 0)),
                "- **자동 적용 대상이 아니다.** `meta-draft.csv`를 열어 한 줄씩 검토한 뒤 반영한다.",
                "- 길이 판정은 한글/영문을 문자열마다 자동 판별한다 "
                "(title 한글 25~30·영문 50~60 / description 한글 70~80·영문 150~160).",
                "- 설명 후보는 **페이지에 이미 있는 문장**만 쓴다. 없으면 TODO로 남겼다 — "
                "본문에 없는 문장을 새로 짓지 마라.", ""]

    out += ["## 6. 배포 후 검증 (크롤러의 눈)", "",
            "지시서대로 올라갔는지 **직접 확인하기 전까지 완료가 아니다.** 상대방 말로 대체하지 마라.",
            "", "```bash"]
    for rel in sorted(ctx.files):
        if rel.endswith((".xml", ".txt")) and "/" not in rel:
            out.append("curl -sI https://%s/%s | head -1        # 200이어야 한다" % (ctx.host, rel))
    out += ["curl -sL https://%s/robots.txt | grep -iE 'GPTBot|ClaudeBot|Yeti|Sitemap'" % ctx.host,
            "curl -sL https://%s/ | grep -o 'application/ld+json'          # LD 삽입 확인" % ctx.host,
            "curl -sL https://%s/ | grep -oiE '<meta[^>]*robots[^>]*>'     # noindex 재확인" % ctx.host,
            "curl -sI https://%s/__no_such_page__ | head -1                # 404여야 한다" % ctx.host,
            "```", "",
            "구조화 데이터는 Google Rich Results Test 또는 schema.org validator로 **배포 후** 한 번 더 본다.",
            "", "## 7. 롤백", "",
            "- 배포 전에 기존 `robots.txt`·`sitemap.xml`·수정할 템플릿을 그대로 복사해 둔다 "
            "(예: `robots.txt.bak-YYYYMMDD`).",
            "- 문제가 생기면 백업본을 되돌려 올리고, `<head>`에 넣은 LD 스니펫 블록을 삭제한다.",
            "- 사이트맵을 교체했으면 되돌린 뒤 검색콘솔에서 다시 제출한다.",
            "- 되돌린 뒤에도 위 6번 curl을 다시 돌려 상태를 확인한다.", ""]

    out += ["## 8. 사람이 채워야 할 TODO", ""]
    out += ["- [ ] %s" % t for t in ctx.todos] or ["(없음)"]
    out += ["", "---", "",
            "생성기는 판단하지 않는다. 위 TODO를 채우고 검토하는 것까지가 배포 준비다."]
    ctx.write("DEPLOY.md", "\n".join(out) + "\n")


# ─────────────────────────────────────────────────────────── 실행

SUBCOMMANDS = OrderedDict([
    ("sitemap", gen_sitemap),
    ("robots", gen_robots),
    ("llms", gen_llms),
    ("jsonld", gen_jsonld),
    ("meta", gen_meta),
    ("deploy", gen_deploy),
])


def run(sub: str, audit: dict, site: dict, outdir: str) -> Ctx:
    ctx = Ctx(audit, site, outdir)
    old = _read_ownership_manifest(outdir)
    if sub in ("all", "deploy"):
        # DEPLOY.md는 나머지 산출물을 설명하는 문서다 — 혼자 만들면 설명할 대상이 없다
        for name, func in SUBCOMMANDS.items():
            func(ctx)
    else:
        if sub == "robots":
            # Sitemap: 선언에 쓸 파일명을 알아야 한다 — 파일은 쓰지 않고 이름만 계산
            safety = sitemap_safety(ctx)
            ctx.notes["sitemap_files"] = ([] if not safety["safe"] else
                (["sitemap_index.xml"] if len(_chunk(eligible_urls(ctx))) > 1 else ["sitemap.xml"]))
        SUBCOMMANDS[sub](ctx)
    _finalize_owned_files(ctx, old, sub)
    return ctx


def _read_ownership_manifest(outdir: str) -> dict:
    path = os.path.join(outdir, OWNERSHIP_MANIFEST)
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if obj.get("schema") == "su-multi-geo/generated-files/1" else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _category(rel: str) -> str:
    if rel.startswith("jsonld/"):
        return "jsonld"
    if rel.startswith("sitemap") and rel.endswith(".xml"):
        return "sitemap"
    if rel.startswith("meta-draft."):
        return "meta"
    return rel.split("/", 1)[0].split(".", 1)[0]


def _finalize_owned_files(ctx: Ctx, old: dict, sub: str) -> None:
    previous = set(old.get("files") or [])
    current_written = set(ctx.files)
    categories = set(SUBCOMMANDS) if sub in ("all", "deploy") else {sub}
    keep = {f for f in previous if _category(f) not in categories}
    final = keep | current_written
    root = os.path.abspath(ctx.outdir) + os.sep
    for rel in sorted(previous - final):
        path = os.path.abspath(os.path.join(ctx.outdir, rel))
        if path.startswith(root) and os.path.isfile(path):
            os.remove(path)
    manifest = {"schema": "su-multi-geo/generated-files/1", "files": sorted(final)}
    path = os.path.join(ctx.outdir, OWNERSHIP_MANIFEST)
    os.makedirs(ctx.outdir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="audit.json → 배포 산출물 초안")
    ap.add_argument("sub", choices=list(SUBCOMMANDS) + ["all"])
    ap.add_argument("audit", help="crawl.py가 만든 audit.json 경로")
    ap.add_argument("--site", default=None, help="회사 사실을 적은 site.json (templates/site.example.json 참조)")
    ap.add_argument("--out", default=None, help="출력 폴더 (기본: audit.json 옆의 deploy/)")
    args = ap.parse_args(argv)

    audit = load_json(args.audit)
    if not str(audit.get("schema", "")).startswith(SCHEMA_PREFIX):
        sys.stderr.write("audit.json 스키마가 아니다: %s\n" % audit.get("schema"))
        return 1

    site = {}
    if args.site:
        if not os.path.exists(args.site):
            sys.stderr.write("site.json이 없다: %s — 회사 사실 없이 만들 수 있는 것만 만든다.\n"
                             % args.site)
        else:
            site = load_json(args.site)

    outdir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.audit)), "deploy")
    ctx = run(args.sub, audit, site, outdir)

    print("")
    print("생성 위치: %s" % outdir)
    for rel in sorted(ctx.files):
        print("  · %s" % rel)
    if ctx.todos:
        print("")
        print("사람이 채워야 할 것 (%d):" % len(ctx.todos))
        for todo in ctx.todos:
            print("  □ %s" % todo)
    if args.sub == "all":
        print("")
        print("배포 지시서: %s" % os.path.join(outdir, "DEPLOY.md"))
    print("")
    print("※ 전부 초안이다. 사람이 검토하고 사람이 배포한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
