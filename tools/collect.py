#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect.py — 인용 측정 자동 수집기.

세 가지 접근을 한 파이프라인으로 묶는다 (ops/coverage.md):

  무인          네이버 · 다음 · 구글 AI개요    스크립트가 혼자 돈다
  브라우저 경유  ChatGPT · Gemini · Claude · Perplexity
                사람은 **로그인만** 하고, 조작·판정은 에이전트가 한다
  수동          GSC · 서치어드바이저 색인 수    계정 화면을 사람이 본다

결과는 tools/measure.py 와 **같은 스키마**로 같은 log.jsonl 에 쓴다. 별도 로그를 만들지 않는다.
따라서 `python tools/measure.py report <audit.json>` 이 그대로 집계·보고한다.

핵심 규칙 (ops/measure.md):
  · 못 잰 것은 0이 아니다. outcome=unmeasured 로 남기고 분모에서 뺀다.
  · 축소 응답으로 0을 기록하지 않는다 — 응답 건전성 게이트가 막는다.
  · 로그인 벽을 만나면 멈추지 말고 **무엇에 로그인해야 하는지 출력**하고 넘긴다.

사용:
  python tools/collect.py <audit.json>                 무인 수집
  python tools/collect.py <audit.json> --coverage      누락 점검만
  python tools/collect.py <audit.json> --browser       로그인 안내 + 자리 예약
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import measure  # noqa: E402  (ENGINES·make_row·append_rows·load_queries 를 재사용한다)

# Windows 콘솔 기본 코드페이지는 cp949다. 한글 대시 하나에 프로세스가 죽으면
# 수집해 놓은 행이 통째로 날아간다 — 실제로 그렇게 잃어봤다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
UA_MO = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

PAUSE = 3.0          # 반복 측정은 이 아래로 내리지 마라 — 아래 SANE_MIN 주석 참조

# 반복 요청을 몰아치면 검색엔진이 축소 응답을 준다. 그 응답으로 집계하면 "인용 0"이
# 찍히는데, 그건 엔진의 사실이 아니라 우리가 만든 0이다. 정상 SERP는 수백 KB다.
SANE_MIN = {"naver": 200_000, "daum": 150_000, "google": 100_000}

# 사람이 로그인만 해두면 에이전트가 조작할 수 있는 엔진.
# "수동"이 아니다 — 조작·판정은 에이전트가 한다 (ops/coverage.md).
BROWSER_ENGINES = {
    "chatgpt":    ("ChatGPT",    "https://chatgpt.com/?temporary-chat=true",
                   "임시 채팅 — 헤더에 '맞춤화 안 함'이 떠야 한다"),
    "gemini":     ("Gemini",     "https://gemini.google.com/app",
                   "임시 채팅 — 우상단 점선 원 안 연필 아이콘"),
    "claude":     ("Claude",     "https://claude.ai/new",
                   "시크릿 모드 — 우상단 유령 아이콘 (프로필 유래 서술은 집계 제외)"),
    "perplexity": ("Perplexity", "https://www.perplexity.ai/",
                   "로그인 필요 (2026-09 확인) — 비로그인은 '가입한 뒤 다시'로 막힌다. 스레드 URL은 공개라 캡처는 된다"),
}


class Throttled(RuntimeError):
    """축소 응답 — 측정값으로 쓰면 안 된다."""


# ───────────────────────────────────────────────────────────── HTTP

def _get(url, ua, referer=None, accept=None, timeout=30):
    """표준 라이브러리만으로 가져온다 (저장소 정책).

    Accept-Encoding 은 보내지 않는다. 압축을 요청하면 직접 풀어야 하고,
    br 로 받으면 본문이 통째로 깨진다 (ops/measure-playbook.md).
    """
    headers = {"User-Agent": ua, "Accept-Language": "ko-KR,ko;q=0.9"}
    if referer:
        headers["Referer"] = referer
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _gate(engine, html, q):
    floor = SANE_MIN[engine]
    if len(html) < floor:
        raise Throttled(
            "%s 응답 %s B < 기준 %s B — 스로틀링으로 보인다. 질의=%r. "
            "간격을 늘리거나 시간을 두고 다시 재라. 이 응답으로 0을 기록하면 안 된다."
            % (engine, format(len(html), ","), format(floor, ","), q))
    return html


def _chrome():
    for c in ("google-chrome", "chrome", "chromium"):
        w = shutil.which(c)
        if w:
            return w
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(c):
            return c
    return None


def _render(url, timeout=90):
    """헤드리스 브라우저의 렌더링 DOM.

    다음·구글은 단순 HTTP 로 받으면 축소 SERP 나 CAPTCHA 가 온다.
    정상 Chrome UA 를 주고 렌더링된 DOM 을 기준으로 삼는다.
    """
    ch = _chrome()
    if not ch:
        raise RuntimeError("Chrome 을 찾지 못했다 — 이 엔진은 헤드리스 브라우저가 필요하다.")
    prof = tempfile.mkdtemp()
    try:
        out = subprocess.run(
            [ch, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-blink-features=AutomationControlled",
             "--user-agent=" + UA_PC, "--lang=ko-KR",
             "--user-data-dir=" + prof, "--virtual-time-budget=15000",
             "--dump-dom", url],
            capture_output=True, timeout=timeout)
        return out.stdout.decode("utf-8", errors="replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)


# ─────────────────────────────────────────────────── 가시 텍스트 판정

def visible(html):
    """script/style/주석을 걷어낸 '보이는 영역'.

    이걸 안 하면 JS 페이로드에 박힌 문자열이 노출로 잡힌다.
    실제로 남의 블로그 썸네일 URL 때문에 오탐이 났다.
    """
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def seen(html, host):
    return bool(re.search(re.escape(host), visible(html), re.I))


def cited_urls_in(urls, host):
    return [u for u in urls if host.lower() in u.lower()]


def domains_of(urls):
    out = []
    for u in urls:
        try:
            d = urllib.parse.urlparse(u).netloc
            if d.startswith("www."):
                d = d[4:]
            if d:
                out.append(d)
        except Exception:
            pass
    return sorted(set(out))


# ─────────────────────────────────────────────────────────── 엔진

def naver(q, host):
    """네이버 자연검색 + AI 브리핑. 모바일 기준 (네이버는 모바일 우선)."""
    url = "https://m.search.naver.com/search.naver?query=" + urllib.parse.quote(q)
    html = _gate("naver", _get(url, UA_MO), q)
    organic = seen(html, host)

    fired = bool(re.search(r'data-block-id="ai-briefing/', html))
    sources = []
    if fired:
        m = re.search(r'"apiURL"\s*:\s*"([^"]+)"', html)
        if m:
            api = m.group(1).encode().decode("unicode_escape")
            ref = "https://m.search.naver.com/search.naver?query=" + urllib.parse.quote(q)
            try:
                body = _get(api, UA_MO, referer=ref, accept="text/event-stream", timeout=45)
            except Exception:
                body = ""
            ev = None
            for line in body.splitlines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                    continue
                if not line.startswith("data:") or ev != "sources":
                    continue
                try:
                    arr = json.loads(line[5:].strip())
                except Exception:
                    continue
                if isinstance(arr, list):
                    sources += [x["url"] for x in arr
                                if isinstance(x, dict) and x.get("url")]
        else:
            m2 = re.search(r'"sources"\s*:\s*(\[.*?\])\s*[,}]', html, flags=re.S)
            if m2:
                try:
                    sources = [x.get("url", "") for x in json.loads(m2.group(1))
                               if isinstance(x, dict)]
                except Exception:
                    pass
    return organic, fired, [u for u in sources if u]


def daum(q, host):
    """다음 웹검색 + AI 요약. 단순 HTTP 는 축소 SERP 를 준다 — 렌더링 DOM 을 쓴다."""
    url = "https://search.daum.net/search?w=tot&q=" + urllib.parse.quote(q)
    html = _gate("daum", _render(url), q)
    organic = seen(html, host)
    fired = bool(re.search(r'disp-attr="AIO"|aioColl', html))
    sources = re.findall(r'"url"\s*:\s*"([^"]+)"\s*,\s*"gsid"', html) if fired else []
    return organic, fired, sources


def google(q, host):
    """구글 자연검색 + AI 개요. 헤드리스 기본 UA 는 CAPTCHA 를 받는다."""
    url = "https://www.google.com/search?hl=ko&gl=kr&q=" + urllib.parse.quote(q)
    html = _render(url)
    if re.search(r"recaptcha|비정상적인 트래픽", html, re.I):
        raise Throttled("google CAPTCHA — 사람이 확인해야 한다. 0으로 기록하지 않는다.")
    html = _gate("google", html, q)
    organic = seen(html, host)
    vis = visible(html)
    fired = "AI 개요" in vis
    sources = []
    if fired:
        raw = re.findall(r'href="(https?://[^"]+)"', html)
        sources = [u for u in raw
                   if "google." not in u and "gstatic" not in u][:40]
    return organic, fired, sources


# ops/coverage.md 의 레인 × 표면을 코드로 옮긴 것. 둘이 어긋나면 문서가 정본이다.
#   (레인, 표면, 로그 engine 키, 접근, 판정 근거)
SURFACES = (
    ("SEO",  "원시 HTML·메타·구조화데이터", None,         "무인",    "audit:pages"),
    ("SEO",  "robots.txt 실효 정책",       None,         "무인",    "audit:robots"),
    ("SEO",  "sitemap 존재·유효성",        None,         "무인",    "audit:sitemap"),
    ("SEO",  "스테이징·미러 공개 노출",     None,         "무인",    "audit:mirrors"),
    ("SEO",  "GSC·Bing 색인 수",          None,         "사람",    "human"),
    ("AEO",  "구글 AI 개요",              "google_aio",  "무인",    "log"),
    ("GEO",  "ChatGPT",                  "chatgpt",     "브라우저", "log"),
    ("GEO",  "Perplexity",               "perplexity",  "브라우저", "log"),
    ("GEO",  "Gemini",                   "gemini",      "브라우저", "log"),
    ("GEO",  "Claude",                   "claude",      "브라우저", "log"),
    ("LLMO", "학습데이터 각인 (검색 끔)",    None,         "브라우저", "log:nosearch"),
    ("LLMO", "엔티티 일관성 (상호·번호·주소)", None,       "사람",    "human"),
    ("NEO",  "네이버 자연검색·AI 브리핑",    "naver_ai",   "무인",    "log"),
    ("NEO",  "서치어드바이저 색인",         None,         "사람",    "human"),
    ("NEO",  "네이버 플레이스 NAP",        None,         "사람",    "human"),
    ("KEO",  "다음 웹검색·AI 요약",         "daum",       "무인",    "log"),
    ("KEO",  "다음 웹마스터도구",           None,         "사람",    "human"),
    ("KEO",  "카카오맵 NAP",              None,         "사람",    "human"),
    ("평판",  "리뷰·별점·채용 플랫폼",       None,         "사람",    "human"),
)


def _pad(text, width) -> str:
    """한글·한자는 터미널에서 두 칸을 먹는다. %-Ns 는 글자 수만 세어 열이 어긋난다."""
    text = str(text)
    used = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(1, width - used)


def _audit_state(kind, audit):
    site = audit.get("site") or {}
    if kind == "audit:pages":
        n = len(audit.get("pages") or [])
        return ("관측", "%d쪽" % n) if n else ("미측정", "크롤 결과 없음")
    if kind == "audit:robots":
        rb = site.get("robots") or {}
        return ("관측", "present=%s" % bool(rb.get("present"))) if rb else ("미측정", "")
    if kind == "audit:sitemap":
        sm = site.get("sitemaps") or []
        return ("관측", "%d개 후보" % len(sm)) if sm else ("미측정", "")
    if kind == "audit:mirrors":
        mir = ((site.get("hygiene") or {}).get("mirrors")) or {}
        if not mir.get("checked"):
            return ("미측정", "이 audit은 미러 프로브 이전 버전이다 — 다시 크롤하라")
        found = mir.get("found") or []
        return ("관측", ("%d건 발견: %s" % (len(found), ", ".join(m["host"] for m in found)))
                if found else "노출 없음")
    return ("미측정", "")


def coverage_report(audit, mdir) -> int:
    """표면마다 관측/미측정을 찍는다. **빈칸을 남긴 채 보고하지 않기 위한 장치다.**"""
    log = measure.load_log(os.path.join(mdir, "log.jsonl"))
    by_engine = {}
    for row in log:
        slot = by_engine.setdefault(row.get("engine"), {"observed": 0, "unmeasured": 0,
                                                        "error": 0, "nosearch": 0})
        slot[row.get("outcome", "unmeasured")] = slot.get(row.get("outcome"), 0) + 1
        if row.get("outcome") == "observed" and row.get("search_enabled") is False:
            slot["nosearch"] += 1

    print("")
    print("커버리지 — 레인 × 표면. 빈칸이 남으면 보고하지 않는다 (ops/coverage.md)")
    print("")
    print(" " + _pad("레인", 6) + _pad("표면", 30) + _pad("접근", 10) + _pad("상태", 8) + "근거")
    print(" " + "-" * 88)

    gaps = []
    for lane, name, key, access, kind in SURFACES:
        if kind.startswith("audit:"):
            state, why = _audit_state(kind, audit)
        elif kind == "human":
            state, why = "미측정", "사람이 계정·현장을 봐야 한다"
        else:
            slot = by_engine.get(key) if key else None
            if kind == "log:nosearch":
                hits = sum(v["nosearch"] for v in by_engine.values())
                state = "관측" if hits else "미측정"
                why = ("검색 끈 측정 %d행" % hits) if hits else "검색 끈 상태로 잰 행이 없다"
            elif not slot or not slot["observed"]:
                state = "미측정"
                why = ("예약 %d행 — 로그인 브라우저 필요" % slot["unmeasured"]) if slot else "측정 없음"
            else:
                state = "관측"
                why = "%d행" % slot["observed"]
                if slot["unmeasured"]:
                    why += " (+미측정 %d)" % slot["unmeasured"]
        if state == "미측정":
            gaps.append((lane, name, access, why))
        print(" " + _pad(lane, 6) + _pad(name, 30) + _pad(access, 10) + _pad(state, 8) + why)

    print("")
    if not gaps:
        print(" 빈칸 없음 — 모든 표면이 관측됐다.")
        return 0
    print(" 빈칸 %d개 — 아래를 채우거나 보고서에 '미측정'으로 명시하라. 0으로 적지 마라." % len(gaps))
    for lane, name, access, why in gaps:
        print("   " + _pad("[%s]" % lane, 8) + _pad(name, 30) + access + " · " + why)
    need = sorted({k for k in BROWSER_ENGINES
                   if not (by_engine.get(k) or {}).get("observed")})
    if need:
        login_request(need)
    return 0


UNATTENDED = (("naver_ai", "네이버", naver),
              ("daum", "다음", daum),
              ("google_aio", "구글", google))


# ─────────────────────────────────────────────────────────── 실행

def record_one(a, mdir, host, today) -> int:
    """브라우저에서 잰 한 건을 무인 수집분과 같은 로그·같은 스키마로 적는다.

    로그인 벽은 '측정 불가'가 아니라 '사람이 로그인, 에이전트가 측정'이다.
    그 결과가 돌아올 통로가 없으면 그 칸은 영원히 unmeasured 로 남는다.
    """
    if a.record not in measure.ENGINES:
        sys.stderr.write("모르는 엔진 %r — 가능: %s\n"
                         % (a.record, ", ".join(measure.ENGINES)))
        return 2
    if not a.query:
        sys.stderr.write("--record 에는 --query <질의 id> 가 필요하다\n")
        return 2

    ours = [u.strip() for u in a.cited.split(",") if u.strip()]
    foreign = [d for d in (x.strip() for x in a.competitors.split(",")) if d]
    if ours and not any(measure.is_ours(u, host) for u in ours):
        sys.stderr.write("경고: --cited 중 %s 도메인이 하나도 없다. 우리 URL 인지 확인하라\n" % host)

    row = measure.make_row(
        today, a.query, a.record, a.run, "browser",
        {"in": False, "out": True, "unknown": None}[a.login],
        bool(ours), ours, a.brand == "yes", foreign,
        note=a.note or "브라우저 측정",
        outcome="observed", surface=a.record,
        login_state={"in": "signed_in", "out": "signed_out", "unknown": "unknown"}[a.login],
        search_enabled={"on": True, "off": False, "unknown": None}[a.search])

    log = os.path.join(mdir, "log.jsonl")
    measure.append_rows(log, [row])
    print("기록 %s / %s #%d — 인용 %s · 언급 %s · 검색 %s"
          % (measure.ENGINES[a.record], a.query, a.run,
             ("%d건" % len(ours)) if ours else "없음",
             "있음" if a.brand == "yes" else "없음", a.search))
    print("-> %s" % log)
    return 0


def login_request(keys):
    """로그인 벽을 만나면 멈추지 말고 무엇이 필요한지 말한다."""
    print("")
    print("-" * 76)
    print(" 로그인이 필요한 엔진 — 열어두면 에이전트가 이어서 측정한다")
    print("-" * 76)
    for key in keys:
        name, url, mode = BROWSER_ENGINES[key]
        print("  %-11s %s" % (name, url))
        print("  %-11s   %s" % ("", mode))
    print("")
    print("  자격증명은 도구에 넣지 않는다. 브라우저만 열어두면 된다.")
    print("  이 엔진들은 지금 unmeasured 로 기록된다 — 0이 아니다.")
    print("-" * 76)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="인용 측정 자동 수집기")
    ap.add_argument("audit", help="out/<host>/audit.json")
    ap.add_argument("--runs", type=int, default=measure.DEFAULT_RUNS,
                    help="질의당 반복 횟수. 생성 답변은 매번 달라 5회 미만은 표본이 아니다")
    ap.add_argument("--pause", type=float, default=PAUSE, help="요청 간격 초")
    ap.add_argument("--coverage", action="store_true", help="누락 점검만 하고 끝낸다")
    ap.add_argument("--browser", action="store_true",
                    help="로그인 필요 엔진의 자리를 unmeasured 로 예약하고 안내를 출력한다")
    g = ap.add_argument_group(
        "브라우저 측정 결과 되받기",
        "로그인 브라우저에서 에이전트가 잰 결과를 같은 log.jsonl 에 observed 로 적는다")
    g.add_argument("--record", metavar="ENGINE",
                   help="engine 키 (%s)" % ", ".join(measure.ENGINES))
    g.add_argument("--query", metavar="QID", help="queries.json 의 질의 id")
    g.add_argument("--run", type=int, default=1, help="회차 (기본 1)")
    g.add_argument("--cited", default="", metavar="URL,URL",
                   help="인용된 우리 URL. 비우면 '인용 안 됨'으로 기록된다")
    g.add_argument("--competitors", default="", metavar="DOM,DOM")
    g.add_argument("--brand", choices=("yes", "no"), default="no",
                   help="우리 상호가 답변에 언급됐는가")
    g.add_argument("--search", choices=("on", "off", "unknown"), default="unknown",
                   help="그 엔진의 웹검색이 켜져 있었는가 (LLMO는 off 로 잰다)")
    g.add_argument("--login", choices=("in", "out", "unknown"), default="in")
    g.add_argument("--note", default="")
    a = ap.parse_args(argv)

    audit = measure.load_json(a.audit)
    base, host = measure.target_of(audit)
    mdir = measure.measure_dir(a.audit)
    queries = measure.load_queries(mdir)
    if not queries:
        sys.stderr.write("질의 세트가 없다. 먼저: python tools/measure.py init %s\n" % a.audit)
        return 2

    today = measure.today_str()
    if a.record:                       # 되받기·점검은 실행 계획을 찍을 일이 없다
        return record_one(a, mdir, host, today)
    if a.coverage:
        return coverage_report(audit, mdir)

    print("대상 %s · 질의 %d개 · 반복 %d회" % (host, len(queries), a.runs))
    if a.runs < measure.MIN_RUNS_WARN:
        print("주의: 반복 %d회는 표본이 아니다 (권장 %d회 이상)"
              % (a.runs, measure.MIN_RUNS_WARN))

    rows, tally = [], {}
    for qi in queries:
        qid, qtext = qi.get("id"), qi.get("text", "")
        for engine, label, fn in UNATTENDED:
            for run in range(1, a.runs + 1):
                try:
                    organic, fired, srcs = fn(qtext, host)
                except Throttled as exc:
                    print("  [중단] %s" % exc)
                    rows.append(measure.make_row(
                        today, qid, engine, run, "auto", True, None, [], False, [],
                        note="스로틀·차단으로 미측정", outcome="unmeasured",
                        error=str(exc)[:200], surface=engine, login_state="signed_out"))
                    break
                except Exception as exc:                      # noqa: BLE001
                    rows.append(measure.make_row(
                        today, qid, engine, run, "auto", True, None, [], False, [],
                        note="수집 오류", outcome="error", error=str(exc)[:200],
                        surface=engine, login_state="signed_out"))
                    break
                ours = cited_urls_in(srcs, host)
                rows.append(measure.make_row(
                    today, qid, engine, run, "auto", True, bool(ours), ours,
                    organic, [d for d in domains_of(srcs) if host not in d],
                    note=("AI 답변 발동" if fired else "AI 답변 미발동"),
                    outcome="observed", surface=engine, login_state="signed_out"))
                slot = tally.setdefault((qid, engine),
                                        {"obs": 0, "organic": 0, "fired": 0, "cited": 0})
                slot["obs"] += 1
                slot["organic"] += int(organic)
                slot["fired"] += int(fired)
                slot["cited"] += int(bool(ours))
                time.sleep(a.pause)

        print("")
        print("[%s] %s" % (qid, qtext))
        for engine, label, _fn in UNATTENDED:
            slot = tally.get((qid, engine))
            if not slot:
                print("   %-5s 미측정 (0 아님)" % label)
                continue
            print("   %-5s 자연노출 %d/%d · AI 발동 %d/%d · 인용 %d/%d"
                  % (label, slot["organic"], slot["obs"], slot["fired"], slot["obs"],
                     slot["cited"], slot["obs"]))
        sys.stdout.flush()

    keys = list(BROWSER_ENGINES) if a.browser else []
    for qi in queries:
        for key in keys:
            rows.append(measure.make_row(
                today, qi.get("id"), key, 1, "browser", None, None, [], False, [],
                note="로그인 브라우저 필요 — 에이전트가 이어서 측정",
                outcome="unmeasured", surface=key, login_state="unknown"))

    # 저장이 출력보다 먼저다. 콘솔에서 죽어도 수집분은 디스크에 남아야 한다.
    log = os.path.join(mdir, "log.jsonl")
    measure.append_rows(log, rows)

    if keys:
        login_request(keys)
    print("")
    print("%d행 -> %s" % (len(rows), log))
    print("집계·보고: python tools/measure.py report %s" % a.audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
