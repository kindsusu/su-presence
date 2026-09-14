# 변경 이력

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/), 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다.
날짜는 해당 작업이 커밋된 날이다.

## [Unreleased]

## [2.1.0] — 2026-09-14

이름을 su-presence로 바꾸고, 다음·카카오를 독립 레인(KEO)으로 세우고, 인용 측정을 자동 수집으로 닫은 판. 크롤러 UA `su-presence-audit/2.1`, 데이터 식별자 `su-presence/…`(구명 읽기 호환).

### 2026-09-13 — 수집·측정 신뢰성 보강

- robots.txt에 선언된 sitemap과 sitemap index가 참조한 자식은 필수로 확인한다. 이들 중 하나라도
  실패하면 진단은 불완전하며 교체용 sitemap XML 생성을 보류한다. 선언되지 않은 기본 후보의
  404·410은 선택적 부재로 허용한다.
- Google은 AI Overview의 명시된 출처 영역만, 네이버는 유효한 AI 브리핑 출처만 인용으로 기록한다.
  경계를 식별하지 못하거나 출처 조회·파싱이 실패하면 0건이 아니라 `unmeasured`다. URL은 hostname
  경계로 비교한다.
- `collect.py`가 만드는 자동·브라우저 측정 행에 질의 fingerprint와 campaign을 남기고, 현재 질의와 맞지 않거나 빈 fingerprint인
  v2 행은 원본을 보존한 채 집계에서 제외한다. `quality.incompatible_rows`로 제외 수를 보고하며,
  과거 빈 fingerprint 행은 재측정해야 한다. v1 읽기 호환은 유지한다.
- 생성기는 새 manifest를 우선하고 실제 구형 manifest 파일명은 fallback으로 읽는다. 새 manifest는
  원자적으로 저장하며 status는 신·구 파일명을 탐색한다. 선택적 provenance 필드를 추가했으며 호환 가능한 선택 필드라 schema 버전은
  올리지 않았다.
- 미러는 root의 `noindex`와 robots 차단·미차단·미확인을 구분한다. 모든 호스트의 색인 가능 여부를
  단정하지 않으며, 정상 `www → apex` 리다이렉트는 접속 실패가 아니다.

### 스키마 식별자 이행 — su-multi-geo/… → su-presence/…

이름 변경(위 항목) 때 **데이터에 박힌 식별자는 일부러 동결**했다. 이제 이행한다.
**쓰기는 새 이름, 읽기는 신·구 둘 다.**

| 옮긴 것 | |
|---|---|
| 스키마 16종 | `audit/1` `queries/2` `measure-row/2` `measure/2` `verify/1` `history/1` `drift/1` `status/1` `jsonld-manifest/1` `generated-files/1` |
| 크롤러 UA | `su-multi-geo-audit/2.0` → `su-presence-audit/2.1` (`crawl.py`·`audit.sh` 동일) |
| 소유권 manifest | `.su-multi-geo-generated.json` → `.su-presence-generated.json` (옛 파일명도 읽는다) |
| 브라우저 저장 키 | `su-multi-geo:measure:` → `su-presence:measure:` |

**읽기 호환을 잃으면 기존 기준선이 에러 없이 조용히 사라진다** — 도구가 파일을
"우리 것이 아니다" 로 판정하고 버리기 때문이다. 특히 `drift.py` 의 history 인덱스가
그렇다. 실제 09-11 기준선으로 읽기를 확인했고 수치가 동일했다.

`tests/test_legacy_schemas.py` 신설 — 쓰기가 새 이름인지, 읽기가 옛 이름을 받는지,
그리고 **엉뚱한 스키마는 여전히 거부하는지**까지 본다. 총 315 테스트.

> 앞 항목의 "바꾸지 않은 것" 표는 이 커밋으로 해소됐다. 남은 것은 **브라우저 저장 키를
> 바꾸면서 진행 중이던 수동 폼 입력이 한 번 날아간다**는 점뿐이다 (폼을 다시 열면 된다).

### 이름 변경 — su-multi-geo → su-presence

`multi-geo` 는 GEO 한 레인만 가리키는데 실제로는 SEO·AEO·GEO·LLMO·NEO·KEO 일곱을 다룬다.
KEO 레인을 세우면서 이름이 내용보다 좁아진 게 분명해져 바꿨다.
`presence` — **검색과 AI 답변에 우리 자리가 있는가**. 도달·인용·각인 세 층을 한 단어로 덮고,
약어 판이 또 바뀌어도 늙지 않는다.

바꾼 것: 저장소명 · `SKILL.md`(국문·영문) · `plugin.json` · `marketplace.json` · README ·
문서 · 생성 robots.txt 의 표식 · 배너 이미지 파일명.

**바꾸지 않은 것 — 데이터에 박힌 식별자다. 바꾸면 예전 스냅샷을 못 읽는다.**

| 값 | 이유 |
|---|---|
| `su-multi-geo/audit/1` 등 스키마 16종 | 기존 `audit.json`·`log.jsonl`·스냅샷에 기록돼 있고, 도구가 이 문자열로 읽기 가능 여부를 판정한다 |
| `su-multi-geo-audit/2.0` (크롤러 UA) | 상대 사이트 서버 로그의 연속성 |
| `.su-multi-geo-generated.json` | 우리가 만든 파일을 식별하는 manifest. 바꾸면 기존 배포본에서 자기 파일을 못 알아본다 |
| `su-multi-geo:measure:` (브라우저 저장 키) | 진행 중인 수동 측정 입력이 날아간다 |

이 식별자들은 **이름과 무관한 데이터 계약**이다. 스키마 버전을 올릴 때 함께 정리한다.

### KEO 레인 분리 — 다음·카카오를 네이버에서 떼어냈다

- `KEO`를 일급 레인으로 승격. 크롤러 토큰·소유확인 방식·AI 요약 모델이 네이버와 전부 달라
  한 칸에 묶어두면 둘 다 보이지 않았다. `lanes/daum.md` 신설, `lanes/naver.md`는 네이버 전용.
- robots 크롤러 UA에 **`DAUM` 추가**. `Daumoa`만 적으면 robots가 가장 구체적인 UA 그룹
  하나만 적용하므로 `DAUM`으로 오는 요청이 `*` 그룹으로 떨어진다. `generate.py` 초안도 수정.
- 다음 크롤러 차단은 `DAUM_CRAWLER_BLOCKED`(critical), 미선언은 `DAUM_CRAWLER_UNDECLARED`.
- 다음 소유확인은 URL+PIN 방식이라 크롤로 증명할 수 없다 — **finding 으로 만들지 않고**
  `ops/coverage.md`의 사람 확인 항목으로 남겼다. 없는 것을 문제로 적지 않는다.
- 레인이 늘어도 옛 `audit.json`의 보고서가 깨지지 않도록 스코어카드 조회를 관대하게 바꿨다.

### 스테이징 미러 탐지 — 본 도메인만 훑으면 안 보이는 표면

- `crawl.py probe_mirrors` 추가. `dev.` `staging.` `test.` 등 여덟 접두를 찔러 공개된
  개발 미러를 찾는다. 실측에서 생성엔진이 우리 개발서버를 "회사 홈페이지"라며 인용했는데,
  크롤 쪽은 그 호스트를 볼 일이 없어 놓쳤다.
- 공개·색인 가능이면 `MIRROR_PUBLIC`(critical), robots로 막혀 있으면 `MIRROR_PRESENT`(warn).
- 와일드카드 DNS면 모든 접두가 응답한다 — 발견으로 보고하지 않고 `MIRROR_WILDCARD_DNS`(info).

### 수집 파이프라인 — 로그인 벽에서 멈추지 않는다

- `measure_kr.py` → **`collect.py`**. 별도 로그를 만들지 않고 `measure.py`와 같은 스키마로
  같은 `log.jsonl`에 쌓아 `measure report`가 그대로 집계한다.
- `--coverage` — 레인 × 표면 전체를 훑어 `관측/미측정`을 찍고 **빈칸 개수와 목록**을 낸다.
- `--browser` — 로그인 필요한 엔진 자리를 `unmeasured`로 예약하고 **무엇에 로그인해야 하는지**
  출력한다. "로그인 필요"는 "수동"이 아니다. 자격증명은 도구에 넣지 않는다.
- `--record` — 브라우저에서 잰 결과를 같은 로그에 `observed`로 되받는다. 되받는 통로가
  없으면 그 칸은 영원히 미측정으로 남는다.
- 응답 건전성 게이트(`SANE_MIN`): 스로틀로 축소된 SERP를 0으로 세지 않고 `unmeasured`로 남긴다.
- Windows 콘솔(cp949)에서 한글 대시 출력에 프로세스가 죽어 수집분이 통째로 날아가던 문제 수정.
  출력보다 **저장을 먼저** 한다.

### 보고 — 0건과 미측정을 같은 칸에 적지 않는다

- `measure.cell()` 도입. 분모가 0이면 `0/0` 대신 `미측정(n)` · `오류(n)` · `—`로 찍는다.
  한번 0으로 보고된 값은 사실로 굳는다.
- `ops/coverage.md` 신설 — 레인 × 표면 정본. 접근 유형은 무인·브라우저 경유·수동 셋뿐.
- `ops/measure-playbook.md` — 엔진별 접근 레시피와 직접 밟은 함정.

### 문서·저장소 탐색

- 한국어·영어 README를 설치·첫 실행·측정·한계 중심으로 정리.
- 문서 안내, 기여 방법, 버그 신고·PR 양식, 조사·실측 범위 추가.
- 신뢰성 검토 기록을 `docs/reviews/`로 옮기고 main 반영·원격 CI 결과 갱신.
- 파이프라인 도식과 명령 참조의 이전 설명을 현재 동작에 맞게 수정.
- GitHub Actions를 Node.js 24 기반 공식 버전으로 갱신해 Node.js 20 지원 종료 경고 제거.

### 신뢰성·실행 흐름 보강

- `seo_geo.py` 통합 CLI: 환경 확인, 진단+보고서, 기록 상태, 도구별 명령 전달.
- robots 경로·그룹·주석·와일드카드와 중복 X-Robots-Tag/UA 범위 처리 교정.
- sitemap seed 및 수집 한계 기록. 불완전한 수집·배포 검증은 종료 코드 2.
- 불완전 크롤로 교체용 사이트맵을 만들지 않으며, JSON-LD 파일명 충돌과 HTML 탈출 방지.
- 파일→URL manifest, 생성기 소유 파일 정리, 라이브 구조화 데이터·메타 초안 대조.
- 측정 v2: 오류·미측정 분리, 표면/모델/질의/표본 구성을 고정한 비교, 스냅샷 무결성 검증.
- FAQ·llms.txt·학습 봇 허용을 검색 인용의 필수 조건으로 판단하던 설명 교정.
- 보고서 검사 범위 표시, HTML escaping, 외부 글꼴 의존 제거, 관련 회귀/E2E 검사 추가.

### 호환성 주의

- 기존 측정 v1 파일은 읽는다. 새 로그·요약은 v2이며 기본 집계는 최신 측정일이다.
  과거 누적 집계는 `--cumulative`를 명시한다. 누적값은 배포 전후 회귀 판정에 사용하지 않는다.
- JSON-LD 적용 위치는 새 `jsonld/manifest.json`을 기준으로 확인한다.
- 종료 코드 0만 완료로 취급하는 자동화는 1(검증 실패), 2(미완료/입력 오류)를 구분해야 한다.

## [2.0.0] — 2026-09-03

절차서 한 벌이던 스킬에 **도구 여섯 개와 스키마 네 개**가 붙었다. 진단부터 재측정 일정까지가
파일로 남는다 — "고쳤다"를 말이 아니라 산출물로 증명한다. 전부 파이썬 3.10+ 표준 라이브러리만
쓰고 pip 의존은 0이다.

### 추가 — 도구 (M1~M5)

- **M1 `tools/crawl.py`** — 사이트 전수 진단. 자바스크립트 없이 받은 HTML만 보고,
  robots.txt의 Disallow를 지키며 BFS로 훑는다. → `out/<host>/audit.json`
- **M1 `tools/report.py`** — `audit.json` 한 개로 자립형 HTML 보고서 8페이지를 만든다.
  창작하지 않고, 판정할 수 없는 칸은 "미확인"으로 남긴다
- **M2 `tools/generate.py`** — `audit.json` + `site.json` → `sitemap.xml`·`robots.txt`·
  `llms.txt`·`jsonld/`·`meta-draft.csv` + `DEPLOY.md`. 기존 robots 원문을 보존하고,
  모르는 값은 지어내지 않고 `<<TODO: ...>>`로 남긴다
- **M3 `tools/verify.py`** — 배포 후 라이브를 다시 받아 항목별 ✅/❌. `deploy`(서빙 검증)와
  `diff`(전/후 진단 비교) 두 모드. fail이 하나라도 있으면 exit code 1
- **M4 `tools/measure.py`** — AI 인용 측정 루프. 수동 폼(CSV + 오프라인 HTML)이 기본 골격이고
  API 자동화는 선택이다. `init` → `form` → `import` → `report` (+ `auto`)
- **M5 `tools/drift.py`** — 불변 스냅샷 보관소와 드리프트 비교. 회귀 6종을 판정해
  하나라도 걸리면 exit code 1, 완료 조건인 `next_due`를 계산한다

### 추가 — 스키마 (계약)

| 스키마 | 만드는 도구 |
|---|---|
| `su-multi-geo/audit/1` | `crawl.py` |
| `su-multi-geo/verify/1` | `verify.py` |
| `su-multi-geo/queries/1` · `su-multi-geo/measure-row/1` · `su-multi-geo/measure/1` | `measure.py` |
| `su-multi-geo/history/1` · `su-multi-geo/drift/1` | `drift.py` |

### 추가 — 품질 체계 (M6)

- **E2E 통합 테스트** `tests/test_e2e.py` — `tests/fixtures/site/`의 결함 심은 8페이지 사이트를
  `http.server`로 127.0.0.1 임시 포트에 띄우고, crawl → report → generate → 배포 흉내 →
  verify → 재크롤 → measure → drift까지 **전부 실제 CLI로** 돈다. 외부 네트워크 없음
- **GitHub Actions CI** `.github/workflows/ci.yml` — ubuntu·windows·macos ×
  Python 3.10·3.12·3.13 매트릭스. 의존성 설치 단계가 없다는 것 자체가 "표준 라이브러리만"의 증명
- 단위 테스트 **217개** (crawl·report·generate·verify·measure·drift + E2E)
- `.editorconfig`, `CHANGELOG.md`(이 파일), `tools/README.md`의 흐름도와 스키마 버전 표

### 변경

- `lanes/aeo.md`(+ en 미러) — "추출되는 문장의 형태"에 **한글 기준 문단 길이 가이드** 추가
- `ops/intent.md` — 포맷 절에서 `aeo.md` 길이 가이드로 상호 참조
- `SKILL.md` — 맨 아래 "도구 요약" 절: 도구 여섯 개를 Phase에 매핑한 표
- `crawl.py` — 대상 호스트가 IP·localhost면 `www↔apex` 변형 접속을 시도하지 않는다
  (`hygiene.alt_host.result = "na"`). 출력 폴더 이름에서 `:` 등을 치환해 Windows에서도
  `127.0.0.1:8000` 같은 대상을 다룰 수 있다

## [1.x] — 2026-08-30 ~ 2026-08-31

절차서(`SKILL.md` + `lanes/` + `ops/` + `en/` 미러) 한 벌과 `tools/audit.sh`(홈 1페이지 빠른 진단).
