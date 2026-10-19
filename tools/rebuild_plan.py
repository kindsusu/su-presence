#!/usr/bin/env python3
"""Build a conservative, offline page and channel plan from recorded evidence.

Term matches are lexical hints, never a semantic or ranking assessment. Missing
observations remain unknown; this module does not make network requests.
"""

from __future__ import annotations

import re
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from rebuild_evidence import validate_brief
from rebuild_protocol import build_protocol

SCHEMA = "su-presence/rebuild-plan/1"


def _host(url):
    try:
        return (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _owned(url, site):
    """Domain boundary is exact host or a real subdomain, never a suffix trap."""
    host, root = _host(url), _host(site)
    return bool(host and root and (host == root or host.endswith("." + root)))


def _text(value):
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value or "")


def _terms_hit(page, terms):
    haystack = " ".join((_text(page.get("title")), _text(page.get("h1")))).casefold()
    return bool(haystack and any(term.casefold() in haystack for term in terms if len(term.strip()) >= 2))


def _suggested_url(site, intent):
    parts = urlsplit(site)
    slug = re.sub(r"[^a-z0-9]+", "-", intent.lower()).strip("-")
    if not slug:
        slug = "intent-" + "".join(format(ord(char), "x") for char in intent)[:28]
    digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8]
    return urlunsplit((parts.scheme, parts.netloc, "/" + slug + "-" + digest + "/", "", ""))


def _channel_guidance(channel):
    """Actions are proposals, with explicit distinction between owned and external surfaces."""
    key = channel.casefold().replace("-", "_").replace(" ", "_")
    if key in {"google", "bing", "naver", "daum", "search", "seo", "google_aio"}:
        return ("site", ["Check indexability, canonical tags, clear answer sections, and crawlable links on owned pages.",
                         "Submit or inspect eligible URLs in the relevant search console after publication."])
    if key in {"chatgpt", "gemini", "claude", "perplexity", "copilot", "ai", "ai_answers", "naver_ai"}:
        return ("site", ["Publish source-backed, directly answerable facts on accessible owned pages.",
                         "Check AI crawler access and record answer citations by exact query; inclusion is not guaranteed."])
    if key in {"naver_blog", "blog", "open_blog"}:
        return ("external_publisher", ["Prepare a distinct, useful Naver Blog post linking to the relevant owned page where appropriate.",
                                       "Disclose affiliation and verify platform rules before posting."])
    if key in {"community", "forums", "forum"}:
        return ("external_publisher", ["Identify relevant existing discussions and contribute a direct answer with disclosed affiliation.",
                                       "Link only when community rules and context permit it."])
    if key in {"video", "youtube", "shorts"}:
        return ("external_publisher", ["Draft a demonstration or explainer video that answers the intent and cites verified facts.",
                                       "Add a useful description and relevant owned-page link after editorial review."])
    if key in {"social", "instagram", "facebook", "x", "threads", "linkedin"}:
        return ("external_publisher", ["Adapt the verified answer into a platform-native post with a clear source reference.",
                                       "Track resulting referral and discussion signals separately from search ranking."])
    if key in {"maps", "map", "local", "naver_place", "google_maps"}:
        return ("external_publisher", ["Verify business name, address, hours, category, and destination URL in the relevant map profile.",
                                       "Collect authentic customer feedback through ordinary service workflows."])
    if key in {"news", "press"}:
        return ("external_publisher", ["Prepare a factual, newsworthy update with primary sources and a media contact.",
                                       "Pitch selectively; publication is an independent editorial decision."])
    if key in {"wiki", "wikipedia"}:
        return ("external_publisher", ["Check independent reliable sources and relevant notability rules before considering an edit.",
                                       "Disclose conflict of interest and follow the wiki's editing process."])
    return ("review", ["Confirm this channel's format, ownership, and publishing rules before preparing an asset."])


def _fallback_sections(page_type):
    """A structural outline only; no claims or values are supplied."""
    key = page_type.casefold().replace("_", "-").strip()
    outlines = {
        "service": [("Service scope", "Define what the service includes and excludes."),
                    ("Process and eligibility", "Explain steps, prerequisites, timing, and who qualifies."),
                    ("Costs and next step", "Present verified costs or a quote path and a relevant contact action.")],
        "product": [("Product fit", "Explain supported use cases and limitations."),
                    ("Features and evidence", "Describe verified capabilities with supporting sources."),
                    ("Pricing and availability", "State verified terms or direct readers to current terms.")],
        "comparison": [("Comparison criteria", "Define comparable dimensions and selection context."),
                       ("Evidence by option", "Compare source-backed facts without unsupported claims."),
                       ("Tradeoffs", "Explain limitations and when each option fits.")],
        "guide": [("Prerequisites", "State materials, eligibility, and limits before the steps."),
                  ("Steps", "Explain the process in a testable order."),
                  ("Checks and next step", "Describe how to confirm completion and where to get help.")],
        "article": [("Key answer", "Give a direct answer with its source and scope."),
                    ("Evidence and context", "Explain supporting facts and uncertainty."),
                    ("Practical next step", "Offer a relevant action or further reading.")],
        "case-study": [("Starting situation", "Define the client's context with publication permission."),
                       ("Work performed", "Explain the process and constraints."),
                       ("Measured outcome", "Use permitted, source-backed metrics and note limitations.")],
        "local": [("Location and coverage", "State verified address, area, and hours."),
                  ("Local service details", "Explain eligibility, booking, and access."),
                  ("Contact and directions", "Provide verified contact and map details.")],
        "home": [("What is offered", "State the verified offer and target audience."),
                 ("Proof and paths", "Show source-backed trust signals and links to key pages."),
                 ("Next step", "Give a clear contact or product path.")],
    }
    return outlines.get(key, [("Core answer", "Answer the intent with cited, verified facts."),
                              ("Scope and next step", "Clarify limitations and a relevant action.")])


def build_plan(audit, brief, observations, research, measurement_rows=None, discovery=None):
    """Return a reviewable plan; measurement rows are normalized by the caller.

    Every competitive conclusion carries observation or fetched-page evidence.
    A declared competitor URL alone does not prove any search visibility.
    """
    brief = validate_brief(brief)
    if not isinstance(audit, dict) or not isinstance(research, dict) or not isinstance(observations, list):
        raise ValueError("audit, research, and observations must be objects/list")
    if research.get("schema") != "su-presence/rebuild-research/1":
        raise ValueError("invalid research schema")
    if not isinstance(audit.get("pages", []), list) or not isinstance(research.get("pages", []), list):
        raise ValueError("pages must be lists")
    if measurement_rows is not None and not isinstance(measurement_rows, list):
        raise ValueError("measurement_rows must be a list when supplied")
    intent_ids = {q["intent_id"] for q in brief["queries"]}
    for fact in brief["facts"]:
        scopes = fact.get("intent_ids")
        if "intent_ids" in fact and (not isinstance(scopes, list) or not scopes or
                                     any(not isinstance(scope, str) or scope not in intent_ids for scope in scopes)):
            raise ValueError("fact.intent_ids must name one or more known intents")

    site = brief["site_url"]
    queries = brief["queries"]
    by_query = {q["id"]: q for q in queries}
    grouped = defaultdict(list)
    for query in queries:
        grouped[query["intent_id"]].append(query)
    evidence = []
    evidence_ids = set()

    def add_evidence(key, kind, url=None, query_id=None, channel=None, observed_at=None, note="", **extra):
        key = str(key)
        if key not in evidence_ids:
            evidence_ids.add(key)
            evidence.append({"id": key, "kind": kind, "url": url, "query_id": query_id,
                             "channel": channel, "observed_at": observed_at, "note": note, **extra})
        return key

    audit_pages = [p for p in audit.get("pages", []) if isinstance(p, dict) and _owned(p.get("url"), site)]
    fetched = [p for p in research.get("pages", []) if isinstance(p, dict)]
    for query in queries:
        add_evidence("brief-query-" + query["id"], "brief_query", site, query["id"],
                     note="Supplied intent and required sections, not an observed result")
    for i, page in enumerate(audit_pages):
        add_evidence("audit-page-%d" % i, "owned_page", page.get("url"),
                     note="HTTP %s; title/H1 from audit" % page.get("status"))
    for i, page in enumerate(fetched):
        add_evidence("research-page-%d" % i, "fetched_page", page.get("final_url") or page.get("url"),
                     observed_at=page.get("fetched_at"),
                     note="%s; HTTP %s" % (page.get("outcome"), page.get("status")))
    discovered = []
    if discovery is not None:
        if not isinstance(discovery, dict) or discovery.get('schema') != 'su-presence/rebuild-discovery/1':
            raise ValueError('invalid discovery schema')
        for candidate in discovery.get('candidates', []):
            if not isinstance(candidate, dict) or candidate.get('query_id') not in by_query:
                raise ValueError('invalid discovery query')
            if candidate.get('query_text') != by_query[candidate['query_id']]['text']:
                raise ValueError('discovery query text changed')
            eid = add_evidence(candidate['id'], 'search_candidate', candidate['url'], candidate['query_id'],
                               observed_at=candidate['observed_at'],
                               note='Discovery only; not a measured channel citation or competitor declaration.',
                               provider=candidate['provider'], locale=candidate['locale'], position=candidate['position'])
            discovered.append((candidate, eid))
    observed = []
    for i, item in enumerate(observations):
        if not isinstance(item, dict) or item.get("query_id") not in by_query:
            continue
        outcome = item.get("outcome")
        if outcome not in ("observed", "unmeasured", "error"):
            continue
        url = item.get("url") if outcome == "observed" else None
        # A hit without a URL can prove a measurement happened, but not a publisher.
        eid = add_evidence("observation-%s" % (item.get("id") or i), "visibility_observation",
                           url, item["query_id"], item.get("channel"), item.get("observed_at"),
                           "outcome=%s; %s" % (outcome, item.get("note") or ""),
                           evidence_url=item.get("evidence_url"),
                           context={key: item[key] for key in ("mode", "locale", "login_state", "search_enabled",
                                                                  "model", "campaign_id") if key in item})
        observed.append((item, eid))

    for fact in brief["facts"]:
        add_evidence("fact-" + fact["id"], "supplied_fact", fact["source_url"],
                     note="verified=%s; %s" % (fact["verified"], fact["text"]))
    critical = []
    for i, finding in enumerate(audit.get("findings", [])):
        if not isinstance(finding, dict) or finding.get("severity") != "critical":
            continue
        eid = add_evidence("audit-finding-%d" % i, "technical_finding",
                           (finding.get("urls") or [site])[0], note="%s: %s" %
                           (finding.get("code"), finding.get("message")))
        critical.append((finding, eid))

    explicit = defaultdict(list)
    url_intents = defaultdict(set)
    for row in brief.get("pages", []):
        if _owned(row["url"], site):
            explicit[row["intent_id"]].append(row["url"])
            url_intents[row["url"].rstrip("/").lower()].add(row["intent_id"])
    conflicts = {url for url, intents in url_intents.items() if len(intents) > 1}
    # A lexical match may point several intents to one page. Flag this for an
    # editorial merge decision even when the brief had no explicit mapping.
    heuristic_intents = defaultdict(set)
    for intent, items in grouped.items():
        terms = [term for query in items for term in query["terms"]]
        for page in audit_pages:
            if page.get("status") == 200 and _terms_hit(page, terms):
                heuristic_intents[page["url"].rstrip("/").lower()].add(intent)
    conflicts.update(url for url, intents in heuristic_intents.items() if len(intents) > 1)
    complete = (audit.get("coverage") or {}).get("complete") is True
    blueprints = []
    for intent, items in grouped.items():
        qids = [q["id"] for q in items]
        terms = list(dict.fromkeys(term for q in items for term in q["terms"]))
        own_candidates = list(dict.fromkeys(explicit.get(intent, [])))
        heuristic = [p["url"] for p in audit_pages if p.get("status") == 200 and _terms_hit(p, terms)]
        for url in heuristic:
            if url not in own_candidates:
                own_candidates.append(url)
        conflicting = any(url.rstrip("/").lower() in conflicts for url in own_candidates)
        if conflicting or len(own_candidates) > 1 or len({q["page_type"] for q in items}) > 1:
            action = "review"
            rationale = "Several intents or candidate pages overlap; review and merge the mapping before editing."
        elif own_candidates:
            action = "update" if own_candidates[0] in explicit.get(intent, []) else "review"
            rationale = ("Explicit owned-page mapping requires an update review." if action == "update"
                         else "Title/H1 term overlap suggests a candidate; confirm intent fit before editing.")
        elif not terms and not explicit.get(intent):
            action = "review"
            rationale = "No explicit page mapping or matching terms were supplied; inspect owned pages before proposing creation."
        elif complete:
            action = "create"
            rationale = "No owned candidate found in the complete audit; validate the proposed new page."
        else:
            action = "review"
            rationale = "No owned candidate found, but audit coverage is incomplete; inspect existing pages first."

        own_ids = ["audit-page-%d" % i for i, p in enumerate(audit_pages) if p.get("url") in own_candidates]
        obs_ids = [eid for row, eid in observed if row["query_id"] in qids]
        comp_rows = []
        for i, p in enumerate(fetched):
            url = p.get("final_url") or p.get("url")
            if not url or p.get("outcome") != "observed":
                continue
            if _owned(url, site):
                if url in own_candidates or p.get("url") in own_candidates:
                    comp_rows.append({"url": url, "role": "owned", "evidence_ids": ["research-page-%d" % i],
                                      "title": p.get("title") or "", "h1": p.get("h1") or "",
                                      "headings": p.get("headings") or [], "features": p.get("features") or {},
                                      "note": "Observed owned-page structure; content quality requires editorial review."})
                continue
            related_obs = [eid for row, eid in observed if row["query_id"] in qids and
                           row.get("outcome") == "observed" and row.get("url") in (url, p.get("url"))]
            declared = any(_host(url) == _host(c) or _host(url).endswith("." + _host(c)) for c in brief["competitors"])
            related_discovery = [eid for row, eid in discovered if row['query_id'] in qids
                                 and row['url'] in (url, p.get('url'))]
            if not (related_obs or related_discovery or (declared and _terms_hit(p, terms))):
                continue
            comp_rows.append({"url": url, "role": "observed_publisher" if related_obs else "discovered_source" if related_discovery else "declared_competitor",
                              "evidence_ids": ["research-page-%d" % i] + related_obs + related_discovery,
                              "title": p.get("title") or "", "h1": p.get("h1") or "",
                              "headings": p.get("headings") or [], "features": p.get("features") or {},
                              "note": "Observed page structure only; topical or ranking advantage is unverified."})
        owned_rows = [row for row in comp_rows if row["role"] == "owned"]
        external_rows = [row for row in comp_rows if row["role"] != "owned"]
        comparison_gaps = []
        required_headings = list(dict.fromkeys(s for q in items for s in q["required_sections"]))
        for heading in required_headings:
            own_hit = any(heading.casefold() == _text(h).casefold() for row in owned_rows for h in row["headings"])
            state = "unknown" if not owned_rows else "present" if own_hit else "missing"
            supporting = [eid for row in external_rows if any(heading.casefold() == _text(h).casefold()
                           for h in row["headings"]) for eid in row["evidence_ids"]]
            comparison_gaps.append({"kind": "required_heading", "item": heading, "state": state,
                                    "evidence_ids": list(dict.fromkeys(
                                        ["brief-query-" + qid for qid in qids] + own_ids +
                                        [eid for row in owned_rows for eid in row["evidence_ids"]] + supporting)),
                                    "note": "Literal casefold heading comparison only; confirm meaning editorially."})
        for feature in ("table", "faq", "sources"):
            external_with = [row for row in external_rows if row["features"].get(feature) is True]
            if not external_with:
                continue
            own_hit = any(row["features"].get(feature) is True for row in owned_rows)
            known_false = bool(owned_rows) and all(type(row["features"].get(feature)) is bool for row in owned_rows)
            state = "present" if own_hit else "missing" if known_false else "unknown"
            comparison_gaps.append({"kind": "feature", "item": feature, "state": state,
                                    "evidence_ids": list(dict.fromkeys(
                                        [eid for row in owned_rows + external_with for eid in row["evidence_ids"]])),
                                    "note": "Structural signal only; assess usefulness and eligibility before adding."})
        section_proposals = []
        seen_headings = {heading.casefold() for heading in required_headings}
        for row in external_rows:
            for heading in row["headings"]:
                heading = _text(heading).strip()
                if (len(section_proposals) >= 3 or len(heading) < 3 or len(heading) > 100 or
                        heading.casefold() in seen_headings or not _terms_hit({"title": heading}, terms)):
                    continue
                seen_headings.add(heading.casefold())
                section_proposals.append({"heading": heading, "status": "review",
                                          "reason": "Source heading has a literal term overlap; assess relevance and originality.",
                                          "evidence_ids": row["evidence_ids"]})
        source_ids = list(dict.fromkeys(["brief-query-" + qid for qid in qids] + own_ids + obs_ids +
                                       [eid for row in comp_rows for eid in row["evidence_ids"]]))
        scoped = [f for f in brief["facts"] if intent in f.get("intent_ids", [])]
        verified = [f["id"] for f in scoped if f["verified"] is True and f.get("source_url")]
        missing = [f["id"] for f in scoped if f["verified"] is not True]
        relevance_review = [f["id"] for f in brief["facts"] if "intent_ids" not in f]
        source_ids.extend("fact-" + fact_id for fact_id in verified + missing + relevance_review)
        source_ids = list(dict.fromkeys(source_ids))
        sections = []
        for heading in dict.fromkeys(s for q in items for s in q["required_sections"]):
            sections.append({"heading": heading, "purpose": "Answer the intent with cited, verified facts.",
                             "required_fact_ids": verified})
        if not sections:
            sections = [{"heading": heading, "purpose": purpose, "required_fact_ids": verified}
                        for heading, purpose in _fallback_sections(items[0]["page_type"])]
        if not verified:
            missing.append("source-backed facts for this intent")
        priority = min(q.get("priority", 2) for q in items)
        slug = re.sub(r"[^a-z0-9]+", "-", intent.lower()).strip("-") or "intent"
        blueprint_id = "page-" + slug + "-" + hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8]
        draft_title = items[0]["text"].strip()
        page_type = items[0]["page_type"]
        draft_meta = "Learn about %s: key details, evidence, and next steps." % draft_title
        schema_candidates = [{"type": "WebPage", "review": "Confirm actual page type and visible content before markup."}]
        if page_type.casefold() in {"product", "service"}:
            schema_candidates.append({"type": "Product" if page_type.casefold() == "product" else "Service",
                                      "review": "Use only if the visible page and required properties meet current eligibility rules."})
        if any("faq" in s["heading"].casefold() for s in sections):
            schema_candidates.append({"type": "FAQPage", "review": "Check current search eligibility and visible Q&A content; no rich-result promise."})
        blueprints.append({"id": blueprint_id, "intent_id": intent, "query_ids": qids,
                           "action": action, "title": items[0]["text"],
                           "page_type": page_type,
                           "draft_copy": {"status": "editorial_draft", "title": draft_title,
                                          "h1": draft_title, "meta_description": draft_meta},
                           "section_layout": [s["heading"] for s in sections],
                           "cta_guidance": "Offer the next step appropriate to this intent; substantiate any claim and avoid invented offers.",
                           "schema_candidates": schema_candidates,
                           "suggested_url": own_candidates[0] if len(own_candidates) == 1 else _suggested_url(site, intent),
                           "existing_candidates": own_candidates, "rationale": rationale,
                           "evidence_ids": source_ids, "sections": sections,
                           "verified_facts": verified, "missing_facts": list(dict.fromkeys(missing)),
                           "facts_requiring_relevance_review": relevance_review,
                           "internal_links": [p["url"] for p in audit_pages if p.get("status") == 200 and
                                              p.get("url") not in own_candidates][:5],
                           "acceptance_checks": ["Confirm intent and page ownership before publishing.",
                                                 "Review the completed body with rebuild review; resolve placeholders and manually check that each answer addresses its question.",
                                                 "Confirm source dates, author responsibility, and applicable limitations; do not add arbitrary numbers or FAQ quotas.",
                                                 "Compare supplied external profiles with verified brand facts; missing information remains unknown.",
                                                 "Verify every factual claim against its cited source and confirm fact-to-intent relevance.",
                                                 "Check indexability, canonical URL, and crawlable internal links.",
                                                 "Record follow-up observations for each query and channel."],
                           "measurement_queries": [q["text"] for q in items], "priority": priority,
                           "priority_reason": "Highest supplied priority among intent variants; visibility evidence does not establish ranking potential.",
                           "source_comparison": comp_rows, "comparison_gaps": comparison_gaps,
                           "section_proposals": section_proposals})

    channel_actions = []
    for channel in dict.fromkeys(brief["channels"]):
        rows = [(row, eid) for row, eid in observed if row.get("channel") == channel]
        hits = [(row, eid) for row, eid in rows if row.get("outcome") == "observed"]
        status = "observed" if hits else "unmeasured"
        actions = (["Review recorded publisher URLs and compare their observable page structure with owned pages."]
                   if hits else ["Collect query-level observations with URLs and timestamps before judging visibility."])
        actions.append("Measure owned-page appearance after publishing; no ranking outcome is guaranteed.")
        surface, guidance = _channel_guidance(channel)
        actions[1:1] = guidance
        channel_actions.append({"channel": channel, "status": status,
                                "surface": surface, "evidence_ids": [eid for _, eid in rows], "actions": actions})
    backlog = []
    blockers = []
    for i, (finding, eid) in enumerate(critical):
        bid = "technical-%d" % (i + 1)
        blockers.append(bid)
        backlog.append({"id": bid, "priority": 1, "action": "Resolve critical audit finding %s: %s" %
                        (finding.get("code"), finding.get("message")), "depends_on": [],
                        "evidence_ids": [eid], "acceptance_checks": ["Re-audit affected URL and confirm finding is resolved."]})
    for page in blueprints:
        backlog.append({"id": page["id"], "priority": page["priority"],
                        "action": "%s page for intent %s after mapping and fact review" % (page["action"], page["intent_id"]),
                        "depends_on": blockers.copy(), "evidence_ids": page["evidence_ids"],
                        "acceptance_checks": page["acceptance_checks"]})
    limits = ["Lexical title/H1 matching is a review hint, not proof of intent fit or ranking potential.",
              "Unmeasured channels and absent hits do not prove zero visibility.",
              "Declared competitor sites do not establish search visibility.",
              "Historical observations preserve their collection context; they are not current visibility rates.",
              "Page briefs require editorial and source verification before publication."]
    if any("intent_ids" not in fact for fact in brief["facts"]):
        limits.append("Facts without intent_ids require relevance review and are not assigned to page claims.")
    if not complete:
        limits.append("Audit coverage is incomplete; missing owned pages may exist.")
    if (research.get("coverage") or {}).get("complete") is not True:
        limits.append("Research coverage is incomplete or unconfirmed; unavailable pages cannot establish content gaps.")
    if measurement_rows:
        limits.append("Raw measurement rows were not interpreted here; caller must normalize and verify query fingerprints.")
    return {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target": site, "summary": {"intents": len(blueprints), "observations": len(observed),
                                            "critical_blockers": len(blockers), "audit_complete": complete},
            "evidence": evidence, "page_blueprints": blueprints, "channel_actions": channel_actions,
            "backlog": backlog, "limits": limits,
            "measurement_protocol": build_protocol(brief)}
