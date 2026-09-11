#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-presence — audit.json을 독립 HTML 보고서로 만든다.

사용:
    python tools/report.py out/<host>/audit.json [--lang ko|en] [--out <경로>]

값은 전부 audit.json에서 온다. 이 파일은 아무것도 창작하지 않는다 —
측정되지 않은 칸은 "미확인"으로 남긴다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from collections import Counter
from html import escape
from string import Template

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(ROOT, "templates", "report.html")
GLOSSARY = os.path.join(ROOT, "templates", "glossary.json")

LANES = ["SEO", "AEO", "GEO", "LLMO", "NEO", "KEO", "reputation"]
SEV_ORDER = {"critical": 0, "warn": 1, "info": 2}


# ─────────────────────────────────────────────────────────── 라벨

L = {
    "ko": {
        "eyebrow": "Phase 0 — 검색·AI 접근성 기술 진단",
        "doc_title": "%s 검색·AI 인용 진단",
        "meta": "기준 시각 %s · 크롤 %d페이지 · su-presence",
        "theme": "화면 밝기 전환",
        "prev": "이전", "next": "다음",
        "footer": "이 보고서의 모든 수치는 자바스크립트 없이 받은 HTML을 직접 측정한 값이다. "
                  "측정되지 않은 항목은 추정하지 않고 “미확인”으로 남겼다.",
        "titles": ["진단 요약", "사이트 구성", "레인별 상세", "강점",
                   "인용 자산", "권고 로드맵", "측정 계획", "용어 설명"],
        "ledes": [
            "여섯 레인의 판정과 한 줄 결론. 칩을 누르면 해당 레인 상세로 간다.",
            "크롤러가 실제로 도달한 페이지와 그 구성.",
            "레인마다 판정·실측 근거·무슨 뜻인지.",
            "이미 되어 있는 것. 여기는 건드리지 않는다.",
            "AI가 우리를 인용할 때 무엇을 근거로 쓰게 될 것인가.",
            "무엇을 어떤 순서로 할 것인가. 진단 결과에서 자동 생성된다.",
            "고쳤다는 말은 절반이다. 언제 무엇을 다시 재는지까지가 완료 조건이다.",
            "이 보고서에 나온 용어를 한 줄로.",
        ],
        "sev": {"critical": "치명", "warn": "주의", "info": "참고"},
        "status": {"ok": "양호", "warn": "주의", "bad": "결함", "na": "미확인"},
        "verdict": "%s를 크롤러의 눈으로 %d페이지 훑었다. 치명 %d건 · 주의 %d건 · 참고 %d건.",
        "verdict_first": "가장 먼저 할 일",
        "verdict_clean": "치명 결함은 발견되지 않았다.",
        "th": {
            "item": "항목", "value": "값", "sev": "심각도", "code": "코드",
            "what": "무엇이 측정됐나", "urls": "해당 URL",
            "pattern": "경로 패턴", "pages": "페이지", "order": "순서",
            "action": "할 일", "basis": "근거", "access": "서버 접근",
            "slot": "칸", "verdict": "판정", "evidence": "근거", "term": "용어",
            "meaning": "뜻", "metric": "지표", "where": "어디서", "cycle": "주기",
            "url": "URL", "status": "상태", "titlelen": "title 길이",
            "desclen": "설명 길이", "ld": "JSON-LD", "chars": "본문 글자수",
        },
        "need_server": "필요", "no_server": "콘텐츠 편집으로 가능",
        "unknown": "미확인",
        "no_findings": "이 레인에서 발견된 문제 없음.",
        "no_strength": "측정으로 확인된 강점이 없다. 로드맵 1번부터 시작한다.",
        "more_urls": "해당 URL 전체 보기 (%d개)",
        "page_table": "페이지별 측정값 (최대 %d개)",
        "asset_note": "네 칸 중 데이터로 판정할 수 있는 것은 “속성”뿐이다. "
                      "나머지 셋은 사이트 밖이거나 사람이 정해야 하는 값이라 미확인으로 둔다.",
        "measure_rules_title": "측정 규칙 여섯 가지",
        "baseline_title": "기준선 — 손대기 전에 먼저 남긴다",
        "lane_note": {
            "SEO": "도달 층. 크롤러가 읽고 색인할 수 있는가. 여기가 비면 위층 작업은 도달하지 않는다.",
            "AEO": "인용 층. 검색결과 상단 답변 박스에 우리 문장이 뽑혀 나가는가.",
            "GEO": "인용 층. ChatGPT·Gemini·Claude·Perplexity가 답을 만들 때 우리를 근거로 쓰는가.",
            "LLMO": "각인 층. 검색을 거치지 않아도 모델이 우리를 하나의 대상으로 아는가.",
            "NEO": "인용 층(네이버). 네이버 색인과 AI 브리핑 출처에 우리가 있는가.",
            "KEO": "인용 층(다음·카카오). 다음 웹검색과 AI 요약 출처에 우리가 있는가. 네이버와 크롤러·소유확인·등록 절차가 모두 달라 따로 본다.",
            "reputation": "각인 층. 제3자가 우리를 어떻게 설명하는가. 사이트 밖이라 크롤로는 잴 수 없다 — 사람이 점검한다.",
        },
    },
    "en": {
        "eyebrow": "Phase 0 — search and AI accessibility audit",
        "doc_title": "%s search and AI-citation audit",
        "meta": "As of %s · %d pages crawled · su-presence",
        "theme": "Toggle theme",
        "prev": "Previous", "next": "Next",
        "footer": "Every number here was measured directly from HTML received without JavaScript. "
                  "Anything not measured is left as “unknown” rather than estimated.",
        "titles": ["Summary", "Site shape", "Lane detail", "Strengths",
                   "Citation assets", "Roadmap", "Measurement plan", "Glossary"],
        "ledes": [
            "Six lanes, one verdict. Click a chip to jump to that lane.",
            "What the crawler actually reached, and how it is shaped.",
            "Per lane: verdict, measured evidence, and what it means.",
            "What already works. Leave it alone.",
            "What an AI would cite us for, if it cited us.",
            "What to do, in what order. Generated from the findings.",
            "“Fixed it” is half the job. When you re-measure is the other half.",
            "Every term in this report, in one line.",
        ],
        "sev": {"critical": "Critical", "warn": "Warning", "info": "Note"},
        "status": {"ok": "OK", "warn": "Warning", "bad": "Broken", "na": "Unknown"},
        "verdict": "Crawled %s with a crawler's eye across %d pages. %d critical, %d warnings, %d notes.",
        "verdict_first": "Do this first",
        "verdict_clean": "No critical defects found.",
        "th": {
            "item": "Item", "value": "Value", "sev": "Severity", "code": "Code",
            "what": "What was measured", "urls": "Affected URLs",
            "pattern": "Path pattern", "pages": "Pages", "order": "Order",
            "action": "Action", "basis": "Basis", "access": "Server access",
            "slot": "Slot", "verdict": "Verdict", "evidence": "Evidence", "term": "Term",
            "meaning": "Meaning", "metric": "Metric", "where": "Where", "cycle": "Cycle",
            "url": "URL", "status": "Status", "titlelen": "Title length",
            "desclen": "Description length", "ld": "JSON-LD", "chars": "Body chars",
        },
        "need_server": "Required", "no_server": "Content edit is enough",
        "unknown": "Unknown",
        "no_findings": "Nothing found in this lane.",
        "no_strength": "No strength confirmed by measurement. Start at roadmap item 1.",
        "more_urls": "Show all affected URLs (%d)",
        "page_table": "Per-page measurements (up to %d)",
        "asset_note": "Only the “subject” slot can be judged from crawl data. "
                      "The other three live outside the site or require a human decision, so they stay unknown.",
        "measure_rules_title": "Six measurement rules",
        "baseline_title": "Baseline — record it before touching anything",
        "lane_note": {
            "SEO": "Reach layer. Can crawlers read and index us at all? If this is empty, nothing above it arrives.",
            "AEO": "Citation layer. Does our sentence get pulled into the answer box?",
            "GEO": "Citation layer. Do ChatGPT, Gemini, Claude and Perplexity use us as evidence?",
            "LLMO": "Recall layer. Does the model know us as one entity without searching?",
            "NEO": "Citation layer, Naver. Are we in Naver's index and AI Briefing sources?",
            "KEO": "Citation layer, Daum/Kakao. Are we in Daum web search and AI summary sources? Different crawlers, ownership proof and registration than Naver, so it is tracked separately.",
            "reputation": "Recall layer. How third parties describe us. Outside the site, so a crawl cannot measure it — a human checks.",
        },
    },
}

# 데이터로 다시 쓰는 영문 메시지 (audit.json의 message는 국문 정본이다)
MSG_EN = {
    "NOINDEX": "{count} pages restrict indexing — check whether exclusion is intentional.",
    "TITLE_DUPLICATE": "{pages} pages share a title — check their distinct purpose and content.",
    "TITLE_MISSING": "{count} pages have no title.",
    "TITLE_TOO_LONG": "{count} titles exceed the recommended length (25-30 KO / 50-60 EN) and get truncated.",
    "TITLE_TOO_SHORT": "{count} titles are far too short.",
    "DESC_DUPLICATE": "{pages} pages share a meta description — the mark of one template stamped everywhere.",
    "DESC_MISSING": "{count} pages have no meta description.",
    "DESC_TOO_LONG": "{count} descriptions exceed the recommended length (70-80 KO / 150-160 EN).",
    "DESC_TOO_SHORT": "{count} descriptions are far too short.",
    "JSONLD_MISSING": "{count} pages carry no JSON-LD at all.",
    "FAQ_MISSING": "No FAQPage/QAPage JSON-LD found. This is optional and not an AI citation requirement.",
    "ORG_JSONLD_MISSING": "No Organization/LocalBusiness JSON-LD — nothing anchors the entity.",
    "ORG_JSONLD_SCATTERED": "{count} pages each declare their own Organization — collapse them onto one global @id.",
    "CANONICAL_MISSING": "{count} pages have no canonical.",
    "CANONICAL_NOT_SELF": "{count} canonicals do not point at their own page — confirm the consolidation is intended.",
    "H1_MISSING": "{count} pages have no h1.",
    "H1_MULTIPLE": "{count} pages carry more than one h1.",
    "THIN_TEXT": "{count} pages hold under 300 characters of body text — suspect client-side rendering.",
    "HTTP_ERROR": "{count} internally linked URLs returned an error.",
    "ROBOTS_MISSING": "robots.txt is missing or malformed.",
    "SITEMAP_NOT_DECLARED": "robots.txt declares no Sitemap: line.",
    "SITEMAP_MISSING": "No reachable sitemap — the index list is never handed to search engines.",
    "SITEMAP_CRAWL_MISMATCH": "Sitemap and crawl disagree: {only_in_sitemap} sitemap-only, {only_in_crawl} crawl-only.",
    "LLMS_TXT_MISSING": "/llms.txt is absent — no summary map for models yet.",
    "SOFT_404": "A missing address returns HTTP {status} instead of 404 — soft 404s burn crawl budget.",
    "REDIRECT_HOPS": "The home page takes {hops} redirect hops — cut it to one.",
    "ALT_HOST_UNREACHABLE": "The www/apex variant {host} is unreachable ({result}) — visitors and crawlers arriving there are lost.",
    "MIRROR_PUBLIC": "A staging mirror at {host} is publicly indexable (HTTP {status}) — if it gets cited instead of production, our description hardens around a dev server.",
    "MIRROR_PRESENT": "A staging mirror at {host} is reachable (HTTP {status}). robots blocks it, but links and citations still leak — close it with auth or an IP allowlist.",
    "MIRROR_WILDCARD_DNS": "Every subdomain answers — this looks like wildcard DNS, so mirror detection cannot be trusted. Have a human check the DNS config.",
    "NAVER_VERIFY_MISSING": "No naver-site-verification meta — Search Advisor may not be connected.",
    "AI_CRAWLER_BLOCKED": "Search or retrieval bots are restricted — review the affected engine's access policy.",
    "AI_CRAWLER_PARTIAL": "AI crawlers are partially restricted — read the rules directly.",
    "AI_CRAWLER_UNDECLARED": "AI crawlers are not declared in robots.txt — allowed by default, but left to chance.",
    "NAVER_CRAWLER_BLOCKED": "Naver's search crawler is blocked — the whole NEO lane is shut.",
    "DAUM_CRAWLER_BLOCKED": "Daum's search crawler is blocked — the whole KEO lane is shut.",
    "DAUM_CRAWLER_UNDECLARED": "Daum crawler UAs are not declared in robots.txt — Daumoa and DAUM are separate tokens, so naming only one leaves the other to chance.",
}

# 권고 로드맵 규칙: code -> (순서, 국문 조치, 영문 조치, 서버 접근 필요)
ROADMAP = {
    "MIRROR_PUBLIC": (0, "개발·스테이징 미러를 인증·IP 제한으로 닫는다. 색인된 적이 있으면 제거 요청까지 한다.",
                      "Close the staging mirror with auth or an IP allowlist; request removal if it was ever indexed.", True),
    "NOINDEX": (1, "색인하려는 페이지인지 확인하고, 의도하지 않은 noindex만 수정한다.",
                "Confirm indexing intent; remove only unintended noindex directives.", True),
    "NAVER_CRAWLER_BLOCKED": (2, "robots.txt에서 Yeti 차단을 푼다.",
                              "Unblock Yeti in robots.txt.", True),
    "DAUM_CRAWLER_BLOCKED": (2, "robots.txt에서 Daumoa·DAUM 차단을 푼다 — 둘은 별개 토큰이다.",
                             "Unblock both Daumoa and DAUM in robots.txt — they are separate tokens.", True),
    "AI_CRAWLER_BLOCKED": (3, "목표 엔진의 검색·열람 정책을 확인한다. 학습 허용 여부는 별도로 결정한다.",
                           "Review search/retrieval access for target engines separately from training consent.", True),
    "THIN_TEXT": (4, "원시 HTML과 렌더링 본문을 대조하고 필요한 콘텐츠를 SSR 또는 사전 렌더링으로 제공한다.",
                  "Compare raw and rendered HTML; consider SSR or pre-rendering for essential content.", True),
    "SITEMAP_MISSING": (5, "검사 범위를 확인하고 색인 대상인 최종 canonical URL로 사이트맵을 만든다.",
                        "Confirm crawl coverage and publish verified, indexable canonical URLs in the sitemap.", True),
    "SITEMAP_NOT_DECLARED": (6, "robots.txt에 Sitemap: 줄을 추가한다.",
                             "Add the Sitemap: line to robots.txt.", True),
    "ROBOTS_MISSING": (6, "robots.txt를 만든다 (ops/crawlers.md의 명시형 템플릿).",
                       "Create robots.txt from the explicit template in ops/crawlers.md.", True),
    "SITEMAP_CRAWL_MISMATCH": (7, "사이트맵과 실제 URL 목록을 맞춘다 — 새 콘텐츠 유형을 넣는 것까지가 출시다.",
                               "Reconcile sitemap and live URLs — shipping includes adding the new type.", True),
    "HTTP_ERROR": (8, "오류를 내는 내부 링크를 고친다.",
                   "Fix internal links that return errors.", False),
    "SOFT_404": (9, "없는 주소는 200이 아니라 404를 내게 한다.",
                 "Make missing addresses return 404, not 200.", True),
    "ALT_HOST_UNREACHABLE": (10, "www↔apex 양쪽이 열리게 하고 한쪽을 1홉 리다이렉트로 모은다.",
                             "Serve both www and apex, folding one into the other in a single hop.", True),
    "REDIRECT_HOPS": (11, "홈 리다이렉트를 1홉으로 줄인다.",
                      "Cut the home redirect chain to one hop.", True),
    "TITLE_MISSING": (12, "title이 없는 페이지에 title을 넣는다.",
                      "Add a title to pages that have none.", False),
    "TITLE_DUPLICATE": (13, "중복 title을 페이지마다 다르게 쓴다 (한글 25~30자).",
                        "Make duplicate titles unique per page (25-30 chars in Korean).", False),
    "DESC_MISSING": (14, "meta description을 채운다 (한글 70~80자).",
                     "Fill in the meta description (70-80 chars in Korean).", False),
    "DESC_DUPLICATE": (15, "템플릿으로 찍힌 설명을 페이지별로 다시 쓴다.",
                       "Rewrite template-stamped descriptions per page.", False),
    "TITLE_TOO_LONG": (16, "권장 길이를 넘는 title을 줄인다.",
                       "Trim titles past the recommended length.", False),
    "TITLE_TOO_SHORT": (16, "지나치게 짧은 title을 보강한다.",
                        "Flesh out titles that are far too short.", False),
    "DESC_TOO_LONG": (17, "긴 설명을 줄인다.", "Trim long descriptions.", False),
    "DESC_TOO_SHORT": (17, "짧은 설명을 보강한다.", "Flesh out short descriptions.", False),
    "H1_MISSING": (18, "페이지마다 h1을 하나 둔다.", "Give each page one h1.", False),
    "H1_MULTIPLE": (19, "h1을 하나로 정리한다.", "Reduce to a single h1.", False),
    "CANONICAL_MISSING": (20, "canonical을 자기 URL로 건다.",
                          "Add a self-referencing canonical.", True),
    "CANONICAL_NOT_SELF": (21, "자기참조가 아닌 canonical이 의도한 통합인지 확인한다.",
                           "Confirm non-self canonicals are the intended consolidation.", True),
    "JSONLD_MISSING": (22, "페이지 유형별 JSON-LD를 넣는다 (Article·Product·BreadcrumbList).",
                       "Add JSON-LD per page type (Article, Product, BreadcrumbList).", False),
    "FAQ_MISSING": (23, "실제 FAQ 콘텐츠가 있는 경우에만 적합한 구조화 데이터를 검토한다. AI 인용의 필수 조건은 아니다.",
                    "Consider FAQ structured data only for real FAQs; it is not required for AI citations.", False),
    "ORG_JSONLD_MISSING": (24, "Organization JSON-LD를 전역 @id 하나로 선언한다.",
                           "Declare Organization JSON-LD once, under one global @id.", False),
    "ORG_JSONLD_SCATTERED": (25, "흩어진 Organization 선언을 전역 @id 하나로 모은다.",
                             "Collapse scattered Organization declarations onto one @id.", False),
    "AI_CRAWLER_PARTIAL": (26, "부분 제한 규칙을 직접 읽고 의도한 제한인지 확인한다.",
                           "Read the partial rules and confirm the restriction is intended.", True),
    "AI_CRAWLER_UNDECLARED": (27, "robots.txt에 AI 크롤러 정책을 명시한다 — 우연에 맡기지 않는다.",
                              "Declare AI crawler policy explicitly in robots.txt.", True),
    "NAVER_VERIFY_MISSING": (28, "네이버 서치어드바이저에 사이트를 등록하고 소유확인 메타를 넣는다.",
                             "Register with Naver Search Advisor and add the verification meta.", True),
    "LLMS_TXT_MISSING": (29, "llms.txt를 만들어 핵심 문서 지도를 준다 (lanes/geo.md).",
                         "Publish llms.txt as a map of key documents (lanes/geo.md).", True),
}

ROADMAP.update({
    "CRAWL_INCOMPLETE": (0, "미검사 URL과 중단 사유를 해결한 뒤 재진단한다. 기존 사이트맵을 축소 교체하지 않는다.",
                         "Resolve incomplete coverage and re-audit before replacing the sitemap.", True),
    "SNIPPET_RESTRICTED": (1, "Google 답변 노출이 목표라면 의도하지 않은 nosnippet·max-snippet:0을 수정한다.",
                           "Review unintended nosnippet/max-snippet:0 when targeting Google AI answers.", True),
    "TRAINING_CRAWLER_BLOCKED": (30, "학습 제외 정책을 기록한다. 검색·열람 허용 여부는 별도로 확인한다.",
                                 "Record training opt-out separately from search/retrieval access.", False),
})

MEASURE_RULES = {
    "ko": [
        ("비로그인·시크릿 창으로 잰다",
         "로그인 상태면 대화 이력이 반영된 개인화 답이 나온다. 우리가 재려는 건 누구에게나 뜨는 답이다."),
        ("같은 질문을 같은 날 5~10회 반복한다",
         "생성 답변은 매번 다르다. 1회 결과는 표본이 아니다 — “떴다/안 떴다”가 아니라 10회 중 몇 회로 적는다."),
        ("인용된 URL을 남긴다",
         "빈도와 URL이 같이 있어야 “그 페이지를 강화”인지 “그 질문용 페이지가 없음”인지 갈린다."),
        ("브랜드 질의와 비브랜드 질의를 따로 센다",
         "섞어서 평균 내면 둘 다 안 보인다. 브랜드만 오르는 건 원래 알던 사람에게만 보이는 상태다."),
        ("엔진 우선순위를 지킨다",
         "전부 못 재면 ChatGPT와 구글 AI Overviews부터. 엔진 수를 늘리는 것보다 같은 조건으로 계속 재는 것이 먼저다."),
        ("변경 후 14일 뒤 재측정 날짜를 보고에 박는다",
         "검색 반영에는 시차가 있다. 집계 지연 때문에 최근 2~3일은 빼고 비교한다."),
    ],
    "en": [
        ("Query logged out, in a private window",
         "A logged-in session returns a personalized answer shaped by chat history. We are measuring the answer anyone gets."),
        ("Repeat the same question 5-10 times on the same day",
         "Generated answers vary. One query is not a sample — record 3-of-10, not yes/no."),
        ("Record the cited URL",
         "Frequency plus URL is what separates “strengthen that page” from “no page exists for that question”."),
        ("Count branded and non-branded queries separately",
         "Averaged together, neither is visible. Branded-only gains mean you are visible only to people who already knew you."),
        ("Respect engine priority",
         "If you cannot measure them all, start with ChatGPT and Google AI Overviews. Measuring two engines consistently beats measuring six once."),
        ("Put the 14-day re-measure date in the report",
         "Search reflects changes on a lag. Drop the most recent 2-3 days to avoid reporting lag."),
    ],
}

BASELINE = {
    "ko": [
        ("노출·클릭·평균순위", "Google Search Console (28일)", "주 1회"),
        ("Bing 노출·클릭", "Bing Webmaster Tools", "주 1회"),
        ("네이버 노출·클릭·검색어", "네이버 서치어드바이저", "주 1회"),
        ("색인 수", "GSC + site: 검색 (구글·빙 각각)", "주 1회"),
        ("AI 인용 O/X", "엔진 직접 질의 (아래 규칙대로)", "2주 1회"),
        ("AI 크롤러 방문", "서버 접근 로그 (UA 집계)", "주 1회"),
        ("AI 리퍼러 유입", "GA4 세션 소스 필터", "주 1회"),
    ],
    "en": [
        ("Impressions, clicks, avg position", "Google Search Console (28d)", "Weekly"),
        ("Bing impressions and clicks", "Bing Webmaster Tools", "Weekly"),
        ("Naver impressions, clicks, queries", "Naver Search Advisor", "Weekly"),
        ("Indexed page count", "GSC + site: search (Google and Bing)", "Weekly"),
        ("AI citation yes/no", "Direct engine queries (rules below)", "Biweekly"),
        ("AI crawler visits", "Server access log (UA tally)", "Weekly"),
        ("AI referral traffic", "GA4 session-source filter", "Weekly"),
    ],
}


# ─────────────────────────────────────────────────────────── 용어 주석

def load_glossary(lang: str) -> dict:
    with open(GLOSSARY, encoding="utf-8") as fh:
        return json.load(fh).get(lang, {})


def make_annotator(gloss: dict):
    if not gloss:
        return lambda text: escape(text)
    terms = sorted(gloss, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(t) for t in terms))

    def annotate(text: str) -> str:
        used = set()
        safe = escape(text)

        def sub(match):
            term = match.group(0)
            if term in used:
                return term
            used.add(term)
            return '<span class="t" tabindex="0" data-d="%s">%s</span>' % (
                escape(gloss[term], quote=True), term)

        return pattern.sub(sub, safe)

    return annotate


# ─────────────────────────────────────────────────────────── HTML 조각

def table(headers, rows, caption=None, min_width=None):
    head = "".join("<th%s>%s</th>" % (' class="num"' if h[0] == "#" else "", escape(h.lstrip("#")))
                   for h in headers)
    body = "".join("<tr>%s</tr>" % "".join(row) for row in rows)
    cap = "<caption>%s</caption>" % caption if caption else ""
    style = ' style="min-width:%dpx"' % min_width if min_width else ""
    return ('<div class="tablewrap"><table%s>%s<thead><tr>%s</tr></thead>'
            "<tbody>%s</tbody></table></div>" % (style, cap, head, body))


def td(value, cls=""):
    return "<td%s>%s</td>" % (' class="%s"' % cls if cls else "", value)


def pill(kind, text):
    return '<span class="pill %s">%s</span>' % (kind, escape(text))


def url_list(urls, label):
    if not urls:
        return ""
    items = "".join('<li class="url">%s</li>' % escape(u) for u in urls)
    return ('<details class="more"><summary>%s</summary><ul class="plain">%s</ul></details>'
            % (escape(label % len(urls)), items))


# ─────────────────────────────────────────────────────────── 섹션

def finding_message(finding: dict, lang: str) -> str:
    if lang == "ko":
        return finding["message"]
    template = MSG_EN.get(finding["code"])
    if not template:
        return finding["message"]
    data = dict(finding.get("data") or {})
    data.setdefault("count", len(finding.get("urls") or []))
    try:
        return template.format(**data)
    except (KeyError, IndexError):
        return finding["message"]


def section_summary(report, lab, ann, lang):
    findings = report["findings"]
    counts = Counter(f["severity"] for f in findings)
    host = report["target"]["host"]
    line = lab["verdict"] % (host, report["stats"]["pages_crawled"],
                             counts["critical"], counts["warn"], counts["info"])
    ordered = sorted(findings, key=lambda f: (SEV_ORDER[f["severity"]],
                                              ROADMAP.get(f["code"], (99,))[0]))
    if ordered and ordered[0]["severity"] == "critical":
        rule = ROADMAP.get(ordered[0]["code"])
        first = rule[1] if (rule and lang == "ko") else (rule[2] if rule else ordered[0]["code"])
        tail = "<strong>%s:</strong> %s" % (escape(lab["verdict_first"]), ann(first))
    else:
        tail = escape(lab["verdict_clean"])
    verdict = '<div class="card verdict">%s<br>%s</div>' % (escape(line), tail)

    chips = []
    for lane in LANES:
        cell = report["scorecard"].get(lane) or {"status": "na", "evidence": []}
        n = len(cell["evidence"])
        detail = ("%d" % n) if cell["status"] != "na" else "—"
        chips.append(
            '<li><a class="chip %s" href="#p3"><span class="lane">%s</span>'
            '<span class="st">%s · %s</span></a></li>'
            % (cell["status"], escape(lane), escape(lab["status"][cell["status"]]), detail))
    chip_html = '<ul class="chips">%s</ul>' % "".join(chips)

    rows = []
    for f in ordered:
        rows.append([
            td(pill({"critical": "bad", "warn": "warn", "info": "info"}[f["severity"]],
                    lab["sev"][f["severity"]])),
            td(escape(f["lane"])),
            td('<code>%s</code>' % escape(f["code"])),
            td(ann(finding_message(f, lang))),
        ])
    body = table([lab["th"]["sev"], "Lane", lab["th"]["code"], lab["th"]["what"]], rows) \
        if rows else '<div class="note">%s</div>' % escape(lab["no_findings"])
    return evidence_scope(report, lang) + verdict + chip_html + body


def evidence_scope(report, lang):
    coverage = report.get("coverage") or {}
    complete = coverage.get("complete")
    label = (("발견한 URL 검사 완료" if complete is True else "검사 범위 불완전 / 미확인")
             if lang == "ko" else ("Discovered URLs checked" if complete is True else "Incomplete / unknown coverage"))
    explanation = ("이 점수는 기술 접근성 검사다. 실제 검색 색인·AI 인용·유입 성과는 별도 측정해야 한다. "
                   "검사 완료도 사이트의 모든 URL을 발견했다는 뜻은 아니다."
                   if lang == "ko" else
                   "These are technical checks. Search indexing, AI citations and traffic require separate measurements. "
                   "Completion covers discovered URLs, not every URL that may exist.")
    parts = [pill("ok" if complete is True else "warn", label), "<p>%s</p>" % escape(explanation)]
    if coverage:
        parts.append('<p class="mono">%s</p>' % escape(
            ("요청 %s · 대기 %s · robots 제외 %s · 상한 %s" if lang == "ko" else
             "Fetched %s · queued %s · robots-excluded %s · limit %s") %
            tuple(coverage.get(k, "—") for k in ("pages_fetched", "queued_remaining", "blocked_count", "max_pages"))))
    reasons = coverage.get("reasons") or []
    if reasons:
        parts.append('<p>%s</p>' % escape("; ".join(str(r) for r in reasons)))
    return '<div class="card scope">%s</div>' % "".join(parts)


def section_shape(report, lab, ann, lang):
    site, stats = report["site"], report["stats"]
    hygiene = site["hygiene"]
    sm_urls = sum(s["url_count"] for s in site["sitemaps"] if s["status"] == 200 and not s["is_index"])
    pairs = [
        (lab["th"]["pages"] if lang == "en" else "크롤 페이지", stats["pages_crawled"]),
        ("Unique titles" if lang == "en" else "고유 title", stats["unique_titles"]),
        ("Unique descriptions" if lang == "en" else "고유 meta description", stats["unique_descriptions"]),
        ("Pages with JSON-LD" if lang == "en" else "JSON-LD 보유 페이지", stats["pages_with_jsonld"]),
        ("Pages with noindex" if lang == "en" else "noindex 페이지", stats["pages_noindex"]),
        ("URLs declared in sitemap" if lang == "en" else "사이트맵이 신고한 URL", sm_urls),
        ("Sitemap-only URLs" if lang == "en" else "사이트맵에만 있는 URL",
         len(site["sitemap_vs_crawl"]["only_in_sitemap"])),
        ("Crawl-only URLs" if lang == "en" else "크롤에만 있는 URL",
         len(site["sitemap_vs_crawl"]["only_in_crawl"])),
        ("robots.txt", "HTTP %s" % site["robots"]["status"]),
        ("/llms.txt", "HTTP %s" % site["llms"].get("llms.txt")),
        ("404 probe" if lang == "en" else "404 프로브", "HTTP %s" % hygiene["probe_404"]),
        ("Home redirect hops" if lang == "en" else "홈 리다이렉트 홉", hygiene["redirect_hops"]),
        ("Home response" if lang == "en" else "홈 응답 시간", "%s ms" % hygiene["home_response_ms"]),
        ("Host variant" if lang == "en" else "도메인 변형",
         hygiene["alt_host"]["result"] if hygiene["alt_host"]["result"] == "na"
         else "%s → %s" % (hygiene["alt_host"]["host"], hygiene["alt_host"]["result"])),
    ]
    stat_rows = [[td(ann(str(k))), td(escape(str(v)), "num")] for k, v in pairs]
    out = table([lab["th"]["item"], lab["th"]["value"]], stat_rows)

    seg = Counter()
    for page in report["pages"]:
        if page["status"] != 200:
            continue
        parts = [p for p in urllib.parse.urlsplit(page["url"]).path.split("/") if p]
        seg["/%s/" % parts[0] if parts else "/"] += 1
    pat_rows = [[td('<span class="url">%s</span>' % escape(k)), td(str(v), "num")]
                for k, v in seg.most_common(25)]
    if pat_rows:
        out += table([lab["th"]["pattern"], "#" + lab["th"]["pages"]], pat_rows)

    cap = 200
    page_rows = []
    for page in report["pages"][:cap]:
        page_rows.append([
            td('<span class="url">%s</span>' % escape(page["url"])),
            td(escape(str(page["status"] or (page.get("error") or "ERR"))), "num"),
            td(str(len(page["title"] or "")), "num"),
            td(str(len(page["meta_description"] or "")), "num"),
            td(str(page["jsonld_count"]), "num"),
            td(str(page["text_chars"]), "num"),
        ])
    if page_rows:
        out += table(
            [lab["th"]["url"], "#" + lab["th"]["status"], "#" + lab["th"]["titlelen"],
             "#" + lab["th"]["desclen"], "#" + lab["th"]["ld"], "#" + lab["th"]["chars"]],
            page_rows, caption=lab["page_table"] % cap, min_width=700)
    return out


def section_lanes(report, lab, ann, lang):
    out = []
    for lane in LANES:
        cell = report["scorecard"].get(lane) or {"status": "na", "evidence": []}
        out.append('<h3>%s %s</h3>' % (escape(lane), pill(cell["status"], lab["status"][cell["status"]])))
        out.append('<p class="lede">%s</p>' % ann(lab["lane_note"].get(lane, lane)))
        mine = [f for f in report["findings"] if f["lane"] == lane]
        if not mine:
            out.append('<div class="note">%s</div>' % escape(lab["no_findings"]))
            continue
        rows = []
        for f in sorted(mine, key=lambda x: SEV_ORDER[x["severity"]]):
            rows.append([
                td(pill({"critical": "bad", "warn": "warn", "info": "info"}[f["severity"]],
                        lab["sev"][f["severity"]])),
                td('<code>%s</code>' % escape(f["code"])),
                td(ann(finding_message(f, lang)) + url_list(f["urls"], lab["more_urls"])),
            ])
        out.append(table([lab["th"]["sev"], lab["th"]["code"], lab["th"]["what"]], rows))

    policies = report["site"]["robots"]["policies"]
    rows = [[td("<code>%s</code>" % escape(ua)), td(escape(policy))]
            for ua, policy in policies.items()]
    out.append("<h3>robots.txt %s</h3>" % ("policy" if lang == "en" else "실효 정책"))
    out.append(table(["User-agent", lab["th"]["verdict"]], rows))
    return "".join(out)


def section_strengths(report, lab, ann, lang):
    site, stats = report["site"], report["stats"]
    codes = {f["code"] for f in report["findings"]}
    ok200 = [p for p in report["pages"] if p["status"] == 200]
    total = max(1, len(ok200))
    items = []
    if not ok200:
        return '<div class="note">%s</div>' % escape(lab["no_strength"])

    def keep(cond, ko, en):
        if cond:
            items.append(ko if lang == "ko" else en)

    keep(stats["pages_noindex"] == 0,
         "검사한 HTML 페이지에서 noindex가 발견되지 않았다. 실제 색인 여부는 검색 도구에서 확인한다.",
         "No noindex found in checked HTML pages. Confirm actual indexing with search tools.")
    live = [s["url"] for s in site["sitemaps"] if s["status"] == 200 and s.get("parsed") is True]
    keep("SITEMAP_MISSING" not in codes and live,
         "사이트맵이 살아 있다: %s" % ", ".join(live),
         "A sitemap responds: %s" % ", ".join(live))
    keep(bool(site["robots"]["sitemap_declared"]),
         "robots.txt가 사이트맵을 선언하고 있다.",
         "robots.txt declares the sitemap.")
    policies = site["robots"].get("policies") or {}
    search_bots = ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot")
    keep(all(policies.get(ua, "").endswith("allow") for ua in search_bots),
         "확인한 AI 검색 봇에 robots 전면 차단이 없다. 서버/WAF와 실제 방문 여부는 별도 확인한다.",
         "Checked AI search bots have allow policies. Server/WAF access and actual visits require separate checks.")
    keep(all(policies.get(ua, "").endswith("allow") for ua in ("Yeti", "Daumoa")),
         "Yeti·Daumoa의 robots 허용 정책을 확인했다.",
         "Yeti and Daumoa have allow policies in robots.txt.")
    keep(stats["pages_with_jsonld"] > total * 0.5,
         "페이지 %d개 중 %d개가 JSON-LD를 갖고 있다." % (total, stats["pages_with_jsonld"]),
         "%d of %d pages carry JSON-LD." % (stats["pages_with_jsonld"], total))
    keep(all(p.get("text_chars", 0) >= 300 for p in ok200),
         "검사한 HTML 페이지에 300자 이상의 텍스트가 있다. 렌더링 본문·콘텐츠 품질은 별도 확인한다.",
         "Checked HTML pages contain at least 300 text characters; rendered content and quality need separate checks.")
    keep("TITLE_DUPLICATE" not in codes and stats["unique_titles"] > 1,
         "검사한 페이지의 title이 서로 다르다.",
         "Checked pages have distinct titles.")
    keep("DESC_DUPLICATE" not in codes and stats["unique_descriptions"] > 1,
         "meta description이 페이지마다 다르다.",
         "Meta descriptions are unique per page.")
    keep(all(p.get("canonical") for p in ok200),
         "검사한 HTML 페이지에 canonical이 있다.",
         "Checked HTML pages carry a canonical.")
    keep(site["hygiene"].get("probe_404") == 404,
         "없는 주소가 정확히 404를 낸다.",
         "Missing addresses return a real 404.")
    keep("REDIRECT_HOPS" not in codes,
         "홈 리다이렉트가 1홉 이내다.",
         "The home page redirects at most once.")
    keep(site["llms"].get("llms.txt") == 200,
         "/llms.txt가 이미 있다.", "/llms.txt already exists.")
    keep(any(p.get("naver_site_verification") for p in ok200),
         "네이버 소유확인 메타가 들어가 있다.",
         "The Naver site-verification meta is in place.")

    if not items:
        return '<div class="note">%s</div>' % escape(lab["no_strength"])
    return '<ul class="plain">%s</ul>' % "".join("<li>%s</li>" % ann(i) for i in items)


def section_assets(report, lab, ann, lang):
    types = Counter()
    for page in report["pages"]:
        types.update(page["jsonld_types"])
    unknown = pill("na", lab["unknown"])

    if types:
        subject_v = pill("ok", lab["status"]["ok"])
        listed = ", ".join("%s(%d)" % (t, n) for t, n in types.most_common(12))
        subject_e = ("사이트가 스스로 선언한 엔티티 유형: %s" if lang == "ko"
                     else "Entity types the site declares: %s") % listed
    else:
        subject_v = unknown
        subject_e = ("선언된 구조화 데이터가 없다. 본문의 엔티티 설명과 실제 인용은 별도 확인한다."
                     if lang == "ko"
                     else "No structured data declared. Entity descriptions and actual citations require separate checks.")

    rows = [
        [td("<strong>%s</strong>" % ("속성" if lang == "ko" else "Subject")),
         td(subject_v), td(ann(subject_e))],
        [td("<strong>%s</strong>" % ("이유" if lang == "ko" else "Reason")),
         td(unknown),
         td(ann("이 사이트만 낼 수 있는 수치·사실이 무엇인가 — 크롤 데이터로는 판정할 수 없다. "
                "Phase 2(공식 답변 확정)에서 사람이 답한다."
                if lang == "ko" else
                "Which numbers only this site can publish — a crawl cannot judge this. "
                "A human answers it in Phase 2."))],
        [td("<strong>%s</strong>" % ("근거" if lang == "ko" else "Evidence")),
         td(unknown),
         td(ann("수치에 분모와 기준일이 붙어 있는지는 본문을 사람이 읽어야 안다. "
                "“96%”보다 “25건 중 24건”이 인용에서 살아남는다."
                if lang == "ko" else
                "Whether figures carry a denominator and an as-of date needs a human read. "
                "“24 of 25” survives citation better than “96%”."))],
        [td("<strong>%s</strong>" % ("평판" if lang == "ko" else "Reputation")),
         td(unknown),
         td(ann("제3자가 우리를 어떻게 설명하는지는 사이트 밖 표면이라 이 크롤로는 닿지 않는다. "
                "lanes/reputation.md 절차로 사람이 점검한다."
                if lang == "ko" else
                "How third parties describe us lives outside this site and beyond this crawl. "
                "A human checks it via lanes/reputation.md."))],
    ]
    return (table([lab["th"]["slot"], lab["th"]["verdict"], lab["th"]["evidence"]], rows)
            + '<div class="note">%s</div>' % lab["asset_note"])


def section_roadmap(report, lab, ann, lang):
    seen = {}
    for f in report["findings"]:
        rule = ROADMAP.get(f["code"])
        if not rule or f["code"] in seen:
            continue
        seen[f["code"]] = (rule, f)
    ordered = sorted(seen.values(), key=lambda pair: (SEV_ORDER[pair[1]["severity"]], pair[0][0]))
    if not ordered:
        return '<div class="note">%s</div>' % escape(lab["no_findings"])
    rows = []
    for n, (rule, f) in enumerate(ordered, 1):
        rows.append([
            td(str(n), "num"),
            td(ann(rule[1] if lang == "ko" else rule[2])),
            td(pill({"critical": "bad", "warn": "warn", "info": "info"}[f["severity"]],
                    lab["sev"][f["severity"]]) + " <code>%s</code>" % escape(f["code"])),
            td(pill("warn", lab["need_server"]) if rule[3] else pill("ok", lab["no_server"])),
        ])
    return table(["#" + lab["th"]["order"], lab["th"]["action"], lab["th"]["basis"],
                  lab["th"]["access"]], rows)


def section_measure(report, lab, ann, lang):
    rows = [[td(ann(m)), td(ann(w)), td(escape(c))] for m, w, c in BASELINE[lang]]
    out = "<h3>%s</h3>" % escape(lab["baseline_title"])
    out += table([lab["th"]["metric"], lab["th"]["where"], lab["th"]["cycle"]], rows)
    out += "<h3>%s</h3>" % escape(lab["measure_rules_title"])
    items = "".join("<li><strong>%s</strong><br>%s</li>" % (ann(title), body)
                    for title, body in MEASURE_RULES[lang])
    out += '<ul class="plain">%s</ul>' % items
    return out


def section_glossary(report, lab, ann, lang, gloss):
    rows = [[td("<strong>%s</strong>" % escape(term)), td(escape(desc))]
            for term, desc in sorted(gloss.items(), key=lambda kv: kv[0].lower())]
    return table([lab["th"]["term"], lab["th"]["meaning"]], rows)


# ─────────────────────────────────────────────────────────── 조립

def render(report: dict, lang: str) -> str:
    lab = L[lang]
    gloss = load_glossary(lang)
    ann = make_annotator(gloss)
    host = report["target"]["host"]

    titles = lab["titles"]
    tabs = "".join('<a href="#p%d">%s</a>' % (i + 1, escape(t)) for i, t in enumerate(titles))

    values = {
        "html_lang": "ko" if lang == "ko" else "en",
        "doc_title": escape(lab["doc_title"] % host),
        "eyebrow": lab["eyebrow"],
        "meta_line": escape(lab["meta"] % (report["generated_at"].replace("T", " "),
                                    report["stats"]["pages_crawled"])),
        "theme_label": lab["theme"],
        "prev_label": lab["prev"], "next_label": lab["next"],
        "footer_note": lab["footer"],
        "tabs": tabs,
        "titles_json": json.dumps(titles, ensure_ascii=False),
        "p1": section_summary(report, lab, ann, lang),
        "p2": section_shape(report, lab, ann, lang),
        "p3": section_lanes(report, lab, ann, lang),
        "p4": section_strengths(report, lab, ann, lang),
        "p5": section_assets(report, lab, ann, lang),
        "p6": section_roadmap(report, lab, ann, lang),
        "p7": section_measure(report, lab, ann, lang),
        "p8": section_glossary(report, lab, ann, lang, gloss),
    }
    for i, title in enumerate(titles, 1):
        values["t%d" % i] = escape(title)
        values["l%d" % i] = lab["ledes"][i - 1]

    with open(TEMPLATE, encoding="utf-8") as fh:
        return Template(fh.read()).safe_substitute(values)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="audit.json → HTML 보고서")
    ap.add_argument("audit", help="crawl.py가 만든 audit.json 경로")
    ap.add_argument("--lang", choices=("ko", "en"), default="ko")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    with open(args.audit, encoding="utf-8") as fh:
        report = json.load(fh)
    if not str(report.get("schema", "")).startswith("su-multi-geo/audit/"):
        sys.stderr.write("audit.json 스키마가 아니다: %s\n" % report.get("schema"))
        return 1

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.audit)), "report.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(report, args.lang))
    print("보고서 저장: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
