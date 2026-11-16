"""Offline, evidence-backed customer questions. No scraping or model calls."""
import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import measure
from rebuild_evidence import SCHEMA, public_url, validate_brief

PROMPTS = {
    'ko': {
        'find': '{situation} {need} 조건에 맞는 곳은 어디인가요?',
        'compare': '{situation} {need} 업체별 비용과 이용 조건은 어떻게 비교하나요?',
        'check': '{situation} {need} 이용 전에 {check} 관련해서 무엇을 확인해야 하나요?',
    },
    'en': {
        'find': '{situation} Where can I find {need}?',
        'compare': '{situation} How can I compare prices and terms for {need}?',
        'check': '{situation} For {need}, what should I check about {check} before booking?',
    },
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def required(row, key):
    if not isinstance(row, dict):
        raise ValueError('expected an object for ' + key)
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key} must be nonempty text')
    return value.strip()


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def official(url, site):
    return public_url(url) and urlsplit(url).hostname == urlsplit(site).hostname


def evidence_for(p, site, folder):
    if p.get('kind') != 'product' or p.get('status') != 'active':
        raise ValueError('not an active product')
    if p.get('verified') is not True or not official(p.get('source_url'), site):
        raise ValueError('official source and human verification required')
    checked = date.fromisoformat(required(p, 'checked_at'))
    if checked > date.today():
        raise ValueError('source check date is in the future')
    relative = Path(required(p, 'snapshot'))
    if relative.anchor or '..' in relative.parts:
        raise ValueError('snapshot must be relative to catalog directory')
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise ValueError('local official text snapshot required')
    snapshot = path.read_text(encoding='utf-8-sig')
    facts = {k: required(p, k) for k in ('product_quote', 'strength_quote', 'scope_quote')}
    if any(value not in snapshot for value in facts.values()):
        raise ValueError('product, strength and scope must match the official snapshot')
    return dict(facts, source_url=p['source_url'], checked_at=checked.isoformat(),
                snapshot=p['snapshot'], snapshot_sha256=hashlib.sha256(snapshot.encode()).hexdigest())


def review_questions(catalog, folder):
    if (not isinstance(catalog, dict) or catalog.get('schema') != 'su-presence/product-catalog/1'
            or not public_url(catalog.get('site_url'))):
        raise ValueError('expected product-catalog/1 and public site_url')
    aliases = catalog['brand_names']
    if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
        raise ValueError('list all brand and proprietary product names in brand_names')
    patterns = PROMPTS[catalog['locale']]
    if (not isinstance(catalog.get('products'), list) or not isinstance(catalog.get('channels'), list)
            or not catalog['channels'] or not all(isinstance(c, str) and c.strip() for c in catalog['channels'])):
        raise ValueError('products list and nonempty channels list required')
    rows, held, seen = [], [], set()
    for p in catalog['products']:
        pid = required(p, 'id')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', pid) or pid in seen:
            raise ValueError(f'invalid or duplicate product ID: {pid}')
        seen.add(pid)
        try:
            evidence = evidence_for(p, catalog['site_url'], folder)
        except (ValueError, KeyError, OSError) as exc:
            held.append({'id': pid, 'reason': str(exc)})
            continue
        relevance = p['relevance']
        if relevance not in ('core', 'supporting'):
            raise ValueError(f'{pid}: classify product relevance')
        terms = {k: required(p, k) for k in ('situation', 'need', 'check')}
        # A supplied verbatim search/CS question replaces the corresponding
        # hypothesis only within its own brand/nonbrand cohort.
        observed = {}
        for item in p.get('wording', []):
            intent = item['intent']
            if intent not in patterns or item['source'] not in ('search', 'cs'):
                raise ValueError(f'{pid}: invalid customer wording source or intent')
            text = required(item, 'text')
            branded = any(normalized(a) in normalized(text) for a in aliases)
            slot = (intent, 'brand' if branded else 'nonbrand')
            if slot in observed:
                raise ValueError(f'{pid}: duplicate wording for {slot}')
            observed[slot] = (text, item['source'], required(item, 'reference'))
        for intent, pattern in patterns.items():
            for cohort in ('nonbrand', 'brand'):
                wording = observed.get((intent, cohort))
                if not wording and cohort == 'brand':
                    continue
                text, origin, ref = wording or (pattern.format(**terms), 'product_hypothesis', None)
                if cohort == 'nonbrand' and any(normalized(a) in normalized(text) for a in aliases):
                    raise ValueError(f'{pid}: remove proprietary names from customer situation/need')
                rows.append(dict(id=f'{pid}.{intent}.{cohort}', text=text, type=cohort, intent=intent,
                                 product_id=pid, relevance=relevance, wording_origin=origin,
                                 wording_reference=ref, evidence=evidence, **terms))
    return rows, held


def signature(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def add_priorities(rows, log, channels):
    """An absent official citation on one comparable run is not general invisibility."""
    by_question = {}
    for obs in log:
        if (obs.get('mode') not in ('manual', 'browser') or obs.get('login_state') != 'signed_out'
                or obs.get('search_enabled') is not True or obs.get('surface') not in channels
                or not measure.citation_known(obs)):
            continue
        key = (obs.get('query_id'), obs.get('query_fingerprint'))
        by_question.setdefault(key, []).append(obs)
    for r in rows:
        comparable = by_question.get((r['id'], measure.query_fingerprint(r)), [])
        misses = [obs for obs in comparable if not obs['cited']]
        r['citation_evidence'] = {
            'known_runs': len(comparable), 'runs_without_official_citation': len(misses),
            'gap_row_keys': [list(measure.row_key(obs)) for obs in misses],
        }
        r['priority'] = 3 - int(r['relevance'] == 'core') - int(bool(misses))


def freeze(catalog, rows, decisions, existing):
    if not isinstance(decisions, list):
        raise ValueError('selection must be a list of reviewed decisions')
    if not isinstance(existing, dict) or existing.get('schema') != measure.SCHEMA_QUERIES or not isinstance(existing.get('queries'), list):
        raise ValueError('expected existing queries/2')
    fixed = list(existing['queries'])
    by_id, by_text = {}, {}
    for q in fixed:
        key, text = required(q, 'id'), normalized(required(q, 'text'))
        if key != q['id'] or key in by_id or text in by_text or q['type'] not in measure.TYPES:
            raise ValueError('invalid or ambiguous existing queries')
        by_id[key], by_text[text] = q, key
    index = {r['id']: r for r in rows}
    brief = dict(schema=SCHEMA, site_url=catalog['site_url'], channels=catalog['channels'],
                 competitors=[], queries=[], pages=[], facts=[])
    used = set()
    selected = set()
    for decision in decisions:
        key = decision['id']
        if key not in index or key in selected:
            raise ValueError(f'unknown or repeated selection: {key}')
        selected.add(key)
        r = index[key]
        if decision['signature'] != signature(r):
            raise ValueError(f'{key}: question or supporting material changed; review again')
        reason = required(decision, 'reason')
        q = {k: r[k] for k in ('id', 'text', 'type')}
        prior = by_id.get(key)
        if prior is not None and any(prior[k] != q[k] for k in q):
            raise ValueError(f'{key}: existing fixed question differs; assign a new ID')
        if by_text.get(normalized(q['text']), key) != key:
            raise ValueError(f'{key}: text already fixed under another ID')
        if prior is None:
            fixed.append(q)
            by_text[normalized(q['text'])] = key
        pid, ev = r['product_id'], r['evidence']
        if pid not in used:
            brief['pages'].append(dict(intent_id=pid, url=ev['source_url']))
            for field in ('product_quote', 'strength_quote', 'scope_quote'):
                brief['facts'].append(dict(id=f'{pid}.{field}', text=ev[field], verified=True,
                                           source_url=ev['source_url'], intent_ids=[pid]))
            used.add(pid)
        brief['queries'].append(dict(q, intent_id=pid, page_type='service', terms=[r['need']],
                                     required_sections=[r['check']], priority=r['priority'],
                                     provenance=dict(r, selection_reason=reason)))
    if not selected:
        raise ValueError('select at least one reviewed question')
    return dict(existing, queries=fixed), validate_brief(brief)


def run(args):
    catalog = read(args.catalog)
    rows, held = review_questions(catalog, Path(args.catalog).resolve().parent)
    if args.log and not Path(args.log).is_file():
        raise ValueError('measurement log not found')
    add_priorities(rows, measure.load_log(args.log) if args.log else [], catalog['channels'])
    rows.sort(key=lambda r: r['priority'])
    decisions = [{'id': r['id'], 'signature': signature(r), 'reason': ''} for r in rows]
    files = {'review.json': dict(candidates=rows, held=held), 'selection-template.json': decisions}
    if args.existing and not args.selection:
        raise ValueError('--existing requires --selection')
    if args.selection:
        existing = read(args.existing) if args.existing else dict(schema=measure.SCHEMA_QUERIES, queries=[])
        files['queries.json'], files['brief.json'] = freeze(catalog, rows, read(args.selection), existing)
    if catalog.get('_synthetic'):
        for name in ('review.json', 'brief.json'):
            if name in files:
                files[name]['_synthetic'] = catalog['_synthetic']
    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=False)
    for name, obj in files.items():
        (folder / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(rows)} candidates, {len(held)} held. Output: {folder}')
    return 0


def add_parsers(sub):
    p = sub.add_parser('questions', help='Derive customer questions from reviewed official text snapshots')
    p.add_argument('catalog')
    p.add_argument('--out', required=True, help='New directory; existing runs are never overwritten')
    p.add_argument('--selection', help='Reviewed subset of selection-template.json with reasons')
    p.add_argument('--existing', help='Append to a copy of an existing queries/2 file')
    p.add_argument('--log', help='This site and campaign only: optional signed-out web measurement log')
    p.set_defaults(func=run)
