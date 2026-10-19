"""Offline editorial checks for supplied, completed page copy.

This checks literal evidence and surfaces questions for an editor. It never
fetches sources, judges truth, or certifies a page ready to publish.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re

from rebuild_evidence import public_url


INPUT_SCHEMA = 'su-presence/content-review-input/1'
OUTPUT_SCHEMA = 'su-presence/content-review/1'
MAX_FILE = 2_000_000
MAX_PAGES = 100
MAX_CLAIMS = 100
MAX_PROFILES = 100


def _string(value, label, limit, *, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('%s must be %s text up to %d characters' %
                         (label, 'nonempty' if required else 'optional', limit))
    return value


def _url(value, label):
    _string(value, label, 2048)
    if not public_url(value):
        raise ValueError('%s must be a public HTTP URL' % label)
    return value


def _identity_fields(obj, label):
    if not isinstance(obj, dict):
        raise ValueError('%s must be an object' % label)
    return {key: _string(obj[key], label + '.' + key, 300, required=False)
            for key in ('name', 'category', 'audience') if key in obj}


def validate_input(obj):
    if not isinstance(obj, dict) or obj.get('schema') != INPUT_SCHEMA:
        raise ValueError('schema must be ' + INPUT_SCHEMA)
    pages = obj.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError('pages must contain 1..%d objects' % MAX_PAGES)
    seen = set()
    for index, page in enumerate(pages):
        label = 'pages[%d]' % index
        if not isinstance(page, dict):
            raise ValueError(label + ' must be an object')
        ident = _string(page.get('id'), label + '.id', 120)
        if ident in seen:
            raise ValueError('duplicate page id: ' + ident)
        seen.add(ident)
        _url(page.get('url'), label + '.url')
        _string(page.get('intent'), label + '.intent', 500)
        _string(page.get('body'), label + '.body', 100_000)
        claims = page.get('claims')
        if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
            raise ValueError(label + '.claims must be a list of up to %d objects' % MAX_CLAIMS)
        if 'updated_at' in page:
            _string(page['updated_at'], label + '.updated_at', 40, required=False)
        for j, claim in enumerate(claims):
            key = '%s.claims[%d]' % (label, j)
            if not isinstance(claim, dict):
                raise ValueError(key + ' must be an object')
            _string(claim.get('text'), key + '.text', 2000)
            for field, limit in (('source_url', 2048), ('quote', 2000),
                                 ('source_text', 30_000), ('source_date', 40)):
                if field in claim:
                    _string(claim[field], key + '.' + field, limit, required=False)
            if claim.get('source_url'):
                _url(claim['source_url'], key + '.source_url')
    if 'identity' in obj:
        _identity_fields(obj['identity'], 'identity')
    profiles = obj.get('profiles', [])
    if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
        raise ValueError('profiles must be a list of up to %d objects' % MAX_PROFILES)
    for i, profile in enumerate(profiles):
        label = 'profiles[%d]' % i
        _identity_fields(profile, label)
        _url(profile.get('url'), label + '.url')
    return obj


def load_input(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError('content review input exceeds 2 MB')
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE:
            raise ValueError('content review input exceeds 2 MB')
        obj = json.loads(raw.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('content review input must be UTF-8 JSON') from exc
    return validate_input(obj), hashlib.sha256(raw).hexdigest()


def _valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _placeholder(body):
    return bool(re.search(r'\b(?:TODO|TBD|FIXME)\b|lorem ipsum|\{\{[^\n]{1,100}\}\}|'
                          r'\[(?:Editorial copy required|Write a direct answer|Insert the relevant real service|'
                          r'확인\s*필요|작성\s*필요)[^\]\n]*\]|확인\s*필요|작성\s*필요',
                          body, re.IGNORECASE))


def _question_answers(body):
    """Return structural candidates only; answer meaning is editorial judgment."""
    lines = body.splitlines()
    results = []
    for i, line in enumerate(lines):
        m = re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$', line)
        if not m or not re.search(r'[?？]\s*$', m.group(1)):
            continue
        answer = ''
        for following in lines[i + 1:]:
            if re.match(r'^\s{0,3}#{1,6}\s+', following):
                break
            cleaned = re.sub(r'^[\s>*-]+', '', following).strip()
            if cleaned:
                answer = cleaned
                break
        results.append({'heading': m.group(1)[:300], 'answer_candidate': bool(len(answer) >= 20 and not _placeholder(answer))})
    return results


def _normalized(value):
    return ' '.join(value.split()).casefold()


def review(obj, input_sha256=None):
    validate_input(obj)
    checked = []
    for page in obj['pages']:
        checks, manual, claim_context = [], [], []
        body = page['body']
        if _placeholder(body):
            checks.append({'code': 'placeholder', 'state': 'found', 'detail': 'Editorial placeholder remains in supplied body.'})
        if re.search(r'^\s*[-*]\s*\[ \]', body, re.MULTILINE):
            checks.append({'code': 'unchecked_task', 'state': 'found', 'detail': 'An unchecked task remains in supplied body.'})
        if not page.get('updated_at'):
            checks.append({'code': 'page_date_missing', 'state': 'found',
                           'detail': 'Page updated_at date is missing.'})
        elif not _valid_date(page['updated_at']):
            checks.append({'code': 'page_date_invalid', 'state': 'found',
                           'detail': 'Page updated_at must be a real YYYY-MM-DD date.'})
        manual.append({'code': 'visible_date_author', 'state': 'review',
                       'detail': 'Confirm a visible date and responsible author or organization where appropriate; check current applicability.'})
        question_answers = _question_answers(body)
        for row in question_answers:
            manual.append({'code': 'question_answer', 'state': 'review', 'detail':
                           ('A paragraph follows the question; confirm it answers directly.' if row['answer_candidate']
                            else 'No substantial opening answer candidate follows the question.'),
                           'heading': row['heading']})
        if not question_answers:
            manual.append({'code': 'direct_answer', 'state': 'review',
                           'detail': 'Confirm the opening copy directly answers the stated intent; question headings are optional.'})
        if re.search(r'\b(?:vs\.?|versus|compare|comparison)\b|비교|대안', body, re.IGNORECASE):
            has_limits = bool(re.search(r'제한|단점|부적합|주의|한계|적합하지|limitations?|trade.?offs?|not suitable',
                                        body, re.IGNORECASE))
            manual.append({'code': 'comparison_balance', 'state': 'review',
                           'detail': ('Limitation language is present; verify fit, exclusions and comparable evidence.'
                                      if has_limits else 'Comparison detected; inspect limitations and unsuitable cases.')})
        if not page['claims']:
            manual.append({'code': 'evidence_coverage', 'state': 'review',
                           'detail': 'No claims were supplied; identify factual assertions in the body and check their sources manually.'})
        for j, claim in enumerate(page['claims']):
            label = 'claim-%d' % (j + 1)
            claim_context.append({'id': label, 'text': claim['text'],
                                  'source_url': claim.get('source_url', ''),
                                  'source_date': claim.get('source_date', '')})
            if claim['text'] not in body:
                checks.append({'code': 'claim_absent', 'state': 'found', 'claim': label,
                               'detail': 'Claim text is absent from supplied body.'})
            source_url = claim.get('source_url', '').strip()
            quote = claim.get('quote', '').strip()
            source_text = claim.get('source_text', '')
            if not source_url or not quote or not source_text:
                checks.append({'code': 'claim_evidence_missing', 'state': 'found', 'claim': label,
                               'detail': 'Source URL, exact quote and supplied source text are required for literal matching.'})
            elif quote not in source_text:
                checks.append({'code': 'quote_absent', 'state': 'found', 'claim': label,
                               'detail': 'Exact quote is absent from supplied source text.'})
            else:
                checks.append({'code': 'literal_quote_match', 'state': 'observed_in_input', 'claim': label,
                               'detail': 'Exact quote occurs in supplied source text; source was not fetched or authenticated.'})
            if not claim.get('source_date'):
                checks.append({'code': 'source_date_missing', 'state': 'found', 'claim': label,
                               'detail': 'Source date is missing.'})
            elif not _valid_date(claim['source_date']):
                checks.append({'code': 'source_date_invalid', 'state': 'found', 'claim': label,
                               'detail': 'Source date must be a real YYYY-MM-DD date.'})
            manual.append({'code': 'claim_truth', 'state': 'review', 'claim': label,
                           'detail': 'Check source authority, claim interpretation, applicability and freshness.'})
        checked.append({'id': page['id'], 'url': page['url'], 'intent': page['intent'],
                        'updated_at': page.get('updated_at', ''), 'claims': claim_context,
                        'checks': checks, 'manual_review': manual,
                        'summary': {'detected_issues': sum(row['state'] == 'found' for row in checks),
                                    'editorial_checks': len(manual)}})
    identity_checks = []
    identity = obj.get('identity', {})
    for profile in obj.get('profiles', []):
        for field in ('name', 'category', 'audience'):
            expected, found = identity.get(field, ''), profile.get(field, '')
            if not _normalized(expected) or not _normalized(found):
                state, detail = 'unknown', 'One or both explicit values are missing.'
            elif _normalized(expected) != _normalized(found):
                state, detail = 'review', 'Explicit values differ after whitespace/case normalization.'
            else:
                state, detail = 'literal_match', 'Explicit values match after whitespace/case normalization.'
            identity_checks.append({'profile_url': profile['url'], 'field': field,
                                    'expected': expected, 'supplied': found, 'state': state,
                                    'detail': detail})
    return {'schema': OUTPUT_SCHEMA, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'status': 'human_review_required', 'input_sha256': input_sha256,
            'pages': checked, 'identity_checks': identity_checks,
            'limits': ['Offline literal checks only; no source fetching, fact verification or semantic judgment.',
                       'Question answers, evidence coverage and comparison balance require editorial review.',
                       'No numeric, FAQ, visibility or publication-readiness score is inferred.']}


def markdown(result):
    e = lambda value: html.escape(str(value), quote=True).replace('\n', ' ')
    lines = ['# Completed content review', '', 'Status: human review required', '',
             'Input SHA-256: ' + e(result.get('input_sha256') or 'unknown'), '', '## Limits', '']
    lines += ['- ' + limit for limit in result['limits']]
    for page in result['pages']:
        lines += ['', '## ' + e(page['id']), '',
                  'URL: ' + e(page['url']), 'Intent: ' + e(page['intent']),
                  'Updated at (supplied): ' + e(page['updated_at'] or 'unknown'),
                  '', 'Supplied claims and source context', '']
        lines += ['- %s · %s · source: %s · source date: %s' %
                  (row['id'], e(row['text']), e(row['source_url'] or 'unknown'),
                   e(row['source_date'] or 'unknown')) for row in page['claims']]
        lines += ['', 'Literal/structural checks', '']
        lines += ['- [%s] %s%s: %s' % (item['state'],
                  (item.get('claim', '') + ' · ') if item.get('claim') else '', item['code'], item['detail'])
                  for item in page['checks']]
        lines += ['', 'Editorial review', '']
        lines += ['- %s%s: %s' % ((item.get('claim', '') + ' · ') if item.get('claim') else '',
                  item['code'], item['detail']) for item in page['manual_review']]
    if result['identity_checks']:
        lines += ['', '## Profile identity comparison', '']
        lines += ['- %s · %s · %s: expected %s; supplied %s. %s' %
                  (e(row['profile_url']), row['field'], row['state'],
                   e(row['expected'] or 'unknown'), e(row['supplied'] or 'unknown'), row['detail'])
                  for row in result['identity_checks']]
    return '\n'.join(lines) + '\n'


def render_html(result):
    e = lambda value: html.escape(str(value), quote=True)
    blocks = []
    for page in result['pages']:
        claim_context = ''.join('<li><strong>' + e(row['id']) + '</strong> · ' + e(row['text']) +
                                ' · source: ' + e(row['source_url'] or 'unknown') +
                                ' · source date: ' + e(row['source_date'] or 'unknown') + '</li>'
                                for row in page['claims'])
        checks = ''.join('<li><strong>' + e(row['state']) + ' · ' +
                         (e(row['claim']) + ' · ' if row.get('claim') else '') + e(row['code']) + '</strong>: ' +
                         e(row['detail']) + '</li>' for row in page['checks'])
        manual = ''.join('<li><strong>' + (e(row['claim']) + ' · ' if row.get('claim') else '') +
                         e(row['code']) + '</strong>: ' + e(row['detail']) +
                         (': ' + e(row['heading']) if 'heading' in row else '') + '</li>'
                         for row in page['manual_review'])
        blocks.append('<section><h2>' + e(page['id']) + '</h2><p>' + e(page['url']) + '</p><p>' +
                      e(page['intent']) + '</p><p>Updated at (supplied): ' + e(page['updated_at'] or 'unknown') +
                      '</p><h3>Supplied claims and source context</h3><ul>' + claim_context +
                      '</ul><h3>Literal and structural checks</h3><ul>' + checks +
                      '</ul><h3>Editorial review</h3><ul>' + manual + '</ul></section>')
    identity = ''.join('<li>' + e(row['profile_url']) + ' · ' + e(row['field']) + ' · ' +
                       e(row['state']) + ': expected ' + e(row['expected'] or 'unknown') +
                       '; supplied ' + e(row['supplied'] or 'unknown') + '. ' +
                       e(row['detail']) + '</li>' for row in result['identity_checks'])
    limits = ''.join('<li>' + e(limit) + '</li>' for limit in result['limits'])
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Completed content review</title><style>body{font:16px/1.6 system-ui;max-width:900px;'
            'margin:2rem auto;padding:0 1rem;color:#19342d;background:#f6f8f7}section{background:white;'
            'padding:1.5rem;margin:1rem 0;border:1px solid #d3ded9;border-radius:10px;overflow-wrap:anywhere}'
            'li{margin:.4rem 0}h1,h2{color:#0b6757}</style></head><body><h1>Completed content review</h1>'
            '<p>Human review required. Input SHA-256: ' + e(result.get('input_sha256') or 'unknown') +
            '</p><section><h2>Limits</h2><ul>' + limits + '</ul></section>' + ''.join(blocks) +
            '<section><h2>Profile identity comparison</h2><ul>' + identity + '</ul></section></body></html>')


def command(args):
    import rebuild
    obj, digest = load_input(args.input)
    result = review(obj, digest)
    folder = rebuild.new_run(args.out)
    rebuild.save(folder / 'review.json', result)
    (folder / 'review.md').write_text(markdown(result), encoding='utf-8')
    (folder / 'review.html').write_text(render_html(result), encoding='utf-8')
    print('Review: %s\nReport: %s' % (folder / 'review.json', folder / 'review.html'))
    return 0


def add_parsers(sub):
    p = sub.add_parser('review', help='Offline checks for completed page copy and profile identity')
    p.add_argument('input', help='su-presence/content-review-input/1 JSON file')
    p.add_argument('--out', required=True, help='Parent for immutable review run')
    p.set_defaults(func=command)
