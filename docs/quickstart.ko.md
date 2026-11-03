# 다른 PC에서 설치·실행하고 결과 보기

## 1. 준비 및 설치

Python 3.10 이상과 Git을 설치합니다. Windows에서는 Python 설치 시 PATH 등록을 선택합니다. 기본 도구는 Python 표준 라이브러리만 사용하므로 pip 패키지 설치가 필요 없습니다. 아래 명령은 Windows PowerShell 기준이며, su-presence 폴더에서 실행합니다.

```powershell
git clone https://github.com/kindsusu/su-presence.git
cd su-presence
python --version
python tools/seo_geo.py doctor
```

`doctor`의 `supported: true`와 도구별 `ok`를 확인합니다. 이는 설치 검사이며 인터넷 연결이나 AI 로그인 검사는 아닙니다. `python`을 찾지 못하면 터미널을 다시 열거나 설치된 Python 런처 `py`로 바꿔 실행합니다. macOS/Linux에서는 환경에 따라 `python3`를 사용합니다.

Git이 없다면 GitHub의 Code → Download ZIP으로 내려받고 압축을 푼 폴더에서 같은 Python 명령을 실행할 수 있습니다. Git으로 설치한 경우 수정 중인 파일을 보존한 뒤 `git pull --ff-only`로 업데이트합니다. 이 PC의 미커밋·미푸시 변경은 다른 PC에서 clone/pull해도 전달되지 않습니다.

## 2. 사이트 진단부터 실행

`https://example.com`을 대상 주소로 바꿉니다.

```powershell
python tools/seo_geo.py audit https://example.com --out out
```

터미널에 출력된 보고서 경로를 확인하고 `out/example.com/report.html`을 브라우저에서 엽니다. 여기의 `example.com`은 실제 출력된 호스트명으로 바꿉니다. 진단은 기술 상태와 수집 범위를 확인하며 실제 검색 순위나 AI 추천을 증명하지 않습니다.

## 3. 경쟁 출처 조사와 페이지 설계

제공된 설정은 Brave Search API를 사용합니다. 해당 API 키를 준비한 뒤 PowerShell 세션에 설정합니다. ChatGPT 등의 로그인이나 구독은 이 API 키를 대신하지 않습니다. 키는 저장소에 기록하지 않습니다.

```powershell
$env:BRAVE_SEARCH_API_KEY = "본인의 API 키"
python tools/seo_geo.py rebuild run https://example.com --topic "핵심 서비스" --config templates/rebuild/run-config.json --out out/example.com/rebuild
```

출력된 `run-*` 폴더의 `report.html`을 먼저 보고 `guide.md`, `pages/`를 확인합니다. 예제 설정의 분석 제공자는 `none`입니다. 별도 AI 분석 설정 없이 고급 AI 분석까지 수행했다고 해석하지 않습니다. 확인된 회사 사실·질문·경쟁사·기존 페이지 매핑을 반영하려면 [상세 실행 안내](plans/rebuild-automation-guide.md)의 브리프와 제공자 설정을 사용합니다.

API 키가 없으면 검색을 완료한 것으로 처리하지 않습니다. 상세 안내의 수동 검색 결과 가져오기 경로를 사용합니다. 사이트 수정·배포는 자동으로 수행하지 않습니다.

```powershell
python tools/seo_geo.py rebuild status out/example.com/rebuild/<run-id>
python tools/seo_geo.py rebuild resume out/example.com/rebuild/<run-id>
```

`<run-id>`를 실제 폴더명으로 바꿉니다. `needs_input`은 추가 입력 필요, `partial`은 일부만 완료했다는 뜻입니다. 종료 코드 2가 나오면 결과가 없다고 판단하지 말고 상태와 보고서의 미완료 사유를 확인합니다.

## 4. 실제 AI 서비스 노출 측정

설치만으로 로그인한 AI 서비스에 질문이 자동 제출되지는 않습니다. 다른 PC에서는 각 서비스에 로그인하고, 브라우저를 제어할 수 있는 에이전트/호스트를 연결하거나 직접 관측해야 합니다. Aside는 선택 사항이며 CLI가 자동 호출하지 않습니다. API 응답과 소비자용 웹 서비스 답변은 별도 측정입니다.

브라우저 자동화가 없어도 다음 수동 측정 경로를 사용할 수 있습니다.

```powershell
python tools/seo_geo.py measure init out/example.com/audit.json
```

생성된 `measure/queries.json`에서 측정 질문을 확인·수정한 뒤 폼을 만듭니다.

```powershell
python tools/seo_geo.py measure form out/example.com/audit.json --engines chatgpt,gemini,claude,perplexity --runs 5
```

생성된 CSV를 엑셀에서 열어 실제 관측 결과로 채우고 CSV 형식으로 저장합니다. 질문·서비스·회차별로 새 임시 대화에서 실행하고, 답변의 브랜드 언급과 공식 사이트 인용을 따로 확인합니다. 미측정이나 출처 확인 실패를 미노출 0으로 채우지 않습니다. 필드와 측정 방법은 [측정 레시피](../ops/measure-playbook.md)를 따릅니다.

```powershell
python tools/seo_geo.py measure import out/example.com/audit.json out/example.com/measure/form-YYYY-MM-DD-filled.csv
python tools/seo_geo.py measure report out/example.com/audit.json
```

CSV 경로는 실제 저장한 파일명으로 바꿉니다. 결과는 `measure/MEASURE.md`에서 읽습니다. CSV는 엑셀로 열 수 있지만 기본 XLSX 파일 생성은 아닙니다.

## 5. 어떤 파일을 열어야 하나

기본 저장 기준은 명령을 실행한 현재 폴더입니다. `--out`으로 절대 경로를 지정하면 별도 위치에도 저장할 수 있습니다. 아래 파일은 해당 단계를 실행했을 때 생성됩니다.

| 목적 | 기본 경로 | 여는 방법 |
|---|---|---|
| 사이트 기술 진단 | `out/<host>/report.html` | 브라우저에서 먼저 확인 |
| 페이지 재설계 요약 | `out/<host>/rebuild/run-*/report.html` | 브라우저 |
| 리빌딩 실행 가이드 | 같은 실행 폴더의 `guide.md` | Codex 또는 Markdown 편집기 |
| 페이지별 설계 초안 | 같은 실행 폴더의 `pages/*.md`, `pages/*.html` | 편집기 또는 브라우저; 실제 게시 페이지가 아님 |
| 실제 AI 측정 결과 | `out/<host>/measure/MEASURE.md` | Markdown 편집기 |
| 측정 입력표 | `out/<host>/measure/form-*.csv` | 엑셀; 입력 후 import 필요 |
| 전후 비교 | `out/<host>/DRIFT.md` | 스냅샷 생성·비교 후 확인 |
| 재개·재집계용 데이터 | `audit.json`, `run.json`, `summary.json`, `log.jsonl`, `history/` 등 | 일반 열람보다 보존·재실행용 |

진단과 재설계는 모두 `report.html`이라는 이름을 사용하므로 상위 폴더를 확인합니다. 원문·스크린샷 등 브라우저 증거의 경로는 사용하는 호스트/실행 설정에 따라 달라집니다.

## 6. 다른 PC로 옮길 때

프로그램은 GitHub에서 새로 설치하고, 이어서 작업할 사이트의 `out/<host>/` 폴더를 별도로 복사합니다. 결과 폴더는 Git으로 자동 동기화되는 것으로 가정하지 않습니다. `--out`을 다른 위치로 지정했다면 그 폴더를 복사합니다. API 키와 로그인 세션은 새 PC에서 별도로 설정합니다.

보고서만 전달하려면 HTML/Markdown을 공유할 수 있지만, 재개·재집계·전후 비교에는 JSON·로그·이력·참조 원문도 필요하므로 실행 폴더 전체를 보관하는 편이 안전합니다. 절대 경로를 담은 설정이나 증거 참조는 새 PC 경로에 맞게 확인합니다. 이번 개발용 참고 저장소와 테스트 복사본은 설치에 필요하지 않습니다.
