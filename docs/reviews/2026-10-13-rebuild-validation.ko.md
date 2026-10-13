# Rebuild module validation

기준: 원격 main `8ae1af02c68e0e1c1ff64c0ab0643946d26ec6f7`, 작업 브랜치 `feat/rebuild-guide`.

## 구현

- `seo_geo.py rebuild`와 직접 실행 가능한 `rebuild.py`의 init/research/plan.
- 선언 경쟁사·실제 관측 출처·자사 페이지를 분리하고 근거 ID와 관측 조건을 보존한다.
- 의도별 페이지 묶음, 기존 페이지 매핑, 구조 차이, 페이지 유형별 설계, 사실의 의도 범위, 선행 기술 과제와 채널별 실행안.
- JSON·Markdown·HTML 보고서, 페이지별 편집용 청사진과 noindex HTML 초안. 이전 실행을 덮지 않는 디렉터리.
- 공개 URL/DNS/IP 고정 연결, robots·리디렉션·응답 크기·시간 제한. 수집 실패는 부분 결과로 남긴다.

## 검증 기록

Windows / Python 3.12.3에서 빈 HTML 처리 수정까지 포함한 전체 unittest **428개 통과**를 확인했다. 로컬 HTTP 서버를 사용하는 기존 E2E도 포함한다.

- 전체 실행: `python -m unittest discover -s tests -q` — 428 tests, OK.
- rebuild 관련 실행: `python -m unittest discover -s tests -p 'test_rebuild*py' -q` — 44 tests, OK. 위 전체 실행에 포함되는 부분집합이다.
- `bash tools/test_audit.sh`: 전체 PASS.
- `python tools/validate.py --root .`: 통과.
- `python tools/seo_geo.py doctor`: rebuild 포함 도구 로딩 통과.
- 새 모듈 문법 검사와 `git diff --check`: 통과.
- `templates/rebuild/` 합성 자료로 CLI 보고서·페이지별 JSON/Markdown/HTML 생성 확인.


## 범위

참고 저장소 3개의 코드·문서를 검토했으며 참조 커밋은 [조사 기록](2026-10-13-reference-projects.ko.md)에 있다. 실서비스의 검색 결과나 로그인 AI 답변을 이번 검증에서 수집하지 않았다. DNS·robots·HTTP 실패와 리디렉션은 모의 응답으로 검사했다. 실제 사이트의 노출 개선이나 모든 매체의 게재를 검증한 결과는 아니다. 발행용 문안은 사실 검토와 편집이 필요하고 자동 배포·외부 게시 기능은 없다.
