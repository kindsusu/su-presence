# Crawler Policy — different purposes need different policies

AI crawlers come in **three purposes**, and robots.txt policy must be written per purpose.
No policy = leaving it to chance.

| Purpose | What blocking it costs |
|---|---|
| **Training** (model training data) | Potential future training through that bot |
| **Search indexing** (the engine's own index) | That bot's direct discovery and refresh route |
| **Live fetch** (retrieval at question time) | Direct retrieval for that user request |

## Per-vendor table

| Vendor | Training | Search index | Live fetch | Official docs | Verified |
|---|---|---|---|---|---|
| **OpenAI** | `GPTBot` | `OAI-SearchBot` | `ChatGPT-User` | [developers.openai.com/api/docs/bots](https://developers.openai.com/api/docs/bots) | 2026-11-12 |
| **Anthropic** | `ClaudeBot` | `Claude-SearchBot` | `Claude-User` | [support.claude.com](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) · machine-readable [bots.json](https://claude.com/crawling/bots.json) | 2026-11-12 |
| **Perplexity** | (none) | `PerplexityBot` | `Perplexity-User` | [docs.perplexity.ai/guides/bots](https://docs.perplexity.ai/guides/bots) | 2026-11-12 |
| **Google** | `Google-Extended` ⚠️ | `Googlebot` | `Google-Agent` | [google-common-crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) · [user-triggered fetchers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-user-triggered-fetchers) | 2026-11-12 |
| **Microsoft** | (none) | `Bingbot` | (none) | [bing.com/webmasters](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) | 2026-08-30 |
| **Naver** (NEO) | (none) | `Yeti` | (none) | [searchadvisor.naver.com](https://searchadvisor.naver.com/guide/seo-basic-robots) | 2026-08-30 |
| **Daum** (KEO) | (none) | `DAUM` · `Daumoa` | (none) | [webmaster.daum.net](https://webmaster.daum.net/) | 2026-09-11 |
| Others (training only) | `CCBot` · `Applebot-Extended` · `Bytespider` · `Meta-ExternalAgent` | | | vendor docs | 2026-08-30 |

Write each Verified date as `YYYY-MM-DD`. `python tools/validate.py` reads this column: a missing or
malformed date is an error, and a row older than 120 days is a warning (`--crawler-max-age DAYS`
turns stale rows into errors).

> **`OAI-AdsBot`** — a fourth OpenAI bot confirmed in 2026. It **only visits pages submitted as
> ads**, to validate landing-page safety. Irrelevant if citation traffic is your goal. Allow it
> **only when you run OpenAI ads** (if you do and it is blocked, creative review stalls). IP list:
> [adsbot.json](https://openai.com/adsbot.json).

> **`Google-Agent`** — a **user-triggered fetcher** that retrieves a page because a user asked a
> Google product to act. Its user-agent string contains `compatible; Google-Agent`. It is neither
> the search crawler (`Googlebot`) nor the training control token (`Google-Extended`), and it
> generally ignores robots.txt rules →
> [user-triggered fetchers](#user-triggered-fetchers-may-not-follow-robotstxt).
> Google Search's AI features use the `Googlebot` index, not a question-time fetch.

### Daum has two tokens — you need both

The official documentation says `DAUM`; industry practice says `Daumoa`. **robots.txt applies
only the single most specific UA group**, so listing `Daumoa` alone drops `DAUM` requests into
the `*` group. `crawl.py` raises `DAUM_CRAWLER_BLOCKED` (critical) if either is blocked.

### For Googlebot and Bingbot, what matters is that they are **not blocked**

They are ordinary search crawlers and allowed by default. There is no need to write `Allow: /`
for them — just confirm nothing blocks them by accident. If those two are blocked, search itself
is closed long before any AI surface matters.

## ⚠️ Google-Extended is not a crawler — the structure differs

`Google-Extended` has **no user-agent**. It never fetches a page. It is a robots.txt **token**
that controls whether content Googlebot has *already* fetched may be used for Gemini training
and some grounding uses.

Three practical consequences:

1. **It never appears in server logs.** Grepping for `GPTBot`/`ClaudeBot` visits as a leading
   indicator does not work for Gemini. Measurement differs → see `measure.md`
2. The token is not an HTTP request, so UA-based rate limits and firewall rules do not target it
3. **Blocking it does not affect Google Search ranking or inclusion** — Google states this
   explicitly. Allowing it does not guarantee ranking gains or a Gemini citation either

Scope: Gemini model training / grounding in Gemini Apps / Google Search grounding on Vertex AI.

> For Google Search-based AI surfaces, check Googlebot access, indexing, and snippet eligibility.
> Manage Google-Extended separately as a training/some-grounding policy; it does not control
> Search inclusion or ranking.

## Full robots.txt (when citation traffic is the goal)

```
# OpenAI
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /

# Anthropic
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: Claude-User
Allow: /

# Perplexity
User-agent: PerplexityBot
Allow: /
User-agent: Perplexity-User
Allow: /

# Google (allow Gemini grounding + training)
User-agent: Google-Extended
Allow: /

# Naver (essential for the Korean market — NEO lane)
User-agent: Yeti
Allow: /

# Daum / Kakao (KEO lane — both tokens required)
User-agent: Daumoa
Allow: /
User-agent: DAUM
Allow: /

Sitemap: https://example.com/sitemap.xml
```

This block must list **exactly the same UAs as `UA_GROUPS` in `tools/generate.py`.** If they
diverge, the comparison test in `tests/test_docs_match_tools.py` fails — a guard against fixing
the document without the tool, or the other way round. `Google-Agent` is left out on purpose: it
is a user-triggered fetcher that generally ignores robots.txt.

**If your content is an asset and you want to block training only**, disallow just the
training column (`GPTBot`, `ClaudeBot`, `Google-Extended`, `CCBot`). Treat search and fetch
controls separately; blocking them limits those bots' direct routes.

Follow Google's current product-specific documentation for the exact Google-Extended scope.
Blocking it does not affect Search inclusion or ranking, and allowing it does not guarantee citation.

## User-triggered fetchers may not follow robots.txt

When a user asks an AI service to open a specific page, a user-triggered fetcher arrives. The
vendors' official positions on robots.txt (checked 2026-11-12, paraphrased):

| Vendor | Token | Official position on robots.txt |
|---|---|---|
| Google | `Google-Agent` | User-triggered fetchers generally ignore robots.txt rules ([docs](https://developers.google.com/crawling/docs/crawlers-fetchers/google-user-triggered-fetchers)) |
| OpenAI | `ChatGPT-User` | Visits are user-initiated, so robots.txt rules may not apply; not used for automatic crawling ([docs](https://developers.openai.com/api/docs/bots)) |
| Perplexity | `Perplexity-User` | User-initiated, so it generally ignores robots.txt rules ([docs](https://docs.perplexity.ai/guides/bots)) |
| Anthropic | `Claude-User` | Anthropic states that its bots honor robots.txt directives; the statement covers its bots generally ([docs](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler)) |

- A robots.txt `Disallow` **does not reliably stop** user-triggered fetches. Blocking or rate-limiting
  them needs server, WAF or CDN rules, with targets identified as in
  [verifying bot identity](#verifying-bot-identity--ip-lists-not-user-agent-strings)
- Blocking them **loses the direct read** of the page a user pointed the AI at. Do not treat it as
  the same decision as blocking training
- The audit (`crawl.py`) still reports the robots policy for `ChatGPT-User`, `Claude-User` and
  `Perplexity-User`. That is a **declaration check**, not proof of access or of a block

## robots.txt response status and token spelling

Read the robots.txt **response status** before its body. The same file means opposite things to a
crawler depending on the status ([RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) ·
[Google's rules](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec)).

| robots.txt response | Crawler interpretation | Audit report |
|---|---|---|
| 200 | Apply the rules in the body | Per-UA policy verdict |
| 4xx (except 429) · HTML error page served as 200 | Treat as no robots.txt; crawling is unrestricted | `ROBOTS_MISSING` |
| 429 | A 4xx in RFC 9309, but Google treats it like 5xx | `ROBOTS_UNREACHABLE`, per-UA policy unverified |
| 5xx · unreachable | Assume a complete disallow | `ROBOTS_UNREACHABLE`, per-UA policy unverified |

⚠️ When robots.txt returns 429 or 5xx, the audit **does not issue** per-UA allow/block verdicts; the GEO, NEO and KEO lanes stay `na` (unassessed).
The audit itself keeps crawling so the diagnosis does not collapse to zero pages; crawl coverage
records `robots_unavailable`. A 429 or 503 on an ordinary page request is recorded as
`HTTP_THROTTLED` (excluded from `HTTP_ERROR`) — a failed observation, not a broken link or
evidence that a bot is blocked.

`User-agent` values are compared as a **product token** (up to the first `/` or whitespace),
case-insensitively. `User-agent: GPTBot/1.1` is read as the `GPTBot` group, but `GPTBot-Image`
is a different token. Write **bare tokens**, without versions or URLs. The audit flags
non-standard spellings as `ROBOTS_NONSTANDARD_UA`.

## Cloudflare managed block and Content-Signal

With Cloudflare's [managed robots.txt](https://developers.cloudflare.com/bots/additional-configurations/managed-robots-txt/)
turned on, the response gains a block between these markers. The inner lines below are a
synthetic example of the shape only; real content depends on the settings.

```text
# BEGIN Cloudflare Managed content
User-agent: *
Content-signal: search=yes,ai-train=no
Allow: /
# END Cloudflare Managed Content
```

- The block comes from **Cloudflare's configuration**, not the origin file. Change it in the
  Cloudflare dashboard, not on the origin. The audit flags it as `ROBOTS_CDN_MANAGED`
- `Content-signal` expresses usage **preferences** with the keys `search`, `ai-input` and
  `ai-train`. It is not access control and is not scored; the audit records
  `CONTENT_SIGNAL_PRESENT` as information only
- robots.txt compliance is voluntary. Enforcement needs a blocking tool such as Cloudflare's bot
  management (AI Crawl Control)
- ⚠️ A named group does not inherit from the `*` group. If a `User-agent: GPTBot` group exists,
  the `*` group's `Content-signal` and `Disallow` lines do not apply to `GPTBot`

## Verification

```bash
curl -sL https://example.com/robots.txt
```

- Whether each UA actually arrives is visible in **access logs** — except Google-Extended,
  which never does
- robots.txt is **advisory**. Major vendors have stated they honor it, but there have
  been documented compliance controversies (Perplexity, 2024) — don't trust the statement,
  **verify compliance in your access logs**
- For real blocking, enforce at the **server/WAF layer**, not in robots.txt — and identify
  the bot by the IP lists below, not by its user-agent

## Verifying bot identity — IP lists, not user-agent strings

Anyone can imitate a user-agent string. When counting logs or writing WAF rules, match bots
against the vendors' published **IP lists**. Do not judge whether a firewall blocks a bot from
requests that only imitate the bot's name (the same rule as in the [gap analysis](gap.md)).
Checked 2026-11-12.

| Vendor | Token | IP list |
|---|---|---|
| OpenAI | `GPTBot` | [gptbot.json](https://openai.com/gptbot.json) |
| OpenAI | `OAI-SearchBot` | [searchbot.json](https://openai.com/searchbot.json) |
| OpenAI | `ChatGPT-User` | [chatgpt-user.json](https://openai.com/chatgpt-user.json) |
| OpenAI | `OAI-AdsBot` | [adsbot.json](https://openai.com/adsbot.json) |
| Anthropic | `ClaudeBot` · `Claude-SearchBot` · `Claude-User` | [bots.json](https://claude.com/crawling/bots.json) |
| Perplexity | `PerplexityBot` | [perplexitybot.json](https://www.perplexity.ai/perplexitybot.json) |
| Perplexity | `Perplexity-User` | [perplexity-user.json](https://www.perplexity.ai/perplexity-user.json) |
| Google | `Google-Agent` | [user-triggered-agents.json](https://developers.google.com/static/crawling/ipranges/user-triggered-agents.json) |
| Google | Other user-triggered fetchers | [user-triggered-fetchers.json](https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers.json) · [user-triggered-fetchers-google.json](https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers-google.json) |

`Google-Extended` has no separate HTTP user agent, so there is nothing to verify by IP.

ℹ️ **Web Bot Auth** — signed agent requests carry a `Signature-Agent` header that points to the
agent's key directory ([Cloudflare docs](https://developers.cloudflare.com/bots/concepts/bot/verified-bots/web-bot-auth/)).
It is a request-verification mechanism, not a directive site owners write in robots.txt. Adoption
is early, so treat it as information only, and do not call a request spoofed merely because the
header is absent.

## Maintenance

**The list changes.** Anthropic revised its crawler documentation in February 2026, and the
September 2026 check found OpenAI had added `OAI-AdsBot` and moved its official documentation
to `developers.openai.com`. The November 2026 check found Perplexity's documentation had moved
to `docs.perplexity.ai/guides/bots` (the old address redirects) and Google's documentation listing
the user-triggered fetcher `Google-Agent`.

**Quarterly, open each "Official docs" link in the table above** and update the **Verified** date
on any row that changed. The dates are per-row for a reason — even a partial pass leaves a record
of which rows are stale.

- Anthropic publishes a **machine-readable list** at
  [`claude.com/crawling/bots.json`](https://claude.com/crawling/bots.json). Automating that
  comparison is a future option
- Next full re-verification: **2026-12** (last full pass 2026-08-30, partial update 2026-11-12 —
  OpenAI, Anthropic, Perplexity, Google)
