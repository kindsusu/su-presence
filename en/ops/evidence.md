# What an observation proves, and what it does not

Checked 2026-09-05. Update these rules against the official documents linked below.

| Observation | What it tells you | What still needs separate proof |
|---|---|---|
| Raw HTTP HTML | Status, body, meta and JSON-LD **for this one request** | Google's rendered view, per-bot WAF behaviour, actual indexing |
| robots policy | The effective rule for a given UA and path | Whether the bot visited, whether search cited you |
| sitemap | The URLs you discovered and checked | Every URL on the site, the indexed count |
| Structured data | That it parses, its type, whether values line up | Whether the values are true, whether it earns citations, ranking effect |
| Direct query | The answer and cited URLs **under those conditions** | Results for other accounts, regions, models or moments |
| Search Console / analytics | Impressions, clicks and conversions **for that product** | Another product's numbers, or that a change caused the delta |

## Technical conditions and where the opportunity actually is

Google AI Overviews and AI Mode run on ordinary search indexing and snippet eligibility. There is
no special AI file or schema.org type you are required to ship. `llms.txt` and FAQ markup are
optional, chosen when they fit the page.
[Google AI features](https://developers.google.com/search/docs/appearance/ai-features).

`none` means noindex plus nofollow; `nosnippet` and `max-snippet:0` restrict snippets. Separate a
deliberate exclusion from a mistake by asking what the page is for.
[robots meta](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).

Google renders JavaScript. A thin raw-HTML body is not on its own proof that a page cannot be
found. SSR or pre-rendering is still worth doing — for the bots that do not render, and for speed.
[JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).

robots.txt has real parsing rules: comments, case-sensitive paths, groups, longest-match, Allow
winning ties, `*` and `$`. Do not eyeball it.
[The specification](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec).

ClaudeBot (training), Claude-SearchBot (search) and Claude-User (user-initiated fetch) are three
different roles. Blocking one is not blocking the others.
[Anthropic's crawlers](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler).
Google-Extended is **not an HTTP user agent** — it is a robots token controlling training and some
Gemini grounding. It does not affect Search inclusion or ranking.
[Google-Extended](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers#google-extended).

## Measurement, and what it can support

**An API error is a failed observation, not a missing citation.** Record failures and gaps
alongside the citation count, never folded into it.

Do not mix web UI with API, brand with non-brand, or a single measurement date with a cumulative
window. Before comparing two numbers, line up the queries, the conditions and the weighting. When
the model, the question or the environment changes, you may need a new baseline rather than a
comparison.

Five to ten runs is enough to see early variance, not to settle anything. One run is still an
observation, just a weakly representative one. Do not turn a small difference into a
success-or-failure verdict: show the sample size, the uncertainty and the error rate. Repeated
observation and a comparison against similar unchanged pages both help interpretation, but a
before-and-after correlation still does not prove your edit caused the change.

`next_due` is a plan, a snapshot is a record, and `verify` is a technical check at one moment.
None of the three is a stand-in for a real citation or a business result.
