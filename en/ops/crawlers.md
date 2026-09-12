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
| **OpenAI** | `GPTBot` | `OAI-SearchBot` | `ChatGPT-User` | [developers.openai.com/api/docs/bots](https://developers.openai.com/api/docs/bots) | 2026-09-12 |
| **Anthropic** | `ClaudeBot` | `Claude-SearchBot` | `Claude-User` | [support.claude.com](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) · machine-readable [bots.json](https://claude.com/crawling/bots.json) | 2026-09-12 |
| **Perplexity** | (none) | `PerplexityBot` | `Perplexity-User` | [perplexity.ai/perplexitybot](https://www.perplexity.ai/perplexitybot) | 2026-08-30 |
| **Google** | `Google-Extended` ⚠️ | `Googlebot` | `Googlebot` | [google-common-crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) | 2026-08-30 |
| **Microsoft** | (none) | `Bingbot` | (none) | [bing.com/webmasters](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) | 2026-08-30 |
| **Naver** (NEO) | (none) | `Yeti` | (none) | [searchadvisor.naver.com](https://searchadvisor.naver.com/guide/seo-basic-robots) | 2026-08-30 |
| **Daum** (KEO) | (none) | `DAUM` · `Daumoa` | (none) | [webmaster.daum.net](https://webmaster.daum.net/) | 2026-09-11 |
| Others (training only) | `CCBot` · `Applebot-Extended` · `Bytespider` · `Meta-ExternalAgent` | | | vendor docs | 2026-08-30 |

> **`OAI-AdsBot`** — a fourth OpenAI bot confirmed in 2026, used to **validate the safety of ad
> landing pages.** Irrelevant if citation traffic is your goal. Allow it **only when you run
> OpenAI ads** (if you do and it is blocked, creative review stalls).

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
the document without the tool, or the other way round.

**If your content is an asset and you want to block training only**, disallow just the
training column (`GPTBot`, `ClaudeBot`, `Google-Extended`, `CCBot`). Treat search and fetch
controls separately; blocking them limits those bots' direct routes.

Follow Google's current product-specific documentation for the exact Google-Extended scope.
Blocking it does not affect Search inclusion or ranking, and allowing it does not guarantee citation.

## Verification

```bash
curl -sL https://example.com/robots.txt
```

- Whether each UA actually arrives is visible in **access logs** — except Google-Extended,
  which never does
- robots.txt is **advisory**. Major vendors have stated they honor it, but there have
  been documented compliance controversies (Perplexity, 2024) — don't trust the statement,
  **verify compliance in your access logs**
- For real blocking, enforce at the **server/WAF layer** by user-agent, not in robots.txt

## Maintenance

**The list changes.** Anthropic revised its crawler documentation in February 2026, and the
September 2026 check found OpenAI had added `OAI-AdsBot` and moved its official documentation
to `developers.openai.com`.

**Quarterly, open each "Official docs" link in the table above** and update the **Verified** date
on any row that changed. The dates are per-row for a reason — even a partial pass leaves a record
of which rows are stale.

- Anthropic publishes a **machine-readable list** at
  [`claude.com/crawling/bots.json`](https://claude.com/crawling/bots.json). Automating that
  comparison is a future option
- Next full re-verification: **2026-12** (last full pass 2026-08-30, partial update 2026-09-12)
