"""Prepare a reproducible, unfilled AI visibility measurement protocol."""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import rebuild_evidence as evidence

SCHEMA = 'su-presence/rebuild-protocol/1'
AI_CHANNELS = ('chatgpt', 'claude', 'gemini', 'perplexity', 'copilot',
               'google_aio', 'naver_ai')
DEFAULT_PROVIDERS = ('chatgpt', 'claude', 'gemini')
MAX_SLOTS = 1000
MAX_BRIEF_BYTES = 2_000_000


def build_protocol(brief: dict, repeats: int = 3) -> dict:
    """Return planned observations only; no result or probability is inferred."""
    brief = evidence.validate_brief(brief)
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('repeats must be 1..20')
    providers = list(dict.fromkeys(c for c in brief['channels'] if c in AI_CHANNELS))
    provider_source = 'brief.channels'
    if not providers:
        providers = list(DEFAULT_PROVIDERS)
        provider_source = 'default; review providers before measurement'
    eligible = [q for q in brief['queries'] if q.get('type') == 'nonbrand']
    needs_review = [{'id': q['id'], 'text': q['text'],
                     'reason': 'query type is unspecified; classify brand or nonbrand first'}
                    for q in brief['queries'] if 'type' not in q]
    excluded = [q['id'] for q in brief['queries'] if q.get('type') == 'brand']
    planned_slots = len(eligible) * len(providers) * repeats
    slots = []
    for query in eligible:
        for provider in providers:
            for repetition in range(1, repeats + 1):
                if len(slots) >= MAX_SLOTS:
                    break
                slots.append({
                    'id': f"{query['id']}:{provider}:{repetition}",
                    'query_id': query['id'], 'query_text': query['text'],
                    'query_sha256': hashlib.sha256(query['text'].encode('utf-8')).hexdigest(),
                    'provider': provider, 'repetition': repetition,
                    'status': 'pending', 'observed_at': None,
                    'conditions': {
                        'product': provider, 'model': None, 'measurement_date': None,
                        'locale': None, 'web_search': None, 'session_mode': 'fresh',
                    },
                    'brand_mentioned': None, 'cited_urls': None,
                    'raw_response_reference': None,
                })
    return {
        'schema': SCHEMA,
        'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'site_url': brief['site_url'], 'repeats': repeats,
        'providers': providers, 'provider_source': provider_source,
        'excluded_brand_query_ids': excluded, 'query_classification_needs_review': needs_review,
        'coverage': {'planned_slots': planned_slots, 'included_slots': len(slots),
                     'omitted_slots': planned_slots - len(slots),
                     'slot_limit_complete': planned_slots == len(slots),
                     'complete': planned_slots == len(slots) and not needs_review},
        'conditions': {
            'product': None, 'model': None, 'measurement_date': None,
            'locale': None, 'web_search': None, 'session_mode': 'fresh',
        },
        'slots': slots,
        'instructions': [
            'Classify every unspecified query before treating this as full nonbrand coverage.',
            'Use a fresh session for every repetition and record product, model, date, locale, and web-search setting.',
            'Use the same query and conditions at baseline and follow-up; record any product/model changes.',
            'Record brand mention separately from actual cited URL; preserve raw response references.',
            'Pending slots are not observations. Small repeated samples are exploratory, not visibility probabilities.',
        ],
    }


def markdown(protocol: dict) -> str:
    def cell(value: object) -> str:
        return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\r', ' ').replace('\n', '<br>')

    lines = ['# AI visibility measurement protocol', '',
             f"Site: {protocol['site_url']}",
             f"Repetitions per query/provider: {protocol['repeats']}",
             f"Providers: {', '.join(protocol['providers'])} ({protocol['provider_source']})", '',
             '## Conditions to record', '',
             'Product, model, measurement date, locale, web-search setting, and session mode.', '',
             '## Procedure', '']
    lines += [f'{i}. {step}' for i, step in enumerate(protocol['instructions'], 1)]
    lines += ['', '## Coverage', '',
              f"Pending slots: {len(protocol['slots'])}",
              f"Omitted slots due to {MAX_SLOTS} slot limit: {protocol['coverage']['omitted_slots']}",
              f"Explicit brand queries excluded: {len(protocol['excluded_brand_query_ids'])}",
              f"Queries needing classification: {len(protocol['query_classification_needs_review'])}", '']
    if protocol['query_classification_needs_review']:
        lines += ['Classify before measuring: ' + ', '.join(
            cell(q['id']) for q in protocol['query_classification_needs_review']), '']
    lines += ['## Measurement worksheet', '',
              '| Query ID | Exact query | SHA-256 | Provider | Repeat | Status | Brand mentioned | Cited URL | Model | Date | Locale | Web search |',
              '| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |']
    for slot in protocol['slots']:
        lines.append('| ' + ' | '.join([
            cell(slot['query_id']), cell(slot['query_text']), cell(slot['query_sha256']),
            cell(slot['provider']), str(slot['repetition']), 'pending', '', '', '', '', '', '',
        ]) + ' |')
    lines.append('')
    return '\n'.join(lines)


def protocol(args) -> int:
    from rebuild import new_run, save

    brief_path = Path(args.brief).resolve()
    with brief_path.open('rb') as source:
        brief_bytes = source.read(MAX_BRIEF_BYTES + 1)
    if len(brief_bytes) > MAX_BRIEF_BYTES:
        raise ValueError(f'brief exceeds {MAX_BRIEF_BYTES} bytes')
    result = build_protocol(json.loads(brief_bytes.decode('utf-8')), args.repeats)
    result['input_provenance'] = {
        'brief_path': str(brief_path),
        'brief_sha256': hashlib.sha256(brief_bytes).hexdigest(),
    }
    folder = new_run(args.out)
    save(folder / 'protocol.json', result)
    (folder / 'protocol.md').write_text(markdown(result), encoding='utf-8')
    print(folder / 'protocol.json')
    return 0


def add_parsers(sub) -> None:
    p = sub.add_parser('protocol', help='Create an unfilled repeatable AI visibility measurement protocol')
    p.add_argument('brief', help='Validated rebuild brief JSON')
    p.add_argument('--out', required=True, help='Parent directory for a new immutable run')
    p.add_argument('--repeats', type=int, default=3)
    p.set_defaults(func=protocol)
