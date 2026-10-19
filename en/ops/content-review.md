# Review completed copy and repeat measurements

After `rebuild run` or page planning, write and review the actual page copy using this sequence:

```text
Page design -> Editorial copy -> Offline review -> Human approval -> Publish -> Repeated observations
                                  |                                       |
                          Supplied profile comparison             Same measurement conditions
```

## Review page copy and supplied profiles

```sh
python tools/seo_geo.py rebuild review templates/rebuild/content-review.json --out out/content-review
```

The input fixture contains synthetic data. Put completed text or Markdown in `pages[].body` and connect each factual claim to its source context in `claims`. `source_text` is material you personally collected and supplied; the program does not fetch it from the URL. Do not invent statistics or testimonials. Keep unavailable evidence as an editorial follow-up.

The offline review flags draft placeholders, literal matches between supplied claim text and body, exact quote presence in supplied source text, date format, and differences in the explicitly supplied profile fields. A match does not verify truth or meaning. A person must check whether the copy directly answers the question, whether sources are authoritative and current, and whether a comparison is fair. The tool does not require a fixed number of FAQs, statistics, or quotations.

`identity` contains verified reference values; `profiles` contains external profile values you collected yourself. Missing values are unknown, and differing values are review prompts. Synonyms or translations are not automatically treated as errors. Each invocation writes JSON, Markdown, and HTML reports to a new run directory; it does not edit external profiles or publish content.

## Repeat measurement protocol

```sh
python tools/seo_geo.py rebuild protocol templates/rebuild/brief.json --repeats 3 --out out/protocol
```

Classify each query's `type` as `brand` or `nonbrand`. Brand queries are excluded from the nonbrand protocol; review unclassified queries before treating the coverage as complete. The generated worksheet has no answers: pending slots are not observations. Use the existing `measure` collection/import/report workflow for actual observations and preserve their conditions. The protocol itself is not a measurement log and cannot be imported as one.

For a baseline and follow-up, keep the query, product, model, search mode, locale, language, and session conditions the same, and record the measurement time. Treat changed conditions as a separate comparison group. Record brand mention separately from an actual first-party URL citation. Do not report blocked or failed observations as non-citations. Three repetitions are exploratory; they do not estimate a stable probability of visibility.

Page plans also contain a `measurement_protocol` worksheet. Automation ends at generating review material and a measurement plan. Fact and editorial approval, publication, and actual observation in authenticated product UIs require separate work. Aside is optional only when an authenticated product session is needed; these commands do not invoke it.
