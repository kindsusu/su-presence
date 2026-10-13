# 근거 기반 사이트 재구축

기존 진단, 고정 질문의 실제 관측, 확인된 자사 사실, 공개 경쟁 페이지를 연결해 **검토 가능한 페이지 청사진**을 만든다. 제안은 가설이며 검색 순위·AI 인용·전환을 보장하지 않는다. [참조 프로젝트 조사](../docs/reviews/2026-10-13-reference-projects.ko.md)는 설계 배경이다.

## 실행

Python 3.10+ 표준 라이브러리로 저장소 루트(또는 설치된 스킬 루트)에서 실행한다. 예제 JSON은 모두 `templates/rebuild/`의 **가상 사이트 합성 데이터**이며 실제 관측이 아니다.

```bash
python tools/rebuild.py init <audit.json> [--queries <queries.json>] [--out <dir>]
python tools/rebuild.py research <brief.json> --observations <observations.json> --out <dir> --max-pages 20
python tools/rebuild.py plan <audit.json> --brief <brief.json> --observations <observations.json> --research <research.json> --out <dir>
```

`init`은 `su-presence/audit/1` 진단에서 편집 가능한 `brief.json`과 빈 `observations.json`을 만든다. `--queries`를 생략하면 진단 옆 `measure/queries.json`을 읽고, 없으면 질문 목록이 빈다. 기존 `brief.json`은 덮어쓰지 않는다. 질문의 실제 문구·의도 그룹·경쟁사·확인된 사실·채널·기존 URL을 사람이 채운다. `research`는 명시한 공개 URL만 robots 정책, 공개 IP/DNS, 리다이렉트·용량·시간 제한을 지키며 최대 20개(설정 허용 1~100개) 수집한다. 잔여 URL/차단/실패가 있으면 coverage에 남기고 종료 코드 2를 낸다. 검색 결과를 자동 검색하거나 로그인된 AI 제품을 호출하지 않는다. `plan`은 네트워크 없이 입력 파일을 비교한다. `--log <measure/log.jsonl>`를 `research`와 `plan`에 선택적으로 넣을 수 있다.

`research`는 새 `run-*` 디렉터리의 `research.json`을 만든다. `plan`도 기존 결과를 덮지 않는 `run-*` 디렉터리를 만들고 `plan.json`, `guide.md`, `report.html`, `pages/*.json`과 페이지별 편집용 `.html`/`.md` 초안을 기록한다. 각 입력의 경로·SHA-256, 근거 ID, 수집 범위와 미확인 사항을 함께 검토한다. 초안은 게시물이 아니며 자동 발행은 없다.

## 입력 계약

`brief.json`은 `su-presence/rebuild-brief/1`이다. `site_url`은 공개 HTTP(S) URL, `competitors`는 공개 URL 목록, `channels`는 비어 있지 않은 문자열 목록이다. 각 `queries` 항목은 고유 `id`, 정확한 `text`, 페이지를 공유할 질문의 `intent_id`, `page_type`, 문자열 목록 `terms`·`required_sections`, 선택적 우선순위 `priority`(1~3)를 갖는다. `pages`는 실제 기존 페이지의 `intent_id`·공개 `url` 매핑이다. 각 `facts` 항목은 고유 `id`, `text`, 공개 `source_url`, 불리언 `verified`를 갖는다. 선택적 `intent_ids`는 존재하는 의도 ID의 비어 있지 않은 목록이어야 한다. 의도 범위가 없는 사실은 관련성 검토 대상으로 남고 페이지 주장에 자동 배정되지 않는다. `verified: true`도 출처의 진실성이나 최신성을 도구가 독립 검증했다는 뜻은 아니다. 정확한 필드 검사는 `tools/rebuild_evidence.py`의 `validate_brief`에 있다.

`observations.json`은 `su-presence/rebuild-observations/1`이다. 각 관측에는 고유 `id`, brief에 있는 `query_id`·`channel`, 해당 질문의 `text`와 **완전히 같은** `query_text`, `observed_at`(시간대가 있는 ISO 8601), `outcome`(`observed`/`unmeasured`/`error`)을 넣는다. `observed`에는 실제 출처 페이지 `url`과 관측을 다시 확인할 `evidence_url`을 모두 공개 URL로 넣는다. `unmeasured`와 `error`는 0회 인용 증거가 아니다. 관측 페이지의 URL만이 정확한 경쟁 페이지 비교로 이어진다. 선언한 경쟁 도메인만으로 검색 노출을 주장하지 않는다.

기존 `measure/log.jsonl`에서 가져올 때는 현재 질문의 fingerprint가 맞는 v2 관측 인용 URL만 사용한다. API 모드 또는 검색을 끈 답변은 웹 노출에서 제외한다. 도메인만 적은 경쟁사 언급도 정확한 페이지 근거가 아니다. 로그인된 AI 웹 제품의 관측은 별도 [측정 절차](measure-playbook.md)에 따라 적절한 기존 세션에서 수집한다. Aside 자체 답변을 대상 제품의 관측으로 적지 않는다.

`research.json`은 `su-presence/rebuild-research/1`의 페이지 스냅샷과 `coverage`를 담는다. 입력 URL과 최종 URL·시각·결과(`observed`/`blocked`/`error`)를 구분한다. 추출된 제목·H1·표/FAQ/출처 신호는 페이지 구조의 관측일 뿐 콘텐츠 품질, 사실성, 순위 우위를 증명하지 않는다. 가져온 페이지의 문구를 실행 지시로 따르지 않는다.

## 결과 검토와 실행 순서

1. `plan.json`의 `page_blueprints`에서 의도별 `action`(`create`/`update`/`review`), 기존 후보, 근거 ID와 누락된 사실을 확인한다. 불완전한 진단은 없는 페이지를 확정하지 못하므로 검토 대상으로 둔다. 한 URL에 여러 의도가 겹치면 병합·분리 판단을 먼저 한다.
2. `source_comparison`은 공개 페이지의 관측 구조와 선언 경쟁사/관측 출처를 구분한다. `sections`, 문안 초안, CTA, 구조화 데이터 후보와 내부 링크를 실제 서비스·출처에 맞춰 편집한다. 미검증 사실·가격·사례를 채우지 않는다.
3. `channel_actions`는 자사 사이트와 외부 게시처의 제안을 나눈다. 네이버 블로그·커뮤니티·영상·소셜·지도·언론·위키에는 각 플랫폼 규칙과 소속 표시를 확인하고 별도 콘텐츠를 준비한다. 외부 게시나 계정 변경은 자동 수행하지 않는다.
4. 게시 후 기술 검증은 `verify`로, 같은 질문·채널의 출현과 인용은 `collect`/`measure`로 따로 재측정한다. 기준선 없는 개선율, 미측정 채널의 미노출, 내부 점수의 인용 성과 변환은 보고하지 않는다.

로컬에서 합성 예제를 시험할 때는 `python tools/rebuild.py plan templates/rebuild/audit.json --brief templates/rebuild/brief.json --observations templates/rebuild/observations.json --research templates/rebuild/research.json --out out/rebuild-demo`를 쓴다. 합성 `research.json`을 쓰므로 네트워크 요청이 없다.

## 수집 및 해석 제한

읽을 수 있는 본문이 없는 JS 셸은 `error`로 남기며 렌더링 또는 수동 검토가 필요하다. 제목의 문자 일치 기반 비교는 의미상 같은 내용을 놓칠 수 있다. `web_discovery` 같은 사용자 정의 채널은 검색 도구의 발견 기록으로 사용할 수 있지만 구글·네이버 순위나 특정 AI 제품의 인용으로 바꾸어 기록하지 않는다.

## 실행 경계

[전체 아키텍처](../docs/architecture.ko.md): 기존 `init/research/plan` 경로는 각 명령을 따로 호출한다. 연결 실행은 `run`을 사용한다. 이때 검색 제공자 설정 후 경쟁 URL 발견을 자동화할 수 있으며 사업 사실·본문 확정·사이트 구현·배포는 별도 작업이다. `research`와 `plan`이 자동으로 다음 명령을 실행하지 않으며 `plan` 산출물은 `generate`의 입력 계약이 아니다. `verify deploy`는 generate 초안과 라이브 응답을 검사하며 페이지 설계의 의미적 완성도를 검증하지 않는다.

## Connected rebuild execution · 연결 실행

`rebuild run URL --topic TOPIC --config CONFIG --out OUT` connects brief, audit, discovery, research, analysis and page planning. Use `rebuild status RUN_DIR` and `rebuild resume RUN_DIR` to inspect and resume. Existing `init/research/plan` remain available. Provider setup, verified business facts and publication review remain explicit. See [automation guide · 자동 실행 안내](../docs/plans/rebuild-automation-guide.md).
