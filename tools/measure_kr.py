#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure-kr.py — 한국 엔진 인용 실측 (네이버 · 다음)

기존 audit.sh의 마지막 줄은 "각 엔진 인용 O/X는 사람이 확인"이었다.
그 중 자동화 가능한 부분을 여기로 옮긴다. 사람에게 미루는 항목을 줄이는 것이 목적이다.

측정 대상
  · 네이버  자연검색 가시 노출 (PC / 모바일)
  · 네이버  AI 브리핑 발동 여부 + 인용 출처 도메인
  · 다음    통합웹·사이트 블록 가시 노출
  · 다음    AI 요약(Solar) 발동 여부 + 인용 출처 도메인

측정 불가 — 브라우저가 필요하다 (렌더링 후에만 보인다)
  · 구글 AI 개요 / Perplexity / ChatGPT
    → 헤드리스 브라우저로 SERP를 열고 '가시 텍스트'에서 도메인을 찾아야 한다

사용:
  python measure-kr.py --domains example.com,example.co.kr \
                       --queries queries.txt [--json out.json]
  (queries.txt: 한 줄에 질문 하나. 5~10개를 고정하고 매번 같은 것으로 잰다)
"""
import argparse, json, re, sys, time, io, urllib.parse
from datetime import datetime

import urllib.request
import urllib.error

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
UA_MO = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# Accept-Encoding 은 아예 보내지 않는다 — br 로 받으면 본문이 통째로 깨진다.
ENC = "gzip, deflate"
PAUSE = 3.0          # 반복 측정은 이 간격 아래로 내리지 마라 (아래 SANE 주석 참조)

# 반복 요청을 몰아치면 검색엔진이 축소 응답을 준다. 그 응답으로 집계하면
# "인용 0"이 찍히는데, 그건 엔진의 사실이 아니라 우리가 만든 0이다.
# 정상 SERP는 수백 KB다 — 이 바닥을 못 넘으면 집계하지 않고 멈춘다.
SANE_MIN = {"naver": 200_000, "daum": 150_000}


class Throttled(RuntimeError):
    """축소 응답 — 측정값으로 쓰면 안 된다."""


def _gate(engine, html, q):
    floor = SANE_MIN[engine]
    if len(html) < floor:
        raise Throttled(
            f"{engine} 응답 {len(html):,}B < 기준 {floor:,}B — 스로틀링으로 보인다. "
            f"질의={q!r}. 간격을 늘리거나 시간을 두고 다시 재라. "
            f"이 응답으로 0을 기록하면 안 된다.")
    return html


def strip_code(html: str) -> str:
    """script/style/주석을 걷어낸 '보이는 영역'.

    이걸 안 하면 JS 페이로드에 박힌 문자열이 노출로 잡힌다.
    실제로 다음 SERP에서 남의 블로그 썸네일 URL 때문에 오탐이 났다.
    """
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def hits(html: str, domains):
    """가시 영역 기준 도메인 히트. raw만 있고 visible이 0이면 오탐이다."""
    vis = strip_code(html)
    out = {}
    for d in domains:
        raw_n = len(re.findall(re.escape(d), html, re.I))
        vis_n = len(re.findall(re.escape(d), vis, re.I))
        if raw_n:
            out[d] = {"visible": vis_n, "script_only": raw_n - vis_n}
    return out


# ─────────────────────────── 네이버 ───────────────────────────

def _get(url, ua, referer=None, accept=None, timeout=30):
    """표준 라이브러리만으로 가져온다 (저장소 정책).

    Accept-Encoding 은 보내지 않는다. 압축을 요청하면 직접 풀어야 하고,
    br 를 받으면 본문이 통째로 깨진다 (ops/measure-playbook.md).
    """
    headers = {"User-Agent": ua, "Accept-Language": "ko-KR,ko;q=0.9"}
    if referer:
        headers["Referer"] = referer
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def naver_serp(q, mobile=False):
    base = ("https://m.search.naver.com/search.naver" if mobile
            else "https://search.naver.com/search.naver")
    url = base + "?query=" + urllib.parse.quote(q)
    return _gate("naver", _get(url, UA_MO if mobile else UA_PC), q)


def naver_ai_briefing(html, q, mobile=True):
    """AI 브리핑 발동 여부와 인용 출처.

    두 변종이 있다:
      aibAnswer        — props.summary/props.sources가 HTML에 인라인 (사전 생성형)
      aibAnswerRuntime — props.apiURL로 SSE 스트리밍 (실시간형)

    주의: API는 아무 질의에나 답을 만들어 준다. 따라서 '실제로 떴는가'는
    반드시 SERP HTML의 fender 블록 존재로 판정해야 한다. API 응답만 보고
    "인용됐다"고 하면 틀린 보고가 된다.
    """
    fired = bool(re.search(r'data-block-id="ai-briefing/', html))
    res = {"fired": fired, "variant": None, "sources": [], "note": ""}
    if not fired:
        return res

    # 1) 인라인 변종
    m = re.search(r'"sources"\s*:\s*(\[.*?\])\s*[,}]', html, flags=re.S)
    if m and '"url"' in m.group(1):
        try:
            res["variant"] = "aibAnswer(inline)"
            res["sources"] = [s.get("url", "") for s in json.loads(m.group(1)) if isinstance(s, dict)]
            return res
        except Exception:
            pass

    # 2) 런타임 변종 — apiURL을 그대로 GET하면 text/event-stream
    m = re.search(r'"apiURL"\s*:\s*"([^"]+)"', html)
    if not m:
        res["note"] = "fender 블록은 있으나 payload를 못 찾았다"
        return res
    api = m.group(1).encode().decode("unicode_escape")
    ref = ("https://m.search.naver.com/search.naver?query=" if mobile
           else "https://search.naver.com/search.naver?query=") + urllib.parse.quote(q)
    try:
        body = _get(api, UA_MO if mobile else UA_PC, referer=ref,
                    accept="text/event-stream", timeout=45)
        res["variant"] = "aibAnswerRuntime(SSE)"
        chips, pool, ev = [], [], None
        for line in body.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
                continue
            if not line.startswith("data:") or ev not in ("sources", "footnote_sources"):
                continue
            try:
                arr = json.loads(line[5:].strip())
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            urls = [x["url"] for x in arr if isinstance(x, dict) and x.get("url")]
            (chips if ev == "sources" else pool).extend(urls)
        res["sources"] = list(dict.fromkeys(chips))          # 화면 노출 = 판정 기준
        res["evidence_pool"] = list(dict.fromkeys(pool))     # 참고: 근거 풀
    except Exception as e:
        res["note"] = f"SSE 실패: {e}"
    return res


# ──────────────────────────── 다음 ────────────────────────────

def _chrome():
    """설치된 Chrome 경로를 찾는다. 없으면 None."""
    import shutil, os
    for c in ("google-chrome", "chrome", "chromium"):
        w = shutil.which(c)
        if w:
            return w
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(c):
            return c
    return None


def daum_serp(q):
    """다음 SERP.

    ⚠️ 단순 HTTP로 받으면 축소된 SERP가 온다 — 실제로는 있는 결과가 0건으로 보인다.
       헤드리스 Chrome의 렌더링 DOM을 기준으로 삼는다. (ops/measure-playbook.md)
    ⚠️ 페이지 인코딩은 EUC-KR이다.
    """
    import subprocess, tempfile, shutil, os
    url = "https://search.daum.net/search?w=tot&q=" + urllib.parse.quote(q)
    ch = _chrome()
    if not ch:
        raise RuntimeError("Chrome을 찾지 못했다 — 다음 측정은 헤드리스 브라우저가 필요하다. "
                           "단순 HTTP 응답은 축소 SERP라 쓰면 안 된다.")
    prof = tempfile.mkdtemp()
    try:
        out = subprocess.run(
            [ch, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-blink-features=AutomationControlled",
             "--user-agent=" + UA_PC, "--lang=ko-KR",
             "--user-data-dir=" + prof, "--virtual-time-budget=15000",
             "--dump-dom", url],
            capture_output=True, timeout=90)
        return _gate("daum", out.stdout.decode("utf-8", errors="replace"), q)
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def daum_ai_summary(html):
    """AI 요약(Solar) 발동 여부와 인용 출처.

    gsid 접두사가 출처 종류를 알려준다:
      tstory- 티스토리 / nvblg_ 네이버블로그 / cafe- 다음카페 / web- 일반 오픈웹 / (빈값) 다음 자사
    자사 도메인만 가진 회사는 'web-' 슬롯을 다투는 것이다.
    """
    fired = bool(re.search(r'disp-attr="AIO"|aioColl', html))
    res = {"fired": fired, "sources": [], "kinds": {}}
    if not fired:
        return res
    for m in re.finditer(r'"url"\s*:\s*"([^"]+)"\s*,\s*"gsid"\s*:\s*"([^"]*)"', html):
        url, gsid = m.group(1), m.group(2)
        res["sources"].append(url)
        if gsid.startswith("tstory-"):
            k = "티스토리"
        elif gsid.startswith("nvblg"):
            k = "네이버블로그"
        elif gsid.startswith("cafe-"):
            k = "다음카페"
        elif gsid.startswith("web-"):
            k = "오픈웹"
        else:
            k = "다음자사"
        res["kinds"][k] = res["kinds"].get(k, 0) + 1
    res["sources"] = list(dict.fromkeys(res["sources"]))
    return res


# ──────────────────────────── 실행 ────────────────────────────

def ours_in(urls, domains):
    return [u for u in urls if any(d.lower() in u.lower() for d in domains)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", required=True, help="쉼표구분. 자사 도메인 전부 (미러·구도메인 포함)")
    ap.add_argument("--queries", required=True, help="질문 파일 (한 줄에 하나)")
    ap.add_argument("--json", help="결과 JSON 저장 경로")
    ap.add_argument("--skip-daum", action="store_true", help="다음 측정 생략")
    ap.add_argument("--repeat", type=int, default=1,
                    help="같은 질문 반복 횟수. 생성 답변은 매번 다르므로 5~10 권장 "
                         "(ops/measure.md — 1회는 표본이 아니다)")
    ap.add_argument("--pause", type=float, default=PAUSE,
                    help=f"요청 간격(초). 기본 {PAUSE}. 반복이 많으면 늘려라")
    a = ap.parse_args()
    globals()["PAUSE"] = a.pause

    domains = [d.strip() for d in a.domains.split(",") if d.strip()]
    with io.open(a.queries, encoding="utf-8") as f:
        queries = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    N = max(1, a.repeat)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"측정 {stamp} · 질문 {len(queries)}개 · 도메인 {len(domains)}개 · 반복 {N}회")
    if N < 5:
        print("주의: 반복 5회 미만이다. 생성 답변은 매번 달라 1~4회는 표본이 아니다 (ops/measure.md)")
    print("=" * 78)

    rows = []
    for q in queries:
        c = {"nv_vis": 0, "nv_ai_fire": 0, "nv_ai_cite": 0,
             "dm_vis": 0, "dm_ai_fire": 0, "dm_ai_cite": 0}
        nv_src, dm_src = set(), set()
        aborted = 0
        for _ in range(N):
            try:
                mo = naver_serp(q, True)
            except Throttled as e:
                aborted += 1
                print(f"   [중단] {e}")
                break
            if any(v["visible"] for v in hits(mo, domains).values()):
                c["nv_vis"] += 1
            ai = naver_ai_briefing(mo, q, True)
            if ai["fired"]:
                c["nv_ai_fire"] += 1
                nv_src.update(urllib.parse.urlparse(u).netloc for u in ai["sources"] if u)
                if ours_in(ai["sources"], domains):
                    c["nv_ai_cite"] += 1
            time.sleep(PAUSE)

            if not a.skip_daum:
                try:
                    dh = daum_serp(q)
                except Exception as e:
                    print(f"   다음 수집 실패: {e}")
                    continue
                if any(v["visible"] for v in hits(dh, domains).values()):
                    c["dm_vis"] += 1
                d = daum_ai_summary(dh)
                if d["fired"]:
                    c["dm_ai_fire"] += 1
                    dm_src.update(urllib.parse.urlparse(u).netloc for u in d["sources"] if u)
                    if ours_in(d["sources"], domains):
                        c["dm_ai_cite"] += 1
                time.sleep(PAUSE)

        done = N - aborted * N  # 중단되면 이 질문은 측정 실패로 남긴다
        print()
        print(f"■ {q}")
        if aborted:
            print("   측정 실패 — 스로틀링. 0으로 기록하지 않는다")
            rows.append({"query": q, "repeat": N, "status": "throttled"})
            continue
        print(f"   네이버 자연노출 : {c['nv_vis']}/{N}")
        nvs = (" · 출처 " + str(sorted(nv_src)[:5])) if nv_src else ""
        print(f"   네이버 AI브리핑 : 발동 {c['nv_ai_fire']}/{N} · 인용 {c['nv_ai_cite']}/{N}{nvs}")
        if not a.skip_daum:
            dms = (" · 출처 " + str(sorted(dm_src)[:5])) if dm_src else ""
            print(f"   다음   자연노출 : {c['dm_vis']}/{N}")
            print(f"   다음   AI요약   : 발동 {c['dm_ai_fire']}/{N} · 인용 {c['dm_ai_cite']}/{N}{dms}")
        rows.append({"query": q, "repeat": N, "counts": c,
                     "naver_sources": sorted(nv_src), "daum_sources": sorted(dm_src)})
        sys.stdout.flush()

    print()
    print("=" * 78)
    print("표기 규약 — 전수로 재지 않은 것을 전수처럼 적지 마라 (ops/measure.md)")
    print("구글 AI개요 · Perplexity · ChatGPT · Gemini · Claude는 렌더링/로그인이 필요하다.")
    print("각 엔진의 중립 모드와 접근법은 ops/measure-playbook.md 를 따른다.")
    print(f"재측정 권장: 변경 후 14일. 이번 기준선 = {stamp}")

    if a.json:
        with io.open(a.json, "w", encoding="utf-8") as f:
            json.dump({"measured_at": stamp, "repeat": N, "domains": domains, "rows": rows},
                      f, ensure_ascii=False, indent=2)
        print(f"-> {a.json}")


if __name__ == "__main__":
    main()
