#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified entry point for local SEO/GEO audit, drafts, verification and measurement."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".seo-geo-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def audit(args):
    import crawl
    import report

    result = crawl.build_report(args.target, args.max_pages, args.delay, args.allow_noindex)
    folder = Path(args.out).resolve() / crawl.safe_host(result["target"]["host"])
    path = folder / "audit.json"
    # Keep the previous local observation; a re-audit must not silently erase it.
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_json(folder / "observations" / ("audit-" + stamp + ".json"), old)
    atomic_json(path, result)
    report_path = folder / "report.html"
    report_path.write_text(report.render(result, args.lang), encoding="utf-8")
    crawl.print_summary(result)
    print("\n진단: %s\n보고서: %s" % (path, report_path))
    print("다음: python tools/seo_geo.py generate all \"%s\" --site <회사사실.json>" % path)
    # Findings are data; incomplete collection is an unsuccessful audit run.
    return 0 if (result.get("coverage") or {}).get("complete") is True else 2


def status(args):
    audit_path = Path(args.audit).resolve()
    root = audit_path.parent
    result = {"schema": "su-presence/status/1", "audit": str(audit_path), "artifacts": {}}
    names = {"audit": audit_path, "deploy": root / "deploy" / ".su-presence-generated.json",
             "verify": root / "verify.json", "measure": root / "measure" / "summary.json",
             "drift": root / "drift.json"}
    for name, path in names.items():
        if not path.exists():
            result["artifacts"][name] = {"state": "missing"}
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("object required")
            result["artifacts"][name] = {
                "state": "recorded", "path": str(path), "schema": data.get("schema"),
                "generated_at": data.get("generated_at"),
            }
            if name == "audit":
                result["artifacts"][name]["coverage"] = data.get("coverage", {"complete": None})
            if name == "verify":
                result["artifacts"][name].update(summary=data.get("summary"), exit_code=data.get("exit_code"))
            if name == "measure":
                result["artifacts"][name]["window"] = data.get("window")
            if name == "drift":
                result["artifacts"][name].update(next_due=data.get("next_due"), exit_code=data.get("exit_code"))
        except (OSError, ValueError):
            result["artifacts"][name] = {"state": "invalid", "path": str(path)}
    result["note"] = "파일은 기록일 뿐 현재 배포·검색 성과의 증명이 아니다. next_due는 예약 실행을 만들지 않는다."
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["artifacts"]["audit"]["state"] == "recorded" else 2


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    dispatch = {name: name for name in ("generate", "verify", "measure", "collect",
                                        "drift", "report")}
    if argv and argv[0] in dispatch:
        try:
            return importlib.import_module(dispatch[argv[0]]).main(argv[1:])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print("입력/산출물을 확인하세요: %s" % exc, file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(
        description="SEO/GEO — audit → generate → verify → measure → drift",
        epilog="세부 도움말: python tools/seo_geo.py <generate|verify|measure|collect|drift|report> --help")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("audit", help="사이트 크롤과 HTML 보고서를 함께 생성")
    p.add_argument("target")
    p.add_argument("--out", default="out")
    p.add_argument("--max-pages", type=int, default=300)
    p.add_argument("--delay", type=float, default=0.5)
    p.add_argument("--allow-noindex", action="append", default=[], metavar="PATH",
                   help="의도한 비색인 경로 (반복 가능)")
    p.add_argument("--lang", choices=("ko", "en"), default="ko")
    p.set_defaults(func=audit)
    p = sub.add_parser("status", help="로컬 산출물 기록 상태 확인 (네트워크 없음)")
    p.add_argument("audit")
    p.set_defaults(func=status)
    p = sub.add_parser("doctor", help="실행 환경과 도구 로딩 점검 (네트워크 없음)")
    p.set_defaults(func=doctor)
    for name in dispatch:
        sub.add_parser(name, help="%s 도구의 명령/옵션 전달" % name, add_help=False)
    args = parser.parse_args(argv)
    if args.command == "audit" and (args.max_pages < 1 or args.delay < 0):
        parser.error("max-pages는 1 이상, delay는 0 이상이어야 합니다")
    try:
        return args.func(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("실행 실패: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("중단했습니다. 완료되지 않은 관측을 성공으로 기록하지 않습니다.", file=sys.stderr)
        return 130


def doctor(args):
    supported = sys.version_info >= (3, 10)
    modules = {}
    for name in ("crawl", "report", "generate", "verify", "measure", "collect", "drift"):
        try:
            importlib.import_module(name)
            modules[name] = "ok"
        except Exception as exc:
            modules[name] = exc.__class__.__name__
    print(json.dumps({"python": sys.version.split()[0], "supported": supported,
                      "tools": modules, "dependencies": "Python standard library",
                      "network_checked": False}, ensure_ascii=False, indent=2))
    return 0 if supported and all(x == "ok" for x in modules.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
