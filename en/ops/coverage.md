# Coverage matrix — so you can tell what you skipped

There are seven lanes and more surfaces than that. **The dangerous state is not knowing which
surfaces you failed to measure.** An unmeasured surface is not "no citations", it is *unknown* —
and once it sits in a table as `0`, the two become indistinguishable.

This document is **the full list of surfaces to check**. Before reporting any measurement, open
this table and fill every cell with one of `observed` / `unmeasured` / `not applicable`.
If a cell is still blank, do not report.

---

## Lane × surface

| Lane | Surface | Tool | Access | What the human does |
|---|---|---|---|---|
| **SEO** | Raw HTML (SSR, meta, structured data) | `audit.sh` · `crawl.py` | unattended | — |
| SEO | Effective robots.txt policy | `audit.sh` | unattended | — |
| SEO | sitemap presence and validity | `audit.sh` · `crawl.py` | unattended | — |
| SEO | Public staging / mirror exposure | `crawl.py` (`probe_mirrors`) | unattended | — |
| SEO | Google Search Console indexed count | — | **sign-in** | account access |
| SEO | Bing WMT indexed count | — | **sign-in** | account access |
| **AEO** | Google AI Overviews firing and citations | `collect.py` | unattended | — |
| AEO | Bing Copilot | `measure.py` form | sign-in | browser |
| **GEO** | ChatGPT | `collect.py` → browser | **sign-in** | sign in only |
| GEO | Perplexity | `collect.py` → browser | **sign-in** | sign in only |
| GEO | Gemini | `collect.py` → browser | **sign-in** | sign in only |
| GEO | Claude | `collect.py` → browser | **sign-in** | sign in only |
| **LLMO** | Entity consistency (name, phone, address) | `audit.sh` + manual | semi-auto | supply the channel list |
| LLMO | Third-party company-database accuracy | manual | manual | request corrections |
| LLMO | Training-data recall (query with search off) | `collect.py` → browser | **sign-in** | sign in only |
| **NEO** | Naver organic search (desktop, mobile) | `collect.py` | unattended | — |
| NEO | Naver AI Briefing | `collect.py` | unattended | — |
| NEO | Search Advisor index and query data | — | **sign-in** | account access |
| NEO | Naver Place NAP | manual | manual | check and correct |
| **KEO** | Daum web search (Site, Unified Web) | `collect.py` | unattended | — |
| KEO | Daum crawler tokens (`DAUM`, `Daumoa`) | `crawl.py` | unattended | — |
| KEO | Daum AI Summary | `collect.py` | unattended | — |
| KEO | Daum Webmaster Tools | — | **PIN** | issue it, renew every 24 months |
| KEO | Kakao Map NAP | manual | manual | check and correct |
| **Reputation** | Reviews, ratings, recruiting platforms | manual | manual | `reputation.md` |

---

## There are only three access types

| Type | Meaning | How the result is handled |
|---|---|---|
| **Unattended** | The script runs on its own | counted as-is |
| **Via browser** | A human **signs in only**; the agent drives from there | counted as-is |
| **Manual** | A human must look and judge | `unmeasured` until filled in |

**"Requires sign-in" is not "manual."** Given a signed-in browser, the agent handles the query,
the verdict and the source extraction. When a tool hits a wall it must not stop — it must
**say what to sign into and hand the work back.**

---

## Before reporting — these three are different, so write them differently

| State | Notation | Meaning |
|---|---|---|
| `observed` | `3/10` | measured, and this is the result |
| `unmeasured` | `not measured` | could not measure. **This is not zero** |
| not applicable | `—` | this target has no such surface (no physical location → no map NAP) |

The `outcome` field in `log.jsonl` carries these three. **Only `observed` goes in the
denominator.** The moment you count something unmeasured as zero, that zero becomes permanent fact.

---

## How a via-browser measurement completes the loop

1. `collect.py <audit.json> --browser` — reserves the blocked engines as `unmeasured` and prints
   **what to sign into.** It does not stop here.
2. The human signs in. Nothing else. Credentials never go into the tool.
3. The agent drives that browser: query, verdict, source extraction
   (neutral modes are in `measure-playbook.md`).
4. `collect.py <audit.json> --record <engine> --query <id> --cited <url> ...` writes the result
   back into the same `log.jsonl` as `observed`. The reservation row is overwritten by run key.
5. `measure.py report <audit.json>` aggregates it **in one table** with the unattended results.

Without a way back in, that cell stays `unmeasured` forever. **Do not skip step 4.**

---

## Checking for gaps

```bash
python tools/collect.py out/<host>/audit.json --coverage
```

It prints `observed / unmeasured` per surface and ends with **the count and list of blanks**.
Every blank comes with a reason — sign-in needed, throttled, or a human has to look.
The code-side source of truth is the `SURFACES` constant in `tools/collect.py`; when the two
disagree, **this document wins.**

Always ship **the list of unmeasured surfaces alongside the observed values.** A report that hides
what it skipped puts "zero citations" and "never looked" in the same cell.
