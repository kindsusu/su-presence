# Runtime architecture

su-presence runs when a person or external orchestrator invokes a CLI command. It has no resident daemon or built-in scheduler. Each stage leaves reviewable local files; publishing and deployment are separate actions. [한국어](architecture.ko.md) · [CLI reference](../tools/README.md)

```text
audit (crawl + report) ──┬──> generate technical deployment drafts
                         └──> rebuild init → human brief + observations
                                               → optional explicit-URL research
                                               → offline rebuild plan
measure init → collect / manual form / optional API auto → report
human deployment → verify → repeat audit/measurement → drift snapshot/compare
```

The technical `generate` branch and editorial `rebuild` branch can follow an audit independently or in parallel. A rebuild plan does not invoke `generate`; neither branch publishes or deploys. `seo_geo.py` dispatches the CLIs. `crawl.py` writes a scoped `audit.json` and `report.py` renders its HTML report. `generate.py` drafts technical files and tracks its own generated files in a deploy manifest. `verify.py` checks deployed output or compares audits. `measure.py` manages questions, a measurement log, and reports; `collect.py` adds search-surface observations. `rebuild.py` coordinates `rebuild_evidence.py` URL research and `rebuild_plan.py` offline evidence comparison. `drift.py` compares saved snapshots and computes `next_due` without scheduling anything. The legacy `audit.sh` checks only the home page.

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
| `out/<host>/history/index.json` (`su-presence/history/1`), `drift.json` (`su-presence/drift/1`) | drift → human | Baseline, comparisons, and computed remeasurement date |

`rebuild research` and `rebuild plan` create separate new `run-*` directories. `plan` works without research but marks its coverage unconfirmed. Missing observations remain `unmeasured`, never zero visibility. An incomplete audit or blocked research page cannot establish an absent page. Page copy and technical files are drafts requiring factual and editorial review.

`collect.py AUDIT` runs unattended collection for Naver AI Briefing, Daum, and Google AI Overview when invoked. `--browser` records `unmeasured` reservations and prints guidance for ChatGPT, Gemini, Claude, and Perplexity; it does not operate a browser. `--record ENGINE --query ID ...` stores an actual browser observation, while `--coverage` checks gaps. Optional `measure.py auto` uses OpenAI or Anthropic APIs with available keys and counts citations in API responses. Web UI and API results, login conditions, and search modes must remain distinct.

Within the rebuild branch, only `research` fetches external pages: it uses URLs supplied by the brief and observations, not search discovery. It applies public HTTP(S), DNS, redirect, robots, response-size, time, and page-count bounds. `rebuild plan` and `drift compare` are offline. `seo_geo.py status` reads local audit, deploy, verify, measure, and drift artifacts only; it does **not** track rebuild runs or verify current deployment or search performance.

| Command-driven automation | Human or external input |
|---|---|
| Invoked commands run audits, reports, technical drafts, specified collection, and evidence comparisons | Start the commands, choose targets, and decide whether to deploy drafts |
| `rebuild init` creates editable files; `research` fetches explicit URLs | Supply question intents, confirmed business facts, observed source URLs, timestamps, and evidence |
| `--browser` reserves UI slots; `--record` saves a supplied result | Log in, observe the browser result, and record it or connect an external orchestrator |
| `drift` compares saved observations and computes `next_due` | Schedule and run the next measurement |

Rebuild output paths above assume `--out out/<host>/rebuild`. Research requires `--out`; plan defaults to the rebuild directory beside the audit when omitted.
