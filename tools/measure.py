#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-presence M4 — AI 인용 측정. "고쳤다"가 아니라 "몇 회 중 몇 번 인용됐다"를 남긴다.

사용:
    python tools/measure.py init   out/<host>/audit.json
    python tools/measure.py form   out/<host>/audit.json --engines chatgpt,google_aio --runs 5
    python tools/measure.py import out/<host>/audit.json out/<host>/measure/form-2026-09-15.csv
    python tools/measure.py report out/<host>/audit.json [--since 2026-09-01]
    python tools/measure.py auto   out/<host>/audit.json --engines chatgpt,claude --runs 5

출력:
    out/<host>/measure/queries.json    고정 질의 세트  (su-multi-geo/queries/1)
    out/<host>/measure/log.jsonl       측정 로그 append-only (su-multi-geo/measure-row/1)
    out/<host>/measure/form-<날짜>.csv 수동 입력용 (엑셀·UTF-8 BOM)
    out/<host>/measure/form-<날짜>.html 수동 입력용 오프라인 폼
    out/<host>/measure/summary.json    집계 (su-multi-geo/measure/1) + MEASURE.md

원칙
  · 수동 입력이 기본 골격이다. 키가 하나도 없어도 측정 루프는 완전히 돈다.
  · 자동화(auto)는 선택 플러그인이며 같은 로그 형식에 쌓인다.
  · API 키는 환경변수에서만 읽는다. 어떤 파일·로그·콘솔에도 쓰지 않는다.
  · 응답 원문은 저장하지 않는다 — 인용 URL과 언급 여부만 남긴다.
  · 질의 문장은 이 도구가 지어내지 않는다. 사람이 queries.json에 적는다.
  · 표준 라이브러리만 쓴다 (pip 의존 0).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from datetime import date as _date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawl  # noqa: E402  (host_of — 호스트 판정을 복제하지 않는다)

SCHEMA_QUERIES = "su-multi-geo/queries/2"
LEGACY_QUERIES_SCHEMAS = ("su-multi-geo/queries/1",)
SCHEMA_ROW = "su-multi-geo/measure-row/2"
LEGACY_ROW_SCHEMAS = ("su-multi-geo/measure-row/1",)
SCHEMA_SUMMARY = "su-multi-geo/measure/2"
AUDIT_SCHEMA_PREFIX = "su-multi-geo/audit/"

# 엔진 고정 목록 — 값이 늘면 스키마 버전을 올린다
ENGINES = OrderedDict([
    ("chatgpt", "ChatGPT"),
    ("google_aio", "Google AI Overviews"),
    ("gemini", "Gemini"),
    ("claude", "Claude"),
    ("perplexity", "Perplexity"),
    ("naver_ai", "네이버 AI 브리핑"),
    ("daum", "다음"),
    ("copilot", "Copilot"),
    ("other", "기타"),
])

# ops/measure.md "엔진 우선순위" — 전부 못 재면 이 둘부터
DEFAULT_ENGINES = ["chatgpt", "google_aio"]

# 자동화 가능한 엔진은 둘뿐이다. 나머지는 수동 폼으로만 잰다.
AUTO_ENGINES = ("chatgpt", "claude")

TYPES = ("brand", "nonbrand")
TYPE_LABEL = {"brand": "브랜드", "nonbrand": "비브랜드"}

REMEASURE_DAYS = 14      # ops/measure.md 3번 — 변경 후 14일 뒤 재측정
DEFAULT_RUNS = 5
MIN_RUNS_WARN = 5

CSV_FIELDS = ["date", "query_id", "query_text", "type", "engine", "run_no",
              "surface", "locale", "login_state", "search_enabled", "campaign_id",
              "cited", "cited_urls", "brand_mentioned", "competitor_domains", "note"]

# ⚠️ 모델명은 각사 사정으로 바뀐다. 여기 값은 출발점일 뿐이다 —
#    OPENAI_MODEL / ANTHROPIC_MODEL 환경변수로 덮어쓰고, 현재 값은 각사 문서에서 확인하라.
OPENAI_MODEL_DEFAULT = "gpt-4.1"
ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-5"
OPENAI_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
API_TIMEOUT = 180

RULES = [
    "비로그인·시크릿 창으로 질의한다 — 로그인 상태의 개인화 답은 우리가 재려는 값이 아니다.",
    "같은 질문을 같은 날 몰아서 5~10회 반복한다 — 며칠에 나눠 재면 분포가 아니라 그 사이 변화가 섞인다.",
    "인용된 URL을 반드시 남긴다 — O/X만 적으면 다음에 할 일이 정해지지 않는다.",
    "브랜드 질의와 비브랜드 질의를 따로 본다 — 섞어서 평균 내면 둘 다 안 보인다.",
    "엔진은 ChatGPT·Google AI Overviews부터 — 엔진 수를 늘리는 것보다 같은 조건 유지가 먼저다.",
    "질의 문장은 고정한다 — 질문이 바뀌면 추이가 무의미해진다.",
]


# ─────────────────────────────────────────────────────────── 작은 도구들

def today_str() -> str:
    return _date.today().isoformat()


def parse_date(value: str):
    """YYYY-MM-DD만 받는다. 아니면 None."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


_YES = {"y", "yes", "true", "1", "o", "예", "네", "인용", "있음"}
_NO = {"n", "no", "false", "0", "x", "아니오", "아니요", "없음"}


def yn(value):
    """Y/N 계열 입력 → True/False. 빈 칸·해석 불가는 None."""
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in _YES:
        return True
    if token in _NO:
        return False
    return None


_SPLIT = re.compile(r"[\s,;|]+")


def split_multi(value) -> list:
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = _SPLIT.split(str(value or "").strip())
    return [i.strip() for i in items if i and i.strip()]


_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)
# "없음"·"—" 같은 사람 입력을 호스트로 오인하지 않도록: 점이 있고 TLD가 2글자 이상
_HOSTNAME = re.compile(r"^[^\s/@:.][^\s/@:]*\.[^\s/@:.\-]{2,}$")


def norm_url(raw):
    """인용 URL 정규화: 스킴 보정·호스트 소문자·프래그먼트 제거. 쿼리는 남긴다."""
    url = str(raw or "").strip().strip("<>\"'()[]").rstrip(".,)")
    if not url:
        return None
    if not _SCHEME.match(url):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    try:
        hostname = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not _HOSTNAME.match(hostname):
        return None
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                    parts.path or "/", parts.query, ""))


def bare(host: str) -> str:
    return re.sub(r"^www\.", "", (host or "").strip().lower()).rstrip(".")


def domain_of(value) -> str:
    """URL이든 도메인이든 www를 뗀 호스트만 돌려준다."""
    token = str(value or "").strip()
    if not token:
        return ""
    if not _SCHEME.match(token):
        token = "https://" + token
    return bare(crawl.host_of(token))


def is_ours(url: str, host: str) -> bool:
    dom, ours = domain_of(url), bare(host)
    return bool(dom) and (dom == ours or dom.endswith("." + ours))


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def query_fingerprint(query: dict) -> str:
    """ID와 무관한 불변 질의 계약. 문장 또는 유형이 바뀌면 cohort도 바뀐다."""
    return stable_hash("%s\n%s" % (query.get("type", ""), query.get("text", "")))


def query_set_fingerprint(queries: list) -> str:
    return stable_hash("\n".join(sorted(query_fingerprint(q) for q in queries)))


def measure_dir(audit_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(audit_path)), "measure")


def target_of(audit: dict) -> tuple:
    target = audit.get("target") or {}
    base = (target.get("base") or "").rstrip("/")
    return base, (target.get("host") or crawl.host_of(base))


def template_path(name: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "templates", name)


# ─────────────────────────────────────────────────────────── 질의 세트

def load_queries(mdir: str) -> list:
    """queries.json → [{id,text,type,note}]. 없으면 빈 리스트."""
    path = os.path.join(mdir, "queries.json")
    if not os.path.exists(path):
        return []
    doc = load_json(path)
    if not isinstance(doc, dict):
        raise ValueError("queries.json은 schema가 있는 JSON 객체여야 한다")
    schema = doc.get("schema")
    if schema not in (SCHEMA_QUERIES,) + LEGACY_QUERIES_SCHEMAS:
        raise ValueError("지원하지 않는 queries schema: %r" % schema)
    items = doc.get("queries")
    out = []
    seen = set()
    for item in items or []:
        qid = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        qtype = str(item.get("type") or "").strip().lower()
        if not qid or qid in seen or qtype not in TYPES:
            continue
        seen.add(qid)
        if not text:
            continue
        out.append({"id": qid, "text": text, "type": qtype,
                    "note": str(item.get("note") or ""),
                    "fingerprint": query_fingerprint({"text": text, "type": qtype})})
    return out


def queries_todo(queries: list) -> list:
    return [q["id"] for q in queries if "<<TODO" in q["text"]]


# ─────────────────────────────────────────────────────────── 로그 (append-only)

def row_key(row: dict) -> tuple:
    # v1의 네 필드 키를 확장한다. 수동 UI와 API, 서로 다른 locale/검색 조건은 별도 관측이다.
    return (row.get("date"), row.get("query_id"), row.get("engine"), row.get("run_no"),
            row.get("mode") or "manual", row.get("surface") or row.get("engine"),
            row.get("locale") or "", row.get("login_state") or
            ("signed_out" if row.get("signed_out") is True else ""),
            str(row.get("search_enabled") if row.get("search_enabled") is not None else ""),
            row.get("campaign_id") or "")


def make_row(date_str, query_id, engine, run_no, mode, signed_out, cited,
             cited_urls, brand_mentioned, competitor_domains, note="", *, outcome=None,
             error=None, surface=None, locale="", login_state=None, search_enabled=None,
             campaign_id="", query_fingerprint_value="", model="") -> dict:
    outcome = outcome or ("observed" if cited is not None else ("error" if error else "unmeasured"))
    return OrderedDict([
        ("schema", SCHEMA_ROW),
        ("date", date_str),
        ("query_id", query_id),
        ("engine", engine),
        ("run_no", int(run_no)),
        ("mode", mode),
        ("surface", surface or ("api" if mode == "api" else engine)),
        ("locale", locale or ""),
        ("login_state", login_state or ("signed_out" if signed_out is True else
                                          "signed_in" if signed_out is False else "unknown")),
        ("search_enabled", search_enabled),
        ("campaign_id", campaign_id or ""),
        ("query_fingerprint", query_fingerprint_value or ""),
        ("model", model or ""),
        ("signed_out", signed_out),
        ("outcome", outcome),
        ("error", error or None),
        ("cited", bool(cited) if outcome == "observed" else None),
        ("cited_urls", list(cited_urls or [])),
        ("brand_mentioned", bool(brand_mentioned)),
        ("competitor_domains", list(competitor_domains or [])),
        ("note", note or ""),
        ("recorded_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
    ])


def append_rows(path: str, rows: list) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_log(path: str) -> list:
    """append-only 로그를 읽어 같은 (날짜·질의·엔진·회차)는 마지막 것만 남긴다."""
    if not os.path.exists(path):
        return []
    latest: dict = OrderedDict()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not row.get("date"):
                continue
            if row.get("schema") not in (SCHEMA_ROW,) + LEGACY_ROW_SCHEMAS:
                continue
            if row.get("engine") not in ENGINES:
                continue
            if row.get("schema") == SCHEMA_ROW and row.get("outcome") not in (
                    "observed", "error", "unmeasured"):
                continue
            latest[row_key(row)] = row
    return list(latest.values())


# ─────────────────────────────────────────────────────────── init

def section_hints(audit: dict, limit: int = 8) -> list:
    """audit.json의 페이지 경로 첫 세그먼트 — "우리가 이미 답을 가진 주제" 힌트."""
    counts = Counter()
    for page in audit.get("pages") or []:
        path = urllib.parse.urlsplit(page.get("url") or "").path
        seg = [s for s in path.split("/") if s]
        if seg:
            counts[seg[0]] += 1
    return counts.most_common(limit)


def cmd_init(args) -> int:
    audit = load_json(args.audit)
    base, host = target_of(audit)
    mdir = measure_dir(args.audit)
    os.makedirs(mdir, exist_ok=True)
    qpath = os.path.join(mdir, "queries.json")

    if os.path.exists(qpath):
        print("queries.json이 이미 있다 — 덮어쓰지 않는다: %s" % qpath)
    else:
        with open(template_path("queries.example.json"), encoding="utf-8") as fh:
            raw = fh.read()
        with open(qpath, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(raw)
        print("질의 세트 초안: %s" % qpath)

    queries = load_queries(mdir)
    todo = queries_todo(queries)
    print("")
    print("대상: %s" % (base or host))
    print("질의 %d개 (브랜드 %d · 비브랜드 %d)"
          % (len(queries),
             sum(1 for q in queries if q["type"] == "brand"),
             sum(1 for q in queries if q["type"] == "nonbrand")))
    if todo:
        print("빈 칸 %d개: %s" % (len(todo), ", ".join(todo)))
    print("")
    print("── 이 도구는 질문을 지어내지 않는다. 아래를 보고 사람이 채운다 ──")
    print(" 1) GSC·서치어드바이저 검색어 Top — 사람들이 실제로 친 말")
    print(" 2) 영업·CS에 실제로 들어온 질문")
    print(" 3) 이미 답 페이지를 가진 주제 (크롤된 섹션):")
    hints = section_hints(audit)
    if hints:
        for seg, n in hints:
            print("      /%-24s %d페이지" % (seg, n))
    else:
        print("      (크롤된 페이지가 없다)")
    print("")
    print("채운 다음: python tools/measure.py form %s" % args.audit)
    return 0


# ─────────────────────────────────────────────────────────── form

def pick_engines(raw) -> tuple:
    """--engines 문자열 → (유효 엔진, 무시된 값)."""
    if not raw:
        return list(DEFAULT_ENGINES), []
    want, bad, seen = [], [], set()
    for token in split_multi(raw):
        key = token.lower()
        if key in ENGINES and key not in seen:
            seen.add(key)
            want.append(key)
        elif key not in ENGINES:
            bad.append(token)
    return want or list(DEFAULT_ENGINES), bad


def plan_rows(queries: list, engines: list, runs: int, date_str: str) -> list:
    """질의 × 엔진 × 회차 — 폼에 미리 채워지는 행."""
    return [{"date": date_str, "query_id": q["id"], "query_text": q["text"],
             "type": q["type"], "engine": e, "run_no": n, "surface": e + "_web_ui",
             "login_state": "signed_out", "search_enabled": "Y"}
            for q in queries for e in engines for n in range(1, runs + 1)]


def write_form_csv(path: str, rows: list) -> None:
    # 엑셀에서 한글이 깨지지 않도록 UTF-8 BOM으로 쓴다
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            out = {k: "" for k in CSV_FIELDS}
            out.update(row)
            writer.writerow(out)


FORM_CSS = """
:root{
  --bg:#F7F9F8; --surface:#FFFFFF; --surface-2:#EFF4F2;
  --ink:#1A2B28; --ink-muted:#5A6C68; --border:#DDE5E2;
  --accent:#0E6B5C; --accent-soft:#E2F0EC;
  --ok:#2C7A4B; --bad:#B3372B; --warn:#A96A00;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#101A18; --surface:#172422; --surface-2:#1D2E2B;
    --ink:#E4EDEA; --ink-muted:#93A6A1; --border:#263936;
    --accent:#3FB39D; --accent-soft:#12332E;
    --ok:#57B87C; --bad:#E0715F; --warn:#D9A441;
  }
}
:root[data-theme="dark"]{
  --bg:#101A18; --surface:#172422; --surface-2:#1D2E2B;
  --ink:#E4EDEA; --ink-muted:#93A6A1; --border:#263936;
  --accent:#3FB39D; --accent-soft:#12332E;
  --ok:#57B87C; --bad:#E0715F; --warn:#D9A441;
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Noto Sans KR",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  font-size:15px;line-height:1.7;word-break:keep-all;overflow-wrap:anywhere}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px 80px}
h1{font-size:1.5rem;margin:28px 0 4px}
h2{font-size:1.1rem;margin:32px 0 8px;padding-top:10px;border-top:1px solid var(--border)}
.sub{color:var(--ink-muted);font-size:.9rem;margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin:16px 0}
.rules li{margin:6px 0}
.rules label{cursor:pointer;display:flex;gap:8px;align-items:flex-start}
.rules input{margin-top:6px;flex:none}
ul.rules{list-style:none;padding:0;margin:0}
.qhead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.tag{font-size:.72rem;padding:1px 8px;border-radius:99px;border:1px solid var(--border);
  background:var(--surface-2);color:var(--ink-muted);white-space:nowrap}
.tag.brand{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
.qtext{font-weight:600}
.tablewrap{overflow-x:auto;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:820px;font-size:.9rem}
th,td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}
th{background:var(--surface-2);font-weight:600;font-size:.8rem;color:var(--ink-muted);
  position:sticky;top:0;z-index:1;white-space:nowrap}
td.mono,th.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:nowrap}
td[rowspan]{vertical-align:top;font-weight:600;white-space:nowrap;background:var(--surface-2)}
input[type=text]{width:100%;min-width:120px;padding:4px 6px;border:1px solid var(--border);
  border-radius:6px;background:var(--bg);color:var(--ink);font:inherit;font-size:.85rem}
.yn{display:flex;gap:10px;white-space:nowrap}
.yn label{cursor:pointer;font-size:.85rem}
tr.done{background:var(--accent-soft)}
.bar{position:sticky;bottom:0;background:var(--surface);border-top:1px solid var(--border);
  padding:12px 0;margin-top:24px;z-index:5}
.bar .wrap{padding-bottom:0;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.prog{flex:1;min-width:180px;height:8px;background:var(--surface-2);border-radius:99px;overflow:hidden}
.prog span{display:block;height:100%;background:var(--accent);width:0}
button{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid var(--accent);
  background:var(--accent);color:#fff;cursor:pointer}
button.ghost{background:transparent;color:var(--accent)}
textarea{width:100%;height:220px;margin-top:12px;font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.8rem;border:1px solid var(--border);border-radius:8px;background:var(--surface);
  color:var(--ink);padding:10px}
.muted{color:var(--ink-muted);font-size:.85rem}
"""

FORM_JS = r"""
var KEY = "su-multi-geo:measure:" + DATA.host + ":" + DATA.date;
var state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { state = {}; }

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* 저장 불가여도 입력은 계속된다 */ }
}
function cell(k, f) { return (state[k] && state[k][f]) || ""; }
function set(k, f, v) { state[k] = state[k] || {}; state[k][f] = v; save(); progress(); }
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

var keys = [];
function build() {
  var host = document.getElementById("rows"), html = [];
  DATA.queries.forEach(function (q) {
    html.push('<h2 class="qhead"><span class="tag ' + (q.type === "brand" ? "brand" : "") + '">' +
      (q.type === "brand" ? "브랜드" : "비브랜드") + '</span> <span class="tag mono">' + esc(q.id) +
      '</span> <span class="qtext">' + esc(q.text) + '</span></h2>');
    if (q.note) { html.push('<p class="muted">' + esc(q.note) + '</p>'); }
    html.push('<div class="tablewrap"><table><thead><tr>' +
      '<th>엔진</th><th class="mono">회차</th><th>인용</th><th>인용된 URL</th>' +
      '<th>브랜드 언급</th><th>경쟁 도메인</th><th>메모</th></tr></thead><tbody>');
    DATA.engines.forEach(function (eng) {
      for (var n = 1; n <= DATA.runs; n++) {
        var k = q.id + "|" + eng[0] + "|" + n;
        keys.push({ k: k, qid: q.id, text: q.text, type: q.type, engine: eng[0], run: n });
        html.push('<tr id="tr-' + k + '">' +
          (n === 1 ? '<td rowspan="' + DATA.runs + '">' + esc(eng[1]) + '</td>' : '') +
          '<td class="mono">' + n + '</td>' +
          '<td><span class="yn">' + radio(k, "cited") + '</span></td>' +
          '<td>' + text(k, "urls", "https://…  (여러 개면 띄어쓰기)") + '</td>' +
          '<td><span class="yn">' + radio(k, "brand") + '</span></td>' +
          '<td>' + text(k, "comp", "competitor.com") + '</td>' +
          '<td>' + text(k, "note", "") + '</td></tr>');
      }
    });
    html.push("</tbody></table></div>");
  });
  host.innerHTML = html.join("");
  host.addEventListener("change", onchange);
  host.addEventListener("input", onchange);
  keys.forEach(function (r) { paint(r.k); });
}
function radio(k, f) {
  var v = cell(k, f), out = "";
  ["Y", "N"].forEach(function (o) {
    out += '<label><input type="radio" name="' + k + ":" + f + '" data-k="' + k + '" data-f="' + f +
      '" value="' + o + '"' + (v === o ? " checked" : "") + "> " + o + "</label>";
  });
  return out;
}
function text(k, f, ph) {
  return '<input type="text" data-k="' + k + '" data-f="' + f + '" placeholder="' + esc(ph) +
    '" value="' + esc(cell(k, f)) + '">';
}
function onchange(ev) {
  var el = ev.target, k = el.getAttribute("data-k");
  if (!k) { return; }
  set(k, el.getAttribute("data-f"), el.value);
  paint(k);
}
function paint(k) {
  var tr = document.getElementById("tr-" + k);
  if (tr) { tr.className = cell(k, "cited") ? "done" : ""; }
}
function progress() {
  var done = 0;
  keys.forEach(function (r) { if (cell(r.k, "cited")) { done++; } });
  document.getElementById("pbar").style.width = (keys.length ? done * 100 / keys.length : 0) + "%";
  document.getElementById("pnum").textContent = done + " / " + keys.length + " 행";
}

function csv() {
  var head = FIELDS, out = [head.join(",")];
  keys.forEach(function (r) {
    var c = cell(r.k, "cited");
    if (!c) { return; }
    out.push([DATA.date, r.qid, r.text, r.type, r.engine, r.run, r.engine + "_web_ui",
      "", "signed_out", "Y", "", c,
      cell(r.k, "urls"), cell(r.k, "brand") || c, cell(r.k, "comp"), cell(r.k, "note")]
      .map(function (v) {
        v = String(v == null ? "" : v);
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(","));
  });
  return out.join("\n") + "\n";
}
function exportCsv() {
  var body = csv(), name = "form-" + DATA.date + "-filled.csv";
  try {
    var url = URL.createObjectURL(new Blob(["﻿" + body], { type: "text/csv;charset=utf-8" }));
    var a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    document.body.removeChild(a); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  } catch (e) { /* 내려받기가 막히면 아래 textarea로 넘어간다 */ }
  var ta = document.getElementById("out");
  ta.style.display = "block"; ta.value = body; ta.focus(); ta.select();
  document.getElementById("outhelp").style.display = "block";
}

document.getElementById("export").addEventListener("click", exportCsv);
document.querySelectorAll(".rules input").forEach(function (el, i) {
  var rk = KEY + ":rule:" + i;
  try { el.checked = localStorage.getItem(rk) === "1"; } catch (e) { }
  el.addEventListener("change", function () {
    try { localStorage.setItem(rk, el.checked ? "1" : "0"); } catch (e) { }
  });
});
build();
progress();
"""


def render_form(host: str, date_str: str, queries: list, engines: list, runs: int) -> str:
    data = {
        "host": host,
        "date": date_str,
        "runs": runs,
        "engines": [[e, ENGINES[e]] for e in engines],
        "queries": queries,
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    fields = json.dumps(CSV_FIELDS, ensure_ascii=False)
    rules = "\n".join(
        '  <li><label><input type="checkbox"> <span>%s</span></label></li>' % esc(r)
        for r in RULES)
    total = len(queries) * len(engines) * runs
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>AI 인용 측정 — %(host)s %(date)s</title>
<style>%(css)s</style>
</head>
<body>
<div class="wrap">
  <h1>AI 인용 측정 — %(host)s</h1>
  <p class="sub">측정일 %(date)s · 질의 %(nq)d개 × 엔진 %(ne)d개 × %(runs)d회 = %(total)d행 ·
     su-presence measure.py · 오프라인 파일이다(외부 연결 없음)</p>

  <div class="card">
    <strong>측정 규칙 — 하나라도 어기면 숫자가 오염된다</strong>
    <ul class="rules">
%(rules)s
    </ul>
  </div>

  <div id="rows"></div>

  <textarea id="out" style="display:none" readonly></textarea>
  <p id="outhelp" class="muted" style="display:none">
    내려받기가 막혔으면 위 내용을 복사해 <code>form-%(date)s-filled.csv</code>로 저장하라
    (UTF-8). 그다음:
    <code>python tools/measure.py import out/%(host)s/audit.json &lt;그 파일&gt;</code>
  </p>
</div>

<div class="bar">
  <div class="wrap">
    <span id="pnum" class="muted">0 / %(total)d 행</span>
    <span class="prog"><span id="pbar"></span></span>
    <button id="export">CSV로 내보내기</button>
  </div>
</div>

<script>
var DATA = %(data)s;
var FIELDS = %(fields)s;
%(js)s
</script>
</body>
</html>
""" % {"host": esc(host), "date": esc(date_str), "css": FORM_CSS, "js": FORM_JS,
       "data": blob, "fields": fields, "rules": rules,
       "nq": len(queries), "ne": len(engines), "runs": runs, "total": total}


def esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def cmd_form(args) -> int:
    audit = load_json(args.audit)
    _, host = target_of(audit)
    mdir = measure_dir(args.audit)
    queries = load_queries(mdir)
    if not queries:
        sys.stderr.write("질의 세트가 없다. 먼저: python tools/measure.py init %s\n" % args.audit)
        return 2

    date_str = args.date or today_str()
    if not parse_date(date_str):
        sys.stderr.write("날짜 형식이 아니다 (YYYY-MM-DD): %s\n" % date_str)
        return 2
    engines, bad = pick_engines(args.engines)
    runs = max(1, int(args.runs))

    os.makedirs(mdir, exist_ok=True)
    rows = plan_rows(queries, engines, runs, date_str)
    csv_path = os.path.join(mdir, "form-%s.csv" % date_str)
    html_path = os.path.join(mdir, "form-%s.html" % date_str)
    write_form_csv(csv_path, rows)
    with open(html_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_form(host, date_str, queries, engines, runs))

    if bad:
        print("⚠️ 모르는 엔진은 뺐다: %s" % ", ".join(bad))
        print("   쓸 수 있는 값: %s" % ", ".join(ENGINES))
    todo = queries_todo(queries)
    if todo:
        print("⚠️ 아직 빈 칸인 질의가 있다 (%s) — 채우고 다시 만들어라." % ", ".join(todo))
    if runs < MIN_RUNS_WARN:
        print("⚠️ %d회는 표본이 아니다 — 같은 날 5~10회를 권한다." % runs)
    print("측정일 %s · 질의 %d × 엔진 %d × %d회 = %d행"
          % (date_str, len(queries), len(engines), runs, len(rows)))
    print("  엑셀용 : %s" % csv_path)
    print("  웹 폼  : %s" % html_path)
    print("")
    print("채운 뒤: python tools/measure.py import %s <채운 CSV>" % args.audit)
    return 0


# ─────────────────────────────────────────────────────────── import

def import_rows(paths: list, queries: list, host: str) -> tuple:
    """채워진 CSV → (로그 행, 문제 목록). 문제 행은 건너뛰고 사유를 남긴다."""
    qindex = {q["id"]: q for q in queries}
    accepted: dict = OrderedDict()
    problems: list = []

    for path in paths:
        try:
            # 엑셀이 붙인 BOM은 utf-8-sig가 걷어낸다
            with open(path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            problems.append("%s: 읽을 수 없다 (%s)" % (path, exc.__class__.__name__))
            continue
        name = os.path.basename(path)
        for lineno, raw in enumerate(rows, start=2):
            row, why = _parse_csv_row(raw, qindex, host)
            if why:
                problems.append("%s:%d %s" % (name, lineno, why))
                continue
            accepted[row_key(row)] = row   # 같은 키는 뒤에 온 것이 이긴다
    return list(accepted.values()), problems


def _parse_csv_row(raw: dict, qindex: dict, host: str):
    get = lambda k: str(raw.get(k) or "").strip()  # noqa: E731

    date_str = get("date")
    if not parse_date(date_str):
        return None, "날짜가 YYYY-MM-DD가 아니다: %r" % date_str

    qid = get("query_id")
    if qid not in qindex:
        return None, "queries.json에 없는 query_id: %r" % qid
    if get("query_text") and get("query_text") != qindex[qid]["text"]:
        return None, "query_text가 현재 queries.json과 다르다 (낡은 폼): %r" % qid
    if get("type") and get("type").lower() != qindex[qid]["type"]:
        return None, "type이 현재 queries.json과 다르다: %r" % qid

    engine = get("engine").lower()
    if engine not in ENGINES:
        return None, "모르는 engine: %r (가능: %s)" % (engine, ", ".join(ENGINES))

    try:
        run_no = int(get("run_no") or 0)
    except ValueError:
        run_no = 0
    if run_no < 1:
        return None, "run_no가 1 이상의 정수가 아니다: %r" % get("run_no")

    cited = yn(get("cited"))
    if cited is None:
        return None, "cited가 비었거나 Y/N이 아니다: %r" % get("cited")

    mentioned = yn(get("brand_mentioned"))
    if mentioned is None:
        mentioned = cited      # 비어 있으면 인용 여부를 따른다

    urls, dropped = [], []
    for token in split_multi(get("cited_urls")):
        url = norm_url(token)
        (urls if url else dropped).append(url or token)
    urls = list(OrderedDict.fromkeys(urls))

    comps = [d for d in (domain_of(t) for t in split_multi(get("competitor_domains"))) if d]
    comps += [domain_of(u) for u in urls if not is_ours(u, host)]
    comps = [c for c in list(OrderedDict.fromkeys(comps)) if c and c != bare(host)]

    note = get("note")
    if dropped:
        note = (note + " " if note else "") + "[URL 아님: %s]" % " ".join(dropped)
    if cited and not urls:
        note = (note + " " if note else "") + "[인용 URL 미기록]"

    login_state = get("login_state") or "signed_out"
    if login_state not in ("signed_out", "signed_in", "unknown"):
        return None, "login_state가 signed_out/signed_in/unknown이 아니다: %r" % login_state
    search_enabled = yn(get("search_enabled"))
    if get("search_enabled") and search_enabled is None:
        return None, "search_enabled가 Y/N이 아니다: %r" % get("search_enabled")
    query = qindex[qid]
    return make_row(date_str, qid, engine, run_no, "manual", login_state == "signed_out", cited,
                    urls, mentioned, comps, note, surface=get("surface") or engine,
                    locale=get("locale"), login_state=login_state,
                    search_enabled=search_enabled, campaign_id=get("campaign_id"),
                    query_fingerprint_value=query_fingerprint(query)), None


def cmd_import(args) -> int:
    audit = load_json(args.audit)
    _, host = target_of(audit)
    mdir = measure_dir(args.audit)
    queries = load_queries(mdir)
    if not queries:
        sys.stderr.write("질의 세트가 없다. 먼저: python tools/measure.py init %s\n" % args.audit)
        return 2

    rows, problems = import_rows(args.csv, queries, host)
    log_path = os.path.join(mdir, "log.jsonl")
    append_rows(log_path, rows)

    print("가져온 행: %d" % len(rows))
    if rows:
        dates = sorted({r["date"] for r in rows})
        engines = sorted({r["engine"] for r in rows})
        cited = sum(1 for r in rows if r["cited"])
        print("  측정일: %s · 엔진: %s · 인용 %d/%d"
              % (", ".join(dates), ", ".join(engines), cited, len(rows)))
    if problems:
        print("")
        print("건너뛴 행: %d" % len(problems))
        for line in problems[:40]:
            print("  - %s" % line)
        if len(problems) > 40:
            print("  … 외 %d건" % (len(problems) - 40))
    print("")
    print("로그: %s" % log_path)
    print("다음: python tools/measure.py report %s" % args.audit)
    return 0


# ─────────────────────────────────────────────────────────── report

def _blank():
    return {"runs": 0, "cited": 0, "mentioned": 0, "queries": 0, "queries_cited": 0,
            "errors": 0, "unmeasured": 0, "attempts": 0}


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def cell(part: dict) -> str:
    """한 칸을 문자열로. **0/0 을 찍지 않는다.**

    분모가 0이면 "인용 0건"이 아니라 "안 쟀다"이다. 같은 칸에 적으면 둘을 구별할 수 없고,
    한번 0으로 보고된 값은 사실로 굳는다 (ops/measure.md 「측정 범위 표기 규약」).
    """
    if part.get("runs"):
        return "%d/%d" % (part["cited"], part["runs"])
    if part.get("unmeasured"):
        return "미측정(%d)" % part["unmeasured"]
    if part.get("errors"):
        return "오류(%d)" % part["errors"]
    return "—"


def aggregate(rows: list, queries: list, host: str, base: str, since=None, until=None,
              cumulative=True) -> dict:
    qindex = {q["id"]: q for q in queries}
    rows = [r for r in rows if r.get("query_id") in qindex]
    if since:
        rows = [r for r in rows if r.get("date", "") >= since]
    if until:
        rows = [r for r in rows if r.get("date", "") <= until]
    # v2 행은 질의 문장/유형 fingerprint가 맞아야 같은 cohort다. v1은 명시적 legacy로 읽는다.
    incompatible = [r for r in rows if r.get("schema") == SCHEMA_ROW and
                    r.get("query_fingerprint") and
                    r.get("query_fingerprint") != query_fingerprint(qindex[r["query_id"]])]
    rows = [r for r in rows if r not in incompatible]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("query_id", ""),
                             r.get("engine", ""), r.get("run_no", 0)))

    dates = sorted({r["date"] for r in rows})
    trend_rows = list(rows)
    if not cumulative and dates:
        rows = [r for r in rows if r["date"] == dates[-1]]
    engines_seen = [e for e in ENGINES if any(r["engine"] == e for r in rows)]

    # 엔진 × 유형 — 회차 합산
    per_engine = {e: {t: _blank() for t in TYPES} for e in engines_seen}
    # 질의 단위 인용 여부 (엔진 무관 / 엔진별) — measure.md 6번 보고 형식용
    q_cited: dict = {}          # (date, qid) -> bool
    q_engine_cited: dict = {}   # (date, qid, engine) -> bool
    ours, comp_domains = Counter(), Counter()
    by_query: dict = OrderedDict((q["id"], {"id": q["id"], "text": q["text"],
                                            "type": q["type"], "runs": 0, "cited": 0,
                                            "engines": {}, "_urls": Counter()})
                                 for q in queries)
    modes = Counter()

    for row in rows:
        qtype = qindex[row["query_id"]]["type"]
        slot = per_engine[row["engine"]][qtype]
        outcome = row.get("outcome") or "observed"  # v1 rows are observed
        slot["attempts"] += 1
        modes[row.get("mode") or "manual"] += 1
        if outcome != "observed":
            slot["errors" if outcome == "error" else "unmeasured"] += 1
            continue
        slot["runs"] += 1
        slot["cited"] += 1 if row.get("cited") else 0
        slot["mentioned"] += 1 if row.get("brand_mentioned") else 0

        key = (row["date"], row["query_id"])
        q_cited[key] = q_cited.get(key, False) or bool(row.get("cited"))
        ekey = key + (row["engine"],)
        q_engine_cited[ekey] = q_engine_cited.get(ekey, False) or bool(row.get("cited"))

        bucket = by_query[row["query_id"]]
        bucket["runs"] += 1
        bucket["cited"] += 1 if row.get("cited") else 0
        eng = bucket["engines"].setdefault(row["engine"], {"runs": 0, "cited": 0})
        eng["runs"] += 1
        eng["cited"] += 1 if row.get("cited") else 0

        for url in row.get("cited_urls") or []:
            if is_ours(url, host):
                ours[url] += 1
            bucket["_urls"][url] += 1
        # 경쟁 도메인은 이 칸만 센다 — import·auto 둘 다 여기에 URL 도메인까지 모아 둔다
        for dom in row.get("competitor_domains") or []:
            if dom and dom != bare(host):
                comp_domains[dom] += 1

    # 질의 단위 수치를 엔진 슬롯에 채운다 (전 기간 기준: 한 번이라도 인용됐나)
    for engine in engines_seen:
        for qtype in TYPES:
            ids = {q["id"] for q in queries if q["type"] == qtype}
            measured = {qid for (_, qid, e) in q_engine_cited if e == engine and qid in ids}
            hit = {qid for (_, qid, e), v in q_engine_cited.items()
                   if v and e == engine and qid in ids}
            per_engine[engine][qtype]["queries"] = len(measured)
            per_engine[engine][qtype]["queries_cited"] = len(hit)

    engine_out = []
    for engine in engines_seen:
        item = {"engine": engine, "label": ENGINES[engine]}
        for qtype in TYPES:
            slot = dict(per_engine[engine][qtype])
            slot["rate"] = _rate(slot["cited"], slot["runs"])
            slot["mentioned_rate"] = _rate(slot["mentioned"], slot["runs"])
            item[qtype] = slot
        engine_out.append(item)

    # 수동 웹 UI와 API는 서로 다른 제품 표면이다. 엔진 합산 외에 비교 가능한 cohort를 분리한다.
    cohort_map = OrderedDict()
    for row in rows:
        key = (row.get("engine"), row.get("mode") or "manual",
               row.get("surface") or row.get("engine"), row.get("locale") or "",
               row.get("login_state") or ("signed_out" if row.get("signed_out") is True else "unknown"),
               row.get("search_enabled"), row.get("model") or "", row.get("campaign_id") or "")
        cohort = cohort_map.setdefault(key, {"engine": key[0], "mode": key[1], "surface": key[2],
                                             "locale": key[3], "login_state": key[4],
                                             "search_enabled": key[5], "model": key[6],
                                             "campaign_id": key[7], "brand": _blank(),
                                             "nonbrand": _blank(), "_queries": {}})
        slot = cohort[qindex[row["query_id"]]["type"]]
        slot["attempts"] += 1
        outcome = row.get("outcome") or "observed"
        qslot = cohort["_queries"].setdefault(row["query_id"], {
            "id": row["query_id"], "fingerprint": query_fingerprint(qindex[row["query_id"]]),
            "attempts": 0, "observed": 0, "errors": 0, "unmeasured": 0})
        qslot["attempts"] += 1
        if outcome == "error":
            slot["errors"] += 1
            qslot["errors"] += 1
        elif outcome == "unmeasured":
            slot["unmeasured"] += 1
            qslot["unmeasured"] += 1
        else:
            slot["runs"] += 1
            qslot["observed"] += 1
            slot["cited"] += int(bool(row.get("cited")))
            slot["mentioned"] += int(bool(row.get("brand_mentioned")))
    cohorts = []
    for cohort in cohort_map.values():
        for qtype in TYPES:
            cohort[qtype]["rate"] = _rate(cohort[qtype]["cited"], cohort[qtype]["runs"])
            cohort[qtype]["error_rate"] = _rate(cohort[qtype]["errors"], cohort[qtype]["attempts"])
        cohort["label"] = ("%s API (%s)" % (ENGINES[cohort["engine"]], cohort["model"] or "model 미기록")
                           if cohort["mode"] == "api" else
                           "%s 웹 UI" % ENGINES[cohort["engine"]])
        cohort["queries"] = sorted(cohort.pop("_queries").values(), key=lambda q: q["id"])
        cohorts.append(cohort)

    trend = []
    for day in dates:
        day_attempts = [r for r in trend_rows if r["date"] == day]
        day_rows = [r for r in day_attempts if (r.get("outcome") or "observed") == "observed"]
        entry = {"date": day, "runs": len(day_rows), "attempts": len(day_attempts),
                 "errors": sum(1 for r in day_attempts if r.get("outcome") == "error"),
                 "unmeasured": sum(1 for r in day_attempts if r.get("outcome") == "unmeasured"),
                 "cited": sum(1 for r in day_rows if r.get("cited")),
                 "engines": {}}
        for qtype in TYPES:
            ids = {q["id"] for q in queries if q["type"] == qtype}
            measured = {r["query_id"] for r in day_rows if r["query_id"] in ids}
            hit = {r["query_id"] for r in day_rows if r["query_id"] in ids and r.get("cited")}
            entry[qtype] = {"queries": len(measured), "queries_cited": len(hit)}
        for engine in engines_seen:
            eng_rows = [r for r in day_rows if r["engine"] == engine]
            if not eng_rows:
                continue
            entry["engines"][engine] = {
                "runs": len(eng_rows),
                "cited": sum(1 for r in eng_rows if r.get("cited")),
                "queries_cited": len({r["query_id"] for r in eng_rows if r.get("cited")}),
            }
        trend.append(entry)

    for bucket in by_query.values():
        bucket["urls"] = [{"url": u, "count": n} for u, n in bucket.pop("_urls").most_common(10)]

    next_measure = None
    if dates:
        last = parse_date(dates[-1])
        if last:
            next_measure = (last + timedelta(days=REMEASURE_DAYS)).isoformat()

    attempts = len(rows)
    observed = sum(1 for r in rows if (r.get("outcome") or "observed") == "observed")
    errors = sum(1 for r in rows if r.get("outcome") == "error")
    summary = {
        "schema": SCHEMA_SUMMARY,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"base": base, "host": host},
        "window": {"since": since, "until": until, "scope": "cumulative" if cumulative else "latest",
                   "dates": dates, "selected_dates": dates if cumulative else dates[-1:],
                   "baseline": dates[0] if dates else None,
                   "latest": dates[-1] if dates else None},
        "query_set": {"fingerprint": query_set_fingerprint(queries),
                      "query_fingerprints": {q["id"]: query_fingerprint(q) for q in queries}},
        "queries": {"total": len(queries),
                    "brand": sum(1 for q in queries if q["type"] == "brand"),
                    "nonbrand": sum(1 for q in queries if q["type"] == "nonbrand"),
                    "measured": len({r["query_id"] for r in rows})},
        "rows": observed,
        "quality": {"attempts": attempts, "observed": observed, "errors": errors,
                    "unmeasured": attempts - observed - errors,
                    "error_rate": _rate(errors, attempts),
                    "incompatible_rows": len(incompatible),
                    "regression_eligible": errors == 0 and not incompatible},
        "modes": dict(modes),
        "conditions": {
            "modes": sorted({r.get("mode") or "manual" for r in rows}),
            "surfaces": sorted({r.get("surface") or r.get("engine") for r in rows}),
            "locales": sorted({r.get("locale") or "" for r in rows}),
            "login_states": sorted({r.get("login_state") or
                                    ("signed_out" if r.get("signed_out") is True else "unknown")
                                    for r in rows}),
            "search_enabled": sorted({str(r.get("search_enabled")) for r in rows}),
            "campaign_ids": sorted({r.get("campaign_id") or "" for r in rows}),
            "models": sorted({r.get("model") or "" for r in rows}),
        },
        "engines": engine_out,
        "cohorts": cohorts,
        "urls": {"ours": [{"url": u, "count": n} for u, n in ours.most_common(20)],
                 "competitors": [{"domain": d, "count": n}
                                 for d, n in comp_domains.most_common(20)]},
        "by_query": list(by_query.values()),
        "trend": trend,
        "next_measure": next_measure,
        "freshness": {"last_observed": dates[-1] if dates else None,
                      "next_due": next_measure,
                      "scheduled": False,
                      "note": "next_due는 계산값이며 외부 스케줄러 등록 상태가 아니다"},
    }
    summary["headline"] = headline(summary)
    return summary


def _day_line(entry: dict, runs_note: str = "") -> str:
    return "브랜드 %d/%d · 비브랜드 %d/%d%s" % (
        entry["brand"]["queries_cited"], entry["brand"]["queries"],
        entry["nonbrand"]["queries_cited"], entry["nonbrand"]["queries"], runs_note)


def headline(summary: dict) -> list:
    """ops/measure.md 6번과 같은 형식의 한 줄 요약."""
    trend = summary["trend"]
    if not trend:
        return ["[측정 없음] log.jsonl이 비었다 — form → import 부터."]
    first, last = trend[0], trend[-1]
    qn = first["brand"]["queries"] + first["nonbrand"]["queries"]
    per_query = first["runs"] / max(1, qn * max(1, len(first["engines"])))
    out = ["[기준선] %s  AI인용 %s (엔진당 각 %d회 질의)"
           % (first["date"], _day_line(first), max(1, round(per_query)))]
    if len(trend) > 1:
        delta = " (기준선 대비 비브랜드 %+d)" % (last["nonbrand"]["queries_cited"]
                                          - first["nonbrand"]["queries_cited"])
        engines = ", ".join("%s %d" % (ENGINES[e], v["queries_cited"])
                            for e, v in last["engines"].items())
        out.append("[결과]   %s  AI인용 %s%s" % (last["date"], _day_line(last), delta))
        if engines:
            out.append("         └ %s" % engines)
    if summary.get("next_measure"):
        out.append("[재측정] %s 예정 (마지막 측정 +%d일)"
                   % (summary["next_measure"], REMEASURE_DAYS))
    return out


def render_measure_md(summary: dict) -> str:
    host = summary["target"]["host"]
    win = summary["window"]
    out = ["# AI 인용 측정 — %s" % host, "",
           "생성: %s · su-presence measure.py"
           % summary["generated_at"].replace("T", " "), ""]

    out += ["```"] + summary["headline"] + ["```", ""]

    if not win["dates"]:
        out += ["측정 기록이 없다. `measure.py form` → 측정 → `measure.py import` 순으로 채운다.", ""]
        return "\n".join(out)

    out += ["측정일 %d일 (%s ~ %s) · 기록 %d행 (%s) · 질의 %d개 중 %d개 측정" % (
        len(win["dates"]), win["baseline"], win["latest"], summary["rows"],
        " · ".join("%s %d" % (k, v) for k, v in sorted(summary["modes"].items())),
        summary["queries"]["total"], summary["queries"]["measured"]), ""]

    quality = summary.get("quality") or {}
    if quality.get("errors") or quality.get("unmeasured") or quality.get("incompatible_rows"):
        out += ["⚠️ 관측 %d/%d · 오류 %d · 미측정 %d · 질의 불일치 %d. 오류·불일치가 있는 회차는 회귀 판정에 쓰지 않는다." % (
            quality.get("observed", 0), quality.get("attempts", 0), quality.get("errors", 0),
            quality.get("unmeasured", 0), quality.get("incompatible_rows", 0)), ""]

    out += ["## 엔진별 인용률 — 회차 합산", "",
            "| 엔진 | 브랜드 인용 | 비브랜드 인용 | 브랜드 언급(전체) |", "|---|---|---|---|"]
    for item in summary["engines"]:
        brand, non = item["brand"], item["nonbrand"]
        out.append("| %s | %s | %s | %d/%d |" % (
            item["label"], cell(brand), cell(non),
            brand["mentioned"] + non["mentioned"], brand["runs"] + non["runs"]))
    out += ["", "비브랜드 질의는 신규 수요 도달을 보는 핵심 지표다. 브랜드 질의만 개선됐다면 "
                "현재 표본에서는 **브랜드를 이미 아는 수요에 성과가 치우쳤을 가능성**을 먼저 점검한다.", ""]

    if summary.get("cohorts"):
        out += ["## 측정 표면별 — 웹 UI와 API 분리", "",
                "| 측정 표면 | locale/login/search | 브랜드 | 비브랜드 | 오류 |",
                "|---|---|---|---|---|"]
        for c in summary["cohorts"]:
            attempts = c["brand"]["attempts"] + c["nonbrand"]["attempts"]
            errors = c["brand"]["errors"] + c["nonbrand"]["errors"]
            out.append("| %s | %s / %s / %s | %s | %s | %d/%d |" % (
                c["label"], c["locale"] or "미기록", c["login_state"],
                "on" if c["search_enabled"] is True else "off" if c["search_enabled"] is False else "미기록",
                cell(c["brand"]), cell(c["nonbrand"]), errors, attempts))
        out += ["", "웹 UI 결과와 API 모델 결과는 같은 ChatGPT 이름이어도 별도 cohort다.", ""]

    ours = summary["urls"]["ours"]
    out += ["## 인용 URL — 어느 페이지가 뽑히나", ""]
    if ours:
        out += ["| 우리 URL | 인용 횟수 |", "|---|---|"]
        out += ["| %s | %d |" % (u["url"], u["count"]) for u in ours]
    else:
        out.append("우리 URL은 한 번도 인용되지 않았다.")
    comps = summary["urls"]["competitors"]
    if comps:
        out += ["", "| 경쟁 도메인 | 등장 회차 |", "|---|---|"]
        out += ["| %s | %d |" % (c["domain"], c["count"]) for c in comps]
    out.append("")

    out += ["## 질의별", "", "| 질의 | 유형 | 인용 | 뽑힌 URL |", "|---|---|---|---|"]
    for q in summary["by_query"]:
        urls = " / ".join("%s (%d)" % (u["url"], u["count"]) for u in q["urls"][:3]) or "—"
        out.append("| `%s` %s | %s | %s | %s |"
                   % (q["id"], q["text"], TYPE_LABEL[q["type"]], cell(q), urls))
    out += ["", "URL 칸이 비어 있으면 **이 회차에서 인용 URL이 관측되지 않았거나 기록되지 않은 것**이다. "
                "같은 URL이 반복 관측되면 해당 페이지를 우선 인용 자산 후보로 점검한다.", ""]

    if len(summary["trend"]) > 1:
        base = summary["trend"][0]
        out += ["## 추이 — 기준선 대비", "",
                "| 측정일 | 회차 | 인용 | 브랜드 질의 | 비브랜드 질의 | 기준선 대비 |",
                "|---|---|---|---|---|---|"]
        for entry in summary["trend"]:
            delta = entry["nonbrand"]["queries_cited"] - base["nonbrand"]["queries_cited"]
            mark = "기준선" if entry is base else "비브랜드 %+d" % delta
            out.append("| %s | %d | %d | %d/%d | %d/%d | %s |" % (
                entry["date"], entry["runs"], entry["cited"],
                entry["brand"]["queries_cited"], entry["brand"]["queries"],
                entry["nonbrand"]["queries_cited"], entry["nonbrand"]["queries"], mark))
        out.append("")

    out += ["## 다음", "",
            "- 재측정 예정일: **%s** (마지막 측정 +%d일, 계산값). "
            "외부 캘린더·CI·자동화에는 아직 등록되지 않았다 — 실제 일정 등록은 별도로 확인한다."
            % (summary["next_measure"], REMEASURE_DAYS),
            "- 인용됐는데 내용이 틀렸으면 `ops/measure.md` 7번 정정 절차를 따른다.",
            "- 같은 오류가 여러 엔진에서 반복되면 **공통 소스를 우선 점검**한다. 반복만으로 "
            "공통 소스가 원인이라고 확정할 수는 없다.",
            ""]
    return "\n".join(out)


def print_report(summary: dict) -> None:
    print("")
    print("════════════════════════════════════════════")
    print(" AI 인용 측정 — %s" % summary["target"]["host"])
    print("════════════════════════════════════════════")
    for line in summary["headline"]:
        print(" " + line)
    if summary["engines"]:
        print("")
        print(" %-22s %-12s %-12s" % ("엔진", "브랜드", "비브랜드"))
        for item in summary["engines"]:
            print(" %-22s %-12s %-12s" % (
                item["label"], cell(item["brand"]), cell(item["nonbrand"])))


def cmd_report(args) -> int:
    audit = load_json(args.audit)
    base, host = target_of(audit)
    mdir = measure_dir(args.audit)
    queries = load_queries(mdir)
    if not queries:
        sys.stderr.write("질의 세트가 없다. 먼저: python tools/measure.py init %s\n" % args.audit)
        return 2
    if args.since and not parse_date(args.since):
        sys.stderr.write("--since 형식이 아니다 (YYYY-MM-DD): %s\n" % args.since)
        return 2
    if args.until and not parse_date(args.until):
        sys.stderr.write("--until 형식이 아니다 (YYYY-MM-DD): %s\n" % args.until)
        return 2
    if args.since and args.until and args.since > args.until:
        sys.stderr.write("--since는 --until보다 늦을 수 없다.\n")
        return 2

    rows = load_log(os.path.join(mdir, "log.jsonl"))
    summary = aggregate(rows, queries, host, base, since=args.since, until=args.until,
                        cumulative=args.cumulative)

    spath = args.out or os.path.join(mdir, "summary.json")
    write_json(spath, summary)
    mpath = os.path.join(os.path.dirname(os.path.abspath(spath)), "MEASURE.md")
    with open(mpath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_measure_md(summary))

    print_report(summary)
    print("")
    print("summary.json: %s" % spath)
    print("MEASURE.md  : %s" % mpath)
    return 0


# ─────────────────────────────────────────────────────────── auto (선택)

def http_json(url: str, payload: dict, headers: dict, timeout: int = API_TIMEOUT) -> dict:
    """기본 전송 함수. 테스트에서는 가짜로 갈아 끼운다."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # 본문에 키가 실릴 이유는 없지만, 상태 코드만 남기고 본문은 버린다
        return {"_error": "http_%s" % exc.code}
    except Exception as exc:
        return {"_error": exc.__class__.__name__}


def ask_openai(send, query: str, api_key: str, model: str) -> tuple:
    """OpenAI Responses API + web_search → (인용 URL, 응답 텍스트, 오류)."""
    payload = {"model": model, "input": query, "tools": [{"type": "web_search"}]}
    data = send(OPENAI_URL, payload, {"Authorization": "Bearer " + api_key}) or {}
    if data.get("_error"):
        return [], "", data["_error"]
    if data.get("error"):
        return [], "", "api_error"
    urls, text = [], []
    for item in data.get("output") or []:
        for block in (item.get("content") or []) if isinstance(item, dict) else []:
            if not isinstance(block, dict):
                continue
            if block.get("text"):
                text.append(str(block["text"]))
            for ann in block.get("annotations") or []:
                if isinstance(ann, dict) and "citation" in str(ann.get("type") or ""):
                    urls.append(ann.get("url"))
    if not text and data.get("output_text"):
        text.append(str(data["output_text"]))
    return urls, "\n".join(text), None


def ask_anthropic(send, query: str, api_key: str, model: str) -> tuple:
    """Anthropic Messages API + 서버 도구 web_search → (인용 URL, 응답 텍스트, 오류)."""
    payload = {"model": model, "max_tokens": 1024,
               "messages": [{"role": "user", "content": query}],
               "tools": [dict(ANTHROPIC_WEB_SEARCH)]}
    data = send(ANTHROPIC_URL, payload,
                {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}) or {}
    if data.get("_error"):
        return [], "", data["_error"]
    if data.get("error") or data.get("type") == "error":
        return [], "", "api_error"
    urls, text = [], []
    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        if block.get("text"):
            text.append(str(block["text"]))
        # 검색 결과 전부가 아니라 실제로 인용된 것만 센다
        for cite in block.get("citations") or []:
            if isinstance(cite, dict) and cite.get("url"):
                urls.append(cite["url"])
    return urls, "\n".join(text), None


ASKERS = {"chatgpt": ("OPENAI_API_KEY", "OPENAI_MODEL", OPENAI_MODEL_DEFAULT, ask_openai),
          "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT,
                     ask_anthropic)}


def brand_hit(text: str, host: str, site_name: str) -> bool:
    low = (text or "").lower()
    needles = [n for n in (site_name, bare(host)) if n]
    return any(n.lower() in low for n in needles)


def run_auto(queries: list, engines: list, runs: int, host: str, keys: dict,
             send=None, models=None, delay: float = 2.0, date_str=None,
             site_name: str = "") -> list:
    """엔진별로 질의를 돌려 같은 로그 형식의 행을 만든다. 응답 원문은 남기지 않는다."""
    send = send or http_json
    models = models or {}
    date_str = date_str or today_str()
    rows = []
    for engine in engines:
        env_key, env_model, default_model, asker = ASKERS[engine]
        api_key = keys.get(engine)
        if not api_key:
            continue
        model = models.get(engine) or default_model
        for query in queries:
            for run_no in range(1, runs + 1):
                if rows and delay:
                    time.sleep(delay)
                raw_urls, text, error = asker(send, query["text"], api_key, model)
                urls = [u for u in (norm_url(u) for u in raw_urls) if u]
                urls = list(OrderedDict.fromkeys(urls))
                cited = any(is_ours(u, host) for u in urls)
                comps = [d for d in (domain_of(u) for u in urls if not is_ours(u, host)) if d]
                rows.append(make_row(
                    date_str, query["id"], engine, run_no, "api", None,
                    None if error else cited, urls,
                    None if error else brand_hit(text, host, site_name),
                    list(OrderedDict.fromkeys(comps)),
                    ("실패: %s" % error) if error else "model=%s" % model,
                    outcome="error" if error else "observed", error=error,
                    surface="api", login_state="not_applicable", search_enabled=True,
                    campaign_id=query_set_fingerprint(queries)[:16],
                    query_fingerprint_value=query_fingerprint(query),
                    model=model))
    return rows


def cmd_auto(args) -> int:
    audit = load_json(args.audit)
    _, host = target_of(audit)
    mdir = measure_dir(args.audit)
    queries = load_queries(mdir)
    if not queries:
        sys.stderr.write("질의 세트가 없다. 먼저: python tools/measure.py init %s\n" % args.audit)
        return 2

    wanted, bad = pick_engines(args.engines or ",".join(AUTO_ENGINES))
    if bad:
        print("⚠️ 모르는 엔진은 뺐다: %s" % ", ".join(bad))
    manual = [e for e in wanted if e not in AUTO_ENGINES]
    engines, keys, models = [], {}, {}
    for engine in wanted:
        if engine not in ASKERS:
            continue
        env_key, env_model, default_model, _ = ASKERS[engine]
        api_key = os.environ.get(env_key)          # 키는 여기서만 읽는다
        if api_key:
            engines.append(engine)
            keys[engine] = api_key
            models[engine] = os.environ.get(env_model) or default_model
        else:
            manual.append(engine)
            print("· %s: %s 없음 — 자동화 건너뜀" % (ENGINES[engine], env_key))

    if manual:
        print("")
        print("수동 모드로 재야 하는 엔진: %s" % ", ".join(ENGINES[e] for e in manual))
        if any(e not in AUTO_ENGINES for e in manual):
            print("  Gemini·Perplexity·Google AI Overviews·네이버·다음·Copilot은 "
                  "자동화 대상이 아니다.")
        print("  → python tools/measure.py form %s --engines %s"
              % (args.audit, ",".join(manual)))
    if not engines:
        print("")
        print("자동화할 엔진이 없다 — 수동 모드다. 위 form 명령으로 폼을 만들어 측정하라.")
        return 0

    runs = max(1, int(args.runs))
    calls = len(engines) * len(queries) * runs
    print("")
    print("자동 측정 예정: 엔진 %d × 질의 %d × %d회 = **API 호출 %d회**"
          % (len(engines), len(queries), runs, calls))
    for engine in engines:
        print("  · %s (model=%s)" % (ENGINES[engine], models[engine]))
    print("  ⚠️ 웹 검색 도구 호출 비용을 포함해 **비용은 전부 사용자 부담**이다.")
    print("  ⚠️ API 응답은 비로그인 웹 UI와 다른 표면이다 — 수동 측정을 대체하지 않는다.")
    if not args.yes:
        try:
            answer = input("계속하려면 yes 를 입력하라: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("취소했다.")
            return 0

    site_path = os.path.join(os.path.dirname(os.path.abspath(args.audit)), "site.json")
    site_name = ""
    if os.path.exists(site_path):
        try:
            site_name = str((load_json(site_path) or {}).get("name") or "")
        except ValueError:
            site_name = ""

    rows = run_auto(queries, engines, runs, host, keys, models=models,
                    delay=args.delay, date_str=args.date or today_str(),
                    site_name=site_name)
    log_path = os.path.join(mdir, "log.jsonl")
    append_rows(log_path, rows)
    failed = sum(1 for r in rows if r.get("outcome") == "error")
    print("")
    print("기록 %d행 · 인용 %d · 실패 %d"
          % (len(rows), sum(1 for r in rows if r.get("cited") is True), failed))
    print("로그: %s" % log_path)
    print("다음: python tools/measure.py report %s" % args.audit)
    return 0


# ─────────────────────────────────────────────────────────── CLI

def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="AI 인용 측정 — 수동 폼이 기본, 자동화는 선택")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="measure/ 폴더와 질의 세트 초안 만들기")
    p.add_argument("audit")

    p = sub.add_parser("form", help="수동 입력용 CSV + 오프라인 HTML 폼 만들기")
    p.add_argument("audit")
    p.add_argument("--engines", default=None,
                   help="쉼표 구분 (기본: %s)" % ",".join(DEFAULT_ENGINES))
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="질의당 회차 (기본 5)")
    p.add_argument("--date", default=None, help="측정일 YYYY-MM-DD (기본 오늘)")

    p = sub.add_parser("import", help="채운 CSV를 검증해 log.jsonl에 append")
    p.add_argument("audit")
    p.add_argument("csv", nargs="+")

    p = sub.add_parser("report", help="log.jsonl 집계 → summary.json + MEASURE.md")
    p.add_argument("audit")
    p.add_argument("--since", default=None, help="이 날짜부터 (YYYY-MM-DD)")
    p.add_argument("--until", default=None, help="이 날짜까지 (YYYY-MM-DD)")
    p.add_argument("--cumulative", action="store_true",
                   help="선택 기간 전체 누적 (기본: 기간 내 최신 측정일만 집계, 추이는 보존)")
    p.add_argument("--out", default=None, help="summary.json 경로")

    p = sub.add_parser("auto", help="선택 자동화 — 환경변수에 키가 있을 때만")
    p.add_argument("audit")
    p.add_argument("--engines", default=None,
                   help="자동화 가능: %s" % ",".join(AUTO_ENGINES))
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    p.add_argument("--date", default=None)
    p.add_argument("--delay", type=float, default=2.0, help="호출 간격(초)")
    p.add_argument("--yes", action="store_true", help="확인 프롬프트 없이 진행")

    args = ap.parse_args(argv)
    try:
        audit = load_json(args.audit)
    except (OSError, ValueError) as exc:
        sys.stderr.write("audit.json을 읽을 수 없다: %s (%s)\n"
                         % (args.audit, exc.__class__.__name__))
        return 2
    if not str(audit.get("schema", "")).startswith(AUDIT_SCHEMA_PREFIX):
        sys.stderr.write("audit.json 스키마가 아니다: %s\n" % audit.get("schema"))
        return 2

    try:
        return {"init": cmd_init, "form": cmd_form, "import": cmd_import,
                "report": cmd_report, "auto": cmd_auto}[args.cmd](args)
    except (OSError, ValueError) as exc:
        sys.stderr.write("측정 파일을 읽을 수 없다: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
