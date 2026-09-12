# tools/ — 진단 도구

추가 패키지 의존성은 없다. `audit.sh`는 bash·curl·Python 3.10+를 함께 쓰며(robots 정책은
`crawl.py`와 같은 파서로 판정), 나머지는 Python 3.10+ 표준 라이브러리만 쓴다.

```
crawl → report → generate → [사람이 배포] → verify → (14일) crawl·measure → drift → next_due
```

### 스키마 버전 — 도구 사이의 계약

| 파일 | 스키마 | 만드는 도구 | 읽는 도구 |
|---|---|---|---|
| `audit.json` | `su-presence/audit/1` | `crawl.py` | `report` `generate` `verify` `measure` `drift` |
| `verify.json` | `su-presence/verify/1` | `verify.py` | `drift`(스냅샷) |
| `measure/queries.json` | `su-presence/queries/1` (v2도 읽기 지원) | 사람(`measure.py init`이 현재 v1 템플릿을 복사) | `measure` |
| `collect.py` | 전 표면 인용 수집 — 무인(네이버·다음·구글) + 브라우저 경유(ChatGPT·Gemini·Claude·Perplexity) + `--coverage` 빈칸 점검. `ops/coverage.md` · `ops/measure-playbook.md` |
| `measure/log.jsonl` | `su-presence/measure-row/2` (v1 읽기 지원) | `measure.py import`·`auto` | `measure report` |
| `measure/summary.json` | `su-presence/measure/2` (drift가 v1도 읽음) | `measure.py report` | `drift` |
| `history/index.json` | `su-presence/history/1` (옛 `su-multi-geo/history/1` 읽기 지원) | `drift.py snapshot` | `drift` |
| `drift.json` | `su-presence/drift/1` | `drift.py compare` | 사람 |

> **옛 `su-multi-geo/...` 스키마도 전부 읽는다.** 2026-09-12 이름 변경 때 쓰기만 새 이름으로
> 바꾸고 읽기는 신·구를 모두 받게 했다. 호환을 잃으면 이미 만들어진 기준선이 **에러 없이
> 조용히 사라진다** — 도구가 "우리 것이 아니다" 로 판정하고 버리기 때문이다.
> 검증은 `tests/test_legacy_schemas.py` 가 한다.

필드를 바꾸면 스키마 버전을 올린다. 측정 v1은 명시적으로 호환 읽기하며, 그 밖의 알 수 없는
스키마는 거부하거나 집계에서 제외한다.

| 도구 | 무엇 | 입력 | 출력 |
|---|---|---|---|
| `audit.sh` | 홈 1페이지 빠른 점검(레거시 보조 도구) | 도메인 | 콘솔 |
| `crawl.py` | 범위가 기록되는 사이트 진단 | 도메인 | `out/<host>/audit.json` + 콘솔 |
| `report.py` | 진단 결과 → 보고서 | `audit.json` | `report.html` |
| `generate.py` | 진단 결과 → 배포 산출물 초안 | `audit.json` (+ `site.json`) | `out/<host>/deploy/` + `DEPLOY.md` |
| `verify.py` | 배포가 실제로 서빙되는지 / 전후 비교 | `audit.json` (+ `deploy/`) | `verify.json` + `VERIFY.md` |
| `measure.py` | AI 인용 측정 루프 | `audit.json` + `queries.json` | `log.jsonl` → `summary.json` + `MEASURE.md` |
| `test_audit.sh` | audit.sh 회귀 테스트 | — | PASS/FAIL |

흐름:

```
crawl.py → report.py(사람에게 보고) → generate.py(고칠 파일 초안)
        → [사람이 배포] → verify.py deploy(크롤러의 눈으로 증명)
        → (14일) crawl.py 재크롤 → verify.py diff(무엇이 해소됐나)

measure.py는 이 흐름과 나란히, 배포 전 기준선부터 돈다:
measure.py init → [사람이 질의 확정] → form → [사람이 측정] → import → report
                                                          ↑ (14일) 반복
```

통합 진입점은 일상 실행과 로컬 상태 확인을 묶는다.

```bash
python tools/seo_geo.py doctor
python tools/seo_geo.py audit example.com --out out --max-pages 300 --lang ko
python tools/seo_geo.py status out/example.com/audit.json
python tools/seo_geo.py generate all out/example.com/audit.json --site out/example.com/site.json
```

`doctor`는 Python 3.10+와 모듈 import만 확인한다. `audit`는 crawl과 HTML report를 함께 만들고,
불완전 크롤이면 관측 파일을 보존하면서 exit 2를 낸다. 재진단 전 기존 audit는 `observations/`에
보존된다. `status`는 audit·deploy manifest·verify·measure·drift의 **로컬 기록 여부**만 읽는다.
네트워크 검증이나 예약 작업 생성은 하지 않는다.

## audit.sh — 빠른 1페이지 점검

```bash
bash tools/audit.sh example.com
```

홈 하나만 훑어 noindex 징후·robots 정책·사이트맵 유무를 표시한다. 요청 시간은 네트워크와
응답에 따라 달라져 완료 시간을 보장하지 않는다. 이 스크립트는 레거시 빠른 점검용이며,
배포 판단·범위 판정·증적은 `crawl.py`가 만든 `audit.json`을 기준으로 한다.

## crawl.py — 범위 제한 진단

```bash
python tools/crawl.py example.com
python tools/crawl.py https://example.com --max-pages 500 --delay 1.0 --out reports/
```

| 옵션 | 기본값 | 뜻 |
|---|---|---|
| `--max-pages` | 300 | 크롤할 최대 페이지 수 |
| `--delay` | 0.5 | 요청 사이 대기(초). 남의 서버다, 줄이지 마라 |
| `--out` | `out` | 출력 루트. 실제 경로는 `<out>/<host>/audit.json` |

홈과 같은 호스트의 sitemap 시드에서 시작해 내부 링크를 BFS로 따라간다. URL에서는
프래그먼트만 제거하고 쿼리스트링은 보존하므로, 쿼리가 다른 URL은 별도 후보가 될 수 있다.
이미지·CSS·JS 같은 자산은 건너뛴다. **robots.txt의 Disallow는 크롤할 때 존중한다.**
User-Agent는 `su-presence-audit/2.1`으로 밝히고 다닌다.

페이지마다 재는 것: 상태 코드·최종 URL·title·meta description·meta robots·
`X-Robots-Tag`·canonical·h1·JSON-LD 개수와 `@type`·본문 글자 수(바이트 아님)·
OG 태그·네이버 소유확인·`lang`·응답 시간.

사이트 수준으로 재는 것: 유효한 `robots.txt` 응답의 **전체 원문**(`site.robots.raw`)과
`crawl.py`의 `ALL_UAS` 목록에 있는 UA별 실효 정책, 사이트맵(선언·존재·URL 수·
크롤 결과와의 차집합), `llms.txt`, 404 프로브, 리다이렉트 홉, www↔apex 변형 접속
(대상이 IP·localhost면 변형이 없으므로 `na`로 남기고 조회하지 않는다).

`coverage`는 전수성의 증명이 아니라 이번 실행의 관측 범위다. 페이지 한도 도달, 탐색 큐 절단,
시작 URL의 robots 차단, 네트워크/HTTP 오류, robots·sitemap 오류가 있으면
`coverage.complete=false`가 된다. 이때 `crawl.py`는 결과를 쓴 뒤 exit 2를 반환한다.
`crawl.py`를 Ctrl+C로 중단하면 현재 구현은 완료된 보고서를 만들거나 부분 결과를 저장하지 않고
exit 1을 반환한다. `seo_geo.py audit`도 인터럽트 시 성공 관측을 저장하지 않고 exit 130을 반환한다.

### audit.json — 계약

`report.py`와 이후 도구가 이 스키마를 쓴다. 필드를 바꾸면 버전(`schema`)을 올린다. `robots.raw`는
유효한 HTTP 200 robots 응답의 전체 본문이며, robots 응답이 없거나 HTML 오류 본문이면 빈 문자열이다.

```json
{"schema":"su-presence/audit/1","generated_at":"ISO8601",
 "target":{"input":"...","base":"https://host","host":"host"},
 "site":{"robots":{"status":200,"present":true,"raw":"...","policies":{"GPTBot":"star-allow"},"sitemap_declared":[]},
         "sitemaps":[{"url":"...","status":200,"is_index":false,"url_count":0}],
         "sitemap_vs_crawl":{"only_in_sitemap":[],"only_in_crawl":[]},
         "llms":{"llms.txt":404,"llms-full.txt":404},
         "hygiene":{"probe_404":404,"redirect_hops":0,"home_response_ms":0,
                    "alt_host":{"host":"www.x","result":"ok|redirect|tls_fail|dns_fail|error|na",
                                "status":null,"location":null}}},
 "pages":[{"url":"...","status":200,"final_url":"...","title":null,"meta_description":null,
           "meta_robots":null,"x_robots_tag":null,"canonical":null,"h1":[],
           "jsonld_count":0,"jsonld_types":[],"text_chars":0,
           "og":{},"naver_site_verification":false,"lang":null,"response_ms":0,"error":null}],
 "stats":{"pages_crawled":0,"unique_titles":0,"unique_descriptions":0,
          "pages_with_jsonld":0,"pages_noindex":0},
 "findings":[{"lane":"SEO","severity":"critical|warn|info","code":"TITLE_DUPLICATE",
              "message":"...","urls":["..."],"data":{}}],
 "scorecard":{"SEO":{"status":"ok|warn|bad|na","evidence":["TITLE_DUPLICATE"]}}}
```

레인은 일곱이다: `SEO` `AEO` `GEO` `LLMO` `NEO`(네이버) `KEO`(다음·카카오) `reputation`.
`reputation`은 사이트 밖 표면이라 크롤로 잴 수 없다 — 항상 `na`로 두고 사람이 점검한다.

## report.py — 보고서 생성

```bash
python tools/report.py out/example.com/audit.json
python tools/report.py out/example.com/audit.json --lang en --out reports/en.html
```

`audit.json` 하나만 읽어 독립 HTML 한 장을 만든다. 외부 자원은 웹폰트뿐이고,
나머지는 파일 안에 다 들어 있다. 여덟 페이지 구성: 진단 요약 → 사이트 구성 → 레인별 상세 →
강점 → 인용 자산 → 권고 로드맵 → 측정 계획 → 용어 설명.

**이 도구는 아무것도 창작하지 않는다.** 수치·회사명·도메인은 전부 `audit.json`에서 오고,
데이터로 판정할 수 없는 칸은 추정하지 않고 "미확인"으로 남긴다.

- 템플릿: `templates/report.html` (구조·CSS·JS)
- 용어 사전: `templates/glossary.json` (`ko`/`en`) — 본문의 용어에 자동으로 툴팁이 붙는다
- 외부 입력(페이지 title·URL·findings 메시지)은 전부 HTML 이스케이프한다

## generate.py — 배포 산출물 초안

```bash
python tools/generate.py all out/example.com/audit.json --site out/example.com/site.json
python tools/generate.py robots out/example.com/audit.json
python tools/generate.py meta out/example.com/audit.json --out /tmp/draft
```

| 서브커맨드 | 입력 | 출력 |
|---|---|---|
| `sitemap` | 크롤한 페이지 | `sitemap.xml` (한도 초과 시 `sitemap_index.xml` + 분할) |
| `robots` | 기존 robots.txt 원문 + UA 실효 정책 | `robots.txt` (기존 보존 + AI 크롤러 명시 + `Sitemap:`) |
| `llms` | 섹션 대표 URL + `site.json` | `llms.txt` |
| `jsonld` | `site.json` + 크롤한 경로 구조 | `jsonld/*.json` + `jsonld/*.snippet.html` |
| `meta` | 페이지별 h1·title·설명 | `meta-draft.csv` / `meta-draft.json` (검토용) |
| `deploy` | 위 전부 | `DEPLOY.md` 배포 지시서 |
| `all` | — | 위 전부를 `out/<host>/deploy/`에 |

옵션: `--site <site.json>` (없으면 회사 사실 없이 만들 수 있는 것만), `--out <폴더>`
(기본 `audit.json` 옆의 `deploy/`).

생성된 파일은 `deploy/.su-presence-generated.json` manifest에 기록한다. 다음 실행은 이
manifest에서 같은 생성 범주의 낡은 파일만 정리하므로 사용자 파일과 소유권이 섞이지 않는다.

### 불완전 크롤에서는 sitemap 교체를 보류한다

`audit.json`의 `coverage.complete`가 true가 아니거나, 구형 audit에서 기존 sitemap URL이
새 초안에서 빠질 위험이 확인되면 sitemap XML을 생성하지 않는다. `DEPLOY.md`에 **교체 금지**와
누락 위험 URL 수를 남기고, robots.txt에도 존재하지 않는 새 sitemap 선언을 추가하지 않는다.
기존 sitemap을 유지한 채 크롤 한도·실패 원인을 해결하고 `audit` → `generate`를 다시 실행한다.

### 무엇을 지어내지 않는가

이 도구는 **초안 생성기**다. 값의 출처는 둘뿐이다 — `audit.json`의 실측값과 `site.json`에
사람이 적어 준 사실. 그 밖의 칸은 `<<TODO: ...>>` 표식으로 남긴다.

- 사이트맵 `lastmod` — 실제 수정일을 모르므로 태그 자체를 넣지 않는다
- llms.txt의 한 줄 소개·페이지 설명·데이터 정책 — 전부 TODO
- Organization의 빈 필드 — TODO가 아니라 **아예 생략**한다 (LD에 빈 값을 넣지 않는다).
  `sameAs`는 `site.json`의 `same_as`에서만 온다
- FAQPage — `site.json`의 `faqs` 중 **크롤된 page_url**에 붙은, q·a가 모두 있는 것만
- Product `offers` — `price`와 `currency`가 둘 다 있을 때만. 가격은 절대 만들지 않는다
- description 초안 — 페이지에 이미 있는 문장(기존 meta description·og:description)만 후보로
  쓴다. 없으면 TODO. `audit.json`에는 본문 텍스트가 없으므로 첫 문장 추출은 사람 몫이다

BreadcrumbList만은 크롤한 URL 경로와 페이지 title에서 **실측 기반으로** 자동 생성한다.

### robots.txt를 다루는 규칙

- 기존 원문을 **그대로 보존**하고 그 뒤에 블록을 덧붙인다. 기존 `Disallow`는 지우지도
  완화하지도 않는다
- 이미 차단된 UA(`explicit-block`·`star-block`)는 허용으로 뒤집지 않는다 —
  `DEPLOY.md`에 "차단 유지 — 의도 확인 필요"로 적는다
- 이미 명시된 UA는 건드리지 않는다
- `User-agent: *`에 부분 제한이 걸린 사이트에서는 그 제한 규칙을 **글자 그대로 복사**해
  UA 그룹을 만든다 (현재 실효 정책과 동일 — 넓히지 않는다)
- 전/후 unified diff를 `DEPLOY.md`에 싣는다

### site.json — 사용자가 채우는 회사 사실

`templates/site.example.json`을 `out/<host>/site.json`으로 복사해 값을 바꾼다.
키: `name` `legal_name` `url` `logo` `description`(우산 메시지 한 문장) `same_as[]`
`contact{phone,email}` `address{street,locality,region,postal_code,country}`
`founding_year` `faqs[{q,a,page_url}]` `products[{page_url,name,offers{price,currency,unit}}]`.

**모르는 값은 빈 문자열로 두거나 키를 지운다.** 빈 값은 생략되거나 TODO로 남고,
지어낸 값은 인용 신뢰를 죽인다.

## verify.py — 배포 후 검증

```bash
python tools/verify.py deploy out/example.com/audit.json
python tools/verify.py deploy out/example.com/audit.json --deploy out/example.com/deploy \
                              --max-urls 1000 --delay 1.0 --out out/example.com/verify.json
python tools/verify.py diff out/example.com/audit.json out/after/example.com/audit.json
```

**"고쳤다"를 증명하는 도구다.** 패키지에 파일이 있다는 것은 근거가 아니다 —
라이브 사이트를 다시 받아 항목별로 ✅/❌를 낸다. `fail`이 하나라도 있으면 **exit code 1**이다.
실패는 없지만 sitemap 또는 URL의 미검사 범위가 남으면 `completion.complete=false`와 함께
**exit code 2**다. 완전한 성공만 exit 0이다.

### `deploy` — 배포 패키지가 실제로 서빙되는가

| 체크 id | 무엇을 본다 |
|---|---|
| `noindex` | **최우선.** 배포로 noindex가 새로 생기지 않았는가 (meta + `X-Robots-Tag`) |
| `robots.status` | robots.txt 200 응답 |
| `robots.preserved` | 배포 전 원문 줄이 **한 줄도 빠짐없이** 남아 있는가 |
| `robots.policy` | 추가한 UA 블록이 실제로 서빙되는가 (`crawl.py`의 `ALL_UAS` 전체 실효 정책 재판정) |
| `robots.sitemap` | `Sitemap:` 선언 존재 |
| `sitemap.reachable` | 선언된 사이트맵 200 · XML 파싱 (인덱스면 하위까지) |
| `sitemap.locs` | 동일 호스트 `<loc>`를 `--max-urls`까지 200 확인. 상한 또는 사이트맵 탐색 상한으로 남은 범위는 `warn`과 증적에 남는다 |
| `sitemap.noindex` | noindex 페이지가 사이트맵에 실렸는가 |
| `sitemap.canonical` | 사이트맵 URL과 canonical이 일치하는가 |
| `llms.status` / `llms.todo` | llms.txt 200 / `<<TODO` 잔존 → ❌ "미완성 배포" |
| `jsonld.present` / `jsonld.type` | 대상 페이지의 LD 존재·타입·핵심 객체 값이 패키지와 일치하는가 |
| `jsonld.visible` | **LD 값이 가시 텍스트에 글자 그대로 있는가** — FAQ 문답, Organization name, Product name·가격. 없으면 ❌ "LD가 화면에 없는 말을 한다"(스팸 리스크) |
| `jsonld.org_id` | Organization `@id`가 전 페이지에서 하나인가 |
| `meta.applied` / `meta.duplicate` | `meta-draft.json`의 검토 가능한 title/description 값과 라이브 값을 공백 정규화 후 정확히 비교 · 중복 title 잔존 |

가시 텍스트 비교는 태그를 걷어내고 **공백만 정규화**한 뒤 부분 문자열로 본다
(가격은 `89000` ↔ `89,000` 표기를 같게 본다).

### `diff` — 전/후 진단 비교

`after`는 사용자가 `crawl.py`를 다시 돌려 만든다. 네트워크를 쓰지 않는다.

| 체크 id | 무엇을 본다 |
|---|---|
| `diff.resolved` | 사라진 findings (code 기준) |
| `diff.new` | 새로 생긴 findings — critical이 섞이면 ❌ |
| `diff.persisting` | 그대로 남은 findings + 영향 URL 수 증감 |
| `diff.scorecard` | 레인별 전후 (`bad→warn` 등). 악화가 있으면 ❌ |
| `diff.stats` | 중복 title·JSON-LD 페이지·noindex 수 전후 |
| `diff.pages` | 사라진 URL(→404 확인 필요) · 새 URL |

### 안전선

- **대상 호스트 외에는 요청하지 않는다.** 리다이렉트 목적지가 밖으로 나가면 본문을 쓰지 않고
  실패로 본다 (SSRF 방지 — `crawl.py`의 사이트맵 후보 필터와 같은 규칙)
- 요청 간격 `--delay`(기본 0.5초)를 지킨다. 같은 URL은 한 번만 받는다
- 파서·robots 정책 판정·URL 정규화·noindex 판정은 전부 `crawl.py` 함수를 **임포트해 쓴다**
- 네트워크 함수는 주입 가능하다 (`verify_deploy(..., fetch=...)`) — 테스트는 가짜 응답으로 돈다

### verify.json — 계약

```json
{"schema":"su-presence/verify/1","mode":"deploy|diff","generated_at":"ISO8601",
 "target":{"base":"https://host","host":"host","deploy":"out/host/deploy"},
 "checks":[{"id":"sitemap.locs","status":"pass|fail|warn|skip","message":"...","evidence":{}}],
 "summary":{"pass":0,"fail":0,"warn":0,"skip":0},
 "completion":{"complete":true,"reasons":[]},"exit_code":0}
```

`VERIFY.md`는 같은 내용의 사람용 요약이다 — ❌를 먼저 싣고 항목마다 다음 조치를 한 줄 붙인다.

## measure.py — AI 인용 측정

```bash
python tools/measure.py init   out/example.com/audit.json
python tools/measure.py form   out/example.com/audit.json --engines chatgpt,gemini,claude,perplexity --runs 5
python tools/measure.py import out/example.com/audit.json out/example.com/measure/form-2026-09-15.csv
python tools/measure.py report out/example.com/audit.json
python tools/measure.py report out/example.com/audit.json --since 2026-09-01 --until 2026-09-30 --cumulative
python tools/measure.py auto   out/example.com/audit.json --engines chatgpt,claude --runs 5
```

**크롤로는 AI 인용을 잴 수 없다.** 엔진에 실제로 물어야 하고, 그 답은 매번 다르다 —
그래서 "떴다/안 떴다"가 아니라 **N회 중 몇 회**로 적는다 (`ops/measure.md` 2번).

| 서브커맨드 | 무엇 | 출력 |
|---|---|---|
| `init` | `measure/` 폴더 + 질의 세트 **빈칸** 생성 (이미 있으면 안 건드림) | `measure/queries.json` |
| `form` | 질의 × 엔진 × 회차 행이 미리 채워진 수동 입력 양식 | `form-<날짜>.csv` · `form-<날짜>.html` |
| `import` | 채운 CSV를 검증해 로그에 append (문제 행은 건너뛰고 사유 출력) | `measure/log.jsonl` |
| `report` | 로그 집계 — 엔진별 인용률·인용 URL 빈도·추이·재측정일 | `summary.json` + `MEASURE.md` |
| `auto` | **선택.** 환경변수에 키가 있을 때만 ChatGPT·Claude 자동 질의 | `log.jsonl`에 append |

옵션: `--engines`(쉼표 구분, 기본은 **브라우저 조작이 필요한 대화형 엔진만** — `chatgpt,gemini,claude,perplexity`. 네이버·다음·구글 AI개요는 `collect.py` 가 무인으로 잰다) · `--runs`(기본 5) ·
`--date YYYY-MM-DD`(기본 오늘) · `--since`·`--until`·`--cumulative`(report) ·
`--delay`·`--yes`(auto).

### 수동이 기본 골격이다

API 키가 하나도 없어도 측정 루프는 완전히 돈다. 자동화는 붙였다 뗐다 하는 플러그인이고,
자동과 수동은 같은 `log.jsonl`에 쌓이지만 `mode`·`surface`·모델·locale·로그인·검색 조건이
다른 **별도 cohort**다. ChatGPT 웹 UI와 OpenAI API 모델 결과를 합쳐 해석하지 않는다.

- `form-<날짜>.csv` — 엑셀용. **UTF-8 BOM**을 붙여 한글이 깨지지 않는다.
  입력 열(`cited`·`cited_urls`·`brand_mentioned`·`competitor_domains`·`note`)만 비어 있다
- `form-<날짜>.html` — **오프라인 단일 파일 폼**. 외부 자원이 하나도 없어 인터넷 없이 열린다.
  상단에 측정 규칙 6가지 체크리스트, 질의별 표(라디오 Y/N·URL·경쟁 도메인·메모), 진행률,
  하단 "CSV로 내보내기"(내려받기가 막히면 textarea로 떨어져 복사 가능).
  입력값은 `localStorage`에 자동 저장된다 — 브라우저를 닫아도 남지만
  **저장 실패해도 입력은 계속된다**(try/catch)

### 계약

```
out/<host>/measure/queries.json   `init`은 현재 su-presence/queries/1 템플릿을 복사하며, /2도 읽는다
  {"queries":[{"id":"Q01","text":"...","type":"brand|nonbrand","note":""}]}

out/<host>/measure/log.jsonl      su-presence/measure-row/2  · append-only · 한 줄 = 질의 1회
  {"date":"2026-09-15","query_id":"Q01","engine":"chatgpt","run_no":1,
   "mode":"manual|api","surface":"chatgpt_web_ui|api","locale":"ko-KR",
   "login_state":"signed_out|signed_in|unknown|not_applicable","search_enabled":true,
   "campaign_id":"...","query_fingerprint":"sha256","model":"","outcome":"observed|error|unmeasured",
   "error":null,"signed_out":true|false|null,"cited":true|false|null,
   "cited_urls":["https://example.com/pricing"],"brand_mentioned":true,
   "competitor_domains":["competitor.com"],"note":"","recorded_at":"ISO8601"}

out/<host>/measure/summary.json   su-presence/measure/2   · report가 생성
```

`engine`은 고정 목록이다: `chatgpt` `google_aio` `gemini` `claude` `perplexity`
`naver_ai` `daum` `copilot` `other`. 값이 늘면 스키마 버전을 올린다.

로그는 **append-only**다 — 고치지 말고 다시 넣어라. 날짜·질의·엔진·회차에 더해 mode·surface·
locale·login·search·campaign까지 같은 관측 키가 여러 번 들어오면 **읽을 때 마지막 것만** 쓴다.
따라서 같은 회차의 수동 UI와 API 측정은 서로 덮어쓰지 않는다. 기존 v1 질의·행은 읽지만 새
기록은 v2이며, 알 수 없는 스키마 행은 제외한다.

### import가 걸러내는 것

날짜가 `YYYY-MM-DD`가 아닌 행 · `queries.json`에 없는 `query_id` · 고정 목록 밖의 `engine` ·
1 미만인 `run_no` · `cited`가 비었거나 Y/N이 아닌 행. **건너뛰고 사유를 출력한다.**
`brand_mentioned`가 비면 `cited` 값을 따르고, URL이 아닌 토큰은 버리되 `note`에 남긴다.
`cited=Y`인데 URL이 없으면 `[인용 URL 미기록]`으로 표시된다 — 다음 작업을 정할 수 없다는 신호다.

### report가 내는 것

- **엔진 × (브랜드/비브랜드) 인용률** `N/M` — 회차 합산
- **인용 URL 빈도** — 우리 호스트 vs 경쟁 도메인, 질의별로 어느 URL이 뽑히는지
- **날짜별 추이** — 첫 측정일이 기준선, 이후는 기준선 대비 증감
- 기본 headline과 엔진별 비율은 선택 기간의 **최신 측정일만** 집계한다. `trend`는 기간 전체를
  보존하며, 여러 날짜 합산은 `--cumulative`로 명시한다
- `observed`만 인용률 분모에 넣는다. `error`와 `unmeasured`는 오류율·품질 정보로 따로 내고,
  오류·질의 fingerprint 불일치가 있으면 `regression_eligible=false`다
- 웹 UI와 API 모델 등 조건 조합은 `cohorts` 표에서 분리한다
- `ops/measure.md` 6번과 같은 형식의 한 줄 요약과 **다음 재측정 예정일**(마지막 측정 +14일)

`브랜드 4/4`(질의 단위: 한 번이라도 인용된 질의 수)와 `ChatGPT 20/30`(회차 합산)은
**다른 숫자다.** 둘 다 낸다.

### auto — 선택이고, 대체가 아니다

`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`가 **환경변수에 있을 때만** 해당 엔진을 돌린다.
없으면 "수동 모드 — form을 쓰라"고 안내하고 **정상 종료한다(에러 아님)**.
Gemini·Perplexity·Google AI Overviews·네이버·다음·Copilot은 자동화 대상이 아니다 —
수동 폼으로 안내한다.

- OpenAI Responses API(`/v1/responses` + `tools:[{"type":"web_search"}]`)의 `url_citation`
  주석에서, Anthropic Messages API(`/v1/messages` + 서버 도구 `web_search_20250305`)의
  text 블록 `citations`에서 URL을 뽑는다. **검색 결과 전체가 아니라 실제 인용만 센다**
- 자동화가 지원하는 것은 OpenAI·Anthropic **API 응답의 인용 관측**뿐이다. 웹 UI 결과, 로그인
  상태별 결과, 색인 여부, 노출 순위·회수·원인은 측정하거나 보장하지 않는다. 웹 검색 도구 사용 가능
  여부와 모델·요금·응답 형식은 계정과 각 API의 현재 정책에 좌우되며, API 오류는 실패 행으로 남긴다
- `urllib`만 쓴다 (SDK 의존 없음). HTTP 전송 함수는 주입 가능하다 — 테스트는 가짜 응답으로 돈다
- ⚠️ **모델명은 각사 문서에서 현재 값을 확인하라.** 코드 상수는 출발점일 뿐이고,
  `OPENAI_MODEL` / `ANTHROPIC_MODEL` 환경변수로 덮어쓴다
- ⚠️ **API 응답은 비로그인 웹 UI와 다른 표면이다.** 자동 측정은 수동 측정을 대체하지 않는다 —
  추세를 싸게 자주 보는 보조 수단으로만 써라

### 안전선 — 키와 비용

- **키는 환경변수에서만 읽는다.** `queries.json`·`log.jsonl`·`summary.json`·폼 어디에도
  쓰지 않고, 콘솔에도 찍지 않는다. 없는 키를 사용자에게 요구하지도 않는다
- **응답 원문을 저장하지 않는다.** 남기는 것은 인용 URL·브랜드 언급 여부·모델명뿐이다
- HTTP 오류는 상태 코드만 `note`에 남기고 본문은 버린다 (본문에 키가 실릴 이유는 없지만 안 받는다)
- **비용은 전부 사용자 부담이다.** 실행 전에 예상 호출 수(엔진 × 질의 × 회차)를 세어
  출력하고, `--yes`가 없으면 확인을 받는다. 회차 사이에 `--delay`(기본 2초)를 지킨다
- 실패한 회차는 `outcome=error`와 사유로 기록하되 **미인용으로 세거나 인용률 분모에 넣지 않는다.**
  오류율은 별도 지표이며 응답 원문은 저장하지 않는다

## drift.py — 기준선 스냅샷 + 드리프트 비교

```bash
python tools/drift.py snapshot out/example.com/audit.json --label "기준선"
python tools/drift.py snapshot out/example.com/audit.json \
       --measure out/example.com/measure/summary.json --date 2026-09-15 --label "P1 배포 후"
python tools/drift.py compare  out/example.com/audit.json [--from 2026-09-01] [--to 2026-09-15]
python tools/drift.py status   out/example.com/audit.json
python tools/drift.py timeline out/example.com/audit.json
# audit.json 경로 대신 --host example.com [--out out] 도 된다 (compare·status·timeline)
```

**"언제 무엇을 다시 잰다"를 기억이 아니라 파일로 강제하는 도구다.** 네트워크를 쓰지 않는다 —
이미 만들어 둔 `audit.json`·`summary.json`·`verify.json`만 읽는다.

### `snapshot` — 불변 보관소

`out/<host>/history/`에 사본을 그대로 복사하고 sha256을 남긴다.

| 파일 | 내용 |
|---|---|
| `audit-<YYYY-MM-DD>.json` | `crawl.py` 결과 사본 (필수) |
| `measure-<YYYY-MM-DD>.json` | `measure.py report`의 `summary.json` 사본 (`--measure`) |
| `verify-<YYYY-MM-DD>.json` | `verify.py`의 `verify.json` 사본 (`--verify`) |
| `index.json` | 스키마 `su-presence/history/1` — 스냅샷 목록·`baseline_date`·`next_due` |

- **같은 날짜 같은 종류는 `--force` 없이 거부한다.** 기준선을 조용히 덮어쓰면 추이가 거짓말이 된다
- 첫 audit 스냅샷이 자동으로 기준선이 된다. 나중에 옮기려면 `--baseline`
- 측정 스냅샷이 있으면 `next_due` = **마지막 측정일 + 14일**, 없으면 마지막 스냅샷을
  기준으로 계산한다 (`ops/measure.md` 3번)
- `next_due`는 계산값이다. `schedule.scheduled=false`이며 캘린더·CI·Codex 자동화를 만들지 않는다
- 모든 입력의 schema와 host를 먼저 검사하고 같은 날짜 충돌도 전부 확인한 뒤 원자적으로 기록한다.
  하나라도 실패하면 기존 파일로 롤백한다. 읽을 때도 저장된 SHA-256을 재검증한다

### `compare` — 기준선 vs 최신

기본은 `baseline_date` → 최신 audit 스냅샷. `--from`/`--to`로 임의의 두 날짜를 고른다.

- **진단 드리프트**는 `verify.py`의 `verify_diff`를 그대로 임포트해 쓴다(복제 없음) —
  findings 해소/신규/유지, 레인 점수 전후, stats 전후, 사라진 URL
- **측정 드리프트**는 두 `summary.json`을 비교한다 — 엔진 × (브랜드/비브랜드) 인용률
  전후(N/M · 증감 포인트), 우리 URL 인용 빈도 전후, **새로 인용되기 시작한 / 인용이 끊긴
  우리 URL**, 경쟁 도메인 전후. 측정 스냅샷이 2개 미만이면 이 절은 건너뛰고 경고만 남긴다

#### 회귀 판정 — 하나라도 걸리면 exit code 1

| 규칙 | 판정 근거 | 결과 |
|---|---|---|
| noindex 신규 발생 | `stats.pages_noindex` 증가 | ❌ 회귀 |
| JSON-LD 페이지 감소 | `stats.pages_with_jsonld` 감소 | ❌ 회귀 |
| 중복 title 증가 | `TITLE_DUPLICATE` finding의 `data.pages` 증가 | ❌ 회귀 |
| 사이트맵 URL 급감 | 비인덱스 사이트맵 `url_count` 합이 **20% 넘게** 감소 | ❌ 회귀 |
| 레인 점수 악화 | `verify_diff`의 `diff.scorecard` = fail | ❌ 회귀 |
| 비브랜드 인용률 하락 | 엔진 합산 `nonbrand` 인용률 하락 | ❌ 회귀 |

같은 지표가 **좋아지면 개선**(pass), **그대로면 변화 없음**(info)이다.
페이지 수 변동과 20% 미만의 사이트맵 감소는 회귀로 치지 않는다 — 사실만 남긴다.
브랜드 인용률 하락도 회귀가 아니다(질의 세트·엔진 편차에 더 흔들린다) — 표에는 그대로 실린다.

비브랜드 인용률은 양쪽 모두 관측 5회 이상이고 차이가 10%p 이상일 때만 의미 있는 변화로
판정하며 Wilson 95% 구간을 함께 기록한다. 질의 fingerprint나 surface/mode/locale/login/search
조건이 다르거나 오류·미측정이 있으면 `comparison.status=inconclusive`로 남기고 exit 1 회귀로
확정하지 않는다. 누적 보고서는 배포 전후 한 회차 비교용이 아니다.

#### 낡은 기준선 경고

비교 대상 기준선이 `--stale-days`(기본 30)보다 오래되면 ⚠️ **"기준선이 낡았다"**를
`warnings`와 `DRIFT.md` 머리에 박는다 (`ops/measure.md` 4번 — 낡음은 하한선 검사로 안 잡힌다).

### drift.json — 계약

```json
{"schema":"su-presence/drift/1","from":"2026-09-01","to":"2026-09-15",
 "baseline":"2026-09-01","baseline_age_days":14,"warnings":["..."],
 "metrics":{"before":{},"after":{}},
 "audit_diff":{"resolved":[],"new":[],"persisting":[],"scorecard":{},"stats":{},"pages":{}},
 "measure_diff":{"comparison":{"status":"comparable|inconclusive"},"totals":{},"engines":[],"ours":[],"ours_new":[],"ours_lost":[],"competitors":[]},
 "regressions":[],"improvements":[],"unchanged":[],
 "next_due":"2026-09-29","schedule":{"next_due":"2026-09-29","scheduled":false},"exit_code":1}
```

`DRIFT.md`는 사람용 요약이다 — **❌ 회귀 → ✅ 개선 → 변화 없음 → 다음 재측정일과 그날 돌릴
명령 순서**로 싣는다. `next_due`가 있어도 실제 예약은 별도다.

### `status` / `timeline`

- `status` — 기준선 날짜, 스냅샷 수, 마지막 측정일, `next_due`까지 남은 일수
  (지났으면 ⚠️ 며칠 초과), 스냅샷 한 줄씩
- `timeline` — 날짜별 페이지 수·중복 title·JSON-LD 페이지·noindex·비브랜드/브랜드 인용률을
  `TIMELINE.md` 표로. 빈칸(`—`)은 그날 그 종류를 안 찍었다는 뜻이고 0이 아니다

## 테스트

```bash
bash tools/test_audit.sh           # audit.sh (bash·curl·Python 필요)
python -m unittest discover tests  # 단위 + E2E (외부 네트워크 없음)
python -m unittest tests.test_e2e  # E2E만
```

`tests/test_e2e.py`는 `tests/fixtures/site/`(결함을 일부러 심은 8페이지)를 `http.server`로
127.0.0.1 임시 포트에 띄우고 **전 루프를 실제 CLI로** 돌린다 —
crawl → report → generate → 배포 흉내 → verify → 재크롤 → measure → drift.
`.github/workflows/ci.yml`이 ubuntu·windows·macos × Python 3.10·3.12·3.13에서 같은 것을 돌린다.
**의존성 설치 단계가 없다는 것이 "표준 라이브러리만"의 증명이다.**
