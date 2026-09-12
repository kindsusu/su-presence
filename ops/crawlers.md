# 크롤러 정책 — 용도가 다르면 정책도 갈라야 한다

AI 크롤러는 **용도가 세 종류**고, robots.txt 정책은 용도별로 나눠 짜야 한다.
무정책 = 우연에 맡기는 것이다.

| 용도 | 막으면 잃는 것 |
|---|---|
| **학습** (모델 훈련 데이터) | 해당 봇을 통한 향후 학습 가능성 |
| **검색 색인** (AI 검색 자체 인덱스) | 해당 봇의 직접 발견·갱신 경로 |
| **실시간 fetch** (질문 시 페이지 열람) | 해당 사용자 요청의 직접 열람 경로 |

## 벤더별 정책표

| 벤더 | 학습 | 검색 색인 | 실시간 fetch | 공식 문서 | 확인일 |
|---|---|---|---|---|---|
| **OpenAI** | `GPTBot` | `OAI-SearchBot` | `ChatGPT-User` | [developers.openai.com/api/docs/bots](https://developers.openai.com/api/docs/bots) | 2026-09-12 |
| **Anthropic** | `ClaudeBot` | `Claude-SearchBot` | `Claude-User` | [support.claude.com](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) · 기계판독 [bots.json](https://claude.com/crawling/bots.json) | 2026-09-12 |
| **Perplexity** | — | `PerplexityBot` | `Perplexity-User` | [perplexity.ai/perplexitybot](https://www.perplexity.ai/perplexitybot) | 2026-08-30 |
| **Google** | `Google-Extended` ⚠️ | `Googlebot` | `Googlebot` | [google-common-crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) | 2026-08-30 |
| **Microsoft** | — | `Bingbot` | — | [bing.com/webmasters](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) | 2026-08-30 |
| **네이버** (NEO) | — | `Yeti` | — | [searchadvisor.naver.com](https://searchadvisor.naver.com/guide/seo-basic-robots) | 2026-08-30 |
| **다음** (KEO) | — | `DAUM` · `Daumoa` | — | [webmaster.daum.net](https://webmaster.daum.net/) | 2026-09-11 |
| 기타 (학습만) | `CCBot` · `Applebot-Extended` · `Bytespider` · `Meta-ExternalAgent` | | | 각사 문서 | 2026-08-30 |

> **`OAI-AdsBot`** — 2026년 확인된 OpenAI의 네 번째 봇으로, **광고 랜딩페이지 안전성 검증** 용도다.
> 인용 유입이 목표라면 불필요하다. **OpenAI 광고를 집행할 때만** 허용하면 된다
> (집행 중인데 막혀 있으면 소재 심사가 막힌다).

### 다음(Daum)은 토큰이 둘이다 — 둘 다 적어야 한다

공식 문서는 `DAUM`, 업계 관행은 `Daumoa`로 갈려 있다. **robots.txt는 가장 구체적인 UA 그룹
하나만 적용하므로**, `Daumoa`만 적어두면 `DAUM`으로 오는 요청은 `*` 그룹으로 떨어진다.
`crawl.py`는 둘 중 하나라도 막혀 있으면 `DAUM_CRAWLER_BLOCKED`(critical)를 낸다.

### Googlebot·Bingbot은 선언보다 **차단돼 있지 않은지**가 중요하다

일반 검색 크롤러라 기본 허용이 전제다. 굳이 `Allow: /`를 쓸 필요는 없고, **실수로 막혀 있지
않은지**만 확인하면 된다. 이 둘이 막히면 AI 표면 이전에 검색 자체가 닫힌다.

## ⚠️ Google-Extended는 크롤러가 아니다 — 구조가 다르다

`Google-Extended`는 **user-agent가 없다.** 페이지를 직접 가져오지 않는다. Googlebot이
이미 가져온 콘텐츠의 Gemini 학습과 일부 grounding 사용을 제어하는 **robots.txt 토큰**이다.

여기서 세 가지 실무적 결론이 나온다:

1. **서버 로그에서 절대 안 보인다.** GPTBot·ClaudeBot처럼 방문 로그를 grep해서 선행지표로
   쓰는 방식이 Gemini에는 통하지 않는다. 측정 방법이 다르다 → `measure.md` 참조
2. 이 토큰 자체는 HTTP 요청이 아니므로 UA 기반 rate-limit·방화벽 대상이 아니다
3. **막아도 구글 검색 순위·색인에는 영향이 없다.** 구글이 명시한 사실이다.
   허용해도 검색 순위 상승이나 Gemini 인용을 보장하지 않는다

적용 범위: Gemini 모델 학습 / Gemini 앱 그라운딩 / Vertex AI의 Google 검색 그라운딩.

> Google Search 기반 AI 표면은 Googlebot 접근·색인·snippet 적격성을 먼저 확인한다.
> Google-Extended는 Search 포함·순위와 별도인 학습/일부 grounding 정책으로 관리한다.

## robots.txt 완본 (인용 유입이 목표일 때)

```
# ── OpenAI ──
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /

# ── Anthropic ──
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: Claude-User
Allow: /

# ── Perplexity ──
User-agent: PerplexityBot
Allow: /
User-agent: Perplexity-User
Allow: /

# ── Google (Gemini 그라운딩·학습 허용) ──
User-agent: Google-Extended
Allow: /

# ── 네이버 (한국 시장이면 필수 — NEO 레인) ──
User-agent: Yeti
Allow: /

# ── 다음·카카오 (KEO 레인 — 토큰 둘 다 필요) ──
User-agent: Daumoa
Allow: /
User-agent: DAUM
Allow: /

Sitemap: https://example.com/sitemap.xml
```

이 블록은 `tools/generate.py`의 `UA_GROUPS`와 **같은 목록이어야 한다.** 어긋나면
`tests/test_generate.py`의 대조 테스트가 깨진다 — 문서만 고치고 도구를 안 고치는(또는 그 반대)
사고를 막기 위한 장치다.

**콘텐츠가 자산이라 학습만 막고 싶다면** 학습 열(`GPTBot`, `ClaudeBot`, `Google-Extended`,
`CCBot`)을 검색·fetch 역할과 분리해 검토한다. 검색·fetch 차단은 해당 봇의 직접 경로를 제한한다.

`Google-Extended`의 정확한 적용 범위는 Google 공식 문서의 제품별 설명을 따른다. 차단해도
Google Search 포함·순위에는 영향이 없으며, 허용만으로 Gemini 인용이 보장되지는 않는다.

## 검증

```bash
curl -sL https://example.com/robots.txt          # 실제 배포본 확인
```

- 각 UA가 실제로 오는지는 **접근 로그**로 본다 (Google-Extended 제외 — 안 온다)
- robots.txt는 **권고**다. 주요 벤더는 준수를 표명했지만 미준수 논란(Perplexity, 2024)도
  있었다 — 표명을 믿지 말고 **접근 로그로 준수 여부를 검증**하라
- 진짜 차단이 필요하면 robots.txt가 아니라 **서버·WAF 레벨**에서 UA를 막아야 한다

## 유지보수

**명단은 변한다.** Anthropic은 2026년 2월 크롤러 문서를 개정했고, 2026년 9월 확인에서는
OpenAI에 `OAI-AdsBot`이 늘어 있었으며 공식 문서 위치도 `developers.openai.com`으로 옮겨져 있었다.

**분기마다 위 표의 「공식 문서」 링크를 하나씩 열어 재확인**하고, 바뀐 행의 **확인일을 갱신**한다.
표의 확인일이 행마다 따로 있는 이유다 — 전체를 한 번에 못 보더라도 어느 행이 낡았는지는 남는다.

- Anthropic은 [`claude.com/crawling/bots.json`](https://claude.com/crawling/bots.json)으로
  **기계 판독 가능한 명단**을 제공한다. 자동 대조를 붙일 여지가 있다
- 다음 재확인 예정: **2026-12** (마지막 전수 확인 2026-08-30, 부분 갱신 2026-09-12)
