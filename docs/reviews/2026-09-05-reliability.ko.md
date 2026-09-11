# SEO/GEO 신뢰성 개선 결과

작업일: 2026-09-05. 기준 커밋: `f7f97af1e98fa7abe7a53112e0c31999ddfd37ff`.
구현 커밋: [`009122d`](https://github.com/kindsusu/su-presence/commit/009122d5f4bc3362f22a33249a1d8d90d871c69c).
2026-09-06 (한국 시간) `main` 반영 완료. 운영 사이트 배포는 수행하지 않았다.

기존 진단·초안 생성·배포 검증·측정 도구의 연결을 유지하면서, 누락이나 관측 실패를 성공으로
판정할 수 있던 경로를 수정했다. 프로그램 동작과 검색 성과는 별개로 검증한다.

## 핵심 변경

| 영역 | 확인한 문제 | 구현한 처리 |
|---|---|---|
| 크롤 | robots 경로·주석·동률·복수 그룹 판정 오류 | 공통 Python 판정기로 통합, query/대소문자 보존 |
| 색인 지시 | none, Googlebot 지시, 반복 HTTP 헤더 누락 | generic/대상 UA 지시 합산, 다른 UA 범위 분리 |
| 수집 범위 | 페이지 상한·사이트맵 누락을 전체 검사처럼 해석 | coverage와 잔여 큐·오류 기록, 미완료 exit 2 |
| 사이트맵 생성 | 크롤하지 않은 기존 URL이 교체본에서 삭제될 위험 | 불완전 수집 시 XML 보류, 기존 URL 차이 명시 |
| JSON-LD 생성 | URL 슬러그 충돌과 script 종료 문자열 | URL 해시·manifest 매핑, HTML 안전 JSON 직렬화 |
| 배포 검증 | 같은 타입의 다른 데이터·임의 메타 변경도 통과 | 핵심 객체·정확한 초안 값 대조, 미검사 범위 별도 처리 |
| 인용 측정 | API 실패를 미인용으로 계산, 수동/API 기록 충돌 | outcome·surface·조건 구분, 오류를 관측 분모에서 제외 |
| 전후 비교 | 누적값·변경된 질의/모델/표본 구성 혼합 | 최신 회차 기본, 상세 조건 비교, 불일치 시 판정 보류 |
| 기록 보존 | 부분 스냅샷·변조·진단 덮어쓰기 | 사전 검증·원자적 기록·해시 확인, 이전 관측 보관 |
| 사용성 | 도구별 실행 진입점 분산 | 통합 CLI, 상태 확인, 국문·영문 문서와 판단 근거 정리 |

학습 봇 정책과 검색 접근성을 분리했다. FAQ·llms.txt·구조화 데이터가 없다는 사실만으로
인용 불가를 판정하지 않는다. 보고서에는 원시 HTML 검사와 실제 색인·인용 측정의 차이를 표시한다.

## 검증

- Python 단위·회귀·E2E 전체 **277개 검사 통과** (최종 실행 2026-09-05).
- 로컬 HTTP 서버에서 실제 CLI를 호출해 크롤 → 보고서 → 생성 → 배포 재현 → 검증 → 측정 → 전후 비교를 실행했다.
- 미완성 배포의 TODO·메타 불일치·중복 제목을 실패로 탐지하고, 검토한 초안을 반영하면 배포 검증이 통과하는 경로를 확인했다.
- API 오류, 다른 FAQ 객체, 12번째 이후 사이트맵 오류, 표본 배분 변화, 불완전 수집, 기록 변조 등의 반례를 회귀 검사에 포함했다.
- 셸 회귀 15개 통과. 의도적 실패가 비정상 종료 코드로 전파되는 것도 확인했다.
- Python 문법 검사, CLI doctor, 스킬 형식 검사, git diff 검사 통과.
- HTML 보고서를 브라우저에서 열어 내용과 화면을 확인했다. 외부 글꼴 없이 렌더링된다.

로컬 실행 환경은 Windows / Python 3.12.3이다. 반영 후 원격 CI에서도 Windows·macOS·Linux ×
Python 3.10·3.12·3.13의 9개 작업이 모두 통과했다.
[main CI 실행 기록](https://github.com/kindsusu/su-presence/actions/runs/33977798978).

## 실행

저장소 폴더에서 다음과 같이 시작한다.

```powershell
python tools/seo_geo.py doctor
python tools/seo_geo.py audit https://YOUR-DOMAIN --out out
python tools/seo_geo.py status out/YOUR-DOMAIN/audit.json
```

의도한 비색인 경로는 `audit ... --allow-noindex /search`처럼 지정한다.
`templates/site.example.json`에 실제 회사 사실을 입력한 뒤 초안을 생성한다.

```powershell
python tools/seo_geo.py generate all out/YOUR-DOMAIN/audit.json --site site.json
python tools/seo_geo.py verify deploy out/YOUR-DOMAIN/audit.json
python tools/seo_geo.py measure init out/YOUR-DOMAIN/audit.json
python tools/seo_geo.py measure form out/YOUR-DOMAIN/audit.json --engines chatgpt,google_aio --runs 5
```

사이트 변경 적용 후 verify를 실행한다. 생성 패키지는 검토할 초안이다.
측정 폼에는 직접 관측한 값만 넣는다. 상세 절차는 [도구 사용법](../../tools/README.md),
[측정 운영](../../ops/measure.md), [판단 근거](../../ops/evidence.md)를 따른다.

## 아직 외부에서 검증해야 하는 것

- 실제 운영 도메인·CMS에 대한 배포, CDN/WAF의 봇별 응답과 JavaScript 렌더링.
- Search Console·서치어드바이저에서 확인하는 색인·노출·클릭과 분석 도구의 전환.
- 고정 질문과 조건으로 관측한 실제 엔진별 인용률. API 어댑터의 유료 실서비스 호출은 수행하지 않았다.
- `next_due`는 계산한 날짜다. 외부 예약 실행을 등록하지 않는다.

사이트별 독자적 사실·가격·사례와 CMS 배포 권한은 프로그램이 임의로 만들 수 없다.
제공한 데모의 인용값은 합성 테스트 입력이다. 기능 검증 통과를 순위·매출 개선으로 보고하지 않는다.

## 호환성

새 측정 기록은 v2다. 기존 v1 기록을 읽지만, 조건 정보가 부족한 자료를 새 회차의 확정적
비교 근거로 삼지 않는다. 누적 보고서는 `--cumulative`를 명시한다.
JSON-LD 배포 위치는 `jsonld/manifest.json`을 사용한다. 검사 자동화는 종료 코드
0(완료), 1(실패), 2(미완료 또는 입력 오류)를 구분한다.
