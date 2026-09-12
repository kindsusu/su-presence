# Per-engine measurement recipes — how you actually get in

`measure.md` decides **what to measure and how to write it down.** This document covers **how to
actually reach each engine.** Everything here was confirmed by measuring, and the traps listed
are only the ones we walked into ourselves.

> As of 2026-09-11. Engine UIs change often — if something stops working, fix this document.

---

## What can be automated

| Surface | Automation | How |
|---|---|---|
| Naver organic | **full** | `urllib` + desktop/mobile UA |
| Naver AI Briefing | **full** | detect the fender block → SSE API |
| Daum web search / AI Summary | **full** | headless Chrome DOM dump |
| Google organic / AI Overviews | **full** | headless + browser UA |
| Perplexity | semi | **sign-in required** (changed 2026-09). Thread URLs stay public, so captures work |
| ChatGPT · Gemini · Claude | semi | sign-in required; drive the browser in neutral mode |
| GSC / Search Advisor index counts | **manual** | account sign-in — this is the only row a human owns |

Only that last row stays "a human checks it."

---

## Naver

Desktop `search.naver.com/search.naver?query=`, mobile `m.search.naver.com/...`. Both come down
over plain HTTP. **Treat mobile as the default** — Naver is mobile-first.

### AI Briefing

1. **Decide whether it fired from the SERP HTML.** A `data-block-id="ai-briefing/..."` fender
   block means it fired.
2. Inside that block, the `entry.bootstrap(...)` payload has two variants.
   - `aibAnswer` — `props.summary` and `props.sources` are **inline in the HTML**. No extra request
   - `aibAnswerRuntime` — `props.apiURL` (`aib-api.naver.com/aibrender.naver`) serves an **SSE stream**
3. The SSE format is `event:<name>` followed by `data:<JSON>`. For `sources` (the on-screen source
   chips) and `footnote_sources` (the evidence pool), `data` is **a bare array, not an object**.

```
⚠️ Do not put br in Accept-Encoding — the body comes back corrupted. gzip, deflate only.
⚠️ The API will compose an answer for any query at all. Judging "it fired" from the API response
   alone is wrong. Firing is decided by the fender block in the SERP HTML, always.
```

---

## Daum

`search.daum.net/search?w=tot&q=`.

```
⚠️ curl's default UA gets you 162 bytes. Send a browser UA or use headless.
⚠️ The page encoding is EUC-KR. Reading it as UTF-8 produces garbage.
```

- The two blocks that matter are **Site** (registration-based) and **Unified Web**
- AI Summary: detect firing with `disp-attr="AIO"`, confirm citations from `sources[].url`
- The `gsid` prefix classifies the source — `tstory-` / `nvblg_` / `cafe-` / `web-` /
  (empty) = Daum's own properties

---

## Google

`google.com/search?q=...&hl=ko&gl=kr`. Detect AI Overviews by the localized "AI Overview" string.

```
⚠️ The default headless UA triggers reCAPTCHA. Do not solve CAPTCHAs.
   Passing a normal Chrome UA via --user-agent gets through.
⚠️ AI Overviews fire inconsistently for the same query. Firing rate only emerges from repetition.
```

---

## Perplexity

**As of 2026-09-11, signed-out measurement is blocked.** The query is accepted and a thread URL
(`/search/<uuid>`) is even created, but where the answer should be you get *"Sign up and send
your request again."* The thread exists with no content, so **do not count this as "zero
citations"** — it is `unmeasured`.

- Measure in a signed-in browser. The human signs in; the agent runs the query and the verdict.
- The thread URL itself is still publicly reachable, so **evidence captures can be taken from it.**
- A sign-up modal covers the first visit. Dismiss it before typing, or the query goes nowhere.

```
⚠️ Opening /search?q= directly in headless also hits the sign-in wall.
⚠️ This section changes often. If it stops working, fix it — believing a stale "no sign-in
   needed" recipe and writing down a zero is the worst outcome.
```

---

## ChatGPT

| State | URL | Behaviour |
|---|---|---|
| Signed out | `chatgpt.com/?q=<query>` | **sends automatically** |
| Signed in | `chatgpt.com/?temporary-chat=true&prompt=<query>` | fills the box; **you send it** |

If you measure while signed in, **temporary chat is mandatory.** Without it, account history and
memory bleed into the answer.

```
⚠️ Send-button coordinates move when the window resizes. Click by element reference, not coordinates.
⚠️ Enter sometimes does not send. Press the send button by element reference.
```

The most reliable way to get an exact query in while signed in is the URL parameter —
open `chatgpt.com/?temporary-chat=true&prompt=<url-encoded query>` and the text lands verbatim.
All that is left is pressing send.

---

## Korean spacing disappears — every engine

Browser automation typing **frequently drops the spaces between Korean words.** If
`장기렌트 업체 추천해줘` arrives as `장기렌트업체추천해줘`, **the query set is broken**, and
recording that result under the same query id poisons the trend.
**Always read the input back before sending.**

In the order that actually worked:

1. **URL parameter** — most reliable on engines that support it (ChatGPT)
2. **The `insertText` command** — fires a real input event on contenteditable editors
   (Claude, Gemini)

   ```js
   const e = document.querySelector('[contenteditable="true"]');
   e.focus();
   document.execCommand('selectAll', false, null);
   document.execCommand('insertText', false, '장기렌트 업체 추천해줘');
   ```

3. Typing word by word with separate space keypresses — **works sometimes.** Verify every time

```
⚠️ Even when method 2 puts the text in, the editor's internal state may not update, so Enter
   does nothing. Press the send button by element reference.
⚠️ Never record a result you sent without verifying the input. You measured a different query.
```

---

## Gemini · Claude

Both **block signed-out access.** Measure in a signed-in browser, in neutral mode.

### The order that works — follow it and both measure automatically

Trying to read the state off the on-screen greeting got it wrong twice.
**The button's aria-label is the accurate state.**

| Engine | Off | On |
|---|---|---|
| Gemini | `Turn on temporary chat` | `Turn off temporary chat` |
| Claude | `Use incognito` | `Exit incognito mode` |

1. **Turn the mode on first, with an empty composer.** Gemini's **toggle does not respond while
   a draft is in the box.** After clicking, confirm the label above flipped.
2. **Insert the query with `insertText`** (see the Korean-spacing section). Read the value back.
3. **Send with the send button.** Enter after `insertText` does nothing — the editor's internal
   state has not caught up. Press the send button.
4. Wait 20–30 seconds, then read the answer from **the full `main` text.**

```
⚠️ Gemini's <model-response> innerText returns only "Gemini's response". Do not conclude
   "no answer" because the body is missing — read all of main and it is there. On 2026-09-11
   this caused one run to be filed as unmeasured before it was reversed.
⚠️ Claude's incognito modal restores the previous conversation. Revisiting the URL does not
   reset it — close the modal and reopen it with the ghost icon for a fresh chat.
⚠️ Gemini has no prompt URL parameter such as ?q= (confirmed 2026-09-11). Use method 2.
```

### Claude incognito **contaminates non-brand queries too**

Asked a brand-free, industry-only question ("recommend some ○○ providers"), Claude came back
with *"are you comparing this against (the audited company)?"* — **it knows the user's employer
from the account profile**, not from the web. Counting that mention as a citation
**creates a result that does not exist.**

- Claude numbers measured through the web UI are aggregated **with brand mentions excluded**
  (`measure.md`, "neutral mode per engine")
- If you need an uncontaminated Claude GEO number, use the **API path** — `measure.py auto` runs
  the Messages API with the `web_search` server tool and counts **only URLs actually cited.**
  But the API and the web UI are **different surfaces**, so they never share a cell
  (the log's cohort field keeps them apart)

```
⚠️ Claude incognito only stops saving and training. The account profile is still live.
   A description that came from the profile is not a web citation — exclude it per
   measure.md, "neutral mode per engine".
```

### A refusal is data too

Gemini sometimes refuses on **financially regulated phrasing** (subprime, no-credit-check and
similar). When it refuses, **competitors do not appear either** — that keyword simply has no
competition to observe. Check each engine for refusals before freezing the query set, and
**replace refused phrasing with a neutral wording of the same intent.**

---

## Evidence capture — headless Chrome

```bash
chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --disable-blink-features=AutomationControlled \
  --user-agent="<normal Chrome UA>" --lang=ko-KR \
  --user-data-dir="<temp profile>" --window-size=1400,2400 \
  --virtual-time-budget=15000 --screenshot="<output path>" "<URL>"
```

```
⚠️ Relative paths fail. Use an absolute path, with forward slashes rather than backslashes.
⚠️ Korean characters in the filename make the write fail. Save as ASCII, then rename.
⚠️ --virtual-time-budget only fast-forwards timers. Network responses arrive in real time, so
   streaming engines (ChatGPT, Perplexity) get captured mid-generation.
```

If you only need the DOM, `--dump-dom` is lighter than a capture and easier to parse.

---

## Freeze the query set in a file

Change the set and the trend breaks. Keep `queries.txt` in the repository, and when it has to
change, **create a new file and keep the old one.** If two people measure with different sets on
the same day, the results cannot be compared at all.

- Split the brand and non-brand sets **into separate files** (see `measure.md`)
- Record the date and reason for any change as a comment at the top of the file
