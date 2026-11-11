# AEO — Getting Cited by Answer Engines (Google AI Overviews · Bing Copilot)

The lane that makes the AI answer box at the top of search results cite you. Answer engines do
not "read and summarize" a page — they **find the sentence that is the answer and extract it.**
Being shaped for extraction is the whole game.

## 0. Bing registration — the half everybody skips

Most sites watch GSC and skip Bing entirely. But **Copilot pulls answers from the Bing index,
and ChatGPT search leans heavily on it too.** Not registered with Bing = throwing away half of
AEO and GEO.

- [ ] Register with [Bing Webmaster Tools](https://www.bing.com/webmasters) — supports
      **one-click import from GSC** (brings over verification and sitemaps). A 10-minute job
- [ ] Submit the sitemap and wire up IndexNow (Bing consumes it directly)
- [ ] **Actually** run `site:yourdomain` on Bing to confirm indexing

## The core rule: one question, one page

Give every question people type into search its own page. A page covering ten questions gets
extracted as the answer to none of them. URL, h1, and title reflect the question directly.

## Which sentences actually get lifted

- [ ] **Answer directly in the first paragraph**: one sentence, ~40 characters, at the very top
- [ ] **Every sentence must state a fact independently**: context-dependent phrasing like
      "the figure mentioned above" becomes meaningless once extracted. Each paragraph must
      carry its own **subject, figure, and as-of date**
- [ ] **State the basis and the date**: a number without a basis is penalized in the engine's
      trust assessment
- [ ] **Use tables**: engines parse tables as structured facts reliably

### Paragraph length — Korean needs its own ruler

Korean takes roughly twice the width of English in a search result. Paragraphs need separate
targets the same way titles and descriptions do. The numbers below are a **practical working
range**, not a threshold any engine has published — do not cite them as measured limits. Being
outside the range is not a disqualification, but it should have a reason.

| What | Korean | English | Why |
|---|---|---|---|
| Direct-answer sentence | 1–2 sentences · **40–80 chars** | 1–2 sentences · 15–30 words | Short enough to be lifted whole into the answer box |
| Supporting paragraph | **150–300 chars** | 60–120 words | One paragraph = one claim + one piece of evidence. Longer usually means two claims |
| Tables | not counted as paragraphs | same | Parsed row by row — length targets do not apply |

- [ ] **One claim, one piece of evidence per paragraph.** Past ~300 Korean characters there are
      usually two claims in there — split it
- [ ] **Subject, figure and as-of date in the paragraph's first sentence.** The fact must hold
      whether the whole paragraph or only its first sentence gets extracted
- [ ] **A direct answer over 80 Korean characters is not a direct answer** — it means background
      was laid before the answer. Move the background below
- [ ] Do not write out in prose what a list or table can carry — it only adds length and makes
      extraction harder

### Writing so the extracted sentence cannot mislead — four checks

If one sentence getting lifted is the premise, then **that sentence must not be wrong on its
own.** Check all four, sentence by sentence.

- [ ] **Conditions and exceptions inside the same sentence.** A condition living in the next
      sentence is a condition that gets cut off
- [ ] **Attach subject, period and basis to every figure.** A number alone becomes a number
      that applies everywhere
- [ ] **Separate fact from evaluation.** "two years" is a fact; "best in the industry" is an
      evaluation. Mixed into one sentence, the evaluation gets cited as fact too
- [ ] **Every sentence complete in subject and meaning.** "in this case", "as mentioned above"
      are meaningless once extracted

**Bad** — the condition is in the next sentence, and fact is fused with evaluation:
> The warranty period is two years, the best in the industry. Consumables and user damage are excluded.

**Good** — condition, exception and basis in one sentence, evaluation dropped:
> The standard warranty runs two years from the purchase date and excludes consumables and
> damage caused by user error (as of 2026-08, units sold through official domestic channels).

## FAQ blocks

- [ ] An FAQ section at the foot of the page, built from 3–5 questions people really search
- [ ] If you add FAQPage JSON-LD, keep its meaning and material values consistent with the visible
      questions and answers. Formatting differences are not automatically spam; do not add hidden FAQs
- [ ] Only answers that data settles. No predictive or advisory Q&A (especially in regulated industries)
- [ ] ℹ️ **Expectation management**: since 2026-05-07 Google no longer shows FAQ rich results
      (the collapsible Q&A UI) in Google Search, and on 2026-06-15 it removed the FAQ rich result
      documentation ([Search Central changelog](https://developers.google.com/search/updates),
      checked 2026-11-12). Keep an FAQ section and FAQPage LD **only when the page visibly has an
      FAQ and you maintain it**. There is no Google rich-result benefit, and it is no promise of
      an AI-answer citation

## Who is speaking (E-E-A-T)

Answer engines care about **who is speaking**. The same data gets cited from a page with a
verifiable identity over an anonymous one.

- [ ] **Disclose the operator**: an About page saying who built this and why, wired to
      the site's Organization JSON-LD
- [ ] **Attribute author and source**: state where the data came from and how it was processed
- [ ] **Be contactable**: a site with an email or form scores higher on trust than a ghost site
- [ ] **Honest `dateModified`**: bumping the date without changing content is manipulation and
      backfires when detected

## Confirming it worked

After deploy, **actually search** the question on Google and Bing and record whether the AI
answer cites you. If not, check in order: ① is the direct-answer sentence above the fold
② data freshness versus competing pages ③ page trust (domain age, structured data).
