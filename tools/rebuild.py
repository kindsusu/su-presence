"""Evidence-backed rebuilding workflow. Offline planning; explicit bounded research."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import measure
import rebuild_evidence as evidence
import rebuild_plan as planner

CHANNELS = ['google', 'bing', 'naver', 'daum', 'google_aio', 'naver_ai',
            'chatgpt', 'gemini', 'claude', 'perplexity', 'copilot',
            'naver_blog', 'open_blog', 'community', 'video', 'social', 'maps', 'news', 'wiki']
SCHEMA_OBS = 'su-presence/rebuild-observations/1'


def read_json(path):
    with open(path, encoding='utf-8') as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError('Expected a JSON object: %s' % path)
    return obj


def save(path, value):
    from seo_geo import atomic_json
    atomic_json(Path(path), value)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def audit_input(path):
    obj = read_json(path)
    if obj.get('schema') not in ('su-presence/audit/1', 'su-multi-geo/audit/1'):
        raise ValueError('Unsupported audit schema')
    if not isinstance(obj.get('target'), dict) or not obj['target'].get('base'):
        raise ValueError('Audit target.base required')
    if not evidence.public_url(obj['target']['base']):
        raise ValueError('Audit target must be a public HTTP URL')
    if not isinstance(obj.get('pages'), list) or any(not isinstance(p, dict) for p in obj['pages']):
        raise ValueError('Audit pages must be a list of objects')
    if not isinstance(obj.get('coverage', {}), dict) or not isinstance(obj.get('findings', []), list):
        raise ValueError('Invalid audit coverage/findings')
    return obj


def observations(path, brief):
    if not path:
        return []
    obj = read_json(path)
    if obj.get('schema') != SCHEMA_OBS or not isinstance(obj.get('observations'), list):
        raise ValueError('Expected rebuild-observations/1 with observations list')
    qindex = {q['id']: q for q in brief['queries']}
    ids, result = set(), []
    for row in obj['observations']:
        if not isinstance(row, dict):
            raise ValueError('Observation must be an object')
        r = dict(row)
        for key in ('id', 'query_id', 'channel', 'outcome', 'observed_at'):
            if not isinstance(r.get(key), str) or not r[key].strip():
                raise ValueError('Observation requires %s' % key)
        if r['id'] in ids:
            raise ValueError('Duplicate observation ID: %s' % r['id'])
        ids.add(r['id'])
        if r['query_id'] not in qindex or r['channel'] not in brief['channels']:
            raise ValueError('Observation query/channel not in brief')
        if r.get('query_text') != qindex[r['query_id']]['text']:
            raise ValueError('Observation query_text must match exact current question')
        try:
            parsed = datetime.fromisoformat(r['observed_at'].replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Timezone required')
        except ValueError:
            raise ValueError('Invalid observation timestamp')
        if r['outcome'] not in ('observed', 'unmeasured', 'error'):
            raise ValueError('Unsupported observation outcome')
        if r['outcome'] == 'observed':
            if not all(evidence.public_url(r.get(k, '')) for k in ('url', 'evidence_url')):
                raise ValueError('Observation URLs must be public HTTP URLs')
        if 'position' in r and (type(r['position']) is not int or r['position'] < 1):
            raise ValueError('Position must be a positive integer')
        result.append(r)
    return result


def from_log(path, brief):
    """Import only comparable v2 observations, retaining product and date distinctions."""
    if not path:
        return [], []
    if not Path(path).is_file():
        raise ValueError('Measurement log does not exist')
    qindex = {q['id']: q for q in brief['queries']}
    result, warnings = [], []
    for row in measure.load_log(str(path)):
        q = qindex.get(row.get('query_id'))
        if not q:
            continue
        if (row.get('query_fingerprint') != measure.query_fingerprint(q) or
                row.get('outcome') != 'observed'):
            warnings.append('Excluded incompatible or unobserved log row for %s' % q['id'])
            continue
        if row.get('mode') == 'api' or row.get('search_enabled') is False:
            warnings.append('Excluded API/search-off row from web visibility: %s' % q['id'])
            continue
        channel = row.get('surface') or row.get('engine')
        if channel not in brief['channels']:
            warnings.append('Unselected/unknown measurement surface: %s' % channel)
            continue
        for url in row.get('cited_urls', []):
            if not evidence.public_url(url):
                warnings.append('Excluded invalid citation URL')
                continue
            digest = hashlib.sha256(json.dumps([measure.row_key(row), url], default=str).encode()).hexdigest()[:16]
            result.append(dict(id='log-' + digest, query_id=q['id'], query_text=q['text'],
                channel=channel, url=url, outcome='observed', observed_at=row.get('recorded_at') or row['date'],
                evidence_url=url, note='Imported citation; original row in measurement log; no SERP rank inferred',
                mode=row.get('mode'), locale=row.get('locale'), login_state=row.get('login_state'),
                search_enabled=row.get('search_enabled'), model=row.get('model'), campaign_id=row.get('campaign_id')))
        if row.get('competitor_domains'):
            warnings.append('Domain-only competitor mentions are not exact observed pages: %s' % q['id'])
    return result, sorted(set(warnings))


def load_research(path):
    if not path:
        return {'schema': 'su-presence/rebuild-research/1', 'pages': [], 'coverage': {'complete': False}}
    obj = read_json(path)
    if obj.get('schema') != 'su-presence/rebuild-research/1' or not isinstance(obj.get('pages'), list):
        raise ValueError('Unsupported research artifact')
    if not isinstance(obj.get('coverage', {}), dict):
        raise ValueError('Research coverage must be an object')
    for page in obj['pages']:
        if not isinstance(page, dict) or page.get('outcome') not in ('observed', 'blocked', 'error'):
            raise ValueError('Invalid researched page')
        if not evidence.public_url(page.get('url', '')) or (page.get('final_url') and not evidence.public_url(page['final_url'])):
            raise ValueError('Research URLs must be public HTTP URLs')
        if not isinstance(page.get('features', {}), dict) or any(type(v) is not bool for v in page.get('features', {}).values()):
            raise ValueError('Research features must be boolean fields')
        if not isinstance(page.get('headings', []), list) or any(not isinstance(h, str) for h in page.get('headings', [])):
            raise ValueError('Research headings must be a string list')
        if not isinstance(page.get('h1', ''), (str, list)) or (isinstance(page.get('h1'), list) and any(not isinstance(h, str) for h in page['h1'])):
            raise ValueError('Research h1 must be text or a string list')
        for key in ('title', 'text_excerpt'):
            if page.get(key) is not None and not isinstance(page[key], str):
                raise ValueError('Invalid researched page text')
    return obj


def safe_name(value):
    stem = re.sub(r'[^a-zA-Z0-9_-]', '-', str(value)).strip('-')[:60] or 'page'
    return stem + '-' + hashlib.sha256(str(value).encode()).hexdigest()[:8]


def markdown(plan):
    lines = ['# Rebuilding guide', '', 'Generated: ' + plan['generated_at'], '',
             'Recommendations are hypotheses, not ranking guarantees. Validate facts before publishing.', '',
             '## Limits', '']
    lines += ['- ' + str(x).replace('\n', ' ') for x in plan.get('limits', [])]
    for b in plan['page_blueprints']:
        lines += ['', '## ' + b['title'], '', 'Action: ' + b['action'],
                  '', 'Proposed URL: ' + b['suggested_url'], '', 'Reason: ' + b['rationale'],
                  '', 'Evidence IDs: ' + ', '.join(b['evidence_ids']), '', '### Sections', '']
        for section in b['sections']:
            lines += ['- ' + section['heading'] + ': ' + section['purpose']]
        lines += ['', '### Required evidence / acceptance', '']
        lines += ['- ' + str(x) for x in b['missing_facts'] + b['acceptance_checks']]
    lines += ['', '## Channel actions', '']
    for c in plan['channel_actions']:
        lines += ['### ' + c['channel'] + ' — ' + c['status'], ''] + ['- ' + a for a in c['actions']] + ['']
    lines += ['## Evidence register', '']
    for e in plan['evidence']:
        lines += ['- %s: %s (%s)' % (e['id'], e.get('url', ''), e.get('kind', ''))]
    return '\n'.join(lines) + '\n'


def render_html(plan):
    """Readable static report; untrusted text is escaped and never executed."""
    def esc(x): return html.escape(str(x), quote=True)
    def listing(items): return '<ul>' + ''.join('<li>' + esc(x) + '</li>' for x in items) + '</ul>'
    cards = []
    for b in plan['page_blueprints']:
        rows = ''.join('<tr><td>' + esc(g['item']) + '</td><td>' + esc(g['state']) + '</td><td>' +
                       esc(', '.join(g['evidence_ids'])) + '</td></tr>' for g in b.get('comparison_gaps', []))
        sources = ''.join('<li><strong>' + esc(r['role']) + '</strong> · ' + esc(r['title']) + '<br>' + esc(r['url']) + '</li>'
                          for r in b.get('source_comparison', []))
        draft_path = 'pages/' + safe_name(b['id']) + '.html'
        cards.append('<section><p class="tag">P' + esc(b['priority']) + ' · ' + esc(b['action']) + ' · ' + esc(b['page_type']) +
                     '</p><h2>' + esc(b['title']) + '</h2><p>' + esc(b['suggested_url']) + '</p><p>' + esc(b['rationale']) +
                     '</p><a href="' + esc(draft_path) + '">페이지 설계 초안 열기</a><h3>비교한 페이지</h3><ul>' + sources +
                     '</ul><h3>구조 비교</h3><table><thead><tr><th>항목</th><th>자사 상태</th><th>근거</th></tr></thead><tbody>' + rows +
                     '</tbody></table><h3>페이지 구성</h3>' + listing(s['heading'] + ': ' + s['purpose'] for s in b['sections']) +
                     '<h3>발행 전 확인</h3>' + listing(b['missing_facts'] + b['acceptance_checks']) +
                     '<details><summary>전체 청사진 JSON</summary><pre>' + esc(json.dumps(b, ensure_ascii=False, indent=2)) + '</pre></details></section>')
    channels = ''.join('<article><h3>' + esc(c['channel']) + ' · ' + esc(c['status']) + '</h3>' + listing(c['actions']) + '</article>' for c in plan['channel_actions'])
    backlog = listing(task['id'] + ' · ' + task['action'] + ' · 선행: ' + ', '.join(task['depends_on']) for task in plan['backlog'])
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>su-presence rebuild guide</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#18352d;background:#f6f8f7}'
            'section{background:white;padding:24px;margin:20px 0;border:1px solid #d4ded9;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'h1,h2,a{color:#0e6b5c}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid #d4ded9;overflow-wrap:anywhere}'
            '.tag{font-weight:bold}article{border-top:1px solid #d4ded9}li{margin:8px 0}p{overflow-wrap:anywhere}</style></head><body>'
            '<header><p>SU-PRESENCE / REBUILD</p><h1>근거에서 페이지 설계까지</h1><p>' + esc(plan['target']) + ' · ' + esc(plan['generated_at']) +
            '</p><p>의도 ' + esc(plan['summary']['intents']) + '개 · 관측 ' + esc(plan['summary']['observations']) + '개 · 중요 기술 과제 ' +
            esc(plan['summary']['critical_blockers']) + '개</p></header><section><h2>분석 범위와 한계</h2>' + listing(plan['limits']) + '</section>' + ''.join(cards) +
            '<section><h2>채널별 실행</h2>' + channels + '</section><section><h2>구현 순서</h2>' + backlog +
            '</section><section><h2>근거와 입력 이력</h2><pre>' + esc(json.dumps({'evidence':plan['evidence'], 'inputs':plan.get('input_provenance',{})},ensure_ascii=False,indent=2)) +
            '</pre></section></body></html>')


def page_draft(page, plan):
    facts = {e['id']: e for e in plan['evidence'] if e['kind'] == 'supplied_fact'}
    lines = ['# ' + page['title'], '', '> REVIEW DRAFT — factual and editorial approval required.', '',
             'Proposed URL: ' + page['suggested_url'], '', '## Opening answer', '',
             '[Write a direct answer supported by the verified facts below; do not invent claims.]']
    for section in page['sections']:
        lines += ['', '## ' + section['heading'], '', section['purpose'], '', '[Editorial copy required]']
    lines += ['', '## Verified, intent-scoped source material', '']
    for fid in page['verified_facts']:
        fact = facts.get('fact-' + fid, {})
        lines += ['- ' + fact.get('note', fid) + ' — ' + str(fact.get('url', ''))]
    lines += ['', '## Internal links', ''] + ['- ' + u for u in page['internal_links']]
    lines += ['', '## Call to action', '', '[Insert the relevant real service/action after review.]',
              '', '## Publication checks', ''] + ['- [ ] ' + c for c in page['acceptance_checks']]
    return '\n'.join(lines) + '\n'


def page_html(page, plan):
    """Static noindex wireframe, with source text escaped and no executable input."""
    e = lambda value: html.escape(str(value), quote=True)
    facts = {row['id']: row for row in plan['evidence'] if row['kind'] == 'supplied_fact'}
    sections = ''.join('<section><h2>' + e(s['heading']) + '</h2><p>' + e(s['purpose']) +
                       '</p><p class="placeholder">검증된 자료를 바탕으로 본문을 작성하세요.</p></section>' for s in page['sections'])
    sources = ''.join('<li>' + e(facts.get('fact-'+fid, {}).get('note', fid)) + '<br>' +
                      e(facts.get('fact-'+fid, {}).get('url', '')) + '</li>' for fid in page['verified_facts'])
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta name="robots" content="noindex"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>' + e(page['title']) + '</title><style>body{font:17px system-ui;max-width:960px;margin:32px auto;padding:0 24px;color:#18352d}'
            'aside{padding:16px;background:#fff2cc}section{padding:24px 0;border-bottom:1px solid #ccd8d2}.placeholder{padding:24px;background:#f0f5f3}'
            'code{overflow-wrap:anywhere}</style></head><body><aside>검토용 설계 초안 · 발행 전 사실 확인과 본문 작성 필요</aside><main>'
            '<header><p>' + e(page['page_type']) + '</p><h1>' + e(page['draft_copy']['h1']) + '</h1><code>' + e(page['suggested_url']) +
            '</code><p class="placeholder">질문에 대한 직접적인 답변과 확인된 핵심 근거를 작성하세요.</p></header>' + sections +
            '<section><h2>확인된 자료</h2><ul>' + sources + '</ul></section><section><h2>다음 행동</h2><p>' + e(page['cta_guidance']) +
            '</p></section><section><h2>내부 링크 후보</h2><ul>' + ''.join('<li>'+e(u)+'</li>' for u in page['internal_links']) +
            '</ul></section></main></body></html>')


def init(args):
    audit = audit_input(args.audit)
    query_path = Path(args.queries) if args.queries else Path(args.audit).parent / 'measure' / 'queries.json'
    if args.queries and not query_path.is_file():
        raise ValueError('Queries file does not exist')
    queries = read_json(query_path).get('queries') if query_path.exists() else []
    if not isinstance(queries, list) or any(not isinstance(q, dict) or not isinstance(q.get('id'), str) or not isinstance(q.get('text'), str) for q in queries):
        raise ValueError('Queries must contain objects with id and text')
    brief = dict(schema='su-presence/rebuild-brief/1', site_url=audit['target']['base'],
                 competitors=[], channels=CHANNELS, facts=[], pages=[],
                 queries=[dict(q, intent_id=q['id'], terms=[], required_sections=[], page_type='guide', priority=2) for q in queries])
    evidence.validate_brief(brief)
    folder = Path(args.out) if args.out else Path(args.audit).parent / 'rebuild'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'brief.json'
    if path.exists():
        raise ValueError('brief.json already exists; edit it without losing existing work')
    save(path, brief)
    obs = folder / 'observations.json'
    if not obs.exists():
        save(obs, {'schema': SCHEMA_OBS, 'observations': []})
    print('Brief: %s\nFill queries, intent groups, confirmed facts, competitor URLs and observed sources before planning.' % path)
    return 0


def research(args):
    brief = evidence.validate_brief(read_json(args.brief))
    if not brief['queries']:
        raise ValueError('Add at least one query to brief before research/planning')
    obs = observations(args.observations, brief)
    imported, warnings = from_log(args.log, brief)
    obs += imported
    urls = list(dict.fromkeys([r['url'] for r in obs if r['outcome'] == 'observed'] +
                             [p['url'] for p in brief.get('pages', [])] + brief['competitors'] + [brief['site_url']]))
    pages = [evidence.fetch_page(u) for u in urls[:args.max_pages]]
    obj = dict(schema='su-presence/rebuild-research/1', generated_at=stamp(), site_url=brief['site_url'],
               pages=pages, warnings=warnings, coverage=dict(requested=len(urls), attempted=len(pages),
               remaining=urls[args.max_pages:], complete=len(urls) <= args.max_pages and all(p['outcome']=='observed' for p in pages)))
    folder = new_run(args.out)
    save(folder / 'research.json', obj)
    print(folder / 'research.json')
    return 0 if obj['coverage']['complete'] else 2


def new_run(parent):
    import tempfile
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-'), dir=parent))


def plan(args):
    audit = audit_input(args.audit)
    brief = evidence.validate_brief(read_json(args.brief))
    if not brief['queries']:
        raise ValueError('Add at least one query to brief before research/planning')
    if urlsplit(audit['target']['base']).hostname != urlsplit(brief['site_url']).hostname:
        raise ValueError('Brief and audit must belong to the same host')
    obs = observations(args.observations, brief)
    imported, warnings = from_log(args.log, brief)
    existing = {r['id'] for r in obs}
    obs += [r for r in imported if r['id'] not in existing]
    research_data = load_research(args.research)
    if research_data.get('site_url') and research_data['site_url'].rstrip('/') != brief['site_url'].rstrip('/'):
        raise ValueError('Research belongs to another site')
    result = planner.build_plan(audit, brief, obs, research_data)
    result.setdefault('limits', []).extend(warnings)
    result['input_provenance'] = {label: {'path': str(Path(path).resolve()),
        'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()} for label, path in
        [('audit', args.audit), ('brief', args.brief), ('observations', args.observations),
         ('research', args.research), ('log', args.log)] if path}
    folder = new_run(args.out or str(Path(args.audit).parent / 'rebuild'))
    save(folder / 'plan.json', result)
    (folder / 'guide.md').write_text(markdown(result), encoding='utf-8')
    (folder / 'report.html').write_text(render_html(result), encoding='utf-8')
    (folder / 'pages').mkdir()
    for page in result['page_blueprints']:
        stem = folder / 'pages' / safe_name(page['id'])
        save(stem.with_suffix('.json'), page)
        draft = page_draft(page, result)
        stem.with_suffix('.md').write_text(draft, encoding='utf-8')
        stem.with_suffix('.html').write_text(page_html(page, result), encoding='utf-8')
    print('Plan: %s\nReport: %s' % (folder / 'plan.json', folder / 'report.html'))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('init', help='Create editable brief from existing audit and queries')
    i.add_argument('audit'); i.add_argument('--queries'); i.add_argument('--out'); i.set_defaults(func=init)
    r = sub.add_parser('research', help='Explicit bounded public URL research; no search engine scraping or Aside')
    r.add_argument('brief'); r.add_argument('--observations'); r.add_argument('--log')
    r.add_argument('--out', required=True); r.add_argument('--max-pages', type=int, default=20); r.set_defaults(func=research)
    a = sub.add_parser('plan', help='Offline evidence comparison and rebuilding guide')
    a.add_argument('audit'); a.add_argument('--brief', required=True); a.add_argument('--observations')
    a.add_argument('--research'); a.add_argument('--log'); a.add_argument('--out'); a.set_defaults(func=plan)
    args = p.parse_args(argv)
    if args.command == 'research' and not 1 <= args.max_pages <= 100:
        p.error('--max-pages must be 1..100')
    try:
        return args.func(args)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print('Rebuild input/output error: %s' % exc)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
