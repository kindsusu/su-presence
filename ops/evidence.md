# 판단 기준과 근거의 범위

확인일: 2026-09-05. 다음 공식 문서를 기준으로 규칙을 갱신한다.

| 관측 | 알 수 있는 것 | 별도 확인할 것 |
|---|---|---|
| HTTP 원시 HTML | 이 요청의 상태·본문·메타·LD | Google 렌더링, 봇별 WAF, 실제 색인 |
| robots 정책 | 특정 UA/경로의 허용 규칙 | 봇 방문·검색 인용 |
| sitemap | 발견/검사한 URL | 사이트의 모든 URL, 색인 수 |
| 구조화 데이터 | 파싱·유형·값 대응 | 사실성·인용 가치·랭킹 효과 |
| 직접 질의 | 해당 조건의 답과 인용 URL | 다른 계정/지역/모델/시점의 결과 |
| 검색 콘솔/분석 | 해당 제품의 노출·클릭·전환 | 다른 제품의 성과·변경의 인과효과 |

## 근거 등급 — 보고하는 주장마다 붙인다

| 등급 | 뜻 | 예 |
|---|---|---|
| **원시 HTML** (source) | HTTP 요청 하나의 상태·헤더·본문 | `curl`, `crawl.py` 산출물 |
| **렌더** (rendered) | 브라우저가 JavaScript를 실행한 뒤의 DOM | 브라우저 호스트 결과, URL 검사 |
| **내보내기** (export) | 검색 콘솔·분석 도구에서 내려받은 집계 | GSC, Bing Webmaster Tools, 서치어드바이저, GA4 |
| **실측** (field) | 실제 답변·접근 로그에서 관측한 것 | 직접 질의 기록, 서버 로그 |
| **가설** (hypothesis) | 아직 관측으로 확인하지 않은 추정 | 원인 추정, 개선 기대 효과 |

- [ ] 보고하는 주장마다 **등급·근거 위치·확인일**을 붙인다. 근거 위치는 파일 경로·URL·로그 줄처럼
      다른 사람이 다시 열 수 있는 곳이어야 한다. 등급이 없는 주장은 가설로 본다
- [ ] 한 등급의 근거로 다른 등급이 답할 질문을 단정하지 않는다. 원시 HTML로 렌더 결과나 색인을,
      렌더 결과로 실제 인용을 확정하지 않는다
- [ ] 가설은 관측으로 확인하기 전까지 결론·점수에 섞지 않는다

### 주장 원장 — 큰 감사에서 주장을 따로 관리한다

페이지와 주장이 많으면 보고서 본문과 별도로 원장을 둔다. 아래는 합성 예시다.

| ID | 주장 | 등급 | 근거 위치 | 재확인 방법 |
|---|---|---|---|---|
| C-01 | `/pricing` 원시 HTML에 `noindex` meta robots가 있다 | 원시 HTML | `out/example.com/audit.json`의 해당 페이지 항목 | `curl -sL https://example.com/pricing`에서 meta robots 확인 |
| C-02 | 고정 질문 Q3에서 ChatGPT가 10회 중 2회 example.com을 인용했다 | 실측 | 측정 스냅숏 파일과 측정일 | 같은 질문·조건으로 재질의 |
| C-03 | 요금표가 CSR이라 렌더하지 않는 봇은 본문을 못 읽는다 | 가설 | — | 아래 렌더 비교로 확인 |

## 실패를 차단으로 부르지 않는다

일반적인 fetch·브라우징 실패(시간 초과, 연결 오류, 도구 오류, 빈 응답)를 401·403·WAF 차단으로
보고하지 않는다. 차단이라고 쓰려면 **실제 상태 코드와 응답 근거**(응답 헤더, 챌린지 본문, 로그 줄)가
있어야 한다. 근거가 없으면 관측 실패로 남기고 다시 잰다. 429는 속도 제한 응답으로 따로 기록한다.

## 기술 조건과 개선 기회

Google AI Overviews/AI Mode는 일반 검색의 색인·snippet 적격성을 사용한다. 특별한 AI 파일이나
schema.org 타입은 필수 조건이 아니다. llms.txt와 FAQ는 필요에 맞는 선택 항목이다.
[Google AI 기능](https://developers.google.com/search/docs/appearance/ai-features).

`none`은 noindex/nofollow, `nosnippet`과 `max-snippet:0`은 snippet 제한이다. 페이지 목적에 맞게
의도된 제외와 실수를 구분한다. [robots 메타](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).

Google은 JavaScript를 렌더링한다. 원시 HTML 본문이 적다는 사실만으로 검색 불가를 확정하지 않는다.
SSR/사전 렌더링은 여러 봇 접근성과 성능을 위해 유용할 수 있다.
[JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).

### JavaScript 렌더 비교 — 원시와 렌더를 나란히

원시 HTML과 렌더 결과가 다를 수 있으면 같은 URL을 두 번 보고 아래 항목을 나란히 적는다.

| 항목 | 원시 HTML | 렌더 |
|---|---|---|
| `title` | 값 | 값 |
| meta robots | 값 | 값 (바뀌면 반드시 기록) |
| canonical | 값 | 값 (바뀌면 반드시 기록) |
| H1 | 개수·텍스트 | 개수·텍스트 |
| 보이는 본문 단어 수 | 수 | 수 |
| 내부 링크 수 | 수 | 수 |
| JSON-LD 블록 수 | 수 | 수 |

- 차이는 **근거로** 보고한다. 두 값과 각각의 등급·확인일을 함께 적고, 어느 쪽이 "진짜"라고 단정하지 않는다
- 렌더 결과에는 브라우저 호스트가 필요하다. 렌더에 쓴 브라우저·시점을 함께 적는다
- 핵심 도구(`crawl.py`·`verify.py`)는 원시 HTTP만 본다. 렌더 값을 도구 산출물로 표기하지 않는다

robots는 주석·경로 대소문자·그룹·최장 규칙·동률 Allow·`*`/`$`를 구분한다.
[공식 규칙](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec).

학습용 ClaudeBot, 검색용 Claude-SearchBot, 사용자 요청 Claude-User는 별도 역할이다.
[Anthropic 봇](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler).
Google-Extended는 HTTP UA가 아닌 학습/일부 Gemini grounding 제어 토큰이다. Search 포함/순위 제어가 아니다.
[Google-Extended](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers#google-extended).

## 측정과 인과관계

API 오류는 미인용이 아니라 관측 실패다. 실패·누락과 인용 성공/관측 수를 함께 기록한다.
UI와 API, 브랜드와 비브랜드, 측정일과 누적 기간을 혼합하지 않는다. 비교 전 질의·조건·가중치를 대조한다.
모델/질문/환경이 바뀌면 새 기준선이 필요할 수 있다.

5~10회는 초기 변동성 관찰용이다. 1회도 관측이지만 대표성은 낮다. 작은 차이를 성공/실패로 확정하지
않고 표본 수·불확실성·오류율을 표시한다. 반복 관측과 유사 미변경 페이지 비교가 해석에 도움이 되지만
전후 상관관계만으로 수정의 인과효과를 증명하지는 못한다.

next_due는 계획, snapshot은 기록, verify는 특정 시점의 기술 검사다. 실제 인용이나 사업 성과의 대체 지표가 아니다.
