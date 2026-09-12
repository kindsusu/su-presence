#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""su-presence M5 — 기준선 스냅샷 + 드리프트 비교.

"언제 무엇을 다시 잰다"를 기억이 아니라 파일로 강제한다.

사용:
    python tools/drift.py snapshot out/<host>/audit.json [--measure summary.json] [--label "P1 배포 후"]
    python tools/drift.py compare  out/<host>/audit.json [--from 2026-09-01] [--to 2026-09-15]
    python tools/drift.py status   out/<host>/audit.json
    python tools/drift.py timeline out/<host>/audit.json
    (audit.json 대신 --host example.com [--out out] 도 된다)

출력:
    out/<host>/history/            불변 스냅샷 보관소 + index.json (su-multi-geo/history/1)
    out/<host>/drift.json          (su-multi-geo/drift/1) + DRIFT.md
    exit code: 회귀가 하나라도 있으면 1

원칙
  · 스냅샷은 불변이다. 같은 날짜 같은 종류는 --force 없이 덮어쓰지 않는다 (기준선 보호).
  · 진단 드리프트는 verify.py의 verify_diff를 임포트해 쓴다 (복제 금지).
  · 낡은 기준선은 하한선 검사로 안 잡힌다 — 스냅샷이 찍힌 날짜를 본다 (ops/measure.md 4번).
  · 표준 라이브러리만 쓴다 (pip 의존 0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import math
from collections import OrderedDict
from datetime import date as _date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify  # noqa: E402  (verify_diff — findings/scorecard/stats/pages 비교를 복제하지 않는다)

SCHEMA_HISTORY = "su-presence/history/1"
# 옛 이름을 안 받으면 기존 history.json 이 버려지고 **기준선이 조용히 초기화된다.**
LEGACY_HISTORY_SCHEMAS = ("su-multi-geo/history/1",)
SCHEMA_DRIFT = "su-presence/drift/1"
AUDIT_SCHEMA_PREFIX = ("su-presence/audit/", "su-multi-geo/audit/")
SUMMARY_V2_SCHEMAS = ("su-presence/measure/2", "su-multi-geo/measure/2")
SUMMARY_SCHEMAS = SUMMARY_V2_SCHEMAS + ("su-multi-geo/measure/1",)
VERIFY_SCHEMA_PREFIX = ("su-presence/verify/", "su-multi-geo/verify/")

REMEASURE_DAYS = 14      # ops/measure.md 3번 — 변경 후 14일 뒤 재측정
STALE_DAYS = 30          # ops/measure.md 4번 — 이보다 오래된 기준선은 경고한다
MIN_MEASURE_RUNS = 5
MIN_RATE_DELTA = 0.10

KINDS = ("audit", "measure", "verify")

# 다음 재측정일에 할 일 — 순서가 곧 절차다
NEXT_STEPS = [
    "python tools/crawl.py <host> --out out",
    "python tools/drift.py snapshot out/<host>/audit.json",
    "python tools/measure.py form out/<host>/audit.json --engines chatgpt,google_aio --runs 5",
    "  ── 비로그인·시크릿 창으로 사람이 측정 ──",
    "python tools/measure.py import out/<host>/audit.json <채운 CSV>",
    "python tools/measure.py report out/<host>/audit.json",
    "python tools/drift.py snapshot out/<host>/audit.json --measure out/<host>/measure/summary.json",
    "python tools/drift.py compare out/<host>/audit.json",
]


# ─────────────────────────────────────────────────────────── 작은 도구들

def today_str() -> str:
    return _date.today().isoformat()


def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def atomic_write_json(path: str, obj) -> None:
    """같은 디렉터리에 완성본을 쓴 뒤 교체한다."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def plus_days(date_str: str, days: int):
    day = parse_date(date_str)
    return (day + timedelta(days=days)).isoformat() if day else None


# ─────────────────────────────────────────────────────────── 지표 추출

def dup_title_pages(audit: dict) -> int:
    """중복 title에 걸린 페이지 수 — findings가 이미 세어 둔 값을 쓴다."""
    for finding in audit.get("findings") or []:
        if finding.get("code") == "TITLE_DUPLICATE":
            return (finding.get("data") or {}).get("pages") or 0
    return 0


def sitemap_url_count(audit: dict) -> int:
    """사이트맵에 실린 URL 수 (인덱스 사이트맵의 자식 목록은 URL이 아니므로 뺀다)."""
    return sum((s.get("url_count") or 0)
               for s in ((audit.get("site") or {}).get("sitemaps") or [])
               if not s.get("is_index"))


def audit_metrics(audit: dict) -> dict:
    stats = audit.get("stats") or {}
    return OrderedDict([
        ("pages", stats.get("pages_crawled") or 0),
        ("dup_titles", dup_title_pages(audit)),
        ("jsonld_pages", stats.get("pages_with_jsonld") or 0),
        ("noindex", stats.get("pages_noindex") or 0),
        ("sitemap_urls", sitemap_url_count(audit)),
    ])


def measure_metrics(summary: dict) -> dict:
    """엔진 합산 인용률 — 브랜드/비브랜드를 따로 본다 (ops/measure.md 2번)."""
    out = OrderedDict()
    for qtype in ("brand", "nonbrand"):
        cited = runs = 0
        for engine in summary.get("engines") or []:
            slot = engine.get(qtype) or {}
            cited += slot.get("cited") or 0
            runs += slot.get("runs") or 0
        errors = sum(((engine.get(qtype) or {}).get("errors") or 0)
                     for engine in summary.get("engines") or [])
        out[qtype] = {"cited": cited, "runs": runs, "errors": errors,
                      "rate": round(cited / runs, 4) if runs else None,
                      "wilson95": wilson_interval(cited, runs)}
    return out


def wilson_interval(successes: int, total: int):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return [round(max(0, center - spread), 4), round(min(1, center + spread), 4)]


def measure_cohort(summary: dict) -> dict:
    """비교 가능성을 결정하는 측정 조건. v1은 legacy cohort로만 서로 비교한다."""
    schema = summary.get("schema")
    qset = summary.get("query_set") or {}
    conditions = summary.get("conditions") or {}
    cohort_rows = []
    for cohort in summary.get("cohorts") or []:
        cohort_rows.append({
            "engine": cohort.get("engine"), "mode": cohort.get("mode"),
            "surface": cohort.get("surface"), "locale": cohort.get("locale"),
            "login_state": cohort.get("login_state"),
            "search_enabled": cohort.get("search_enabled"), "model": cohort.get("model"),
            "campaign_id": cohort.get("campaign_id"),
            "queries": sorted(({"id": q.get("id"), "fingerprint": q.get("fingerprint"),
                                 "observed": q.get("observed"), "attempts": q.get("attempts")}
                                for q in cohort.get("queries") or []), key=lambda q: q["id"] or "")})
    cohort_rows.sort(key=lambda c: tuple(str(c.get(k) or "") for k in
                                         ("engine", "mode", "surface", "locale", "login_state",
                                          "search_enabled", "model", "campaign_id")))
    return {"schema_family": "v2" if schema in SUMMARY_V2_SCHEMAS else "legacy-v1",
            "query_set": qset.get("fingerprint"),
            "scope": (summary.get("window") or {}).get("scope"),
            "surfaces": conditions.get("surfaces"),
            "modes": conditions.get("modes"),
            "locales": conditions.get("locales"),
            "login_states": conditions.get("login_states"),
            "search_enabled": conditions.get("search_enabled"),
            "cohorts": cohort_rows or None}


def audit_line(audit: dict) -> str:
    m = audit_metrics(audit)
    return ("페이지 %d · 중복 title %d · JSON-LD %d · noindex %d · 사이트맵 URL %d"
            % (m["pages"], m["dup_titles"], m["jsonld_pages"], m["noindex"], m["sitemap_urls"]))


def measure_line(summary: dict) -> str:
    m = measure_metrics(summary)
    return ("브랜드 %d/%d · 비브랜드 %d/%d (회차 합산)"
            % (m["brand"]["cited"], m["brand"]["runs"],
               m["nonbrand"]["cited"], m["nonbrand"]["runs"]))


def verify_line(report: dict) -> str:
    s = report.get("summary") or {}
    return ("❌ %d · ⚠️ %d · ✅ %d"
            % (s.get("fail") or 0, s.get("warn") or 0, s.get("pass") or 0))


LINE_OF = {"audit": audit_line, "measure": measure_line, "verify": verify_line}


# ─────────────────────────────────────────────────────────── history/index.json

def history_dir(outdir: str) -> str:
    return os.path.join(outdir, "history")


def index_path(outdir: str) -> str:
    return os.path.join(history_dir(outdir), "index.json")


def load_index(outdir: str, host: str = "") -> dict:
    path = index_path(outdir)
    if os.path.exists(path):
        index = load_json(path)
        if (index.get("schema") not in (SCHEMA_HISTORY,) + LEGACY_HISTORY_SCHEMAS
                or not isinstance(index.get("snapshots"), list)):
            raise SystemExit("지원하지 않거나 손상된 history index: %s" % path)
        if host and index.get("host") and index["host"].lower() != host.lower():
            raise SystemExit("history host 불일치: %s != %s" % (index["host"], host))
        return index
    return {"schema": SCHEMA_HISTORY, "host": host, "baseline_date": None,
            "next_due": None, "snapshots": []}


def sort_snapshots(index: dict) -> None:
    index["snapshots"].sort(key=lambda s: (s.get("date", ""), KINDS.index(s["kind"])
                                           if s.get("kind") in KINDS else 9))


def refresh_due(index: dict) -> None:
    measured = [s["date"] for s in index["snapshots"]
                if s.get("date") and s.get("kind") == "measure"]
    dates = measured or [s["date"] for s in index["snapshots"] if s.get("date")]
    index["next_due"] = plus_days(max(dates), REMEASURE_DAYS) if dates else None
    index["schedule"] = {"next_due": index["next_due"], "scheduled": False,
                         "note": "날짜 계산값이며 외부 스케줄러 등록 상태가 아니다"}


def find_snapshot(index: dict, kind: str, date_str: str):
    for snap in index["snapshots"]:
        if snap.get("kind") == kind and snap.get("date") == date_str:
            return snap
    return None


def snapshots_of(index: dict, kind: str) -> list:
    return [s for s in index["snapshots"] if s.get("kind") == kind]


def latest_on_or_before(index: dict, kind: str, date_str: str):
    picks = [s for s in snapshots_of(index, kind) if s.get("date", "") <= date_str]
    return picks[-1] if picks else None


def snapshot_json(outdir: str, snap: dict) -> dict:
    path = os.path.join(history_dir(outdir), snap["file"])
    actual = sha256_of(path)
    if snap.get("sha256") and actual != snap["sha256"]:
        raise SystemExit("스냅샷 sha256 불일치: %s" % path)
    return load_json(path)


# ─────────────────────────────────────────────────────────── snapshot

def guard_overwrite(index: dict, kind: str, date_str: str, force: bool) -> None:
    existing = find_snapshot(index, kind, date_str)
    if existing and not force:
        raise SystemExit(
            "거부: %s %s 스냅샷이 이미 있다 (%s). 스냅샷은 불변이다 — "
            "정말 덮어쓰려면 --force." % (date_str, kind, existing["file"]))


def store(outdir: str, index: dict, kind: str, src: str, date_str: str,
          label: str) -> dict:
    existing = find_snapshot(index, kind, date_str)
    payload = load_json(src)
    name = "%s-%s.json" % (kind, date_str)
    dest = os.path.join(history_dir(outdir), name)
    os.makedirs(history_dir(outdir), exist_ok=True)
    shutil.copyfile(src, dest)

    snap = {"date": date_str, "kind": kind, "file": name,
            "sha256": sha256_of(dest), "label": label or None,
            "line": LINE_OF[kind](payload)}
    if existing:
        index["snapshots"][index["snapshots"].index(existing)] = snap
    else:
        index["snapshots"].append(snap)
    return snap


def validate_snapshot_payload(kind: str, payload: dict, host: str, src: str) -> None:
    if not isinstance(payload, dict):
        raise SystemExit("%s JSON 객체가 아니다: %s" % (kind, src))
    schema = str(payload.get("schema") or "")
    valid = ((kind == "audit" and schema.startswith(AUDIT_SCHEMA_PREFIX)) or
             (kind == "measure" and schema in SUMMARY_SCHEMAS) or
             (kind == "verify" and schema.startswith(VERIFY_SCHEMA_PREFIX)))
    if not valid:
        raise SystemExit("%s 스키마가 아니다: %s (%s)" % (kind, schema or "없음", src))
    payload_host = ((payload.get("target") or {}).get("host") or "").lower()
    if host and payload_host and payload_host != host.lower():
        raise SystemExit("%s host 불일치: %s != %s" % (kind, payload_host, host))


def cmd_snapshot(args) -> int:
    outdir, host = resolve(args)
    date_str = args.date or today_str()
    if not parse_date(date_str):
        raise SystemExit("--date는 YYYY-MM-DD 형식이다: %s" % date_str)

    index = load_index(outdir, host)
    if index.get("host") and host and index["host"].lower() != host.lower():
        raise SystemExit("history host 불일치: %s != %s" % (index["host"], host))
    index["host"] = index.get("host") or host
    first_audit = not snapshots_of(index, "audit")

    # 하나라도 충돌하면 아무것도 쓰지 않는다 — 반쯤 갱신된 이력이 제일 나쁘다
    plan = [("audit", args.audit)]
    plan += [("measure", args.measure)] if args.measure else []
    plan += [("verify", args.verify)] if args.verify else []
    payloads = {}
    for kind, src in plan:
        try:
            payloads[kind] = load_json(src)
        except (OSError, ValueError) as exc:
            raise SystemExit("%s 입력을 읽을 수 없다: %s (%s)" %
                             (kind, src, exc.__class__.__name__))
        validate_snapshot_payload(kind, payloads[kind], host, src)
    for kind, _ in plan:
        guard_overwrite(index, kind, date_str, args.force)

    # 모든 입력 검증 후에만 교체한다. 실패하면 기존 파일까지 원복한다.
    os.makedirs(history_dir(outdir), exist_ok=True)
    staged, backups, made = [], {}, []
    try:
        for kind, src in plan:
            name = "%s-%s.json" % (kind, date_str)
            dest = os.path.join(history_dir(outdir), name)
            fd, tmp = tempfile.mkstemp(prefix=".stage-", suffix=".json", dir=history_dir(outdir))
            os.close(fd)
            shutil.copyfile(src, tmp)
            if sha256_of(tmp) != sha256_of(src):
                raise OSError("staged sha256 mismatch")
            staged.append((kind, name, dest, tmp))
        for kind, name, dest, tmp in staged:
            if os.path.exists(dest):
                backup = dest + ".rollback"
                shutil.copyfile(dest, backup)
                backups[dest] = backup
            os.replace(tmp, dest)
            snap = {"date": date_str, "kind": kind, "file": name,
                    "sha256": sha256_of(dest), "label": args.label or None,
                    "line": LINE_OF[kind](payloads[kind])}
            existing = find_snapshot(index, kind, date_str)
            if existing:
                index["snapshots"][index["snapshots"].index(existing)] = snap
            else:
                index["snapshots"].append(snap)
            made.append(snap)

        if first_audit or args.baseline:
            index["baseline_date"] = date_str
        sort_snapshots(index)
        refresh_due(index)
        atomic_write_json(index_path(outdir), index)
    except Exception:
        for _, _, dest, tmp in staged:
            if os.path.exists(tmp):
                os.remove(tmp)
            if dest in backups:
                os.replace(backups[dest], dest)
            elif os.path.exists(dest):
                os.remove(dest)
        raise
    finally:
        for backup in backups.values():
            if os.path.exists(backup):
                os.remove(backup)

    print("")
    print("스냅샷 저장: %s" % history_dir(outdir))
    for snap in made:
        print("  %-8s %s  %s" % (snap["kind"], snap["file"], snap["line"]))
        print("           sha256 %s" % snap["sha256"])
    if first_audit:
        print("")
        print("이것이 기준선이다 (baseline_date=%s)." % date_str)
        print("앞으로의 모든 비교는 이 스냅샷을 원점으로 한다 — 지우지 마라.")
    elif args.baseline:
        print("")
        print("기준선을 %s로 옮겼다." % date_str)
    print("")
    print("다음 재측정 예정일: %s (마지막 스냅샷 +%d일)" % (index["next_due"], REMEASURE_DAYS))
    if not first_audit:
        print("비교: python tools/drift.py compare %s" % args.audit)
    return 0


# ─────────────────────────────────────────────────────────── compare

def check_by_id(result: dict, cid: str) -> dict:
    for check in result.get("checks") or []:
        if check.get("id") == cid:
            return check
    return {}


# (키, 사람이 읽는 이름, 방향) — 방향은 "무엇이 나쁜 변화인가"
METRIC_RULES = [
    ("noindex", "noindex 페이지", "up_bad"),
    ("jsonld_pages", "JSON-LD 보유 페이지", "down_bad"),
    ("dup_titles", "중복 title 페이지", "up_bad"),
    ("sitemap_urls", "사이트맵 URL 수", "shrink20"),
    ("pages", "크롤 페이지 수", "info"),
]


def compare_metrics(before: dict, after: dict) -> tuple:
    """(회귀, 개선, 변화없음) — 숫자 지표만. scorecard·인용률은 따로 본다."""
    regressions, improvements, flat = [], [], []
    for key, label, rule in METRIC_RULES:
        b, a = before.get(key) or 0, after.get(key) or 0
        item = {"code": key, "label": label, "before": b, "after": a, "delta": a - b}
        if b == a:
            flat.append(item)
            continue
        bad = ((rule == "up_bad" and a > b)
               or (rule == "down_bad" and a < b)
               or (rule == "shrink20" and b > 0 and a < b * 0.8))
        if bad:
            item["message"] = "%s %d → %d" % (label, b, a)
            regressions.append(item)
        elif rule == "info" or (rule == "shrink20" and a < b):
            # 페이지 수 변동, 20% 미만의 사이트맵 감소는 회귀로 치지 않는다 — 사실만 남긴다
            flat.append(item)
        else:
            item["message"] = "%s %d → %d" % (label, b, a)
            improvements.append(item)
    return regressions, improvements, flat


def compare_measure(before: dict, after: dict) -> dict:
    """두 summary.json(su-multi-geo/measure/1)의 인용 드리프트."""
    bm, am = measure_metrics(before), measure_metrics(after)
    bc, ac = measure_cohort(before), measure_cohort(after)
    comparable = bc == ac
    reasons = [] if comparable else ["질의 세트 또는 surface/mode/locale/login/search 조건이 다르다"]
    if (bc["schema_family"] == "v2" or ac["schema_family"] == "v2") and (
            not bc.get("query_set") or not ac.get("query_set") or
            not bc.get("cohorts") or not ac.get("cohorts")):
        comparable = False
        reasons.append("v2 비교에 필요한 query-set 또는 상세 cohort 표본 구성이 없다")
    bq, aq = before.get("quality") or {}, after.get("quality") or {}
    if ((bq and bq.get("regression_eligible") is not True) or
            (aq and aq.get("regression_eligible") is not True) or
            bq.get("errors") or aq.get("errors") or bq.get("unmeasured") or aq.get("unmeasured") or
            bq.get("incompatible_rows") or aq.get("incompatible_rows")):
        comparable = False
        reasons.append("오류·미측정 또는 질의 fingerprint 불일치가 있는 측정 회차다")
    if ((before.get("window") or {}).get("scope") == "cumulative" or
            (after.get("window") or {}).get("scope") == "cumulative"):
        comparable = False
        reasons.append("누적 보고서는 배포 전후 단일 회차 회귀 판정에 사용할 수 없다")

    engines = []
    b_eng = {e["engine"]: e for e in before.get("engines") or []}
    a_eng = {e["engine"]: e for e in after.get("engines") or []}
    for eid in sorted(set(b_eng) | set(a_eng)):
        row = {"engine": eid,
               "label": (a_eng.get(eid) or b_eng.get(eid) or {}).get("label", eid)}
        for qtype in ("brand", "nonbrand"):
            bs = (b_eng.get(eid) or {}).get(qtype) or {}
            as_ = (a_eng.get(eid) or {}).get(qtype) or {}
            br, ar = bs.get("rate"), as_.get("rate")
            row[qtype] = {
                "before": {"cited": bs.get("cited") or 0, "runs": bs.get("runs") or 0, "rate": br},
                "after": {"cited": as_.get("cited") or 0, "runs": as_.get("runs") or 0, "rate": ar},
                "points": round(((ar or 0) - (br or 0)) * 100, 1) if (br is not None or ar is not None) else None,
            }
        engines.append(row)

    b_ours = {u["url"]: u["count"] for u in ((before.get("urls") or {}).get("ours") or [])}
    a_ours = {u["url"]: u["count"] for u in ((after.get("urls") or {}).get("ours") or [])}
    ours = [{"url": u, "before": b_ours.get(u, 0), "after": a_ours.get(u, 0)}
            for u in sorted(set(b_ours) | set(a_ours))]

    b_comp = {c["domain"]: c["count"] for c in ((before.get("urls") or {}).get("competitors") or [])}
    a_comp = {c["domain"]: c["count"] for c in ((after.get("urls") or {}).get("competitors") or [])}

    return {
        "comparison": {"status": "comparable" if comparable else "inconclusive",
                       "reasons": reasons, "before_cohort": bc, "after_cohort": ac},
        "totals": {"before": bm, "after": am},
        "engines": engines,
        "ours": ours,
        "ours_new": sorted(u for u in a_ours if u not in b_ours),
        "ours_lost": sorted(u for u in b_ours if u not in a_ours),
        "competitors": [{"domain": d, "before": b_comp.get(d, 0), "after": a_comp.get(d, 0)}
                        for d in sorted(set(b_comp) | set(a_comp),
                                        key=lambda d: -a_comp.get(d, 0))][:20],
    }


def build_drift(outdir: str, host: str, index: dict, from_date: str, to_date: str,
                stale_days: int) -> dict:
    b_snap, a_snap = find_snapshot(index, "audit", from_date), find_snapshot(index, "audit", to_date)
    before, after = snapshot_json(outdir, b_snap), snapshot_json(outdir, a_snap)

    audit_diff = verify.verify_diff(before, after)
    b_metrics, a_metrics = audit_metrics(before), audit_metrics(after)
    regressions, improvements, flat = compare_metrics(b_metrics, a_metrics)

    score = check_by_id(audit_diff, "diff.scorecard")
    if score.get("status") == "fail":
        regressions.append({"code": "scorecard", "label": "레인 점수",
                            "message": score.get("message"),
                            "lanes": (score.get("evidence") or {}).get("lanes")})
    else:
        lanes = (score.get("evidence") or {}).get("lanes") or {}
        better = ["%s %s→%s" % (k, v["before"], v["after"]) for k, v in lanes.items()
                  if verify.SCORE_RANK.get(v["after"], -1) < verify.SCORE_RANK.get(v["before"], -1)]
        if better:
            improvements.append({"code": "scorecard", "label": "레인 점수",
                                 "message": "레인 점수 개선: " + ", ".join(better),
                                 "lanes": lanes})

    measure_diff = None
    bm_snap = latest_on_or_before(index, "measure", from_date)
    am_snap = latest_on_or_before(index, "measure", to_date)
    if bm_snap and am_snap and bm_snap["date"] != am_snap["date"]:
        measure_diff = compare_measure(snapshot_json(outdir, bm_snap),
                                       snapshot_json(outdir, am_snap))
        measure_diff["from"] = bm_snap["date"]
        measure_diff["to"] = am_snap["date"]
        br = measure_diff["totals"]["before"]["nonbrand"]["rate"]
        ar = measure_diff["totals"]["after"]["nonbrand"]["rate"]
        comparison = measure_diff.get("comparison") or {}
        if br is not None and ar is not None:
            item = {"code": "nonbrand_rate", "label": "비브랜드 인용률",
                    "before": br, "after": ar, "delta": round((ar - br) * 100, 1),
                    "message": "비브랜드 인용률 %.1f%% → %.1f%% (%+.1f포인트)"
                               % (br * 100, ar * 100, (ar - br) * 100)}
            b_runs = measure_diff["totals"]["before"]["nonbrand"]["runs"]
            a_runs = measure_diff["totals"]["after"]["nonbrand"]["runs"]
            meaningful = abs(ar - br) >= MIN_RATE_DELTA and min(b_runs, a_runs) >= MIN_MEASURE_RUNS
            item["policy"] = {"min_runs": MIN_MEASURE_RUNS, "min_delta_points": MIN_RATE_DELTA * 100,
                              "meaningful": meaningful,
                              "before_wilson95": measure_diff["totals"]["before"]["nonbrand"]["wilson95"],
                              "after_wilson95": measure_diff["totals"]["after"]["nonbrand"]["wilson95"]}
            if comparison.get("status") != "comparable" or not meaningful:
                item["status"] = "inconclusive"
                flat.append(item)
            elif ar < br:
                regressions.append(item)
            elif ar > br:
                improvements.append(item)
            else:
                flat.append(item)

    warnings = []
    age = None
    day = parse_date(from_date)
    if day:
        age = (_date.today() - day).days
        if age > stale_days:
            warnings.append(
                "⚠️ 기준선이 낡았다 — 기준 스냅샷 %s, %d일 전이다(임계 %d일). 그 사이 사이트도 엔진도 "
                "여러 번 바뀌었다. 이 비교는 '무엇이 바뀌었나'는 말해도 '무엇 때문인가'는 "
                "말하지 못한다. 최근 스냅샷을 하나 더 찍고 다시 비교하라."
                % (from_date, age, stale_days))
    if measure_diff is None:
        warnings.append("측정 스냅샷이 2개 미만이다 — 인용 드리프트는 비교하지 않았다. "
                        "`measure.py report` 후 `snapshot --measure`로 남겨라.")

    return {
        "schema": SCHEMA_DRIFT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"host": host, "base": (after.get("target") or {}).get("base")},
        "from": from_date,
        "to": to_date,
        "baseline": index.get("baseline_date"),
        "baseline_age_days": age,
        "stale_days": stale_days,
        "warnings": warnings,
        "metrics": {"before": b_metrics, "after": a_metrics},
        "audit_diff": {
            "resolved": (check_by_id(audit_diff, "diff.resolved").get("evidence") or {}).get("items", []),
            "new": (check_by_id(audit_diff, "diff.new").get("evidence") or {}).get("items", []),
            "persisting": (check_by_id(audit_diff, "diff.persisting").get("evidence") or {}).get("items", []),
            "scorecard": (check_by_id(audit_diff, "diff.scorecard").get("evidence") or {}).get("lanes", {}),
            "stats": (check_by_id(audit_diff, "diff.stats").get("evidence") or {}).get("stats", {}),
            "pages": check_by_id(audit_diff, "diff.pages").get("evidence") or {},
            "summary": audit_diff.get("summary"),
        },
        "measure_diff": measure_diff,
        "regressions": regressions,
        "improvements": improvements,
        "unchanged": flat,
        "next_due": index.get("next_due"),
        "schedule": index.get("schedule") or {"next_due": index.get("next_due"),
                                               "scheduled": False},
        "exit_code": 1 if regressions else 0,
    }


def _pct(rate):
    return "—" if rate is None else "%.0f%%" % (rate * 100)


def render_drift_md(drift: dict) -> str:
    out = ["# 드리프트 — %s" % (drift["target"].get("host") or "?"), "",
           "%s → %s (기준선 %s) · su-presence drift.py"
           % (drift["from"], drift["to"], drift.get("baseline")), ""]

    for warn in drift.get("warnings") or []:
        out += ["> %s" % warn, ""]

    out += ["## ❌ 회귀 — 이것부터 본다", ""]
    if drift["regressions"]:
        for item in drift["regressions"]:
            out.append("- **%s**" % (item.get("message") or item.get("label") or item["code"]))
        out.append("")
        out += ["회귀는 배포가 만든 것일 수도, 그 사이 다른 변경이 만든 것일 수도 있다. "
                "`audit_diff.new`의 findings부터 대조하라.", ""]
    else:
        out += ["없다.", ""]

    out += ["## ✅ 개선", ""]
    if drift["improvements"]:
        for item in drift["improvements"]:
            out.append("- %s" % (item.get("message") or item.get("label") or item["code"]))
    else:
        out.append("없다.")
    out.append("")

    out += ["## — 변화 없음", ""]
    if drift["unchanged"]:
        for item in drift["unchanged"]:
            out.append("- %s %s → %s" % (item.get("label") or item["code"],
                                         item.get("before"), item.get("after")))
    else:
        out.append("없다.")
    out.append("")

    ad = drift["audit_diff"]
    out += ["## 진단 드리프트", "",
            "| 항목 | 수 |", "|---|---|",
            "| 해소된 findings | %d |" % len(ad["resolved"]),
            "| 새로 생긴 findings | %d |" % len(ad["new"]),
            "| 그대로 남은 findings | %d |" % len(ad["persisting"]),
            "| 사라진 URL | %d |" % len(ad["pages"].get("gone") or []),
            "| 새 URL | %d |" % len(ad["pages"].get("new") or []), ""]
    if ad["new"]:
        out += ["새로 생긴 findings:", ""]
        for f in ad["new"]:
            out.append("- `%s` (%s/%s) — %s" % (f.get("code"), f.get("lane"),
                                                f.get("severity"), f.get("message")))
        out.append("")

    md = drift.get("measure_diff")
    out += ["## 측정 드리프트", ""]
    if not md:
        out += ["비교할 측정 스냅샷이 없다.", ""]
    else:
        out += ["%s → %s" % (md["from"], md["to"]), "",
                "| 엔진 | 브랜드 전 | 브랜드 후 | 비브랜드 전 | 비브랜드 후 | 비브랜드 증감 |",
                "|---|---|---|---|---|---|"]
        for row in md["engines"]:
            nb = row["nonbrand"]
            out.append("| %s | %d/%d | %d/%d | %d/%d | %d/%d | %s |" % (
                row["label"],
                row["brand"]["before"]["cited"], row["brand"]["before"]["runs"],
                row["brand"]["after"]["cited"], row["brand"]["after"]["runs"],
                nb["before"]["cited"], nb["before"]["runs"],
                nb["after"]["cited"], nb["after"]["runs"],
                ("%+.1f포인트" % nb["points"]) if nb["points"] is not None else "—"))
        tb, ta = md["totals"]["before"], md["totals"]["after"]
        out += ["", "합계: 브랜드 %s → %s · 비브랜드 %s → %s"
                % (_pct(tb["brand"]["rate"]), _pct(ta["brand"]["rate"]),
                   _pct(tb["nonbrand"]["rate"]), _pct(ta["nonbrand"]["rate"])), ""]
        if md["ours_new"]:
            out += ["새로 인용되기 시작한 우리 URL:", ""]
            out += ["- %s" % u for u in md["ours_new"]] + [""]
        if md["ours_lost"]:
            out += ["인용이 끊긴 우리 URL — 왜 빠졌는지 확인하라:", ""]
            out += ["- %s" % u for u in md["ours_lost"]] + [""]
        if md["competitors"]:
            out += ["경쟁 도메인 (전 → 후):", ""]
            out += ["- %s %d → %d" % (c["domain"], c["before"], c["after"])
                    for c in md["competitors"][:10]] + [""]

    out += ["## 다음 재측정일 — %s" % (drift.get("next_due") or "미정"), "",
            "그날 이 순서대로 돌린다. 기억에 맡기지 마라 — 이 문서가 일정이다.", "",
            "```bash"] + NEXT_STEPS + ["```", "",
            "완료 조건은 \"고쳤다\"가 아니라 **다음 재측정일이 잡혀 있는 것**이다.", ""]
    return "\n".join(out)


def cmd_compare(args) -> int:
    outdir, host = resolve(args)
    index = load_index(outdir, host)
    audits = snapshots_of(index, "audit")
    if len(audits) < 2:
        raise SystemExit(
            "비교할 audit 스냅샷이 2개 미만이다 (%d개). 먼저 재크롤하고 "
            "`drift.py snapshot`으로 하나 더 남겨라." % len(audits))

    from_date = args.from_date or index.get("baseline_date") or audits[0]["date"]
    to_date = args.to_date or audits[-1]["date"]
    for label, value in (("--from", from_date), ("--to", to_date)):
        if not find_snapshot(index, "audit", value):
            raise SystemExit("%s %s 에 해당하는 audit 스냅샷이 없다. "
                             "`drift.py status`로 목록을 봐라." % (label, value))
    if from_date == to_date:
        raise SystemExit("--from과 --to가 같다 (%s) — 비교할 것이 없다." % from_date)

    drift = build_drift(outdir, host, index, from_date, to_date, args.stale_days)
    out_json = args.out_file or os.path.join(outdir, "drift.json")
    write_json(out_json, drift)
    md_path = os.path.join(os.path.dirname(os.path.abspath(out_json)), "DRIFT.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_drift_md(drift))

    print("")
    print("════════════════════════════════════════════")
    print(" 드리프트 — %s  %s → %s" % (host, from_date, to_date))
    print("════════════════════════════════════════════")
    for warn in drift["warnings"]:
        print(" %s" % warn)
    for item in drift["regressions"]:
        print(" ❌ %s" % (item.get("message") or item["code"]))
    for item in drift["improvements"]:
        print(" ✅ %s" % (item.get("message") or item["code"]))
    if not drift["regressions"] and not drift["improvements"]:
        print(" — 회귀도 개선도 없다.")
    print("")
    print(" 회귀 %d · 개선 %d · 변화 없음 %d"
          % (len(drift["regressions"]), len(drift["improvements"]), len(drift["unchanged"])))
    print(" %s" % out_json)
    print(" %s" % md_path)
    print(" 다음 재측정일: %s" % (drift.get("next_due") or "미정"))
    return drift["exit_code"]


# ─────────────────────────────────────────────────────────── status

def cmd_status(args) -> int:
    outdir, host = resolve(args)
    index = load_index(outdir, host)
    if not index["snapshots"]:
        print("")
        print("스냅샷이 하나도 없다: %s" % history_dir(outdir))
        print("먼저: python tools/drift.py snapshot out/%s/audit.json" % host)
        return 0

    measures = snapshots_of(index, "measure")
    print("")
    print("════════════════════════════════════════════")
    print(" 스냅샷 이력 — %s" % host)
    print("════════════════════════════════════════════")
    print(" 기준선        : %s" % (index.get("baseline_date") or "미지정"))
    print(" 스냅샷 수     : %d" % len(index["snapshots"]))
    print(" 마지막 측정일 : %s" % (measures[-1]["date"] if measures else "없음"))

    due = parse_date(index.get("next_due") or "")
    if due:
        left = (due - _date.today()).days
        if left < 0:
            print(" 다음 재측정   : %s ⚠️ %d일 초과 — 지금 재측정하라" % (due.isoformat(), -left))
        else:
            print(" 다음 재측정   : %s (%d일 남음)" % (due.isoformat(), left))
    else:
        print(" 다음 재측정   : 미정")
    scheduled = (index.get("schedule") or {}).get("scheduled")
    print(" 일정 등록 상태 : %s" % ("등록됨" if scheduled else "미등록 (위 날짜는 계산값)"))

    print("")
    for snap in index["snapshots"]:
        mark = " ←기준선" if (snap["kind"] == "audit"
                            and snap["date"] == index.get("baseline_date")) else ""
        label = " [%s]" % snap["label"] if snap.get("label") else ""
        print(" %s %-8s %s%s%s" % (snap["date"], snap["kind"], snap["line"], label, mark))
    print("")
    return 0


# ─────────────────────────────────────────────────────────── timeline

def timeline_rows(outdir: str, index: dict) -> list:
    rows = OrderedDict()
    for snap in index["snapshots"]:
        row = rows.setdefault(snap["date"], {"date": snap["date"], "label": snap.get("label")})
        if snap["kind"] == "audit":
            row.update(audit_metrics(snapshot_json(outdir, snap)))
        elif snap["kind"] == "measure":
            row["citation"] = measure_metrics(snapshot_json(outdir, snap))
        if snap.get("label") and not row.get("label"):
            row["label"] = snap["label"]
    return list(rows.values())


def render_timeline_md(host: str, index: dict, rows: list) -> str:
    out = ["# 추이 — %s" % host, "",
           "기준선 %s · 다음 재측정 %s · su-presence drift.py"
           % (index.get("baseline_date") or "미지정", index.get("next_due") or "미정"), "",
           "| 날짜 | 페이지 | 중복 title | JSON-LD | noindex | 비브랜드 인용률 | 브랜드 인용률 | 메모 |",
           "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        cite = row.get("citation") or {}
        nb, br = cite.get("nonbrand") or {}, cite.get("brand") or {}
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["date"],
            row.get("pages", "—"), row.get("dup_titles", "—"),
            row.get("jsonld_pages", "—"), row.get("noindex", "—"),
            ("%s (%d/%d)" % (_pct(nb.get("rate")), nb.get("cited", 0), nb.get("runs", 0)))
            if nb else "—",
            ("%s (%d/%d)" % (_pct(br.get("rate")), br.get("cited", 0), br.get("runs", 0)))
            if br else "—",
            row.get("label") or ""))
    out += ["", "빈칸(—)은 그날 그 종류의 스냅샷을 찍지 않았다는 뜻이다 — 값이 0이라는 뜻이 아니다.", ""]
    return "\n".join(out)


def cmd_timeline(args) -> int:
    outdir, host = resolve(args)
    index = load_index(outdir, host)
    if not index["snapshots"]:
        raise SystemExit("스냅샷이 없다 — 먼저 `drift.py snapshot`을 돌려라.")
    rows = timeline_rows(outdir, index)
    path = args.out_file or os.path.join(outdir, "TIMELINE.md")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_timeline_md(host, index, rows))
    print("")
    print("추이 %d행: %s" % (len(rows), path))
    return 0


# ─────────────────────────────────────────────────────────── CLI

def resolve(args) -> tuple:
    """audit.json 경로 또는 --host 로 out/<host>/ 를 찾는다 → (dir, host)."""
    if getattr(args, "audit", None):
        path = os.path.abspath(args.audit)
        outdir = os.path.dirname(path)
        host = ((load_json(path).get("target") or {}).get("host")) or os.path.basename(outdir)
        return outdir, host
    if not getattr(args, "host", None):
        raise SystemExit("audit.json 경로 또는 --host 중 하나는 필요하다.")
    return os.path.join(os.path.abspath(args.out), args.host), args.host


def add_target(parser, audit_required: bool) -> None:
    parser.add_argument("audit", nargs=None if audit_required else "?", default=None,
                        help="out/<host>/audit.json")
    if not audit_required:
        parser.add_argument("--host", default=None, help="audit.json 대신 호스트로 지정")
        parser.add_argument("--out", default="out", help="출력 루트 (기본 out)")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="기준선 스냅샷 + 드리프트 비교")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="지금 상태를 불변 스냅샷으로 남긴다")
    add_target(s, True)
    s.add_argument("--date", default=None, help="스냅샷 날짜 (기본: 오늘)")
    s.add_argument("--measure", default=None, help="measure/summary.json")
    s.add_argument("--verify", default=None, help="verify.json")
    s.add_argument("--label", default=None, help='메모 (예: "P1 배포 후")')
    s.add_argument("--baseline", action="store_true", help="이 스냅샷을 기준선으로 지정")
    s.add_argument("--force", action="store_true", help="같은 날짜 스냅샷을 덮어쓴다")

    c = sub.add_parser("compare", help="두 스냅샷을 비교한다 (기본: 기준선 vs 최신)")
    add_target(c, False)
    c.add_argument("--from", dest="from_date", default=None, help="기준 날짜 (기본: 기준선)")
    c.add_argument("--to", dest="to_date", default=None, help="비교 날짜 (기본: 최신)")
    c.add_argument("--stale-days", type=int, default=STALE_DAYS,
                   help="기준선이 이보다 오래되면 경고 (기본 %d)" % STALE_DAYS)
    c.add_argument("--out-file", default=None, help="drift.json 경로")

    st = sub.add_parser("status", help="이력 요약과 다음 재측정일")
    add_target(st, False)

    t = sub.add_parser("timeline", help="날짜별 핵심 지표 표 → TIMELINE.md")
    add_target(t, False)
    t.add_argument("--out-file", default=None, help="TIMELINE.md 경로")

    args = ap.parse_args(argv)
    return {"snapshot": cmd_snapshot, "compare": cmd_compare,
            "status": cmd_status, "timeline": cmd_timeline}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
