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
| **OpenAI** | `GPTBot` | `OAI-SearchBot` | `ChatGPT-User` | [developers.openai.com/api/docs/bots](https://developers.openai.com/api/docs/bots) | 2026-11-12 |
| **Anthropic** | `ClaudeBot` | `Claude-SearchBot` | `Claude-User` | [support.claude.com](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) · 기계판독 [bots.json](https://claude.com/crawling/bots.json) | 2026-11-12 |
| **Perplexity** | — | `PerplexityBot` | `Perplexity-User` | [docs.perplexity.ai/guides/bots](https://docs.perplexity.ai/guides/bots) | 2026-11-12 |
| **Google** | `Google-Extended` ⚠️ | `Googlebot` | `Google-Agent` | [google-common-crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) · [사용자 요청형 fetcher](https://developers.google.com/crawling/docs/crawlers-fetchers/google-user-triggered-fetchers) | 2026-11-12 |
| **Microsoft** | — | `Bingbot` | — | [bing.com/webmasters](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) | 2026-08-30 |
| **네이버** (NEO) | — | `Yeti` | — | [searchadvisor.naver.com](https://searchadvisor.naver.com/guide/seo-basic-robots) | 2026-08-30 |
| **다음** (KEO) | — | `DAUM` · `Daumoa` | — | [webmaster.daum.net](https://webmaster.daum.net/) | 2026-09-11 |
| 기타 (학습만) | `CCBot` · `Applebot-Extended` · `Bytespider` · `Meta-ExternalAgent` | | | 각사 문서 | 2026-08-30 |

확인일은 행마다 `YYYY-MM-DD`로 적는다. `python tools/validate.py`가 이 열을 읽어 날짜가 없거나
형식이 틀리면 오류, 120일이 지난 행은 경고를 낸다 (`--crawler-max-age 일수`를 주면 경고 대신 오류).

> **`OAI-AdsBot`** — 2026년 확인된 OpenAI의 네 번째 봇으로, **광고로 제출된 페이지만** 방문해
> 랜딩페이지 안전성을 검증한다. 인용 유입이 목표라면 불필요하다. **OpenAI 광고를 집행할 때만**
> 허용하면 된다 (집행 중인데 막혀 있으면 소재 심사가 막힌다). IP 목록:
> [adsbot.json](https://openai.com/adsbot.json).

> **`Google-Agent`** — Google 제품에서 사용자가 요청한 작업으로 페이지를 가져오는 **사용자 요청형
> fetcher**다. UA에 `compatible; Google-Agent`가 들어 있다. 검색 색인(`Googlebot`)이나 학습 제어
> 토큰(`Google-Extended`)과 다르며, robots.txt 규칙을 대체로 따르지 않는다 →
> [사용자 요청형 fetcher](#사용자-요청형-fetcher는-robotstxt를-따르지-않을-수-있다).
> Google Search의 AI 기능은 질문 시점 fetch가 아니라 `Googlebot` 색인을 쓴다.

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
`tests/test_docs_match_tools.py`의 대조 테스트가 깨진다 — 문서만 고치고 도구를 안 고치는(또는 그 반대)
사고를 막기 위한 장치다. `Google-Agent`는 robots.txt를 대체로 따르지 않는 사용자 요청형
fetcher라 이 완본에 넣지 않는다.

**콘텐츠가 자산이라 학습만 막고 싶다면** 학습 열(`GPTBot`, `ClaudeBot`, `Google-Extended`,
`CCBot`)을 검색·fetch 역할과 분리해 검토한다. 검색·fetch 차단은 해당 봇의 직접 경로를 제한한다.

`Google-Extended`의 정확한 적용 범위는 Google 공식 문서의 제품별 설명을 따른다. 차단해도
Google Search 포함·순위에는 영향이 없으며, 허용만으로 Gemini 인용이 보장되지는 않는다.

## 사용자 요청형 fetcher는 robots.txt를 따르지 않을 수 있다

사용자가 AI 서비스에 특정 페이지를 열어 달라고 요청하면 사용자 요청형 fetcher가 온다. 벤더 공식 문서의
robots.txt 입장은 다음과 같다 (확인일 2026-11-12, 요약).

| 벤더 | 토큰 | 공식 문서의 robots.txt 입장 |
|---|---|---|
| Google | `Google-Agent` | 사용자 요청형 fetcher는 robots.txt 규칙을 대체로 무시한다 ([문서](https://developers.google.com/crawling/docs/crawlers-fetchers/google-user-triggered-fetchers)) |
| OpenAI | `ChatGPT-User` | 사용자 요청으로 여는 것이라 robots.txt 규칙이 적용되지 않을 수 있다. 자동 크롤링에는 쓰지 않는다 ([문서](https://developers.openai.com/api/docs/bots)) |
| Perplexity | `Perplexity-User` | 사용자 요청형이라 robots.txt 규칙을 대체로 무시한다 ([문서](https://docs.perplexity.ai/guides/bots)) |
| Anthropic | `Claude-User` | Anthropic은 자사 봇이 robots.txt 지시를 따른다고 밝힌다. 봇 전반에 대한 설명이다 ([문서](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler)) |

- robots.txt `Disallow`는 사용자 요청형 fetch를 **확실히 막지 못한다.** 막거나 속도를 제한하려면
  서버·WAF·CDN 규칙이 필요하고, 대상 판정은 아래 [봇 신원 확인](#봇-신원-확인--ua가-아니라-ip-목록으로)을 따른다
- 막으면 그 AI 답변에서 사용자가 지목한 페이지를 **직접 읽는 경로를 잃는다.** 학습 차단과 같은
  결정으로 다루지 않는다
- 감사(`crawl.py`)는 `ChatGPT-User`·`Claude-User`·`Perplexity-User`의 robots 정책도 보고한다.
  이는 **선언 확인**일 뿐 실제 접근 가능 여부나 차단의 증거가 아니다

## robots.txt 응답 상태와 토큰 표기

robots.txt는 본문보다 **응답 상태**를 먼저 본다. 같은 파일이라도 상태 코드에 따라 크롤러의 해석이
정반대가 된다 ([RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) ·
[Google 규칙](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec)).

| robots.txt 응답 | 크롤러 해석 | 감사 보고 |
|---|---|---|
| 200 | 본문 규칙을 적용한다 | UA별 정책 판정 |
| 4xx (429 제외) · 200으로 꾸민 HTML 오류 페이지 | 파일이 없는 것으로 보고 제한 없이 크롤할 수 있다 | `ROBOTS_MISSING` |
| 429 | RFC 9309상 4xx지만 Google은 5xx처럼 처리한다 | `ROBOTS_UNREACHABLE`, UA별 정책 미확인 |
| 5xx · 연결 실패 | 전면 Disallow로 간주한다 | `ROBOTS_UNREACHABLE`, UA별 정책 미확인 |
| 3xx | 리다이렉트를 따라간다(최소 5홉) | www↔apex 사이는 5홉까지 따라가 도착 파일로 판정. 다른 도메인·5홉 초과는 `ROBOTS_UNREACHABLE` |

⚠️ robots.txt가 429·5xx면 감사는 UA별 허용/차단을 **판정하지 않는다.** GEO·NEO·KEO 레인은 `na`(미평가)로 남는다.
진단 자체는 계속 크롤한다 — 진단 대상 페이지를 0개로 만들지 않기 위해서이며, 크롤 범위에는
`robots_unavailable`로 기록된다. 일반 페이지 요청이 429·503이면 `HTTP_THROTTLED`로 남기며(`HTTP_ERROR`에서 제외),
이는 관측 실패이지 깨진 링크나 봇 차단의 증거가 아니다.

`User-agent` 값은 **제품 토큰**(첫 `/`나 공백 전까지)으로 대소문자 구분 없이 비교한다.
`User-agent: GPTBot/1.1`은 `GPTBot` 그룹으로 읽히지만 `GPTBot-Image`는 다른 토큰이다.
버전·URL 없이 **토큰만** 적는다. 감사는 비표준 표기를 `ROBOTS_NONSTANDARD_UA`로 표시한다.

## Cloudflare 관리형 블록과 Content-Signal

Cloudflare의 [관리형 robots.txt](https://developers.cloudflare.com/bots/additional-configurations/managed-robots-txt/)를
켜면 응답에 다음 표지 사이의 블록이 삽입된다. 아래 안쪽 줄은 형태만 보여 주는 합성 예시이며
실제 내용은 설정에 따라 다르다.

```text
# BEGIN Cloudflare Managed content
User-agent: *
Content-signal: search=yes,ai-train=no
Allow: /
# END Cloudflare Managed Content
```

- 이 블록은 원본 서버 파일이 아니라 **Cloudflare 설정**에서 온다. 바꾸려면 원본 파일이 아니라
  Cloudflare 대시보드에서 바꾼다. 감사는 `ROBOTS_CDN_MANAGED`로 표시한다
- `Content-signal`은 `search`·`ai-input`·`ai-train` 키로 이용 **선호**를 표현한다. 접근 제어가 아니며
  점수에 반영하지 않는다. 감사는 `CONTENT_SIGNAL_PRESENT`로 정보만 남긴다
- robots.txt 준수는 자발적이다. 강제하려면 Cloudflare 봇 관리(AI Crawl Control) 같은 차단 수단이 필요하다
- ⚠️ 이름이 지정된 그룹은 `*` 그룹을 상속하지 않는다. `User-agent: GPTBot` 그룹이 따로 있으면 `*`
  그룹의 `Content-signal`·`Disallow`는 `GPTBot`에 적용되지 않는다

## 검증

```bash
curl -sL https://example.com/robots.txt          # 실제 배포본 확인
```

- 각 UA가 실제로 오는지는 **접근 로그**로 본다 (Google-Extended 제외 — 안 온다)
- robots.txt는 **권고**다. 주요 벤더는 준수를 표명했지만 미준수 논란(Perplexity, 2024)도
  있었다 — 표명을 믿지 말고 **접근 로그로 준수 여부를 검증**하라
- 진짜 차단이 필요하면 robots.txt가 아니라 **서버·WAF 레벨**에서 막아야 한다. 대상 판정은 UA가
  아니라 아래 IP 목록으로 한다

## 봇 신원 확인 — UA가 아니라 IP 목록으로

UA 문자열은 누구나 흉내 낼 수 있다. 로그 집계·WAF 규칙에서 봇을 판정할 때는 벤더가 공개한
**IP 목록**과 대조한다. 봇 이름만 흉내 낸 요청으로 방화벽 차단 여부를 판정하지 않는다
([공백 진단](gap.md)과 같은 원칙). 확인일 2026-11-12.

| 벤더 | 토큰 | IP 목록 |
|---|---|---|
| OpenAI | `GPTBot` | [gptbot.json](https://openai.com/gptbot.json) |
| OpenAI | `OAI-SearchBot` | [searchbot.json](https://openai.com/searchbot.json) |
| OpenAI | `ChatGPT-User` | [chatgpt-user.json](https://openai.com/chatgpt-user.json) |
| OpenAI | `OAI-AdsBot` | [adsbot.json](https://openai.com/adsbot.json) |
| Anthropic | `ClaudeBot` · `Claude-SearchBot` · `Claude-User` | [bots.json](https://claude.com/crawling/bots.json) |
| Perplexity | `PerplexityBot` | [perplexitybot.json](https://www.perplexity.ai/perplexitybot.json) |
| Perplexity | `Perplexity-User` | [perplexity-user.json](https://www.perplexity.ai/perplexity-user.json) |
| Google | `Google-Agent` | [user-triggered-agents.json](https://developers.google.com/static/crawling/ipranges/user-triggered-agents.json) |
| Google | 기타 사용자 요청형 fetcher | [user-triggered-fetchers.json](https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers.json) · [user-triggered-fetchers-google.json](https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers-google.json) |

`Google-Extended`는 별도 HTTP UA가 없으므로 IP로 확인할 대상도 없다.

ℹ️ **Web Bot Auth** — 서명된 에이전트 요청은 키 디렉터리를 가리키는 `Signature-Agent` 헤더를
싣는다 ([Cloudflare 문서](https://developers.cloudflare.com/bots/concepts/bot/verified-bots/web-bot-auth/)).
요청을 검증하는 방식이지 사이트 운영자가 robots.txt에 적는 지시어가 아니다. 아직 도입 초기라
참고 정보로만 다루고, 이 헤더가 없다는 이유만으로 위조로 단정하지 않는다.

## 유지보수

**명단은 변한다.** Anthropic은 2026년 2월 크롤러 문서를 개정했고, 2026년 9월 확인에서는
OpenAI에 `OAI-AdsBot`이 늘어 있었으며 공식 문서 위치도 `developers.openai.com`으로 옮겨져 있었다.
2026년 11월 확인에서는 Perplexity 문서가 `docs.perplexity.ai/guides/bots`로 옮겨져 있었고
(구 주소는 리다이렉트), Google 사용자 요청형 fetcher `Google-Agent`가 문서에 올라 있었다.

**분기마다 위 표의 「공식 문서」 링크를 하나씩 열어 재확인**하고, 바뀐 행의 **확인일을 갱신**한다.
표의 확인일이 행마다 따로 있는 이유다 — 전체를 한 번에 못 보더라도 어느 행이 낡았는지는 남는다.

- Anthropic은 [`claude.com/crawling/bots.json`](https://claude.com/crawling/bots.json)으로
  **기계 판독 가능한 명단**을 제공한다. 자동 대조를 붙일 여지가 있다
- 다음 재확인 예정: **2026-12** (마지막 전수 확인 2026-08-30, 부분 갱신 2026-11-12 —
  OpenAI·Anthropic·Perplexity·Google)
