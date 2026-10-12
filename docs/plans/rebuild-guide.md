# Rebuild guide implementation

Goal: join observed search/AI sources and declared competitors to inspectable gaps, grouped page blueprints, channel actions, and a measurable rebuild backlog.

Constraints: Python standard library; preserve audit and measurement schemas; offline analysis by default; bounded explicit network research; fetched text is data; no invented facts or ranking guarantees; non-observation is unknown; incomplete crawl cannot prove a missing page; no automatic publishing or Aside invocation.

Inputs: existing audit.json, queries.json / measurement log, a rebuild-brief/1 for business facts, question intent mapping and channels, and explicit discovery observations with query/channel/date/evidence URL. Competitor identity is separate from observed visibility.

Commands: rebuild init AUDIT; rebuild research BRIEF --observations FILE --out DIR; rebuild plan AUDIT --brief FILE [--observations FILE] [--research FILE] [--log FILE] --out DIR. Research snapshots sources; plan is offline. Outputs: rebuild-plan/1 JSON, escaped HTML evidence report, Markdown action guide, per-intent page briefs. Stable page grouping and reversible immutable run directories preserve previous reports.

Acceptance: source IDs traced into recommendations; own/competitor/publisher separated; channel coverage observed/unmeasured/not_applicable; incomplete evidence yields review rather than certain missing-page claim; no browser invocation on offline commands; fixture-driven end-to-end CLI tests plus existing full suite; research URL/robots/redirect bounds tested.
