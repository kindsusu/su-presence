#!/usr/bin/env bash
# Phase 0 진단 — 크롤러의 눈으로 사이트를 훑는다.
# 사용: bash audit.sh example.com
set -uo pipefail

# 한글 등 멀티바이트 글자 수를 바이트가 아니라 글자로 세기 위한 로케일 (${#var}·wc -c는 바이트를 센다)
export LC_ALL=C.UTF-8
[ "$(printf '한' | wc -m | tr -d ' ')" = "1" ] || export LC_ALL=en_US.UTF-8

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "사용법: bash audit.sh <도메인>   (예: bash audit.sh example.com)"
  exit 1
fi
case "$DOMAIN" in http*) URL="$DOMAIN" ;; *) URL="https://$DOMAIN" ;; esac
authority="${URL#*://}"; authority="${authority%%[/?#]*}"
BASE="${URL%%://*}://$authority"
# crawl.py 와 같은 신분으로 다닌다. 토큰이 갈리면 상대 서버 로그에서 한 도구로 안 보인다.
# Mozilla 껍데기는 의도적이다 — 맨 토큰만 보내면 축소 응답을 주는 사이트가 있다.
UA='Mozilla/5.0 (compatible; su-presence-audit/2.1)'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NL='
'
COUNT() { grep -oi "$1" | wc -l | tr -d ' '; }
NCHAR() { wc -m | tr -d ' '; }

# 태그만 지우면 inline <script>/<style> 본문이 '본문 텍스트'로 잡힌다.
# 그러면 CSR 셸이 텍스트 풍부한 페이지로 보여, CSR 탐지기가 CSR을 통과시킨다.
VISIBLE() {
  perl -0777 -pe 's{<script\b.*?</script>}{ }gis; s{<style\b.*?</style>}{ }gis; s{<!--.*?-->}{ }gs' 2>/dev/null \
    | sed 's/<[^>]*>/ /g' | tr -s ' \n' ' '
}

# 속성 순서·따옴표 종류에 의존하지 않는 추출 (stdin: HTML)
#   $1 = 태그 매칭 정규식, $2 = 뽑을 속성명
ATTR() {
  tr '\n' ' ' | sed 's/>/>\n/g' | grep -iE "$1" | head -1 \
    | grep -oiE "$2=(\"[^\"]*\"|'[^']*')" | head -1 | sed -E 's/^[^=]*=.//; s/.$//'
}

# 판정기(crawl.py)를 돌릴 Python 3.10+를 고른다 → $PY (없으면 빈 값).
# macOS·일부 Linux에는 python이 없거나 2.x이고, Windows의 python3는 스토어 안내용 가짜일 수 있다.
PICK_PY() {
  PY=""
  for c in python python3; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$c"; return 0
    fi
  done
  return 1
}

# robots.txt에서 특정 UA의 실효 정책 판정. stdin=robots.txt, $1=UA
# 출력: explicit-allow|explicit-block|explicit-partial|star-allow|star-block|star-partial|none
# Python 크롤러와 같은 파서(crawl.robots_policy)를 써서 Shell/Python 판정 차이를 막는다 —
# User-agent는 제품 토큰(첫 '/'·공백 앞, 대소문자 무시)으로 맞춘다: GPTBot/1.1=GPTBot, GPTBot-Image≠GPTBot.
# 판정기를 못 돌리면 아무것도 출력하지 않고 실패한다(호출 쪽이 '미확인'으로 적는다 — '허용'이 아니다).
POLICY() {
  [ -n "${PY:-}" ] || return 1
  "$PY" -c 'import sys
sys.path.insert(0, sys.argv[1])
import crawl
raw = sys.stdin.buffer.read().decode("utf-8", "replace")
print(crawl.robots_policy(raw, sys.argv[2]))' "$SCRIPT_DIR" "$1" 2>/dev/null | tr -d '\r'
}

# robots.txt 응답 분류 — crawl.robots_fetch_state(RFC 9309 2.3.1)와 같은 규칙.
#   $1=HTTP 코드(curl %{http_code}, 접속 실패면 000) $2=curl 종료 코드 $3=본문
# 출력: ok(2xx 텍스트) | absent(429 뺀 4xx, 200으로 꾸민 HTML → 제한 없음)
#       | unreachable(429·5xx·접속 실패·시간 초과·따라가지 못한 3xx → 정책 미확인)
RB_STATE() {
  case "$1" in ''|*[!0-9]*) echo unreachable; return ;; esac
  if [ "$1" -lt 100 ] || [ "$1" = 429 ] || [ "$1" -ge 500 ]; then echo unreachable; return; fi
  if [ "$1" -ge 200 ] && [ "$1" -lt 300 ]; then
    # 본문을 받다 끊기면(시간 초과 등) 일부만 읽은 규칙으로 판정하지 않는다
    if [ "$2" != 0 ]; then echo unreachable; return; fi
    if [ "$(printf '%s' "$3" | tr -d ' \t\r\n' | cut -c1)" = "<" ]; then echo absent; return; fi
    echo ok; return
  fi
  if [ "$1" -ge 400 ]; then echo absent; return; fi
  echo unreachable
}

# URL → 비교용 호스트[:포트] (crawl._host_key와 같은 규칙: 소문자, 스킴 기본 포트는 뗀다).
# http(s)가 아니거나 호스트를 읽을 수 없으면 실패. '@'·'\'가 든 authority는 해석이 도구마다
# 갈리므로(userinfo·역슬래시) 읽지 않는다 — 그런 리다이렉트는 따라가지 않는다.
HOST_KEY() {
  local u="$1" scheme auth host port def
  case "$u" in *://*) ;; *) return 1 ;; esac
  scheme=$(printf '%s' "${u%%://*}" | tr 'A-Z' 'a-z')
  case "$scheme" in http) def=80 ;; https) def=443 ;; *) return 1 ;; esac
  auth="${u#*://}"; auth="${auth%%[/?#]*}"
  auth=$(printf '%s' "$auth" | tr 'A-Z' 'a-z')
  case "$auth" in *@*|*\\*|*[[:space:][:cntrl:]]*) return 1 ;; esac
  case "$auth" in
    \[*\]|\[*\]:*) host="${auth%%]*}]"; port="${auth#"$host"}"; port="${port#:}" ;;
    *:*)           host="${auth%%:*}";  port="${auth#*:}" ;;
    *)             host="$auth";        port="" ;;
  esac
  [ -n "$host" ] && [ "$host" != "[]" ] || return 1
  case "$port" in *[!0-9]*) return 1 ;; esac
  if [ -n "$port" ]; then
    [ "${#port}" -le 5 ] || return 1
    port=$((10#$port))
    [ "$port" -le 65535 ] || return 1
    [ "$port" = "$def" ] && port=""
  fi
  printf '%s%s' "$host" "${port:+:$port}"
}

# 호스트 키의 www↔apex 변형 (crawl.alt_host_of와 같은 규칙). IP·localhost에는 변형이 없다 → 실패.
ALT_HOST() {
  local key="$1" host port=""
  case "$key" in ''|\[*) return 1 ;; esac
  host="${key%%:*}"; [ "$host" = "$key" ] || port=":${key#*:}"
  case "$host" in
    ''|www.|localhost) return 1 ;;
    www.*) printf '%s%s' "${host#www.}" "$port" ;;
    *[!0-9.]*) printf 'www.%s%s' "$host" "$port" ;;
    *) return 1 ;;
  esac
}

# 리다이렉트 목적지를 따라가도 되는가: $1=진단 대상 호스트 키, $2=목적지 URL.
# http(s)이고 호스트가 대상 그대로이거나 그 www↔apex 변형일 때만 허용 (crawl.fetch_robots와 같은 범위).
HOST_ALLOWED() {
  local k
  [ -n "$1" ] || return 1
  k=$(HOST_KEY "$2") || return 1
  [ "$k" = "$1" ] && return 0
  [ "$k" = "$(ALT_HOST "$1")" ]
}

# Apply the fetched policy only to its own origin, including each content redirect.
PATH_ALLOWED() {
  case "$1" in "$BASE"|"$BASE/"*|"$BASE?"*|"$BASE#"*) ;; *) return 1 ;; esac
  [ -z "$RB" ] || printf '%s' "$RB" | "$PY" -c 'import sys, urllib.parse
sys.path.insert(0, sys.argv[1])
import crawl
u = urllib.parse.urlsplit(sys.argv[2])
rules = crawl.crawl_rules(sys.stdin.buffer.read().decode("utf-8","replace"), crawl.UA.split("/")[0])
sys.exit(0 if crawl.crawl_allowed(rules, (u.path or "/") + ("?" + u.query if u.query else "")) else 1)' "$SCRIPT_DIR" "$1"
}

# 한 URL을 받는다. curl -L은 어느 도메인으로든 따라가므로 쓰지 않고, 리다이렉트를 한 홉씩 직접 따라간다.
#   $1=URL $2=최대 홉 수 $3=홉당 시간 제한(초) $4=0이면 본문을 버린다(기본 1)
# 결과(전역): F_CODE(마지막 응답 코드, 접속 실패 000) F_RC(curl 종료 코드) F_BODY F_URL(마지막 요청 주소)
#   F_HOPS(따라간 홉 수) F_NEXT(따라가지 않은 목적지) F_MAX(=$2)
#   F_REASON — 따라가지 않은 이유: other_host(대상·www↔apex 밖, http(s) 아님) · too_many_redirects · 빈 값
# 본문과 상태 코드를 한 요청으로 받는다 (두 번 요청하면 두 응답이 다를 수 있다).
FETCH() {
  local out meta
  F_URL="$1"; F_MAX="$2"; F_HOPS=0; F_REASON=""; F_NEXT=""; F_BODY=""
  while :; do
    if [ "${POLICY_READY:-0}" = 1 ] && ! PATH_ALLOWED "$F_URL"; then
      F_RC=1; F_CODE=000; F_REASON=policy; F_NEXT=""; F_BODY=""; break
    fi
    if [ "${4:-1}" = 0 ]; then
      out="$(curl -s -o /dev/null --max-time "$3" -A "$UA" -w "${NL}%{http_code} %{redirect_url}" -- "$F_URL")"
    else
      out="$(curl -s --max-time "$3" -A "$UA" -w "${NL}%{http_code} %{redirect_url}" -- "$F_URL")"
    fi
    F_RC=$?
    meta="${out##*"$NL"}"; F_BODY="${out%"$NL"*}"
    F_CODE="${meta%% *}"; F_NEXT="${meta#* }"
    [ "$F_RC" = 0 ] || break
    case "$F_CODE" in 301|302|303|307|308) ;; *) F_NEXT=""; break ;; esac
    [ -n "$F_NEXT" ] || break                  # Location 없는 3xx — 호출 쪽이 3xx로 다룬다
    if ! HOST_ALLOWED "$ORIGIN_KEY" "$F_NEXT"; then F_REASON=other_host; break; fi
    if [ "$F_HOPS" -ge "$F_MAX" ]; then F_REASON=too_many_redirects; break; fi
    F_HOPS=$((F_HOPS + 1)); F_URL="$F_NEXT"
  done
}

# FETCH가 리다이렉트를 따라가지 않은 이유 한 문장 (F_REASON이 비면 아무것도 출력하지 않는다)
BLOCKED_NOTE() {
  local k
  case "$F_REASON" in
    policy) printf 'robots.txt가 허용하지 않거나 대상 origin 밖인 주소' ;;
    other_host)
      if k=$(HOST_KEY "$F_NEXT"); then printf '다른 도메인(%s)으로 리다이렉트된다' "$k"
      else printf 'http(s)가 아니거나 읽을 수 없는 주소로 리다이렉트된다'; fi ;;
    too_many_redirects) printf '리다이렉트가 연속 %s번을 넘는다' "$F_MAX" ;;
  esac
}

# 상태 코드만 (본문은 버린다). 따라가지 않은 리다이렉트는 이유를 덧붙인다.
CODE() {
  FETCH "$1" 10 15 0
  printf '%s' "$F_CODE"
  [ -z "$F_REASON" ] || printf ' (%s — 따라가지 않음)' "$(BLOCKED_NOTE)"
}

echo "════════════════════════════════════════════"
echo " Phase 0 진단 — $BASE"
echo " $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════"

ORIGIN_KEY=$(HOST_KEY "$BASE") || { echo "진단 URL의 호스트를 읽을 수 없다"; exit 2; }
# 리다이렉트는 crawl.fetch_robots와 같은 범위만 따라간다: 진단 대상 호스트와 www↔apex 변형 사이,
# RFC 9309 최소치이자 Google과 같은 5홉까지. 다른 도메인·5홉 초과는 3xx로 끝나 '미확인'이 된다.
FETCH "$BASE/robots.txt" 5 20
RB_RC=$F_RC; RB_CODE=$F_CODE; RB="$F_BODY"
RB_URL="$F_URL"; RB_HOPS=$F_HOPS; RB_REASON="$F_REASON"; RB_NOTE="$(BLOCKED_NOTE)"
RB_ST=$(RB_STATE "$RB_CODE" "$RB_RC" "$RB")
[ -z "$RB_REASON" ] || RB_ST=unreachable
[ "$RB_HOPS" -gt 0 ] && [ -z "$RB_REASON" ] && RB_FOLLOWED="   └ 리다이렉트 ${RB_HOPS}홉을 따라감 → $RB_URL" || RB_FOLLOWED=""
case "$RB_ST" in
  ok)
    echo "   robots.txt     : 있음 (HTTP $RB_CODE)"
    [ -z "$RB_FOLLOWED" ] || echo "$RB_FOLLOWED" ;;
  # 404를 200처럼 꾸민 HTML 오류 페이지도 여기 — robots.txt로 오인하지 않는다
  absent)
    echo "   robots.txt     : ❌ 없음 또는 비정상 (HTTP $RB_CODE) — 크롤러는 제한 없음으로 본다"
    [ -z "$RB_FOLLOWED" ] || echo "$RB_FOLLOWED"
    RB="" ;;
  *)
    if [ "$RB_REASON" = other_host ]; then
      echo "   robots.txt     : ❓ 미확인 — HTTP $RB_CODE, $RB_NOTE — 서버 장애가 아니다"
      echo "   └ 진단 대상과 www↔apex 변형 밖의 호스트는 따라가지 않아 그쪽 내용은 확인하지 않았다."
      echo "     크롤러는 리다이렉트를 따라가 도착한 파일을 적용할 수 있으니 의도한 설정인지 확인하라"
    elif [ "$RB_REASON" = too_many_redirects ]; then
      echo "   robots.txt     : ❓ 미확인 — HTTP $RB_CODE, $RB_NOTE"
      echo "   └ RFC 9309와 Google은 5홉까지만 따라가므로 크롤러도 이 파일을 읽지 못할 수 있다."
      echo "     robots.txt를 한 번에 최종 주소로 보내라"
    else
      if [ "$RB_CODE" = "000" ] || [ "$RB_RC" != 0 ]; then
        RB_WHY="접속 실패·시간 초과 (HTTP $RB_CODE, curl $RB_RC)"
      else
        RB_WHY="HTTP $RB_CODE"
      fi
      echo "   robots.txt     : ❓ 미확인 — $RB_WHY"
      echo "   └ 429·5xx·접속 실패는 '없음'이 아니다. 크롤러가 사이트 전체를 차단으로 볼 수 있다"
    fi
    echo "   본문·사이트 검사를 보류했다. 정책을 확인한 뒤 재진단하라."
    printf '   일반 브라우저 경로: python %q audit %q --browser-fallback\n' "$SCRIPT_DIR/seo_geo.py" "$URL"
    exit 2 ;;
esac
PICK_PY || { echo "robots.txt 판정에는 Python 3.10+가 필요하다"; exit 2; }
POLICY_READY=1
# 홈도 진단 대상과 www↔apex 변형 안에서만 따라간다 — 다른 도메인의 HTML을 이 사이트 것으로 적지 않는다.
FETCH "$URL" 10 20
HTML="$F_BODY"; HOME_URL="$F_URL"; HOME_HOPS="$F_HOPS"
if [ "$F_REASON" = policy ]; then
  echo "본문 요청 보류 — $(BLOCKED_NOTE)"
  printf '일반 브라우저 경로: python %q audit %q --browser-fallback\n' "$SCRIPT_DIR/seo_geo.py" "$URL"
  exit 2
fi
if [ -n "$F_REASON" ]; then
  echo "❌ 홈을 확인하지 못함 (HTTP $F_CODE) — $(BLOCKED_NOTE). 따라가지 않았다"
  echo "   └ 의도한 이전이면 도착 주소로 다시 진단하라"
  exit 1
fi
if [ -z "$HTML" ]; then
  echo "❌ 응답 없음 — 도메인·네트워크를 확인하라"
  exit 1
fi

echo ""
echo "── 0. noindex 사고 점검 (최우선) ──"
XROBOTS="$(curl -sI --max-time 20 -A "$UA" -- "$HOME_URL" | grep -i '^x-robots-tag:' | cut -d: -f2- || true)"
if printf '%s' "$HTML" | "$PY" -c 'import sys
sys.path.insert(0, sys.argv[1])
import crawl
p = crawl.PageParser()
p.feed(sys.stdin.buffer.read().decode("utf-8", "replace"))
p.close()
sys.exit(0 if crawl._noindex({"meta_robots": p.meta_robots, "meta_googlebot": p.meta_googlebot,
                              "x_robots_tag": sys.argv[2]}) else 1)' "$SCRIPT_DIR" "$XROBOTS"; then
  echo "🚨 일반 검색용 noindex 발견 — 의도한 비색인 대상인지 확인하라"
else
  echo "일반 검색용 noindex가 관측되지 않았다"
fi

echo ""
echo "── 1. SEO: 크롤러가 보는 HTML ──"
H1=$(printf '%s' "$HTML" | COUNT '<h1')
OG=$(printf '%s' "$HTML" | COUNT 'og:')
LD=$(printf '%s' "$HTML" | grep -oiF 'application/ld+json' | wc -l | tr -d ' ')
TITLE=$(printf '%s' "$HTML" | grep -oiE '<title[^>]*>[^<]*' | head -1 | sed 's/<[^>]*>//')
DESC=$(printf '%s' "$HTML" | ATTR '<meta[^>]*name=.?description' 'content')
CANON=$(printf '%s' "$HTML" | ATTR '<link[^>]*rel=.?canonical' 'href')
TEXT=$(printf '%s' "$HTML" | VISIBLE | NCHAR)
NAVER_V=$(printf '%s' "$HTML" | grep -oi 'naver-site-verification' | head -1)

echo "   h1 태그        : ${H1}개"
echo "   title          : ${TITLE:-(없음)}  ($(printf '%s' "$TITLE" | NCHAR)자 · 권장 한글 25~30/영문 50~60)"
echo "   meta desc      : $(printf '%s' "$DESC" | NCHAR)자  (권장 한글 70~80/영문 150~160)"
echo "   og: 태그       : ${OG}개"
echo "   JSON-LD        : ${LD}개"
echo "   canonical      : ${CANON:-(없음)}"
echo "   본문 텍스트량  : 약 ${TEXT}자   (script/style 제외한 실제 텍스트)"
[ "${TEXT:-0}" -lt 500 ] && echo "   └ 500자 미만 — CSR 셸 가능성. 크롤러는 빈 페이지로 본다"
echo "   네이버 소유확인: $([ -n "$NAVER_V" ] && echo '✅ naver-site-verification 있음' || echo '없음 (서치어드바이저 미연결 가능성)')"

echo ""
echo "── 2. robots / sitemap ──"
# 후보: robots.txt의 Sitemap: 선언(최대 3) + 표준 경로 2종
SM_DECL=$(printf '%s' "$RB" | grep -iE '^[[:space:]]*sitemap:' | sed -E 's/^[[:space:]]*[Ss][Ii][Tt][Ee][Mm][Aa][Pp]:[[:space:]]*//' | tr -d '\r' | head -3)
if [ -n "$SM_DECL" ]; then
  echo "   └ Sitemap 참조 : ✅ (robots.txt에 선언됨)"
else
  echo "   └ Sitemap 참조 : ❌ robots.txt에 없음"
fi
HOST=${BASE#*://}; HOST=${HOST%%/*}
SM_FOUND=0
for u in $SM_DECL "$BASE/sitemap.xml" "$BASE/sitemap_index.xml"; do
  # robots.txt가 준 값을 그대로 믿지 않는다: http/https + 진단 대상 호스트만 접속
  case "$u" in
    http://"$HOST"/*|https://"$HOST"/*) : ;;
    http://*|https://*) echo "   ${u}  ⚠️ 외부 도메인 선언 — 자동 접속 안 함, 수동 확인"; continue ;;
    *) echo "   (무시된 Sitemap 값: $u — URL 형식 아님)"; continue ;;
  esac
  # 선언 주소를 걸러도 리다이렉트로 밖에 나갈 수 있다 — 홈·robots와 같은 범위 안에서만 따라간다
  FETCH "$u" 10 20
  SM_CODE="$F_CODE"
  printf '   %-40s HTTP %s' "${u#"$BASE"}" "$SM_CODE"
  if [ -n "$F_REASON" ]; then
    echo "  ⚠️ $(BLOCKED_NOTE) — 따라가지 않음, 수동 확인"
  elif [ "$SM_CODE" = "200" ]; then
    SM_FOUND=1
    BODY="$F_BODY"
    LOCS=$(printf '%s' "$BODY" | grep -o '<loc>' | wc -l | tr -d ' ')
    if printf '%s' "$BODY" | grep -qi '<sitemapindex'; then
      echo "  (인덱스 · 하위 사이트맵 ${LOCS}개)"
    else
      echo "  (URL ${LOCS}개)"
    fi
  else
    echo ""
  fi
done
[ "$SM_FOUND" = "0" ] && echo "   ⚠️ 접근 가능한 사이트맵 없음"

echo ""
echo "── 3. GEO: AI 크롤러 정책 (robots.txt 실효 판정) ──"
POLICY_FAILED=0
for ua in GPTBot OAI-SearchBot ChatGPT-User Googlebot Bingbot \
          ClaudeBot Claude-SearchBot Claude-User \
          PerplexityBot Perplexity-User Google-Extended Yeti DAUM Daumoa; do
  V=$(printf '%s' "$RB" | POLICY "$ua") || V=""
  [ -n "$V" ] || { V=error; POLICY_FAILED=1; }
  case "$V" in
    explicit-allow)   STATE="✅ 명시 허용" ;;
    explicit-block)
      case "$ua" in GPTBot|ClaudeBot|Google-Extended) STATE="학습 사용 차단 (검색 접근과 별도)" ;;
        *) STATE="🚫 검색/사용자 요청 접근 차단" ;; esac ;;
    explicit-partial) STATE="⚠️  부분 제한 (규칙 수동 확인 필요)" ;;
    star-allow)       STATE="허용 (User-agent:* 적용)" ;;
    star-block)       STATE="🚫 차단 (User-agent:* 전체 차단에 걸림)" ;;
    star-partial)     STATE="⚠️  부분 제한 (* 규칙 적용, 수동 확인)" ;;
    none)             STATE="미설정 → 기본 허용 (명시 권장)" ;;
    unknown)          STATE="❓ 미확인 (robots.txt를 읽지 못함 — 판정 안 함)" ;;
    *)                STATE="❓ 미확인 (판정 실패)" ;;
  esac
  printf '   %-18s %s\n' "$ua" "$STATE"
done
[ "$POLICY_FAILED" = "1" ] && echo "   ※ 판정기를 실행하지 못했다 — Python 3.10+(python 또는 python3)와 tools/crawl.py가 필요하다"
echo "   ※ Google-Extended는 UA가 아니라 robots 토큰이다 — 서버 로그에 안 잡힌다"
echo "   ※ Yeti는 네이버 검색 크롤러다 — 차단이면 NEO 레인 전체가 닫힌다"

echo ""
echo "── 4. GEO: llms.txt ──"
for f in llms.txt llms-full.txt; do
  printf '   /%-14s HTTP %s\n' "$f" "$(CODE "$BASE/$f")"
done

echo ""
echo "── 5. 응답 위생 ──"
echo "   404 동작       : HTTP $(CODE "$BASE/__multi_geo_404_probe__")  (404여야 정상)"
echo "   리다이렉트 홉  : $HOME_HOPS"
echo "   응답 시간      : $(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -A "$UA" -- "$HOME_URL")s"
case "$HOST" in www.*) ALT="${HOST#www.}" ;; *) ALT="www.$HOST" ;; esac
ALT_RES=$(curl -sI --max-time 15 -A "$UA" -o /dev/null -w '%{http_code} → %{redirect_url}' "https://$ALT" 2>/dev/null) \
  || ALT_RES="❌ 접속 불가 — TLS 인증서에 $ALT 미포함 또는 DNS 미설정 (한쪽 주소로 온 사용자·크롤러를 잃는다)"
echo "   도메인 변형    : https://$ALT → $ALT_RES"

echo ""
echo "════════════════════════════════════════════"
echo " 이 결과로 레인별 ✅/⚠️/❌ 점수표를 만들고"
echo " 우선순위를 승인받은 뒤 Phase 1부터 진행한다."
echo ""
echo " 스크립트로 안 되는 것 (사람이 확인):"
echo "  · GSC / Bing WMT / 네이버 서치어드바이저 색인 수"
echo "  · 각 엔진에 직접 질의한 AI 인용 O/X (특히 Gemini)"
echo "  · 상세 페이지 표본 점검 — 이 스크립트는 홈만 본다"
echo "════════════════════════════════════════════"
