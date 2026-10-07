# GEO — Generative Engine Optimization (per-engine lanes)

The lane that makes generative AI cite you as a **primary source**. Because engines draw on
**different index sources**, split the work into shared tasks plus per-engine tasks.

## Engine matrix — where to actually push

| Engine | Index source | Decisive control point | Log-observable |
|---|---|---|---|
| **ChatGPT** | Search/user-request crawlers and search providers | OAI-SearchBot access + observed sources | Partly |
| **Gemini** | Google Search and product-specific grounding | Googlebot access/indexing + observed sources | Google-Extended is not logged |
| **Claude** | Own crawler (Claude-SearchBot) | Allow all three robots.txt agents | ✅ |
| **Perplexity** | Own crawler | Allow PerplexityBot | ✅ |

**How to read it:**
- ChatGPT not showing up → check OAI-SearchBot access, major search indexes, and observed sources
- Gemini not showing up → check Googlebot access and GSC indexing
- Claude/Perplexity not showing up → check role-specific robots policy, responses, and sources

## 1. Shared — llms.txt

A markdown site guide for AI at `/llms.txt`:

```markdown
# {Service Name}

> One line — nail down here which data you originate

## Main pages
- [Pricing](https://example.com/pricing): per-vehicle daily and monthly rates, updated daily
- [Locations](https://example.com/branches): nationwide branches and hours

## Data source and reuse
- Source: own operational data, updated daily
- Attribute citations to: example.com
```

- [ ] Use `/llms.txt` as a low-cost supplementary guide when useful. Add `/llms-full.txt` only
      when its consumer and maintenance purpose are clear
- [ ] Carry trust signals: source, update cadence, what you originate
- [ ] Serving it from an app route is fine — keeps it current

⚠️ llms.txt is a **convention, not a standard**. There is no guarantee every engine reads it.
The cost is low so do it, but do not expect citations from this alone. The body of the work is §3.

## 2. Shared — crawler access

Read `crawlers.md` and set policy by crawler purpose. Blocking one bot limits that bot's direct
access; it does not prove that every search index, crawler, or user-request route is unavailable.

## 3. Shared — becoming the primary source (the real work)

Generative engines trace "where did this number start." A page that summarizes someone
else's data loses the citation to the origin.

- [ ] Define which numbers **you** compute or collect (own metrics, aggregates, observations)
- [ ] Name them and always serve them from the **same URL** (stable URL = citable address)
- [ ] **Paragraph-level citability**: when each paragraph carries [subject + figure + as-of date
      + method], it gets cited as a paragraph. Engines slice paragraphs, not pages

**Owned and external have to say the same thing.** AI trusts a claim on your own page and takes
it **when people outside — communities, press, social — are saying the same thing.** A claim
stacked only on owned surfaces is half a claim. Reinforcement is not manufactured buzz: it is
**giving people the same experience so they say the same thing.**
→ Where to start on third-party surfaces: `reputation.md`.

### Channel map — what goes where

The moment you pick a channel you have also picked which engines you can reach. And each
channel wants **different content** — pasting the same post everywhere is duplication,
not distribution.

| Channel | Mainly reaches | What to publish | Format & cautions |
|---|---|---|---|
| **Own domain** | every engine | The ledger of facts — **originals** of pricing, terms, data; intent landings; FAQ | Phase 3–4 output. Paragraph-level citation structure (subject + number + as-of date). Every other channel links here |
| **Closed-platform blogs** (Naver etc.) | that platform + some engines | **Summaries + link to the original**, reviews, news-style posts | Put the original here and it does not exist for global engines (`naver.md` §3) |
| **Open-web blogs** (Tistory etc.) | ChatGPT & Gemini lines | Open-web editions of guides and comparisons, build logs | **Same numbers, same as-of dates** as the owned original — a mismatch backfires |
| **Job-board & business profiles** | every engine (board-like markup reads well) | The **official description fixed in Phase 2, verbatim** + a fresh updated-at | Neglect is the worst case — a stale profile keeps describing you (`reputation.md` §4) |
| **YouTube** | citations observed most in the Google line (AI Overviews · Gemini) | Explainer/comparison videos + **key numbers and the source link in the description** | Speech indexes poorly — put facts in text (title, description, captions) |
| **Communities & reviews** | absorb evaluation-stage questions | Not a place you post — a place you **earn the same words by giving the same experience** | Disguised posting and reciprocal schemes, once detected, sink the whole channel's trust |
| **Press** | varies by engine; trade press can outrank national dailies in B2B | Announcements with evidence (numbers + as-of dates), interviews | Put the official message in releases **verbatim** — headlines and sentences recur in AI answers |
| **Wikis** | high citation weight | Not yours to edit | **Monitor** facts and freshness only (`reputation.md` §3) |

The order never changes: **ledger (owned) first, distribution (channels) second.** Without an
original there is nothing to link to, and the moment two channels disagree on a number you
lose them both.

## 4. Per-engine — ChatGPT

- [ ] Use **Bing Webmaster Tools** as a discovery and diagnostics channel. ChatGPT's source
      selection can change, so registration alone does not guarantee visibility or citation
- [ ] Wire up IndexNow (Bing consumes it directly) to accelerate new pages
- [ ] Actually run `site:yourdomain` **on Bing** to confirm indexing
- [ ] robots: allow `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`

## 5. Per-engine — Gemini ★ structurally different

Gemini search-grounded answers can use Google Search and grounding infrastructure. Behavior can
vary by product and mode, so observe the sources returned by the actual surface.

- [ ] **Check GSC indexing first.** It is an important prerequisite for Google Search-based surfaces —
      go do the SSR and sitemap items in `seo.md`
- [ ] `Google-Extended` separately controls Gemini training and some grounding uses. It is not
      required for Google Search inclusion or ranking; apply the documented product scope
- [ ] AI Overviews and Gemini are separate surfaces. AI Overviews belongs to `aeo.md`
- [ ] ⚠️ **Measurement differs** — invisible in logs, so direct querying is the only way → `measure.md`

## 6. Per-engine — Claude

- [ ] robots: allow `ClaudeBot` (training) / `Claude-SearchBot` (indexing) / `Claude-User` (live).
      The three are independent, so **blocking training while keeping search and fetch open**
      is a valid choice
- [ ] Watch for `Claude-SearchBot` in logs — a leading indicator of index entry
- [ ] Claude does a meaningful share of live fetching, so **SSR response time** affects citation

## 7. Verification

Actually ask each engine and check whether your domain appears in the sources.
If not, check in order:

1. Is the crawler allowed (robots.txt)?
2. Are the raw HTML and any required rendered result accessible?
3. Is it observed in relevant search indexes and the answer's actual sources?
4. Does a competing primary source already own it? If so, differentiate what you originate

These are technical checks. For the full order through our own facts, question conditions and
third-party channels, and where to add material, follow [visibility gaps](../ops/gap.md).
