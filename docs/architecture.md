# Runtime architecture

su-presence runs when a person or external orchestrator invokes a CLI command. It has no resident daemon or built-in scheduler. `rebuild run` chains its own stages and saves state for resume; publishing and deployment remain separate. [한국어](architecture.ko.md) · [CLI reference](../tools/README.md) · [Automation guide](plans/rebuild-automation-guide.md)

![Current runtime architecture: connected rebuild, technical drafts, measurement and human handoffs](../assets/runtime-architecture.svg)

[Open the interactive architecture diagram](diagrams/runtime-architecture.html). Both diagrams use English labels; the stage boundaries are explained below.

```text
rebuild run → brief draft → audit → configured Brave search/import → public-page research
            → structural analysis (optional OpenAI/import) → page plan
            all stages recorded in run.json
rebuild status / resume → inspect or continue the saved run
legacy: audit (crawl + report) ──┬──> generate technical deployment drafts
                                 └──> rebuild init → human brief → research → plan
measure init → collect / manual form / optional API auto → report
human deployment → verify → repeat audit/measurement → drift snapshot/compare
```

The technical `generate` branch and editorial `rebuild` branch remain separate. A rebuild plan does not invoke `generate`; neither branch publishes or deploys. `seo_geo.py` dispatches the CLIs. `rebuild_run.py` records stage status, fingerprints and artifact hashes; `rebuild_discovery.py` validates search candidates; `rebuild_evidence.py` fetches bounded public pages; `rebuild_analysis.py` compares evidence within the configured scope; `rebuild_plan.py` drafts page decisions. The default analysis is structural; optional OpenAI or validated import can add source-linked findings for an intent. Neither path establishes factual truth. `crawl.py` writes a scoped `audit.json` and `report.py` renders it. `generate.py` drafts technical files, `verify.py` checks deployed output, `measure.py` and `collect.py` handle actual observations, and `drift.py` compares snapshots without scheduling. The legacy `audit.sh` checks only the home page.

| Contract | Producer → consumer | Interpretation |
|---|---|---|
| `out/<host>/audit.json` (`su-presence/audit/1`) | audit → report, generate, verify, measure, rebuild, drift | Records crawl coverage; incomplete coverage cannot prove a page is missing |
| `out/<host>/deploy/.su-presence-generated.json` | generate → human deployment review | Ownership of technical draft files |
| `out/<host>/measure/queries.json` (`su-presence/queries/2`; /1 accepted) | measure init, then human → collection and planning | Questions must be reviewed and completed |
| `out/<host>/measure/log.jsonl` (`su-presence/measure-row/2`) | collect, manual import, optional API auto → report, optional rebuild | Append-only observations; UI and API conditions remain distinct cohorts |
| `out/<host>/measure/summary.json` (`su-presence/measure/2`) | measure report → drift | Only observed runs enter citation-rate denominators |
| `out/<host>/rebuild/brief.json` (`su-presence/rebuild-brief/1`) | rebuild init, then human → research and plan | Verified facts, intent mapping, channels, declared competitors |
| `out/<host>/rebuild/observations.json` (`su-presence/rebuild-observations/1`) | init, then human/external research → research and plan | Query, channel, timestamp, observed URL, evidence URL; declaration is not observed visibility |
| `out/<host>/rebuild/run-*/research.json` (`su-presence/rebuild-research/1`) | research → optional plan input | Bounded snapshots of explicitly supplied public URLs |
| `out/<host>/rebuild/run-*/plan.json` (`su-presence/rebuild-plan/1`) | plan → human | Linked evidence, page blueprints, channel actions, backlog; sibling `guide.md`, `report.html`, `pages/*.json|md|html` |
| `out/<host>/rebuild/run-*/run.json` (`su-presence/rebuild-run/1`) | run → status/resume | Saved stage state, input fingerprints and output hashes; not a daemon |
| `discovery.json` (`su-presence/rebuild-discovery/1`) | configured Brave Search or host import → research | Candidate URLs with query, provider, locale, timestamp and tentative role; not AI citations |
| `analysis.json` (`su-presence/rebuild-analysis/1`) | structural analysis, optional OpenAI/import → plan | Intent-scoped comparison; source-linked findings only when supported by research excerpts, otherwise structural limits remain explicit |
| `out/<host>/history/index.json` (`su-presence/history/1`), `drift.json` (`su-presence/drift/1`) | drift → human | Baseline, comparisons, and computed remeasurement date |

`rebuild research` and `rebuild plan` create separate new `run-*` directories. `plan` works without research but marks its coverage unconfirmed. Missing observations remain `unmeasured`, never zero visibility. An incomplete audit or blocked research page cannot establish an absent page. Page copy and technical files are drafts requiring factual and editorial review.

`collect.py AUDIT` runs unattended collection for Naver AI Briefing, Daum, and Google AI Overview when invoked. `--browser` records `unmeasured` reservations and prints guidance for ChatGPT, Gemini, Claude, and Perplexity; it does not operate a browser. `--record ENGINE --query ID ...` stores an actual browser observation, while `--coverage` checks gaps. Optional `measure.py auto` uses OpenAI or Anthropic APIs with available keys and counts citations in API responses. Web UI and API results, login conditions, and search modes must remain distinct.

In the separate `rebuild init/research/plan` path, `research` fetches supplied external pages; it does not discover search results. In the connected `rebuild run`, discovery uses a configured Brave Search API or validated host import, then research fetches selected public candidates. With no provider, discovery records `needs_input`; the run can still produce a limited plan from available site evidence. An incomplete audit or failed fetch can leave stages `partial`; failed stages and their dependent stages are marked in `run.json`. The overall status and exit code report incomplete work even if a draft plan exists. Public-page research applies DNS, redirect, robots, response-size, time, and page-count bounds. Search hits are candidates, not actual AI citations or rankings on other engines. `rebuild plan` and `drift compare` are offline. The runner never calls Aside. `rebuild status` reads saved run state; top-level `seo_geo.py status` reads the existing site artifacts and does not verify current deployment or search performance.

| Command-driven automation | Human or external input |
|---|---|
| Invoked commands run audits, reports, technical drafts, specified collection, and evidence comparisons | Start the commands, choose targets, and decide whether to deploy drafts |
| `rebuild init` creates editable files; `research` fetches explicit URLs | Supply question intents, confirmed business facts, observed source URLs, timestamps, and evidence |
| `rebuild run` chains brief, audit, configured discovery, research, structural or optional provider analysis, and plan; `resume` reuses valid outputs | Configure a search provider or import, review facts and page decisions; perform implementation and publishing separately |
| `--browser` reserves UI slots; `--record` saves a supplied result | Log in, observe the browser result, and record it or connect an external orchestrator |
| `drift` compares saved observations and computes `next_due` | Schedule and run the next measurement |

Rebuild output paths above assume `--out out/<host>/rebuild`. Research requires `--out`; plan defaults to the rebuild directory beside the audit when omitted.
