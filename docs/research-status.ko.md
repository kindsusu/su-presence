# 조사와 실측의 범위

확인일: 2026-09-06 (한국 시간).

## 접근해서 확인한 자료

공개 저장소·원격 브랜치·GitHub Actions 상태를 확인했다. 아래 공식 문서도 이번 정리 시점에
정상적으로 열렸다. 접근하지 못한 내용을 읽었다고 간주하지 않는다.

- [Google AI 기능과 사이트](https://developers.google.com/search/docs/appearance/ai-features): AI Overviews·AI Mode에 별도의 AI 파일이나 특수 구조화 데이터가 필수라는 근거는 없다.
- [Google robots.txt 해석](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec): 그룹 선택·경로별 규칙·Allow 우선순위 판단의 근거.
- [Anthropic 크롤러 안내](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler): 학습·검색·사용자 요청 봇을 구분하는 근거.

그 밖의 진단 규칙과 근거는 [ops/evidence.md](../ops/evidence.md)에 정리했다.
공식 문서는 기술 조건을 설명하지만 특정 사이트가 인용될지, 얼마나 빨리 성과가 나타날지까지
보장하지 않는다. 14일은 이 프로젝트의 첫 재관측 제안이며 검색 반영 기한이 아니다.

## 아직 검증하지 않은 범위

| 범위 | 현재 상태 | 다음 확인에 필요한 것 |
|---|---|---|
| 운영 사이트 색인·노출·클릭 | 계정 데이터 미조회 | 대상 도메인과 Search Console·서치어드바이저 데이터 |
| 유입·전환 | 운영 분석 데이터 미조회 | 분석 도구 지표와 비교 기간 |
| 엔진별 실제 답변·인용률 | 자동 테스트는 합성 입력 사용 | 고정 질의, 지역·로그인·검색 조건, 실제 반복 관측 |
| 유료 API 어댑터 | 오류 처리 등 로컬 회귀 검증, 실서비스 호출 미실행 | API 접근 권한과 호출 예산 |
| JS 렌더링·봇별 WAF | 원시 HTTP 진단 범위를 벗어남 | 운영 사이트의 브라우저 결과·URL 검사·접근 로그 |
| CMS 배포 | 생성물과 로컬 배포 재현까지 검증 | 실제 사이트 소스 또는 CMS 배포 권한 |

이는 검색 중 차단을 겪었다는 기록이 아니라 **미실행·미검증 범위**다. 과거 조사 전체의 HTTP
상태 로그가 남아 있지 않아 모든 외부 주소에 차단이 전혀 없었다고 단정하지 않는다.

## 프로그램 검증에서 발견하고 해결한 문제

부분 크롤의 거짓 완료, 사이트맵 URL 누락 위험, 중복 robots 헤더 손실, API 오류를 분모에
넣는 문제, 다른 측정 조건의 비교를 수정했다. 최종 기능 검증은 Python 277개·셸 15개이며,
원격 CI에서도 Windows·macOS·Linux × Python 3.10·3.12·3.13이 통과했다.
[main CI 실행 기록](https://github.com/kindsusu/su-presence/actions/runs/33977798978).

기술 검증 통과와 실제 검색 성과 개선을 분리해 보고한다.
