# Content operations — from question to refresh

Use this guide to find real questions, improve an existing answer, and publish a new page only
when its answer needs one. Publishing does not guarantee search exposure or AI citation. Use
[intent discovery](intent.md) to select questions, [AEO](../lanes/aeo.md) for the answer,
[SEO](../lanes/seo.md) for access, and [measurement](measure.md) for outcomes.

## 1. Record the current pages and questions

Copy the [inventory CSV](../../templates/content-inventory.example.csv) and enter one page per
row. Record its URL, `query_id` if the question belongs to the fixed measurement set,
publication date, date of a real content change, status, and next review date. For search
performance, record the source, start and end of the metric window, impressions, and clicks.
Leave numbers **blank** when the period was not measured or the console is unavailable. Blank
is not zero. `next_due` is a review note, not a scheduled job.
Use consistent team status values (for example, `draft`, `published`, `refresh`, `merge`,
`retired`) and put the canonical URL in `destination_url` for a merged page.

The source of truth for fixed questions is `out/<host>/measure/queries.json`; citation
observations live in `out/<host>/measure/log.jsonl` and its reports. Use `query_id` and
`evidence_ref` to point to those records instead of copying citation rates into the CSV. Do
not commit real site data or customer enquiries to this repository.

## 2. Keep a question backlog with evidence

Collect questions from GSC and Naver Search Advisor queries, actual customer enquiries,
autocomplete, and pages cited by competitors. For each candidate, keep the **exact question,
discovery date, source link or internal record, audience, existing answer URL, and decision**.
Remove personal details from enquiries. Estimated volume or intuition alone does not establish
demand. Group wording variants that share an answer. Consider a separate page only when the
answer, audience, or evidence differs; first see whether an existing URL can answer it. When
a question enters the fixed measurement set, add a new ID to `queries.json` without changing
existing IDs.

## 3. Write a brief after securing the evidence

Fill in the [content brief](../../templates/content-brief.example.md) with the question, a
direct answer, conditions and exceptions, evidence and as-of date for each claim, primary
source links, and related internal pages. Do not invent a figure or comparative advantage.
Distinguish facts known directly by the publisher from external material. If the answer is
already on another page, propose a refresh or merge instead of a new publication.

## 4. Run the checks that apply

- Put the answer near the start. Check that conditions, units, and dates still make sense
  when a sentence is extracted; follow the [AEO guide](../lanes/aeo.md). Add an FAQ only for
  real questions, and FAQPage structured data only when it matches visible content.
- For a public web page, use the [SEO guide](../lanes/seo.md) to check access, index blocks,
  canonical URL, sitemap, and mobile rendering. `llms.txt` is not a publishing requirement.
- If deployment files are being served, run [deployment verification](../../tools/README.md#verifypy--배포-후-검증).
  Observe search exposure and citations separately through [measurement](measure.md).
- For crossposts, check **Markdown rendering in the target channel's editor** (headings,
  tables, links, images) and its mobile preview. Adapt the summary, link, and attribution to
  that channel's policy.

## 5. Record refresh and merge decisions

When the underlying fact changes, update the answer and evidence at the same URL and record
the date of the real content change. Changing a date alone is not a refresh. If two pages
repeat the same answer, choose the canonical URL and record the source URL, move or canonical
handling, updated links, and verification result in the brief and inventory. Then compare
search metrics for like windows and fixed-query observations under like conditions. Neither
indexing nor citation has a guaranteed timeline; unmeasured or failed observations are not
zero performance.
