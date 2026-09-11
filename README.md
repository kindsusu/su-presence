# su-multi-GEO

![su-multi-GEO — five engines, one audit lens](assets/su-multi-geo.png)

> multi-engine GEO, hand-tuned by **su** ([kindsusu](https://github.com/kindsusu))

<p align="center">
  <a href="README.md"><b>English</b></a> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="https://github.com/kindsusu/su-multi-geo/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/kindsusu/su-multi-geo/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="version 2.0.0" src="https://img.shields.io/badge/version-2.0.0-0E6B5C">
  <img alt="stdlib only, zero dependencies" src="https://img.shields.io/badge/stdlib%20only-zero%20dependencies-1A2B28">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-0E6B5C">
  <a href="LICENSE"><img alt="License PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-A96A00"></a>
  <img alt="Korean-first" src="https://img.shields.io/badge/Korean-first-B3372B">
</p>

**A Korean-first skill for auditing technical search access, drafting site changes, verifying the live result, and measuring AI-search citations.** It keeps SEO, AEO, GEO, LLMO, Naver, and reputation as separate evidence lanes; crawler access, index state, citations, and business outcomes are not treated as the same result.

## Quick start

Choose one installation path. The plugin is convenient for Claude Code; a clone is useful for standalone local runs and development.

### Install the plugin

```text
/plugin marketplace add kindsusu/su-multi-geo
/plugin install su-multi-geo@su-multi-geo
```

Run commands from the installed skill directory, not from the project being audited:

```bash
python <skill-root>/tools/seo_geo.py doctor
python <skill-root>/tools/seo_geo.py audit https://example.com --out <project>/out
```

### Clone and run locally

```bash
git clone https://github.com/kindsusu/su-multi-geo.git
cd su-multi-geo
python tools/seo_geo.py doctor
python tools/seo_geo.py audit https://example.com --out out
```

The audit writes `out/<host>/audit.json` and `report.html`. It honours applicable `robots.txt`, records crawl coverage, and returns exit code `2` when collection is incomplete. Read an incomplete audit as partial evidence, not a complete site inventory.

## The practical loop

![Pipeline: audit, baseline, build, deploy, verify, re-measure, and compare](assets/pipeline.svg)

### Before changing the site: record a baseline

`compare` needs at least two dated snapshots. Capture the first audit and citation cohort **before** generating or deploying changes. Initialise the query set before making a form, then import the filled form and create its report before snapshotting it.

```bash
# 1. Audit and record the pre-change citation cohort
python tools/seo_geo.py audit https://example.com --out out
python tools/seo_geo.py measure init out/example.com/audit.json
python tools/seo_geo.py measure form out/example.com/audit.json \
  --engines chatgpt,google_aio --runs 5
python tools/seo_geo.py measure import out/example.com/audit.json \
  out/example.com/measure/form-YYYY-MM-DD-filled.csv
python tools/seo_geo.py measure report out/example.com/audit.json
python tools/seo_geo.py drift snapshot out/example.com/audit.json \
  --measure out/example.com/measure/summary.json --baseline
```

### Generate, deploy, and verify

Copy [`templates/site.example.json`](templates/site.example.json) to `out/<host>/site.json` and enter only confirmed company facts before generating drafts. Generated files are drafts; a human reviews them and deploys only to an authorized environment.

```bash
python tools/seo_geo.py generate all out/example.com/audit.json \
  --site out/example.com/site.json

# After deployment, check the values the live server actually returns.
python tools/seo_geo.py verify deploy out/example.com/audit.json
```

`verify deploy` checks what the live server returns; matching a JSON-LD `@type` alone is not enough, and structured-data values are also compared with visible text where applicable.

### After the change: re-measure, take a second snapshot, and compare

On a later date, use the same query cohort and conditions. Re-audit, import the completed second form, report it, then take the second snapshot. Only then does `compare` have before/after evidence to compare.

```bash
python tools/seo_geo.py audit https://example.com --out out
python tools/seo_geo.py measure form out/example.com/audit.json \
  --engines chatgpt,google_aio --runs 5
python tools/seo_geo.py measure import out/example.com/audit.json \
  out/example.com/measure/form-YYYY-MM-DD-filled.csv
python tools/seo_geo.py measure report out/example.com/audit.json
python tools/seo_geo.py drift snapshot out/example.com/audit.json \
  --measure out/example.com/measure/summary.json
python tools/seo_geo.py drift compare out/example.com/audit.json
```

`noindex` is a finding to investigate, not proof that every page or result is invalid. Intentional non-indexable paths can be declared with `--allow-noindex`; the audit records what it observed. `status` only reports locally recorded artifacts and never proves a current deployment, index, citation, or scheduled run.

Replace `YYYY-MM-DD` with the actual form date. Fill the generated CSV with observed results,
save it as `-filled.csv`, or pass the actual saved filename to `import`.

## What each artifact means

| Stage | Main artifacts | What they establish |
|---|---|---|
| `audit` | `audit.json`, `report.html` | Raw HTTP observations and the actual collection scope |
| `generate` | `deploy/`, `DEPLOY.md` | Reviewable deployment drafts, never a completed deployment |
| `verify deploy` | `verify.json`, `VERIFY.md` | Live-response checks after deployment; exit `1` is a verified failure and `2` is incomplete or invalid scope |
| `measure` | manual form, `log.jsonl`, `summary.json`, `MEASURE.md` | Repeated citation observations for a fixed cohort; API and web UI are separate surfaces |
| `collect.py` | rows in `measure/log.jsonl` | **Gap-free collection across every surface.** Unattended for Naver / Daum / Google AI Overviews; `--browser` reserves the login-walled engines as `unmeasured` and prints what to sign into; `--record` takes the browser result back into the same log; `--coverage` names the blanks. A truncated response is recorded as `unmeasured`, never as a zero |
| `drift` | immutable `history/`, `drift.json`, `DRIFT.md` | Compared snapshots and a due date; `next_due` does not schedule work |

The plugin metadata version is **2.0.0**. Reliability and workflow changes on `main` are documented under [Unreleased in the changelog](CHANGELOG.md); this does not imply a published GitHub release or a version bump.

## Read next

- [Full tool guide](tools/README.md) — commands, schemas, exit codes, and generated files
- [Operating procedure](SKILL.md) — choosing a lane and using the skill from an installed location
- [Evidence boundaries](ops/evidence.md) — what a crawler audit can and cannot prove
- [Per-engine measurement recipes](ops/measure-playbook.md) — how to actually reach each engine, and the traps
- [Documentation index](docs/README.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Scope and limits

- Python 3.10+ standard library only; `tools/audit.sh` additionally needs bash and curl.
- The audit reads raw HTTP HTML. It does not prove JavaScript rendering, vendor index state, rankings, or citations.
- Citation is observed through a fixed, repeated query cohort. It is never guaranteed by technical readiness; errors and unmeasured rows are kept separate from non-citations.
- **Measurement coverage is written next to the number.** Full sweep (`0/7`), representative query (`0 hits`) and not-measured are distinct; a partial sweep is never written as a full one.
- **A `0` may be your own tooling.** Bursts of repeated requests make search engines return truncated pages. A response that fails the sanity gate is recorded as a failed measurement, not as zero.
- Engines that require sign-in (ChatGPT, Gemini, Claude) are measured in each service's **temporary / incognito mode**. Anything the model drew from the account profile is not a web citation and is excluded from the tally.
- Naver (NEO) and Daum/Kakao (KEO) are **separate first-class lanes** — different crawler tokens, ownership proof and AI models. Citation measurement for both is automated in `tools/collect.py`. Registration, third-party reputation, and production deployment still require human decisions and access.
- The bounded crawl defaults to 300 pages. If coverage is incomplete, the generator will not produce a replacement sitemap.

Run the local test suite with:

```bash
python -m unittest discover tests
```

## License

**PolyForm Noncommercial 1.0.0** — see [LICENSE](LICENSE).

- **Free for personal, nonprofit, educational, and research use**
- **Commercial or corporate use is not permitted** under this license — for a separate commercial license, contact **scitusu@gmail.com**
