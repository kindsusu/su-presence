# Documentation · 문서 안내

[한국어 시작](../README.ko.md) · [English quick start](../README.md)

## 목적별로 찾기 · Find your next step

| 목적 / Task | 문서 / Guide |
|---|---|
| 설치하고 첫 진단 실행 / Install and audit | [한국어](../README.ko.md#빠른-시작) · [English](../README.md#quick-start) |
| CLI 명령·옵션·파일 스키마 / CLI reference | [tools/README.md](../tools/README.md) |
| 에이전트 실행 절차 / Agent workflow | [SKILL.md](../SKILL.md) · [English](../en/SKILL.md) |
| 실제 인용 측정·기준선 비교 / Citation measurement | [측정 운영](../ops/measure.md) · [English](../en/ops/measure.md) |
| 진단 근거와 한계 / Evidence and limits | [판단 근거](../ops/evidence.md) · [조사·검증 범위](research-status.ko.md) |
| 수정 기여·테스트 / Contribute and test | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| 무엇이 바뀌었나 / Change history | [CHANGELOG.md](../CHANGELOG.md) · [2026-09 신뢰성 검증](reviews/2026-09-05-reliability.ko.md) |
| 왜 그렇게 만들었나 / Design decisions | [결정 기록](decisions/) — 구조를 바꾼 판단과 그 근거 |

## 주제별 지침 · Topic guides

| 주제 / Topic | 한국어 | English |
|---|---|---|
| SEO 접근성 / Technical accessibility | [SEO](../lanes/seo.md) | [SEO](../en/lanes/seo.md) |
| 답변 콘텐츠 / Answer content | [AEO](../lanes/aeo.md) | [AEO](../en/lanes/aeo.md) |
| 검색 엔진별 인용 / Engine-specific citations | [GEO](../lanes/geo.md) | [GEO](../en/lanes/geo.md) |
| 조직·엔티티 일관성 / Entity consistency | [LLMO](../lanes/llmo.md) | [LLMO](../en/lanes/llmo.md) |
| 네이버 검색·AI브리핑 / Naver (NEO) | [Naver](../lanes/naver.md) | [Naver](../en/lanes/naver.md) |
| 다음·카카오 검색·AI요약 / Daum (KEO) | [Daum](../lanes/daum.md) | [Daum](../en/lanes/daum.md) |
| 외부 평판 / Third-party reputation | [평판](../lanes/reputation.md) | [Reputation](../en/lanes/reputation.md) |
| 봇 정책 / Crawler policies | [크롤러](../ops/crawlers.md) | [Crawlers](../en/ops/crawlers.md) |
| 질문·페이지 매핑 / Intent mapping | [의도](../ops/intent.md) | [Intent](../en/ops/intent.md) |

`lanes/`와 `ops/`는 한국어 기준 문서이며 `en/`에 영문 지침을 유지한다.
실행 도구와 템플릿 경로는 저장소 루트 기준이다. 설치된 스킬을 사용할 때는 해당 스킬의
절대 경로를 사용하고, 진단 결과는 대상 프로젝트의 출력 폴더에 저장한다.

The Korean guides in `lanes/` and `ops/` are the source documents; `en/` contains English guides.
Run CLI examples from the repository root. For an installed skill, use its absolute tool path and
write output to the target project's output directory.

실측 결과 `out/`, API 키, 고객 데이터는 저장소에 올리지 않는다.
예시 입력은 [templates/](../templates/)에, 자동 검사용 합성 사이트는 [tests/fixtures/](../tests/fixtures/)에 있다.
