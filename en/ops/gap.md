# Visibility gaps — where to start when answers do not name us

When AI answers to real customer questions do not call on us, **eliminate causes from the front.**
If an earlier step is blocked, material added at a later step cannot show an effect. Read each step as
**check → fix if blocked → where to add material**. The linked documents hold the detailed method.

## 0. Is it really absent — check the measurement first

- [ ] Is the cell in `measure report` `0/10` or `unmeasured`? What was not measured is not zero ([coverage](coverage.md))
- [ ] Is the question a real customer sentence? Two or three keywords and a sentence carrying a situation and conditions get different answers ([intent](intent.md))
- [ ] Are branded and non-branded queries read separately? If we are missing even from branded queries, suspect indexing, crawlers or a split entity ([measure](measure.md) §2)
- [ ] **Separate mention from citation.** Named but not linked usually points to step 4 (third-party channels);
      not even named means start at steps 1-3

## 1. Can they get in — access

| Check | Signal | Fix |
|---|---|---|
| robots policy | `AI_CRAWLER_BLOCKED` · `AI_CRAWLER_PARTIAL` | Allow search and retrieval bots for target engines ([crawlers](crawlers.md)) |
| Raw HTML links | `LINKS_NOT_IN_HTML` · `SITEMAP_ONLY_PAGES` | Serve menus, listings and booking links as `<a href>`. Crawlers and agents that do not run scripts follow links, not the sitemap |
| Raw HTML body | `THIN_TEXT` | SSR or pre-rendering ([seo](../lanes/seo.md) §1) |
| Firewall and bot blocking | Server access logs · the host dashboard's bot classification | Check whether search and retrieval bots get 403s or challenges. Real bots are also verified by IP, so do not judge by requests that only imitate a bot name |
| Search index | GSC · Bing · Naver Search Advisor | Without an index, the path to Gemini, Copilot and Naver surfaces is narrow ([geo](../lanes/geo.md) engine matrix) |

## 2. Can they find an answer — our own facts

To put us in a comparison, an answer needs **price, conditions, duration and contact details as text in the body**.
A page that only says "ask for a quote" has nothing to compare, so another company takes the slot.

- [ ] Check for the five page types below. What is missing is the next page to build

| Page | What it holds |
|---|---|
| Brand definition | One paragraph on who the company serves. The same category name on every surface ([llmo](../lanes/llmo.md) §1) |
| Product or service | Key attributes, price, conditions and comparison criteria in a table, with units and as-of dates |
| Situation guide | One piece per customer situation, comparing options on that situation's conditions |
| Evidence and FAQ | Tests, certifications, cases, review summaries and questions actually received ([aeo](../lanes/aeo.md)) |
| Current information | Present values for price, stock, hours and policy, with the modification date |

- [ ] Write sentences that survive extraction — conditions, exceptions and as-of date in the same sentence ([aeo](../lanes/aeo.md) four checks)
- [ ] Structured data only when it matches visible values. Put only prices shown on the page into `site.json`

## 3. Do we answer the question's conditions — decompose the question

A situational question states several conditions at once. The answer picks candidates with evidence for each,
and **if one condition lacks evidence, another candidate takes that place.**

- [ ] Split each measured question into five cells: **situation** (who, when) / **criteria and constraints** (budget, size, scale) /
      **what to avoid** / **taste** (tone, style) / **proof** (reviews, ratings)
- [ ] Mark whether our pages hold a sentence answering each cell. Empty cells are what to add
- [ ] Copy down the **reason** the answer gave for choosing a competitor. If we have a fact on the same axis but never wrote it, go to step 2;
      if we genuinely lack it, that is a product or service issue — do not paper over it with copy
- [ ] Facts become a recommendation only when they chain **attribute → reason → evidence → rating** ([reputation](../lanes/reputation.md) §1)

Questions like "recommend a ..." are measurement targets. Do not build a separate direct-answer page for them; answer with the
situation guide's comparison table ([intent](intent.md) §2 excludes predictive or advisory questions as direct-answer pages only).

## 4. Do others say the same — third-party channels

Answers tend to use **what others have verified** before what a company says about itself. Decide where to add material
not from general advice but from **the channels that actually served as evidence for our own query set**.

```bash
python <skill-root>/tools/seo_geo.py measure report <audit.json>
# MEASURE.md → "출처 채널 — 답변이 무엇을 근거로 썼나" (source channels)
```

For that table to fill, record **every source URL, including ones that are not ours**, while measuring
(`--sources` for browser measurement, the source URL column in the manual form). Runs without recorded sources leave the denominator and show as `미기록` (not recorded).

| Frequent channel | How to add material | See |
|---|---|---|
| Video | Explainer and comparison videos. Put figures and the source link in the title, description and captions as text | [geo](../lanes/geo.md) channel map |
| Open blogs | An open-web version with **the same figures and as-of date** as our original | [geo](../lanes/geo.md) channel map |
| Naver blog | Summary of the original plus a link back (two-track) | [naver](../lanes/naver.md) §3 |
| Wikis | Not ours to edit. Request corrections of factual errors with evidence | [reputation](../lanes/reputation.md) §3 |
| Communities and Q&A | Do not post directly. Let real customer experience produce the same words. Shape our FAQ as Q&A | [intent](intent.md) format |
| Marketplaces | Align specs, prices and options in product listings with our original | — |
| Maps, reviews, booking | Keep address, hours and prices current on profiles; respond to reviews | [naver](../lanes/naver.md) §4 · [daum](../lanes/daum.md) · [reputation](../lanes/reputation.md) §4 |
| Jobs and company data | The official description verbatim, with an owning department | [reputation](../lanes/reputation.md) §4-5 |
| News | Announcements with figures and dates, consistent official wording | [geo](../lanes/geo.md) channel map |
| Unclassified (competitor sites, etc.) | Compare what that page states in text with ours → steps 2-3 | — |

## 5. Same questions next month — repeat

- [ ] Start with about five customer situations and a few questions each, then grow to the 40-50 range in [measure](measure.md) §2
- [ ] Each round, list **situations where we are not called** and **facts described wrongly**. Handle wrong facts with [measure](measure.md) §7
- [ ] Record the date of each step 2-4 change and re-measure with **the same query set** (`drift compare`). Changing questions breaks the comparison
- [ ] Do not claim causation between a change and citation movement ([evidence](evidence.md))

## Do not

Fabricated or paid reviews, community posts with a hidden identity, hidden text, date bumps without content changes,
mass duplication of the same text. Once caught, trust drops across the whole channel ([reputation](../lanes/reputation.md) §6).
