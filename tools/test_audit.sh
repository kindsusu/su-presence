#!/usr/bin/env bash
# audit.sh 핵심 로직 회귀 테스트. 사용: bash test_audit.sh   (전부 PASS여야 정상)
set -uo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
cd "$(dirname "$0")"
FAIL=0
SCRIPT_DIR="$(pwd)"

# 판정 함수만 추출해 로드 (PICK_PY: Python 선택, POLICY: UA 정책, RB_STATE: robots 응답 분류)
for fn in PICK_PY POLICY RB_STATE; do
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
# 경로 첫 조각 /sNNN 이 robots.txt 응답 코드를 정한다 — /s503/robots.txt → 503. 외부 사이트에 접속하지 않는다.
E2E_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t audit_e2e)"
SRV_PID=""
cleanup() { [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null; rm -rf "$E2E_DIR"; }
trap cleanup EXIT
SRV_PY='
import http.server, socketserver, threading
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
        code, body, ctype = 404, b"not found", "text/plain"
        if scen[:1] == "s" and scen[1:].isdigit():
            if len(parts) == 3 and parts[2] == "robots.txt":
                code = int(scen[1:])
                body = b"User-agent: GPTBot/1.1\nDisallow: /\n" if code == 200 else b"busy"
            elif len(parts) == 2 or (len(parts) == 3 and parts[2] == ""):
                code, ctype = 200, "text/html; charset=utf-8"
                body = b"<html><head><title>t</title></head><body><h1>t</h1></body></html>"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
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
  "$PY" -u -c "$SRV_PY" > "$E2E_DIR/port" 2>/dev/null &
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

e2e() { # $1=설명 $2=시나리오(sNNN) $3=UA $4=기대 문구 $5=있으면 안 되는 문구(선택)
  out=$(NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 "$BASH" ./audit.sh "http://127.0.0.1:$PORT/$2" 2>&1)
  line=$(printf '%s\n' "$out" | awk -v u="$3" '$1 == u { print; exit }')
  if printf '%s' "$line" | grep -qF -- "$4" \
     && { [ -z "${5:-}" ] || ! printf '%s' "$line" | grep -qF -- "$5"; }; then
    echo "PASS e2e $1"; return 0
  fi
  echo "FAIL e2e $1: line='${line}'"
  return 1
}
if [ -z "$PORT" ]; then
  echo "FAIL e2e 로컬 HTTP 서버를 띄우지 못함"
  FAIL=1
else
  e2e "robots 503 → 미확인"            s503 GPTBot  "미확인" "허용" || FAIL=1
  e2e "robots 429 → 미확인"            s429 Yeti    "미확인" "허용" || FAIL=1
  e2e "robots 404 → 제한 없음"         s404 GPTBot  "기본 허용" || FAIL=1
  e2e "robots 200 GPTBot/1.1 → 차단"   s200 GPTBot  "학습 사용 차단" || FAIL=1
  e2e "robots 200 GPTBot/1.1 ≠ DAUM"   s200 DAUM    "기본 허용" || FAIL=1
  # 서버가 없는 포트 = 접속 실패 (홈도 응답이 없어 audit.sh가 일찍 끝나므로 분류는 RB_STATE 단위 테스트가 맡는다)
fi

exit $FAIL
