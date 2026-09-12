# Measurement Loop — the fix is half, the check is the other half

This is where a playbook differs from an agency report. **Optimization without measurement
is just a claim.**

## 1. Baseline — record it before you touch anything

If you do not record the "before," you can never prove the effect.

Start with the unified entry point when this is a new installation or local artifacts are uncertain:

```bash
python tools/seo_geo.py doctor
python tools/seo_geo.py audit example.com --out out --max-pages 300 --lang en
python tools/seo_geo.py status out/example.com/audit.json
```

`doctor` checks Python 3.10+ and tool imports without using the network. `audit` runs the crawl and
HTML report together; an incomplete crawl is preserved but exits 2. `status` reads local artifacts
only. It does not re-check deployment or search performance, and a displayed `next_due` is not an
actual scheduled job.

`generate.py` records only its own files in `.su-multi-geo-generated.json`. If
`coverage.complete` is not true or known sitemap URLs may be missing, it withholds replacement
sitemap XML and marks **do not replace** in `DEPLOY.md`. Keep the existing sitemap, fix crawl limits
or failures, then rerun `audit` and `generate`.

| Item | Where | Cadence |
|---|---|---|
| Impressions · clicks · avg position | Google Search Console (28d) | weekly |
| Bing impressions · clicks | Bing Webmaster Tools | weekly |
| Naver impressions · clicks · queries | Naver Search Advisor | weekly |
| Indexed pages | GSC + `site:domain` (Google and Bing separately) | weekly |
| **AI citation O/X** | protocol in §2 | biweekly |
| **AI crawler visits** | server access log | weekly |
| **AI referral traffic** | GA4 Traffic acquisition → session source filter | weekly |

Filter sessions whose source is `chatgpt.com`, `perplexity.ai`, `gemini.google.com`,
`claude.ai`, `copilot.microsoft.com`. Unlike the manual citation O/X check, this is the
**only lagging citation indicator that collects itself** — and the only automatic evidence
that a citation turned into an actual visit.
⚠️ It **undercounts structurally.** Clients that do not pass a referrer (in-app browsers,
some redirect hops) land in Direct, so read the **trend**, never the absolute number.

```bash
# AI crawler visits — the leading indicator that moves before citations do
grep -icE 'GPTBot|OAI-SearchBot|ChatGPT-User|ClaudeBot|Claude-SearchBot|Claude-User|PerplexityBot|Perplexity-User' access.log

# Break it down per vendor to see the trend
for ua in GPTBot OAI-SearchBot ChatGPT-User ClaudeBot Claude-SearchBot Claude-User PerplexityBot; do
  printf '%-20s %s\n' "$ua" "$(grep -icF "$ua" access.log)"
done
```

No log access? Substitute your host's bot-traffic classification (Cloudflare, Vercel, etc.).

## 2. AI citation protocol — the method differs per engine

Fix **5–10 target questions** and measure with the same ones every time.
If the questions change, the trend is meaningless. 5–10 is the starting point — once this runs
as real operations, grow the set to **40–50 questions**, brand and non-brand combined.

| Engine | Log-observable | Method |
|---|---|---|
| ChatGPT | ✅ 3 UAs | Query in search mode → domain in sources, O/X |
| **Gemini** | ❌ **impossible** | **Direct querying is the only method** |
| Claude | ✅ 3 UAs | Query with web search on → cited, O/X |
| Perplexity | ✅ 2 UAs | Query → source card, O/X |
| Naver AI Briefing | ❌ | Search in the Naver app → source chip, O/X |
| Google AI Overviews | ❌ | Google search → answer box sources, O/X |

> **⚠️ Gemini has no leading indicator.**
> `Google-Extended` is a robots.txt token, not a user-agent, so it **never appears in server
> logs.** You cannot watch "the crawler came, so a citation is coming."
> Use the **GSC indexed-page count** as Gemini's leading indicator instead — Googlebot's index
> is the only gateway to Gemini. More indexed pages means more reachable surface.

### Engine priority — where to start when you cannot measure them all

**Start with ChatGPT and Google AI Overviews.** Google is the surface exposed by default, with
no login and no subscription, so it carries the largest share of real usage — and showing up
there carries into AI Mode and Gemini. Claude and Copilot hold a relatively smaller usage share:
**add them later**. Measuring two engines **under the same conditions, continuously** beats
adding more engines.

### Measurement conditions — break any of these three and the numbers are contaminated

- [ ] **Query logged out, in a private window.** Signed in, you get a personalized answer shaped
      by chat history and account settings. What you want to measure is not "the answer shown
      to that person" but "the answer shown to anyone"
- [ ] **Repeat the same question 5–10 times on the same day** (10 if you can) and look at the
      sources that appear consistently. Generated answers vary every run — ⚠️ **a single query
      is not a sample.** Record **N out of 10**, not "yes/no".
      ⚠️ The repeats must be **run on one day** to read a distribution. Spread across days, what
      you see is not the distribution but index and source changes mixed into it
- [ ] **Keep the cited URLs.** O/X alone does not tell you what to do next. Frequency (how
      often) plus URL (which page) is what separates "strengthen that page" from
      "no page exists for that question"

### Brand and non-brand queries are different metrics

Fix **two separate sets** and track them separately. Averaged together, neither is visible.

| Set | Example | How to read it |
|---|---|---|
| **Brand queries** | "○○ rental rates", "what kind of company is ○○" | Usually cited. Not cited = an incident signal (indexing, crawler, entity fragmentation) |
| **Non-brand queries** | "long-term lease rate comparison", "corporate vehicle lease terms" | **Where it is actually won.** Cited here means you reached demand that never knew you |

Brand-only gains are not improvement — they mean **you are visible only to people who already
knew you**.

Record like this — **frequency and URL on the same line**:

```
2026-09-15 | non-brand Q3 "long-term lease rate comparison" | logged out, 10 runs same day
  ChatGPT   3/10  https://example.com/long-term-rate
  Gemini    0/10  —
  Claude    7/10  https://example.com/long-term-rate (5) / /faq-rate (2)
  Naver AI  0/10  —
```

An empty URL column means there is no page to cite; the same URL repeating means that page is
a citation asset. The next task is decided by reading that column.

### The tool — `tools/measure.py`

Do not run this protocol by hand. Once the record format drifts between people, the trend breaks.

```bash
python tools/measure.py init   out/<host>/audit.json   # blank query set — a human fills it in
python tools/measure.py form   out/<host>/audit.json --engines chatgpt,gemini,claude,perplexity --runs 5
#   → measure/form-<date>.csv (Excel) + measure/form-<date>.html (offline entry form)
#   ── a human now measures, signed out, in a private window ──
python tools/measure.py import out/<host>/audit.json measure/form-<date>-filled.csv
python tools/measure.py report out/<host>/audit.json   # latest measurement date → summary.json + MEASURE.md
# Opt in only when a cumulative range is required
python tools/measure.py report out/<host>/audit.json --since 2026-09-01 --until 2026-09-30 --cumulative
```

- **Manual entry is the backbone.** The loop runs end to end with no API keys at all.
  The six measurement conditions above are pinned to the top of the form as a checklist
- **The tool never invents a question.** `init` produces blanks and hints only — a human writes
  the wording, and once fixed it does not change
- `report` uses the **latest measurement date** in the selected range for headline rates by default;
  the date trend is retained. Use `--cumulative` only to combine dates deliberately
- `python tools/measure.py auto ...` is an **optional plug-in**. It runs ChatGPT and Claude only
  when `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are in the environment; without them it points you
  at the manual form and exits cleanly (not an error). Both land in `log.jsonl`, but mode, surface,
  model, locale, login and search conditions define separate cohorts. Do not combine ChatGPT web UI
  results with an OpenAI API model
- ⚠️ **The API is a different surface from the signed-out web UI.** Automation does not replace
  manual measurement; it is a cheap way to watch the trend more often. Gemini, Perplexity,
  Google AI Overviews, Naver, Daum and Copilot are not automatable — measure them by hand
- The user pays for every call. `auto` counts the expected calls and asks before spending them
- API failures use `outcome=error`; they are neither non-citations nor part of the citation-rate
  denominator. Error rate is reported separately, and runs with errors cannot confirm a regression.
  Raw responses are not stored

## 3. The re-measure date is part of the work

- Search reflection lags. **Re-measure 14 days after the change** as a default
- Exclude the **most recent 2–3 days** from comparisons — aggregation lags
- LLMO moves on training cycles, so measure it **quarterly**, separately (`llmo.md`)
- Do not leave re-measurement to memory. **Naming the re-measure date in the report is the
  completion condition.**

### The tool — `tools/drift.py` holds that date in a file

```bash
python tools/drift.py snapshot out/<host>/audit.json --label "baseline"
#   → out/<host>/history/audit-<date>.json + index.json (sha256, baseline_date, next_due)
python tools/drift.py snapshot out/<host>/audit.json --measure out/<host>/measure/summary.json
python tools/drift.py compare  out/<host>/audit.json   # baseline vs latest → drift.json + DRIFT.md
python tools/drift.py status   out/<host>/audit.json   # days left until the next re-measure
python tools/drift.py timeline out/<host>/audit.json   # per-date trend table → TIMELINE.md
```

- **Snapshots are immutable.** Storing the same date and kind again is refused without `--force` —
  quietly overwriting a baseline later turns the whole trend into a lie
- With measurement snapshots, `next_due` is computed as **last measurement + 14 days**; otherwise it
  falls back to the last snapshot. This is a calculated date, not a Calendar or CI registration.
  Check `schedule.scheduled=false` and the “not registered” status output. The last section of `DRIFT.md` lists the
  commands to run that day, in order (crawl → snapshot → form → import → report → snapshot → compare)
- `compare` only compares cohorts with the same query fingerprint and surface/mode/locale/login/search
  conditions. Query changes, missing conditions, or API errors make a result `inconclusive`.
  Non-brand rate changes need at least five observations on each side and at least 10 percentage
  points; Wilson 95% intervals retain the uncertainty. Confirmed regressions exit 1

## 4. The stale-data trap

> **A lower bound cannot detect staleness.**

A monitor that only checks "not zero" will happily pass a value that has been frozen for days.
So what the pipeline must watch is not the value but **the timestamp on it**.
"If the last update is older than N days, do not display this metric at all" is the safe default.

`drift.py compare` runs that check for you. When the baseline being compared against is older
than `--stale-days` (default 30), it stamps ⚠️ **"the baseline is stale"** into `warnings` in
`drift.json` and at the head of `DRIFT.md`. Both the site and the engines changed several times
in between, so that comparison can tell you *what* changed but not *why* — take one more recent
snapshot and compare again.

## 5. How to read the numbers

- **Impressions first, clicks later.** The first signal of structural work is impressions.
  CTR moves only after titles and descriptions follow. Impressions up but CTR flat → next job is meta
- **Weekend dips are normal.** Weekday-shaped topics (B2B, finance, corporate) go quiet on
  weekends. That pattern is evidence of real demand
- **The query list is the roadmap.** Top queries in GSC and Search Advisor are the questions
  people actually type. A top query with no dedicated landing page = the next page to build
- **AI referrals are a lagging indicator.** The order is crawler visits (leading) → citation
  O/X (present) → referral traffic (lagging). Zero referrals alongside a confirmed citation is
  normal — you were cited but not clicked, or the referrer was stripped. Referrals rising is
  evidence citations are rising
- **Beware growth rates.** When the baseline is near zero, any change produces a percentage in
  the tens of thousands. **Always report absolute values alongside.**

## 6. Report format

The first block of the `MEASURE.md` that `measure.py report` writes uses the same shape as the
`AI cited` lines below. Search metrics (impressions, clicks, indexed) come from GSC and Search
Advisor and a human puts them in front.

```
[Baseline] 8/1–8/28  impressions 12,400 · clicks 180 · indexed 340
                     AI cited: brand 2/4 · non-brand 0/6 (10 runs each per engine)
[Change]   8/29      6 intent landings + llms.txt + robots for all vendors + FAQ LD
[Result]   9/12      impressions 31,000 (+18,600) · clicks 610 · indexed 890
                     AI cited: brand 4/4 · non-brand 3/6 (non-brand +3 vs baseline)
                     └ ChatGPT 2, Google AI Overviews 1, Gemini 0
[Next]     9/26      scheduled (last measurement + 14 days)
```

`brand 4/4` means **4 of the 4 brand queries were cited at least once**. The run-summed rate
(`ChatGPT 20/30`) lives in the per-engine table of `MEASURE.md` — they are different numbers.
Report **both**.

## 7. When the citation is wrong — the correction procedure

The AI cited you, but got it wrong. **Do not treat every dissatisfaction as an error.**
Time spent on wording differences is time the real factual errors stay live.

### Three error classes — sort these first

| Class | What | Action |
|---|---|---|
| ① **Factual error** | a figure, date, condition or classification is wrong | **Correct first.** Immediately, with evidence attached |
| ② **Missing context** | the fact is right but the subject, condition, timeframe or scope is missing | Make the source **state the condition** so it gets rewritten |
| ③ **Wording difference** | phrased differently, same meaning | **Not a correction target.** Move on |

⚠️ Mistaking ③ for ① is the biggest waste in this procedure. "That is not what we call it"
is not an error.

### Whose problem is it

**Absent from the answer entirely** is a content and distribution problem — marketing and PR own it.
**Present but wrong** is a problem of standards — what counts as the official fact is decided by
the executive or business owner. Without that line, correction requests stall between departments.

### Three correction routes

1. **Fix your own channel directly** — if the cause is a stale sentence or a missing condition
   on your page, it ends here. This is the fastest and by far the most common cause. Look here first
2. **Request a correction externally** — **the original publisher first**, the platform second.
   A request without evidence (official page URL, as-of date) is usually declined
3. **If you cannot fix it, strengthen the evidence** — instead of deleting the wrong statement,
   make the accurate one exist on more surfaces, more recently
   (`llmo.md` §2, `reputation.md` §3)

⚠️ **A generative AI answer itself is reportable but not permanently fixable.** Answers are
regenerated every time, so "fixed and verified" does not apply. What you fix is not the answer —
it is **the source the answer reads.**

### Prioritize by potential harm

1. **Legal, financial, safety** — wrong pricing, contract terms, safety information. Immediately
2. **Affects customer choice or transactions** — wrong service scope, availability, turnaround
3. **Positioning and wording** — industry classification, tone of the blurb. Batch them quarterly

If the same error repeats across several engines, it is not an individual answer that is wrong
but a **shared source**. The cited-URL record from §2 points you at it.
