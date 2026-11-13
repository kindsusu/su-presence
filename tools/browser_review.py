"""Open robots-excluded URLs in a desktop browser and record separate observations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import webbrowser


LIMIT = 30
REASONS = {
    "disallow": ("robots.txt 차단", "Disallowed by robots.txt"),
    "robots_unavailable": ("robots.txt 미확인", "Robots policy unavailable"),
}
RESULTS = ("visible", "login_required", "blocked", "error")


def _origin(url):
    if not isinstance(url, str) or len(url) > 8192 or "\\" in url or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme not in ("http", "https") or not p.hostname or p.username is not None
                or p.password is not None or "%" in p.netloc):
            return None
        return (p.scheme, p.hostname.lower(), p.port if p.port is not None else
                (443 if p.scheme == "https" else 80))
    except ValueError:
        return None


def offer(coverage, base, url, reason):
    origin = _origin(base)
    if reason not in REASONS or not origin or _origin(url) != origin:
        return
    items = coverage.setdefault("browser_review", [])
    if len(items) < LIMIT and not any(item["url"] == url for item in items):
        items.append({"url": url, "reason": reason})


def candidates(audit):
    """Validate again before rendering a saved file or passing a URL to the OS."""
    result = {}
    for item in (audit.get("coverage") or {}).get("browser_review") or []:
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            offer(result, (audit.get("target") or {}).get("base"), item.get("url"), item["reason"])
    return result.get("browser_review", [])


def open_page(item):
    print("일반 브라우저에서 확인:", item["url"])
    try:
        if webbrowser.open_new_tab(item["url"]):
            return True
    except (OSError, webbrowser.Error):
        pass
    print("브라우저를 열 수 없습니다. 위 주소를 직접 여세요.", file=sys.stderr)
    return False


def add_option(parser):
    parser.add_argument("--browser-fallback", action=argparse.BooleanOptionalAction, default=None,
                        help="robots 제외 시 첫 후보를 기본 브라우저로 열기 (기본: 대화형 터미널에서만)")


def after_audit(audit, path, launch=None):
    items = candidates(audit)
    if items:
        print("일반 브라우저 확인 후보 %d개 (최대 %d). 목록과 기록: "
              'python tools/seo_geo.py browser-review "%s"' % (len(items), LIMIT, path))
        if launch is True or (launch is None and sys.stdin.isatty() and sys.stdout.isatty()):
            open_page(items[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description="크롤 정책으로 제외된 URL을 일반 브라우저에서 확인하고 별도로 기록")
    parser.add_argument("audit", type=Path)
    parser.add_argument("--page", type=int, default=1, help="목록의 번호 (기본 1)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--open", action="store_true", help="선택한 URL을 기본 브라우저로 열기")
    action.add_argument("--result", choices=RESULTS, help="직접 확인한 결과 기록")
    parser.add_argument("--note", default="", help="공개 화면에서 확인한 내용 (선택)")
    args = parser.parse_args(argv)
    if args.note and (not args.result or len(args.note) > 2000):
        parser.error("--note는 --result와 함께 최대 2000자로 기록합니다")
    raw = args.audit.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("target"), dict):
        parser.error("target이 있는 audit.json을 지정하세요")
    items = candidates(data)
    digest = hashlib.sha256(raw).hexdigest()
    log = args.audit.parent / "browser-reviews" / (digest + ".jsonl")
    if not args.open and not args.result:
        if not items:
            print("이 진단에 기록된 브라우저 확인 후보가 없습니다.")
            return 0
        latest = {}
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                latest[record["url"]] = record["result"]
        for i, item in enumerate(items, 1):
            print("%d. %s | %s | %s" % (i, item["url"], REASONS[item["reason"]][0],
                                        latest.get(item["url"], "미확인")))
        return 0
    if not 1 <= args.page <= len(items):
        parser.error("이 진단의 목록에 있는 페이지 번호를 선택하세요")
    item = items[args.page - 1]
    if args.open:
        return 0 if open_page(item) else 2
    record = dict(item, schema="su-presence/browser-observation/1", audit_sha256=digest,
                  surface="ordinary_browser", result=args.result, note=args.note,
                  observed_at=datetime.now(timezone.utc).isoformat())
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("브라우저 관측 기록:", log)
    return 0
