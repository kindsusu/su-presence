#!/usr/bin/env bash
# audit.sh 핵심 로직 회귀 테스트. 사용: bash test_audit.sh   (전부 PASS여야 정상)
set -uo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
cd "$(dirname "$0")"
FAIL=0
SCRIPT_DIR="$(pwd)"

# 판정 함수만 추출해 로드 (PICK_PY: Python 선택, POLICY: UA 정책, RB_STATE: robots 응답 분류,
# HOST_KEY·ALT_HOST·HOST_ALLOWED: 리다이렉트를 따라가도 되는 호스트 판정)
for fn in PICK_PY POLICY RB_STATE HOST_KEY ALT_HOST HOST_ALLOWED; do
  eval "$(sed -n "/^${fn}()/,/^}/p" audit.sh)"
done
if ! PICK_PY; then
  echo "FAIL Python 3.10+(python 또는 python3)를 찾지 못함 — POLICY 판정 불가"
  FAIL=1
fi

t() { # $1=설명 $2=UA $3=기대값 $4=robots
  got=$(printf '%s' "$4" | POLICY "$2")
  if [ "$got" = "$3" ]; then echo "PASS $1"; return 0; fi
  echo "FAIL $1: got=$got want=$3"
  return 1
}
t "명시 차단" GPTBot explicit-block $'User-agent: GPTBot\nDisallow: /\n' || FAIL=1
t "명시 허용" GPTBot explicit-allow $'User-agent: GPTBot\nAllow: /\n' || FAIL=1
t "와일드카드 차단" ClaudeBot star-block $'User-agent: *\nDisallow: /\n' || FAIL=1
t "빈 Disallow=허용" GPTBot star-allow $'User-agent: *\nDisallow:\n' || FAIL=1
t "CRLF" GPTBot explicit-block $'User-agent: GPTBot\r\nDisallow: /\r\n' || FAIL=1
t "다중 UA 그룹" B explicit-block $'User-agent: A\nUser-agent: B\nDisallow: /\n' || FAIL=1
t "부분 제한" GPTBot star-partial $'User-agent: *\nDisallow: /admin/\n' || FAIL=1
t "동률 Allow" GPTBot explicit-allow $'User-agent: GPTBot\nDisallow: /\nAllow: /\n' || FAIL=1
t "주석 제거" GPTBot explicit-block $'User-agent: GPTBot\nDisallow: / # all\n' || FAIL=1
t "빈 robots" GPTBot none '' || FAIL=1
# User-agent는 제품 토큰(첫 '/'·공백 앞, 대소문자 무시)으로 맞춘다 — crawl.py와 같은 그룹 선택
t "버전 붙은 토큰 GPTBot/1.1" GPTBot explicit-block $'User-agent: GPTBot/1.1\nDisallow: /\n' || FAIL=1
t "GPTBot-Image는 GPTBot 그룹 아님" GPTBot star-allow \
  $'User-agent: GPTBot-Image\nDisallow: /\n\nUser-agent: *\nAllow: /\n' || FAIL=1
t "GPTBot-Image만 있으면 GPTBot 미설정" GPTBot none $'User-agent: GPTBot-Image\nDisallow: /\n' || FAIL=1
t "Daumoa 그룹은 DAUM에 안 걸림" DAUM none $'User-agent: Daumoa\nDisallow: /\n' || FAIL=1
t "Daumoa 그룹은 Daumoa에 걸림" Daumoa explicit-block $'User-agent: Daumoa\nDisallow: /\n' || FAIL=1
t "DAUM 그룹은 Daumoa에 안 걸림" Daumoa star-partial \
  $'User-agent: DAUM\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp/\n' || FAIL=1
t "소문자 gptbot" GPTBot explicit-block $'User-agent: gptbot\nDisallow: /\n' || FAIL=1
t "대문자 UA로 소문자 그룹 부분 제한" GPTBot explicit-partial $'User-agent: gptbot/2.0 (+https://openai.com)\nDisallow: /private/\n' || FAIL=1

# robots.txt 응답 분류 — crawl.robots_fetch_state와 같은 규칙 (429·5xx·접속 실패 = 미확인)
s() { # $1=설명 $2=HTTP 코드 $3=curl 종료 코드 $4=본문 $5=기대값
  got=$(RB_STATE "$2" "$3" "$4")
  if [ "$got" = "$5" ]; then echo "PASS robots 응답: $1"; return 0; fi
  echo "FAIL robots 응답: $1: got=$got want=$5"
  return 1
}
s "200 텍스트"            200 0 $'User-agent: *\nDisallow:\n' ok || FAIL=1
s "200 빈 본문"           200 0 ''                          ok || FAIL=1
s "200으로 꾸민 HTML"     200 0 $'\n  <!doctype html><p>404' absent || FAIL=1
s "200이지만 받다 끊김"   200 28 'User-agent: *'            unreachable || FAIL=1
s "404"                   404 0 'not found'                 absent || FAIL=1
s "410"                   410 0 ''                          absent || FAIL=1
s "403"                   403 0 ''                          absent || FAIL=1
s "429"                   429 0 'slow down'                 unreachable || FAIL=1
s "500"                   500 0 ''                          unreachable || FAIL=1
s "503"                   503 0 ''                          unreachable || FAIL=1
s "접속 실패(000)"        000 7 ''                          unreachable || FAIL=1
s "따라가지 못한 3xx"     301 47 ''                         unreachable || FAIL=1
s "코드 없음"             ''  6 ''                          unreachable || FAIL=1

# Sitemap 선언 필터: 외부 도메인·비URL은 접속 대상에서 제외되는지 (audit.sh의 case 로직과 동일)
HOST="example.com"
chk() { # $1=값 $2=기대(fetch|skip)
  case "$1" in
    http://"$HOST"/*|https://"$HOST"/*) got=fetch ;;
    http://*|https://*) got=skip ;;
    *) got=skip ;;
  esac
  if [ "$got" = "$2" ]; then echo "PASS sitemap:$1"; else echo "FAIL sitemap:$1 got=$got"; FAIL=1; fi
}
chk "https://example.com/sitemap.xml"  fetch
chk "http://127.0.0.1/steal"           skip
chk "https://evil.com/sitemap.xml"     skip
chk "--url=http://169.254.169.254/"    skip

# 리다이렉트 목적지 호스트 판정 — crawl.fetch_robots와 같은 범위 (대상 호스트 + www↔apex, http(s)만)
ha() { # $1=진단 대상 URL $2=리다이렉트 목적지 $3=기대(allow|deny)
  local o got=deny
  o=$(HOST_KEY "$1") || o=""
  HOST_ALLOWED "$o" "$2" && got=allow
  if [ "$got" = "$3" ]; then echo "PASS host $3: $1 → $2"; return 0; fi
  echo "FAIL host: $1 → $2 got=$got want=$3"
  return 1
}
ha https://example.com             https://example.com/robots.txt          allow || FAIL=1
ha https://example.com             https://www.example.com/robots.txt      allow || FAIL=1
ha https://www.example.com         https://example.com/robots.txt          allow || FAIL=1
ha https://www.example.com/sub     http://WWW.Example.COM/robots.txt       allow || FAIL=1
ha https://example.com             http://example.com:80/robots.txt        allow || FAIL=1
ha https://example.com             https://example.com:443/robots.txt      allow || FAIL=1
ha https://example.com:8443        https://www.example.com:8443/robots.txt allow || FAIL=1
ha https://example.com             https://example.com:8443/robots.txt     deny  || FAIL=1
ha https://example.com             https://cdn.example.com/robots.txt      deny  || FAIL=1
ha https://www.example.com         https://www.www.example.com/robots.txt  deny  || FAIL=1
ha https://example.com             https://example.com.evil.test/robots.txt deny || FAIL=1
ha https://example.com             https://evil.test/?u=example.com        deny  || FAIL=1
ha https://example.com             https://example.com@evil.test/robots.txt deny || FAIL=1
ha https://example.com             'https://evil.test\@example.com/'       deny  || FAIL=1
ha https://example.com             ftp://example.com/robots.txt            deny  || FAIL=1
ha https://example.com             /robots.txt                             deny  || FAIL=1
ha http://127.0.0.1:8080/s200      http://127.0.0.1:8080/x/robots.txt      allow || FAIL=1
ha http://127.0.0.1:8080           http://localhost:8080/robots.txt        deny  || FAIL=1
ha http://127.0.0.1:8080           http://www.127.0.0.1:8080/robots.txt    deny  || FAIL=1
ha http://127.0.0.1:8080           http://127.0.0.1/robots.txt             deny  || FAIL=1
ha http://localhost:8080           http://www.localhost:8080/robots.txt    deny  || FAIL=1
ha 'http://[::1]:8080'             'http://[::1]:8080/robots.txt'          allow || FAIL=1
ha 'http://[::1]:8080'             'http://[::2]:8080/robots.txt'          deny  || FAIL=1
ha ''                              https://example.com/robots.txt          deny  || FAIL=1
alt() { # $1=호스트 키 $2=기대 변형(없으면 빈 값)
  local got
  got=$(ALT_HOST "$1") || got=""
  if [ "$got" = "$2" ]; then echo "PASS alt ${1:-(빈 값)} → ${2:-(없음)}"; return 0; fi
  echo "FAIL alt $1: got=$got want=$2"
  return 1
}
alt example.com         www.example.com      || FAIL=1
alt www.example.com     example.com          || FAIL=1
alt example.com:8080    www.example.com:8080 || FAIL=1
alt 127.0.0.1:8080      ''                   || FAIL=1
alt localhost           ''                   || FAIL=1
alt '[::1]:8080'        ''                   || FAIL=1

# 실패 집계 자체의 회귀 검사: 자식 실행에 고의 실패를 넣었을 때 반드시 exit 1이어야 한다.
if [ "${AUDIT_FAILURE_PROBE:-0}" = "1" ]; then
  t "failure probe" GPTBot explicit-allow $'User-agent: GPTBot\nDisallow: /\n' || FAIL=1
  exit "$FAIL"
fi
PROBE_OUT=$(AUDIT_FAILURE_PROBE=1 "$BASH" "$SELF" 2>&1)
PROBE_RC=$?
if [ "$PROBE_RC" = "1" ] && printf '%s' "$PROBE_OUT" | grep -q 'FAIL failure probe'; then
  echo "PASS 실패가 비정상 종료로 전파됨"
else
  echo "FAIL 고의 실패가 정상 종료로 숨겨짐 (exit=$PROBE_RC)"
  FAIL=1
fi

# audit.sh 전체 실행(로컬 HTTP 서버): robots.txt 응답 상태가 3절 UA 판정까지 이어지는지.
# 시나리오는 별도 파일로 선택한다. 실제 감사 대상의 정책은 항상 /robots.txt에 있다.
#   /sNNN  robots.txt 응답 코드 — /s503/robots.txt → 503
#   /hN    robots.txt가 같은 호스트 안에서 N홉 리다이렉트(상대 경로 Location) 뒤 200
#   /abs   robots.txt가 같은 호스트 절대 주소로 리다이렉트
#   /xrb   robots.txt가 다른 호스트(localhost:PORT)로, /xhome 홈이, /xsm sitemap.xml이 다른 호스트로 리다이렉트
#   /trap-*  다른 호스트 목적지 — 요청이 오면 경로를 hits 파일에 적는다 (오면 안 된다)
E2E_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t audit_e2e)"
SRV_PID=""
cleanup() { [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null; rm -rf "$E2E_DIR"; }
trap cleanup EXIT
SRV_PY='
import http.server, socketserver, threading, sys
from pathlib import Path
HITS = []
HTML = b"<html><head><title>t</title></head><body><h1>t</h1></body></html>"
BLOCK = b"User-agent: GPTBot/1.1\nDisallow: /\n"
class S(http.server.ThreadingHTTPServer):
    # HTTPServer.server_bind 는 socket.getfqdn() 으로 역방향 DNS 를 조회한다.
    # macOS 러너에서는 이 조회가 몇 초씩 걸려 포트 출력이 대기 시간을 넘겼다(PR #16 CI).
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _reply(self, send_body):
        parts = self.path.split("?")[0].split("/")
        scen = parts[1] if len(parts) > 1 else ""
        leaf = parts[2] if len(parts) == 3 else None
        home = len(parts) == 2 or leaf == ""
        active = Path(sys.argv[1]).read_text().strip()
        if self.path.split("?")[0] in ("/robots.txt", "/sitemap.xml", "/sitemap_index.xml"):
            scen, leaf, home = active, self.path.strip("/"), False
        elif active == "s503" and scen != "__hits":
            HITS.append("unexpected-503-request:" + self.path)
        other = "http://localhost:%d" % self.server.server_port
        code, body, ctype, location = 404, b"not found", "text/plain", None
        if scen.startswith("trap-"):
            HITS.append(self.path)
            code, body = 200, BLOCK
        elif scen == "__hits":
            code, body = 200, "".join(p + "\n" for p in HITS).encode()
        elif home and scen not in ("xhome", "deny"):
            code, body, ctype = 200, HTML, "text/html; charset=utf-8"
        elif scen == "xhome" and home:
            code, location = 301, other + "/trap-xhome/"
        elif scen == "deny" and leaf == "robots.txt":
            code, body = 200, b"User-agent: *\nDisallow: /deny\n"
        elif scen == "deny" and home:
            HITS.append("unexpected-denied-request:" + self.path)
        elif scen[:1] == "s" and scen[1:].isdigit() and leaf == "robots.txt":
            code = int(scen[1:])
            body = BLOCK if code == 200 else b"busy"
        elif scen[:1] == "h" and scen[1:].isdigit() and leaf == "robots.txt":
            n = int(scen[1:])
            if n == 0:
                code, body = 200, BLOCK
            else:
                code, location = 302, "/h%d/robots.txt" % (n - 1)
        elif scen == "abs" and leaf == "robots.txt":
            code, location = 301, "http://127.0.0.1:%d/s200/robots.txt" % self.server.server_port
        elif scen == "xrb" and leaf == "robots.txt":
            code, location = 301, other + "/trap-xrb/robots.txt"
        elif scen == "xsm" and leaf == "sitemap.xml":
            code, location = 301, other + "/trap-xsm/sitemap.xml"
        if location:
            body = b""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        if send_body:
            self.wfile.write(body)
    def do_GET(self): self._reply(True)
    def do_HEAD(self): self._reply(False)
srv = S(("127.0.0.1", 0), H)
print(srv.server_address[1])
threading.Timer(300, srv.shutdown).start()
srv.serve_forever()
'
if [ -n "${PY:-}" ]; then
  "$PY" -u -c "$SRV_PY" "$E2E_DIR/scenario" > "$E2E_DIR/port" 2>/dev/null &
  SRV_PID=$!
fi
PORT=""
i=0
while [ "$i" -lt 75 ]; do   # 최대 15초
  i=$((i + 1))
  [ -n "${PY:-}" ] || break
  PORT=$( (tr -dc '0-9' < "$E2E_DIR/port") 2>/dev/null)
  [ -n "$PORT" ] && break
  sleep 0.2
done

# localhost도 프록시를 거치지 않게 한다 — 다른 호스트 리다이렉트를 따라갔다면 트랩에 실제로 닿아야 잡힌다.
LOCAL_NP=127.0.0.1,localhost
LAST_SCEN=""
run_audit() { # $1=시나리오 → $out (같은 시나리오는 한 번만 실행)
  [ "$1" = "$LAST_SCEN" ] && return 0
  printf "%s" "$1" > "$E2E_DIR/scenario"
  out=$(NO_PROXY=$LOCAL_NP no_proxy=$LOCAL_NP "$BASH" ./audit.sh "http://127.0.0.1:$PORT/$1" 2>&1)
  AUDIT_RC=$?
  LAST_SCEN="$1"
}
e2e() { # $1=설명 $2=시나리오 $3=줄 첫 칸(UA·robots.txt 등) $4=기대 문구 $5=있으면 안 되는 문구(선택)
  run_audit "$2"
  # LC_ALL=C: macOS awk 는 문자열을 strcoll 로 비교한다. UTF-8 로캘에서는 ※·❌·═ 같은 기호가
  # 같은 정렬 값으로 취급돼 엉뚱한 줄이 걸렸다(PR #17 macOS CI). 바이트 단위로 비교한다.
  line=$(printf '%s\n' "$out" | LC_ALL=C awk -v u="$3" '$1 == u { print; exit }')
  if printf '%s' "$line" | grep -qF -- "$4" \
     && { [ -z "${5:-}" ] || ! printf '%s' "$line" | grep -qF -- "$5"; }; then
    echo "PASS e2e $1"; return 0
  fi
  echo "FAIL e2e $1: line='${line}'"
  return 1
}
e2e_has() { # $1=설명 $2=시나리오 $3=출력 어딘가에 있어야 할 문구
  run_audit "$2"
  if printf '%s' "$out" | grep -qF -- "$3"; then echo "PASS e2e $1"; return 0; fi
  echo "FAIL e2e $1: '$3' 없음"
  return 1
}
if [ -z "$PORT" ]; then
  echo "FAIL e2e 로컬 HTTP 서버를 띄우지 못함"
  FAIL=1
else
  e2e "robots 503 → 미확인"            s503 robots.txt "미확인" || FAIL=1
  [ "$AUDIT_RC" = 2 ] || { echo "FAIL 503 exit: $AUDIT_RC"; FAIL=1; }
  e2e_has "루트 정책이 시작 경로를 막으면 요청하지 않음" deny "본문 요청 보류" || FAIL=1
  [ "$AUDIT_RC" = 2 ] || { echo "FAIL deny exit: $AUDIT_RC"; FAIL=1; }
  e2e_has "일반 브라우저 후속 경로 안내" deny "--browser-fallback" || FAIL=1
  e2e "robots 429 → 미확인"            s429 robots.txt "미확인" || FAIL=1
  e2e "robots 404 → 제한 없음"         s404 GPTBot  "기본 허용" || FAIL=1
  e2e "robots 200 GPTBot/1.1 → 차단"   s200 GPTBot  "학습 사용 차단" || FAIL=1
  e2e "robots 200 GPTBot/1.1 ≠ DAUM"   s200 DAUM    "기본 허용" || FAIL=1
  # 서버가 없는 포트 = 접속 실패 (홈도 응답이 없어 audit.sh가 일찍 끝나므로 분류는 RB_STATE 단위 테스트가 맡는다)

  # robots.txt 리다이렉트 — crawl.fetch_robots와 같은 범위만 따라간다 (127.0.0.1에는 www 변형이 없다)
  e2e "robots 같은 호스트 1홉(상대 경로) → 따라가 판정" h1  GPTBot     "학습 사용 차단" || FAIL=1
  e2e_has "robots 따라간 홉 수 표시"                   h1  "리다이렉트 1홉을 따라감 → http://127.0.0.1:$PORT/h0/robots.txt" || FAIL=1
  e2e "robots 같은 호스트 절대 주소 → 따라가 판정"     abs GPTBot     "학습 사용 차단" || FAIL=1
  e2e "robots 5홉(한도) → 따라가 판정"                 h5  GPTBot     "학습 사용 차단" || FAIL=1
  e2e "robots 6홉 → 미확인"                            h6  robots.txt "미확인" || FAIL=1
  e2e "robots 6홉 → 5번 초과 사유"                     h6  robots.txt "리다이렉트가 연속 5번을 넘는다" || FAIL=1
  e2e "robots 다른 호스트 → 미확인"                    xrb robots.txt "미확인" || FAIL=1
  e2e "robots 다른 호스트 → 다른 도메인 사유"          xrb robots.txt "다른 도메인(localhost:$PORT)으로 리다이렉트된다 — 서버 장애가 아니다" || FAIL=1
  e2e_has "robots 다른 호스트 → 본문 보류" xrb "본문·사이트 검사를 보류" || FAIL=1
  e2e "sitemap 다른 호스트 리다이렉트 → 따라가지 않음" xsm /sitemap.xml "다른 도메인(localhost:$PORT)" || FAIL=1
  e2e "홈 다른 호스트 리다이렉트 → 진단 중단"          xhome ❌        "다른 도메인(localhost:$PORT)" || FAIL=1

  # 트랩: 대조군으로 localhost:PORT에 직접 닿는지 먼저 확인한 뒤(닿지 않으면 위 '따라가지 않음'이 무의미하다),
  # audit.sh가 다른 호스트 목적지에 요청을 하나도 보내지 않았는지 본다.
  NO_PROXY=$LOCAL_NP no_proxy=$LOCAL_NP curl -s -o /dev/null --max-time 5 "http://localhost:$PORT/trap-ctl/"
  HITS=$(NO_PROXY=$LOCAL_NP no_proxy=$LOCAL_NP curl -s --max-time 5 "http://127.0.0.1:$PORT/__hits/" | tr -d '\r')
  if [ "$HITS" = "/trap-ctl/" ]; then
    echo "PASS e2e 다른 호스트 트랩에 audit.sh 요청 없음 (대조 요청만 도착)"
  else
    echo "FAIL e2e 트랩 요청 기록이 대조 요청 하나가 아님: $(printf '%s' "$HITS" | tr '\n' ' ')"
    FAIL=1
  fi
fi

exit $FAIL
