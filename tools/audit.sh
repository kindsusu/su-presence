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
BASE="${URL%/}"
# crawl.py 와 같은 신분으로 다닌다. 토큰이 갈리면 상대 서버 로그에서 한 도구로 안 보인다.
# Mozilla 껍데기는 의도적이다 — 맨 토큰만 보내면 축소 응답을 주는 사이트가 있다.
UA='Mozilla/5.0 (compatible; su-presence-audit/2.1)'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

C()     { curl -sL  --max-time 20 -A "$UA" -- "$@"; }
CODE()  { curl -sL -o /dev/null -w '%{http_code}' --max-time 15 -A "$UA" -- "$1"; }
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

# robots.txt에서 특정 UA의 실효 정책 판정. stdin=robots.txt, $1=UA
# 출력: explicit-allow|explicit-block|explicit-partial|star-allow|star-block|star-partial|none
# Python 크롤러와 같은 파서를 사용해 Shell/Python 판정 차이를 막는다.
POLICY() {
  python -c 'import sys; sys.path.insert(0, sys.argv[1]); import crawl; print(crawl.robots_policy(sys.stdin.read(), sys.argv[2]))' "$SCRIPT_DIR" "$1"
}

echo "════════════════════════════════════════════"
echo " Phase 0 진단 — $BASE"
echo " $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════"

HTML="$(C "$BASE")"
if [ -z "$HTML" ]; then
  echo "❌ 응답 없음 — 도메인·네트워크를 확인하라"
  exit 1
fi

echo ""
echo "── 0. noindex 사고 점검 (최우선) ──"
META_ROBOTS="$(printf '%s' "$HTML" | grep -oiE '<meta[^>]*robots[^>]*>' | head -3)"
XROBOTS="$(curl -sIL --max-time 20 -A "$UA" "$BASE" | grep -i 'x-robots-tag' || true)"
if printf '%s%s' "$META_ROBOTS" "$XROBOTS" | grep -Eqi 'noindex|content=[^>]*none'; then
  echo "🚨 noindex 발견 — 다른 모든 최적화가 무효다. 이것부터 고쳐라"
  [ -n "$META_ROBOTS" ] && echo "   meta   : $META_ROBOTS"
  [ -n "$XROBOTS" ]     && echo "   header : $XROBOTS"
else
  echo "✅ noindex 없음"
  [ -n "$META_ROBOTS" ] && echo "   (meta robots: $META_ROBOTS)"
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
RB_CODE=$(CODE "$BASE/robots.txt")
RB="$(C "$BASE/robots.txt")"
# 404를 200처럼 꾸민 HTML 오류 페이지를 robots.txt로 오인하지 않는다
if [ "$RB_CODE" != "200" ] || printf '%s' "$RB" | head -1 | grep -qi '<'; then
  echo "   robots.txt     : ❌ 없음 또는 비정상 (HTTP $RB_CODE)"
  RB=""
else
  echo "   robots.txt     : 있음 (HTTP 200)"
fi
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
  SM_CODE=$(CODE "$u")
  printf '   %-40s HTTP %s' "${u#"$BASE"}" "$SM_CODE"
  if [ "$SM_CODE" = "200" ]; then
    SM_FOUND=1
    BODY="$(C "$u")"
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
for ua in GPTBot OAI-SearchBot ChatGPT-User Googlebot Bingbot \
          ClaudeBot Claude-SearchBot Claude-User \
          PerplexityBot Perplexity-User Google-Extended Yeti DAUM Daumoa; do
  V=$(printf '%s' "$RB" | POLICY "$ua")
  case "$V" in
    explicit-allow)   STATE="✅ 명시 허용" ;;
    explicit-block)
      case "$ua" in GPTBot|ClaudeBot|Google-Extended) STATE="학습 사용 차단 (검색 접근과 별도)" ;;
        *) STATE="🚫 검색/사용자 요청 접근 차단" ;; esac ;;
    explicit-partial) STATE="⚠️  부분 제한 (규칙 수동 확인 필요)" ;;
    star-allow)       STATE="허용 (User-agent:* 적용)" ;;
    star-block)       STATE="🚫 차단 (User-agent:* 전체 차단에 걸림)" ;;
    star-partial)     STATE="⚠️  부분 제한 (* 규칙 적용, 수동 확인)" ;;
    *)                STATE="미설정 → 기본 허용 (명시 권장)" ;;
  esac
  printf '   %-18s %s\n' "$ua" "$STATE"
done
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
echo "   리다이렉트 홉  : $(curl -sIL -o /dev/null -w '%{num_redirects}' --max-time 20 -A "$UA" "$BASE")"
echo "   응답 시간      : $(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -A "$UA" "$BASE")s"
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
