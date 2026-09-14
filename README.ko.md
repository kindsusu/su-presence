# su-presence

![su-presence — 일곱 레인, 하나의 진단 렌즈](assets/su-presence.png)

> 검색·AI 답변에서 우리 자리가 있는가 — SEO·AEO·GEO·LLMO·네이버·다음을 한 표면으로. **su**(권수, [kindsusu](https://github.com/kindsusu))가 직접 다듬는 스킬

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ko.md"><b>한국어</b></a>
</p>

<p align="center">
  <a href="https://github.com/kindsusu/su-presence/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/kindsusu/su-presence/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="version 2.0.0" src="https://img.shields.io/badge/version-2.0.0-0E6B5C">
  <img alt="stdlib only, zero dependencies" src="https://img.shields.io/badge/stdlib%20only-zero%20dependencies-1A2B28">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-0E6B5C">
  <a href="LICENSE"><img alt="License PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-A96A00"></a>
  <img alt="Korean-first" src="https://img.shields.io/badge/Korean-first-B3372B">
</p>

**기술 검색 접근성을 진단하고, 사이트 변경 초안을 만들고, 라이브 배포를 검증하고, AI 검색 인용을 측정하는 한국어 우선 스킬입니다.** SEO·AEO·GEO·LLMO·네이버·평판을 별도 레인으로 다루며, 크롤러 접근·색인 상태·인용·사업 성과를 같은 결과로 섞지 않습니다.

## 빠른 시작

설치 경로 하나를 고릅니다. 플러그인은 Claude Code에서 편하고, 클론은 독립 실행과 개발에 적합합니다.

### 플러그인 설치

```text
/plugin marketplace add kindsusu/su-presence
/plugin install su-presence@su-presence
```

설치한 스킬의 디렉터리에서 도구를 실행합니다. 진단 대상 프로젝트에 `tools/`가 있다고 가정하지 않습니다.

```bash
python <skill-root>/tools/seo_geo.py doctor
python <skill-root>/tools/seo_geo.py audit https://example.com --out <project>/out
```

### 클론 후 로컬 실행

```bash
git clone https://github.com/kindsusu/su-presence.git
cd su-presence
python tools/seo_geo.py doctor
python tools/seo_geo.py audit https://example.com --out out
```

`audit`는 `out/<host>/audit.json`과 `report.html`을 만듭니다. 적용되는 `robots.txt`를 지키고 수집 범위를 기록하며, 수집이 끝나지 않으면 종료 코드 `2`를 냅니다. 불완전한 진단은 사이트 전체 목록이 아니라 일부 관측입니다.

## 실제 작업 순서

![파이프라인 — 진단, 기준선, 생성, 배포, 검증, 재측정, 비교](assets/pipeline.ko.svg)

### 사이트 변경 전: 기준선을 남긴다

`compare`에는 날짜가 다른 스냅샷이 최소 두 개 필요합니다. 초안을 만들거나 배포하기 **전** 첫 진단과 인용 cohort를 남깁니다. 질의 세트는 폼보다 먼저 초기화하고, 채운 폼은 import와 report까지 끝낸 뒤 스냅샷으로 보관합니다.

```bash
# 1. 진단하고 변경 전 인용 cohort를 기록한다
python tools/seo_geo.py audit https://example.com --out out
python tools/seo_geo.py measure init out/example.com/audit.json
python tools/seo_geo.py measure form out/example.com/audit.json \
  --engines chatgpt,gemini,claude,perplexity --runs 5
python tools/seo_geo.py measure import out/example.com/audit.json \
  out/example.com/measure/form-YYYY-MM-DD-filled.csv
python tools/seo_geo.py measure report out/example.com/audit.json
python tools/seo_geo.py drift snapshot out/example.com/audit.json \
  --measure out/example.com/measure/summary.json --baseline
```

`YYYY-MM-DD`는 폼에 사용한 실제 날짜로 바꿉니다. 생성된 CSV를 직접 관측한 결과로 채우고
`-filled.csv` 이름으로 저장하거나, import에 실제 저장 경로를 전달하세요.

### 초안 생성, 배포, 라이브 검증

초안을 만들기 전에 [`templates/site.example.json`](templates/site.example.json)을 `out/<host>/site.json`으로 복사해 확인한 회사 사실만 입력합니다. 생성물은 배포 초안이며, 사람은 이를 검토한 뒤 승인된 운영 환경에만 적용합니다.

```bash
python tools/seo_geo.py generate all out/example.com/audit.json \
  --site out/example.com/site.json

# 배포한 뒤 실제 서버가 반환하는 값을 확인한다.
python tools/seo_geo.py verify deploy out/example.com/audit.json
```

`verify deploy`는 실제 서버 응답을 확인합니다. JSON-LD의 `@type`만 맞는다고 검증이 끝나는 것은 아니며, 해당하는 값은 가시 본문과도 대조합니다.

### 변경 후: 재측정, 두 번째 스냅샷, 비교

다른 날짜에 같은 질의 cohort와 조건으로 측정합니다. 재진단하고, 두 번째 폼을 채워 import와 report를 끝낸 뒤 두 번째 스냅샷을 남깁니다. 이때부터 `compare`가 전후 근거를 비교할 수 있습니다.

```bash
python tools/seo_geo.py audit https://example.com --out out
python tools/seo_geo.py measure form out/example.com/audit.json \
  --engines chatgpt,gemini,claude,perplexity --runs 5
python tools/seo_geo.py measure import out/example.com/audit.json \
  out/example.com/measure/form-YYYY-MM-DD-filled.csv
python tools/seo_geo.py measure report out/example.com/audit.json
python tools/seo_geo.py drift snapshot out/example.com/audit.json \
  --measure out/example.com/measure/summary.json
python tools/seo_geo.py drift compare out/example.com/audit.json
```

`noindex`는 확인할 finding이지 모든 페이지나 결과가 무효라는 뜻이 아닙니다. 의도한 비색인 경로는 `--allow-noindex`로 밝힐 수 있고, 진단은 관측한 사실을 남깁니다. `status`는 로컬 산출물 기록만 보여 주며 현재 배포·색인·인용·예약 실행을 증명하지 않습니다.

## 산출물이 뜻하는 것

| 단계 | 주요 산출물 | 확인하는 범위 |
|---|---|---|
| `audit` | `audit.json`, `report.html` | 원시 HTTP 관측과 실제 수집 범위 |
| `generate` | `deploy/`, `DEPLOY.md` | 검토 가능한 배포 초안. 배포 완료 증거는 아님 |
| `verify deploy` | `verify.json`, `VERIFY.md` | 배포 뒤 라이브 응답 검사. `1`은 검증된 실패, `2`는 불완전 또는 잘못된 범위 |
| `measure` | 수동 폼, `log.jsonl`, `summary.json`, `MEASURE.md` | 고정 cohort의 반복 인용 관측. API와 웹 UI는 별도 표면 |
| `collect.py` | `measure/log.jsonl` 행 | **표면 누락 없는 수집.** 네이버·다음·구글 AI개요는 무인, `--browser`는 로그인 필요한 엔진을 `unmeasured`로 예약하고, `--record`로 브라우저 측정 결과를 같은 로그에 되받으며, `--coverage`는 빈칸을 찍는다. 축소 응답·AI 출처 경계 미식별·출처 조회/파싱 실패는 0이 아니라 `unmeasured`다. 일반 검색 결과 링크는 AI 인용이 아니다. |
| `drift` | 불변 `history/`, `drift.json`, `DRIFT.md` | 스냅샷 비교와 다음 점검일. `next_due`는 예약을 만들지 않음 |

플러그인 메타데이터 버전은 **2.0.0**입니다. `main`의 신뢰성·실행 흐름 변경은 [CHANGELOG의 Unreleased](CHANGELOG.md)에 기록합니다. 이는 공개 GitHub 릴리스나 버전 변경을 뜻하지 않습니다.

## 더 읽기

- [도구 전체 안내](tools/README.md) — 명령, 스키마, 종료 코드, 산출물
- [운영 절차](SKILL.md) — 설치된 스킬 경로에서 실행하는 법과 레인 선택
- [증거의 경계](ops/evidence.md) — 크롤 진단이 증명하는 것과 증명하지 못하는 것
- [엔진별 측정 레시피](ops/measure-playbook.md) — 각 엔진에 실제로 어떻게 접근하는가, 그리고 함정
- [문서 목차](docs/README.md)
- [기여 안내](CONTRIBUTING.md)
- [변경 이력](CHANGELOG.md)

## 범위와 한계

- Python 3.10+ 표준 라이브러리만 사용합니다. `tools/audit.sh`만 bash와 curl이 추가로 필요합니다.
- 진단은 원시 HTTP HTML을 봅니다. JavaScript 렌더링, 벤더 색인 상태, 순위, 인용을 증명하지 않습니다.
- 인용은 고정한 질의·표면·회차 cohort에서 관측합니다. 기술 준비가 인용을 보장하지 않으며, 오류와 미측정은 미인용과 분리합니다.
- `collect.py`가 만드는 자동·브라우저 측정 행에는 질의 fingerprint와 campaign을 남깁니다. 현재 질의와 맞지 않거나 fingerprint가 빈 신·구 v2 행은 집계에서 빼고 incompatible로 보고합니다. 원본 로그는 보존하고 재측정하며, 구형 v1 읽기 호환은 유지합니다.
- **측정 범위를 수치에 붙여 적습니다.** 전수(`0/7`)와 대표질문(`0건`)과 미측정을 구분하며, 전수로 재지 않은 것을 전수처럼 적지 않습니다.
- **`0`은 도구가 만들었을 수 있습니다.** 반복 요청을 몰아치면 검색엔진이 축소 응답을 줍니다. 응답 건전성 게이트를 통과하지 못하면 0으로 기록하지 않고 측정 실패로 남깁니다.
- 로그인이 필요한 엔진(ChatGPT·Gemini·Claude)은 각 서비스의 **임시·시크릿 모드**로 잽니다. 계정 프로필에서 유래한 서술은 웹 인용이 아니므로 집계에서 제외합니다.
- 네이버(NEO)와 다음·카카오(KEO)는 **각각 독립 레인**입니다 — 크롤러 토큰도, 소유확인 방식도, AI 요약을 만드는 모델도 다릅니다. 두 엔진의 인용 측정은 `tools/collect.py`로 자동화되어 있습니다. 등록, 외부 평판, 운영 배포에는 사람의 판단과 접근 권한이 필요합니다.
- 기본 크롤 한도는 300페이지입니다. 선언된 sitemap 또는 sitemap index 자식 조회 실패는 수집을 불완전으로 만들고 교체용 sitemap 생성을 막습니다. 선언되지 않은 기본 sitemap의 404·410은 선택적 부재입니다.
- 미러 검사는 root `noindex`, robots 차단, 미차단, 미확인을 구분하며 모든 미러 호스트의 색인 가능 여부를 단정하지 않습니다. 정상 `www → apex` 리다이렉트는 접속 실패가 아닙니다.

로컬 테스트는 다음 명령으로 실행합니다.

```bash
python -m unittest discover tests
```

## 라이선스

**PolyForm Noncommercial 1.0.0** — [LICENSE](LICENSE)를 참조하세요.

- **개인·비영리·교육·연구 용도는 무료입니다**
- 이 라이선스에서는 **상업적 또는 기업 용도가 허용되지 않습니다**. 별도 상업 라이선스는 **scitusu@gmail.com**으로 문의하세요.
