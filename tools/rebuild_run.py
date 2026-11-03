"""Durable, bounded orchestration of the rebuild evidence and design stages."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import crawl
import rebuild
import rebuild_analysis
import rebuild_discovery
import rebuild_evidence as evidence
import rebuild_plan
import rebuild_protocol
import rebuild_review
import rebuild_browser

SCHEMA = 'su-presence/rebuild-run/1'
STAGES = ('brief', 'audit', 'discovery', 'research', 'analysis', 'plan')
DEPENDENCIES = {'brief': (), 'audit': (), 'discovery': ('brief',),
                'research': ('brief', 'audit', 'discovery'),
                'analysis': ('brief', 'research'),
                'plan': ('brief', 'audit', 'discovery', 'research', 'analysis')}
OUTPUTS = {'brief': ('brief.json',), 'audit': ('audit.json',),
           'discovery': ('discovery.json',), 'research': ('research.json',),
           'analysis': ('analysis.json',), 'plan': ('plan.json', 'guide.md', 'report.html')}
MAX_AUDIT_PAGES = 100
MAX_RESEARCH_PAGES = 100
MAX_QUERIES = 20


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def _file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _bounded(value, default, maximum, label):
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('%s must be an integer from 1 to %d' % (label, maximum))
    return value


def _config(path):
    if not path:
        return {}, None
    file = Path(path).resolve(strict=True)
    data = rebuild.read_json(file)
    _validate_config_data(data)
    # Imported files are resolved against the config, so resume is independent of cwd.
    for section, field in (('discovery', 'file'), ('analysis', 'analysis_file')):
        if isinstance(data.get(section), dict) and data[section].get(field):
            item = data[section][field]
            data[section][field] = str((file.parent / item).resolve())
    for key in ('audit_file', 'brief_file'):
        if data.get(key):
            data[key] = str((file.parent / data[key]).resolve())
    for key in ('observations_file', 'log_file', 'review_file', 'queue_file'):
        if data.get('cycle', {}).get(key):
            data['cycle'][key] = str((file.parent / data['cycle'][key]).resolve())
    return data, str(file)


def _validate_config_data(data):
    if not isinstance(data, dict):
        raise ValueError('Config must be an object')
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError('Config keys must be strings')
                lower = key.casefold()
                if lower != 'api_key_env' and (lower in ('key', 'secret', 'token', 'password', 'api_key', 'apikey') or lower.endswith(('_secret', '_token', '_password', '_api_key'))):
                    raise ValueError('Keep credentials in environment variables, not the config file')
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(data)
    for section in ('discovery', 'analysis', 'cycle'):
        if section in data and not isinstance(data[section], dict):
            raise ValueError('%s config must be an object' % section)
    for section, field in (('discovery', 'file'), ('analysis', 'analysis_file')):
        if isinstance(data.get(section), dict) and data[section].get(field):
            item = data[section][field]
            if not isinstance(item, str):
                raise ValueError('%s.%s must be a path' % (section, field))
    for key in ('audit_file', 'brief_file'):
        if data.get(key):
            if not isinstance(data[key], str):
                raise ValueError('%s must be a path' % key)
    cycle = data.get('cycle', {})
    for key in ('observations_file', 'log_file', 'review_file', 'queue_file'):
        if key in cycle and (not isinstance(cycle[key], str) or not cycle[key]):
            raise ValueError('cycle.%s must be a path' % key)
    if 'repeats' in cycle and (type(cycle['repeats']) is not int or not 1 <= cycle['repeats'] <= 20):
        raise ValueError('cycle.repeats must be 1..20')
    _bounded(data.get('max_audit_pages'), 20, MAX_AUDIT_PAGES, 'max_audit_pages')
    _bounded(data.get('max_research_pages'), 30, MAX_RESEARCH_PAGES, 'max_research_pages')
    _bounded(data.get('max_queries'), 4, MAX_QUERIES, 'max_queries')
    delay = data.get('crawl_delay', 0.3)
    if type(delay) not in (int, float) or not 0 <= delay <= 10:
        raise ValueError('crawl_delay must be from 0 to 10 seconds')
    return data


def _external_hash(path):
    return _file_hash(path) if path and Path(path).is_file() else None


def _input_signature(state, config):
    return {'site_url': state['site_url'], 'topic': state['topic'], 'locale': state['locale'],
            'config': config,
            'external': {key: _external_hash(config.get(key)) for key in ('audit_file', 'brief_file')},
            'discovery_import': _external_hash(config.get('discovery', {}).get('file')),
            'analysis_import': _external_hash(config.get('analysis', {}).get('analysis_file'))}


def _cycle_config(config):
    return config.get('cycle') or {}


def _cycle_with_queue(folder, config):
    if 'cycle' not in config:
        return config
    updated = dict(config)
    updated['cycle'] = dict(_cycle_config(config))
    updated['cycle'].setdefault('queue_file', str(folder / 'browser-queue.json'))
    updated['cycle']['protocol_file'] = str(folder / 'browser-protocol.json')
    return updated


def _observations(brief, config):
    cycle = _cycle_config(config)
    explicit = rebuild.observations(cycle.get('observations_file'), brief)
    imported, warnings = rebuild.from_log(cycle.get('log_file'), brief)
    if cycle.get('queue_file') and Path(cycle['queue_file']).is_file():
        queued, queue_warnings = _queue_observations(cycle['queue_file'], brief, cycle.get('protocol_file'))
        imported.extend(queued)
        warnings.extend(queue_warnings)
    rows, ids = [], set()
    excluded = []
    query_types = {q['id']: q.get('type') for q in brief['queries']}
    for row in explicit + imported:
        if row['id'] in ids:
            continue
        ids.add(row['id'])
        if query_types[row['query_id']] != 'nonbrand':
            excluded.append({'id': row['id'], 'query_id': row['query_id'],
                             'reason': 'query is brand or needs explicit classification'})
            continue
        rows.append(row)
    return rows, warnings, excluded


def _queue_observations(path, brief, current_protocol_path=None):
    queue = rebuild_browser._queue(path)
    if current_protocol_path and Path(current_protocol_path).is_file():
        current = rebuild.read_json(current_protocol_path)
        prior = rebuild.read_json(queue['protocol_path'])
        identity = lambda value: [(row['id'], row['query_sha256']) for row in value.get('slots', [])]
        if identity(current) != identity(prior):
            return [], ['Excluded stale browser queue: query slots changed; use a new queue file.']
    query_index = {query['id']: query for query in brief['queries']}
    rows, warnings = [], []
    for slot in queue['slots']:
        query = query_index.get(slot.get('query_id'))
        if not query or slot.get('query_sha256') != hashlib.sha256(query['text'].encode()).hexdigest():
            warnings.append('Excluded stale queue slot: %s' % slot.get('id'))
            continue
        if slot.get('status') not in ('complete', 'incomplete'):
            continue
        result = slot.get('result') or {}
        if result.get('answer_complete') is not True:
            continue
        for index, url in enumerate(result.get('cited_urls') or []):
            if not evidence.public_url(url):
                warnings.append('Excluded invalid queue citation URL')
                continue
            rows.append({'id': 'queue-%s-%d' % (hashlib.sha256(slot['id'].encode()).hexdigest()[:12], index),
                         'query_id': query['id'], 'query_text': query['text'],
                         'channel': slot['provider'], 'outcome': 'observed', 'url': url,
                         'evidence_url': url, 'observed_at': result['observed_at'],
                         'raw_response_reference': result.get('raw_response_reference'),
                         'session_evidence': result.get('session_evidence'),
                         'brand_mentioned': result.get('brand_mentioned'),
                         'citation_coverage': result.get('citation_coverage'),
                         'conditions': result.get('conditions')})
    return rows, warnings


def _queue_stale(path, current_protocol_path):
    queue = rebuild_browser._queue(path)
    current = rebuild.read_json(current_protocol_path)
    prior = rebuild.read_json(queue['protocol_path'])
    identity = lambda value: [(row['id'], row['query_sha256']) for row in value.get('slots', [])]
    return identity(current) != identity(prior)


def _ensure_protocol_queue(folder, config):
    cycle = _cycle_config(config)
    brief_path = folder / 'brief.json'
    protocol_path = folder / 'protocol.json'
    digest = _file_hash(brief_path)
    previous = rebuild.read_json(protocol_path) if protocol_path.is_file() else None
    if not isinstance(previous, dict) or previous.get('input_provenance', {}).get('brief_sha256') != digest or previous.get('repeats') != cycle.get('repeats', 3):
        protocol = rebuild_protocol.build_protocol(rebuild.read_json(brief_path), cycle.get('repeats', 3))
        protocol['input_provenance'] = {'brief_path': str(brief_path), 'brief_sha256': digest}
        rebuild.save(protocol_path, protocol)
        _write_text(folder / 'protocol.md', rebuild_protocol.markdown(protocol))
    else:
        protocol = previous
    queue_path = Path(cycle.get('queue_file') or folder / 'browser-queue.json')
    browser_protocol_path = folder / 'browser-protocol.json'
    supported = [slot for slot in protocol['slots'] if slot['provider'] in rebuild_browser.PRODUCTS]
    browser_protocol = dict(protocol, slots=supported,
                            browser_excluded_providers=sorted({slot['provider'] for slot in protocol['slots']}
                                                              - set(rebuild_browser.PRODUCTS)))
    previous_browser = rebuild.read_json(browser_protocol_path) if browser_protocol_path.is_file() else None
    if (not isinstance(previous_browser, dict) or previous_browser.get('input_provenance', {}).get('brief_sha256') != digest
            or previous_browser.get('repeats') != cycle.get('repeats', 3)):
        rebuild.save(browser_protocol_path, browser_protocol)
    if not queue_path.exists() and supported:
        immutable_protocol = folder / ('browser-protocol-' + _file_hash(browser_protocol_path)[:16] + '.json')
        if not immutable_protocol.exists():
            shutil.copy2(browser_protocol_path, immutable_protocol)
        rebuild_browser.initialize(immutable_protocol, queue_path)
    return protocol, queue_path


def _state_path(folder):
    return folder / 'run.json'


def _load(folder):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir() or _state_path(folder).is_symlink():
        raise ValueError('Run folder or state path is invalid')
    state = rebuild.read_json(_state_path(folder))
    if state.get('schema') != SCHEMA or not isinstance(state.get('stages'), dict):
        raise ValueError('Unsupported or corrupt rebuild run state')
    if set(state['stages']) != set(STAGES):
        raise ValueError('Run state has unexpected stages')
    if not evidence.public_url(state.get('site_url')):
        raise ValueError('Run state has invalid site URL')
    if not isinstance(state.get('topic'), str) or not state['topic'].strip() or not isinstance(state.get('locale'), str):
        raise ValueError('Run state has invalid topic or locale')
    if not isinstance(state.get('config_snapshot', {}), dict):
        raise ValueError('Run state has invalid config snapshot')
    _validate_config_data(state.get('config_snapshot', {}))
    for name in STAGES:
        stage = state['stages'][name]
        if not isinstance(stage, dict) or stage.get('status') not in ('pending', 'running', 'succeeded', 'partial', 'needs_input', 'failed', 'skipped'):
            raise ValueError('Invalid stage state: ' + name)
        if not isinstance(stage.get('outputs', {}), dict) or any(not _allowed_output(name, key) for key in stage['outputs']):
            raise ValueError('Invalid output entry for ' + name)
        if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in stage['outputs'].values()
               if isinstance(value, str)) or any(not isinstance(value, str) for value in stage['outputs'].values()):
            raise ValueError('Invalid output digest for ' + name)
        if type(stage.get('attempts', 0)) is not int or stage.get('attempts', 0) < 0:
            raise ValueError('Invalid attempt count for ' + name)
    return folder, state


def _write_state(folder, state):
    if _state_path(folder).is_symlink():
        raise ValueError('Run state path cannot be a symlink')
    state['updated_at'] = _stamp()
    rebuild.save(_state_path(folder), state)


def _allowed_output(name, key):
    return key in OUTPUTS[name] or (name == 'plan' and isinstance(key, str) and
                                     (key in ('protocol.json', 'protocol.md', 'browser-protocol.json', 'cycle-report.json',
                                              'review.json', 'review.md', 'review.html') or
                                      re.fullmatch(r'pages/[A-Za-z0-9_-]+-[0-9a-f]{8}\.(?:json|md|html)', key) is not None))


def _expected_outputs(folder, name):
    outputs = list(OUTPUTS[name])
    if name == 'plan':
        plan = rebuild.read_json(folder / 'plan.json')
        pages = plan.get('page_blueprints')
        if not isinstance(pages, list):
            raise ValueError('Plan pages are invalid')
        for page in pages:
            stem = rebuild.safe_name(page['id'])
            outputs.extend('pages/' + stem + '.' + ext for ext in ('json', 'md', 'html'))
        if isinstance(plan.get('cycle'), dict):
            outputs.extend(('protocol.json', 'protocol.md', 'browser-protocol.json', 'cycle-report.json'))
            if plan['cycle'].get('review', {}).get('status') in ('human_review_required', 'partial'):
                outputs.extend(('review.json', 'review.md', 'review.html'))
    return outputs


def _outputs_valid(folder, name, stage):
    outputs = stage.get('outputs') or {}
    try:
        expected = _expected_outputs(folder, name)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if set(expected) != set(outputs):
        return False
    return all((folder / key).is_file() and not (folder / key).is_symlink() and
               _file_hash(folder / key) == outputs[key] for key in expected)


def _stage_fingerprint(name, state, config):
    upstream = {dep: {'status': state['stages'][dep]['status'],
                      'outputs': state['stages'][dep].get('outputs', {})}
                for dep in DEPENDENCIES[name]}
    common = {'site_url': state['site_url']}
    specific = {
        'brief': {'topic': state['topic'], 'locale': state['locale'],
                  'brief_file': _external_hash(config.get('brief_file')),
                  'channels': config.get('channels'), 'max_queries': config.get('max_queries')},
        'audit': {'audit_file': _external_hash(config.get('audit_file')),
                  'max_audit_pages': config.get('max_audit_pages'), 'crawl_delay': config.get('crawl_delay')},
        'discovery': {'locale': state['locale'], 'settings': config.get('discovery', {}),
                      'import': _external_hash(config.get('discovery', {}).get('file'))},
        'research': {'max_research_pages': config.get('max_research_pages'),
                     'observations_import': _external_hash(_cycle_config(config).get('observations_file')),
                     'log_import': _external_hash(_cycle_config(config).get('log_file')),
                     'queue_import': _external_hash(_cycle_config(config).get('queue_file'))},
        'analysis': {'settings': config.get('analysis', {}),
                     'import': _external_hash(config.get('analysis', {}).get('analysis_file'))},
        'plan': {'cycle': _cycle_config(config),
                 'observations_import': _external_hash(_cycle_config(config).get('observations_file')),
                 'log_import': _external_hash(_cycle_config(config).get('log_file')),
                 'queue_import': _external_hash(_cycle_config(config).get('queue_file')),
                 'review_import': _external_hash(_cycle_config(config).get('review_file'))}}[name]
    code_files = {'brief': (evidence,), 'audit': (crawl,),
                  'discovery': (rebuild_discovery,), 'research': (evidence, rebuild_discovery),
                  'analysis': (rebuild_analysis,),
                  'plan': (rebuild_plan, rebuild_analysis, rebuild, rebuild_protocol, rebuild_review)}[name]
    code_versions = [_file_hash(__file__)] + [_file_hash(module.__file__) for module in code_files]
    return _digest({'version': 1, 'name': name, 'code_versions': code_versions,
                    'input': dict(common, **specific),
                    'upstream': upstream})


def _lock(folder, recover=False):
    """Create a process lock. Recovery needs an explicit flag and a dead, old owner."""
    path = folder / '.run.lock'
    token = uuid.uuid4().hex
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if not recover:
            raise ValueError('Run is locked; use --recover-lock only after confirming its owner stopped')
        try:
            owner = json.loads(path.read_text(encoding='utf-8'))
            pid = owner['pid']
            at = datetime.fromisoformat(owner['created_at'])
            age = (datetime.now(timezone.utc) - at).total_seconds()
            if type(pid) is not int or age < 900:
                raise ValueError('Lock is recent or invalid; cannot recover safely')
            if os.name == 'nt':
                import ctypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
                kernel.GetExitCodeProcess.restype = ctypes.c_int
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                handle = kernel.OpenProcess(0x1000, False, pid)
                if handle:
                    code = ctypes.c_ulong()
                    try:
                        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259:
                            raise ValueError('Lock owner may still be active')
                    finally:
                        kernel.CloseHandle(handle)
                elif ctypes.get_last_error() != 87:
                    raise ValueError('Lock owner status cannot be confirmed')
            else:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    raise ValueError('Lock owner may still be active')
                else:
                    raise ValueError('Lock owner is still active')
            # A second recovery process can race. Recheck content before unlinking.
            if json.loads(path.read_text(encoding='utf-8')) != owner:
                raise ValueError('Lock changed during recovery')
            path.unlink()
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Cannot safely recover lock') from exc
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump({'pid': os.getpid(), 'created_at': _stamp(), 'token': token}, output)
    return path, token


def _unlock(lock):
    path, token = lock
    try:
        if json.loads(path.read_text(encoding='utf-8')).get('token') == token:
            path.unlink()
    except (OSError, ValueError):
        pass


def _brief(state, config):
    if config.get('brief_file'):
        brief = evidence.validate_brief(rebuild.read_json(config['brief_file']))
        if urlsplit(brief['site_url']).hostname != urlsplit(state['site_url']).hostname:
            raise ValueError('Brief belongs to another site')
        if not brief['queries']:
            raise ValueError('Brief has no queries')
        return brief
    topic = state['topic'].strip()
    if not topic or len(topic) > 160:
        raise ValueError('Topic must contain 1..160 characters')
    if state['locale'].lower().startswith('ko'):
        texts = [topic, topic + ' 비교', topic + ' 선택 기준', topic + ' 자주 묻는 질문']
    else:
        texts = [topic, topic + ' comparison', topic + ' how to choose', topic + ' frequently asked questions']
    queries = [{'id': 'q%d' % (i + 1), 'text': text, 'intent_id': 'topic', 'terms': [topic],
                'required_sections': [], 'page_type': 'guide', 'priority': 2}
               for i, text in enumerate(texts[:_bounded(config.get('max_queries'), 4, MAX_QUERIES, 'max_queries')])]
    brief = {'schema': evidence.SCHEMA, 'site_url': state['site_url'], 'queries': queries,
             'competitors': [], 'channels': config.get('channels', rebuild.CHANNELS),
             'facts': [], 'pages': []}
    return evidence.validate_brief(brief)


def _make_research(brief, audit, discovery, config, observations=None):
    candidates = discovery.get('candidates', [])
    own_host = urlsplit(brief['site_url']).hostname
    def unique_urls(urls):
        seen = set()
        result = []
        for url in urls:
            key = rebuild_discovery._url_key(url)
            if key not in seen:
                result.append(url)
                seen.add(key)
        return result

    owned = unique_urls([brief['site_url']] + [page['url'] for page in brief['pages']]
                        + [page['url'] for page in audit.get('pages', [])
                           if isinstance(page, dict) and evidence.public_url(page.get('url'))
                           and urlsplit(page['url']).hostname == own_host])
    candidate_urls = unique_urls(row['url'] for row in candidates
                                 if urlsplit(row['url']).hostname != own_host)
    observed_urls = unique_urls(row['url'] for row in (observations or [])
                                if row['outcome'] == 'observed' and urlsplit(row['url']).hostname != own_host)
    # Spread external sources across hosts before taking second results from one host.
    seen_hosts = set()
    first, later = [], []
    for url in observed_urls + candidate_urls + brief['competitors']:
        host = urlsplit(url).hostname
        if host == own_host:
            continue
        (first if host not in seen_hosts else later).append(url)
        seen_hosts.add(host)
    external = unique_urls(first + later)
    urls = []
    for index in range(max(len(owned), len(external))):
        if index < len(owned):
            urls.append(owned[index])
        if index < len(external):
            urls.append(external[index])
    limit = _bounded(config.get('max_research_pages'), 30, MAX_RESEARCH_PAGES, 'max_research_pages')
    by_url = {}
    for candidate in candidates:
        by_url.setdefault(rebuild_discovery._url_key(candidate['url']), []).append(candidate)
    pages = []
    for url in urls[:limit]:
        page = evidence.fetch_page(url)
        hits = by_url.get(rebuild_discovery._url_key(url), [])
        if hits:
            page['query_ids'] = list(dict.fromkeys(hit['query_id'] for hit in hits))
            page['discovery_ids'] = list(dict.fromkeys(hit['id'] for hit in hits))
            page['discovery_providers'] = list(dict.fromkeys(hit['provider'] for hit in hits))
        observations_for_page = [row for row in (observations or [])
                                 if row['outcome'] == 'observed' and rebuild_discovery._url_key(row['url']) == rebuild_discovery._url_key(url)]
        if observations_for_page:
            page['observation_ids'] = list(dict.fromkeys(row['id'] for row in observations_for_page))
            page['query_ids'] = list(dict.fromkeys(page.get('query_ids', []) +
                                                  [row['query_id'] for row in observations_for_page]))
        pages.append(page)
    return {'schema': 'su-presence/rebuild-research/1', 'generated_at': _stamp(),
            'site_url': brief['site_url'], 'pages': pages,
            'coverage': {'requested': len(urls), 'attempted': len(pages), 'remaining': urls[limit:],
                         'complete': len(urls) <= limit and all(p['outcome'] == 'observed' for p in pages)},
            'discovery_provenance': {'provider': discovery.get('provider'),
                                     'candidate_ids': [row['id'] for row in candidates]},
            'warnings': list(discovery.get('warnings', []))}


def _write_text(path, value):
    fd, temp = tempfile.mkstemp(prefix='.rebuild-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(value)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _within(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _archive_existing(folder, name, stage):
    if not stage.get('outputs'):
        return
    if (folder / 'history').is_symlink():
        raise ValueError('History directory cannot be a symlink')
    if (folder / 'history' / name).is_symlink():
        raise ValueError('Stage history directory cannot be a symlink')
    history = folder / 'history' / name / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:6])
    if not _within(history.parent, folder):
        raise ValueError('History directory escapes the run')
    for filename in stage['outputs']:
        source = folder / filename
        if source.is_file() and not source.is_symlink():
            target = history / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _execute(name, folder, state, config):
    for filename in OUTPUTS[name]:
        if (folder / filename).is_symlink():
            raise ValueError('Stage output cannot be a symlink')
    if name == 'brief':
        rebuild.save(folder / 'brief.json', _brief(state, config))
        return 'succeeded', None
    if name == 'audit':
        if config.get('audit_file'):
            audit = rebuild.audit_input(config['audit_file'])
            if urlsplit(audit['target']['base']).hostname != urlsplit(state['site_url']).hostname:
                raise ValueError('Audit belongs to another site')
        else:
            audit = crawl.build_report(state['site_url'],
                                       _bounded(config.get('max_audit_pages'), 20, MAX_AUDIT_PAGES, 'max_audit_pages'),
                                       config.get('crawl_delay', 0.3))
        rebuild.save(folder / 'audit.json', audit)
        complete = audit.get('coverage', {}).get('complete') is True
        return ('succeeded' if complete else 'partial'), (None if complete else 'Audit coverage is incomplete; inspect audit.json and refresh after fixing access')
    brief = evidence.validate_brief(rebuild.read_json(folder / 'brief.json'))
    if name == 'discovery':
        result = rebuild_discovery.discover(brief, {'discovery': dict(config.get('discovery', {}), locale=state['locale'])})
        rebuild.save(folder / 'discovery.json', result)
        return result['status'], result.get('next_action') or ('; '.join(result.get('warnings', [])[:3]) if result['status'] == 'partial' else None)
    discovery = rebuild.read_json(folder / 'discovery.json')
    if name == 'research':
        observations, _, _ = _observations(brief, config) if 'cycle' in config else ([], [], [])
        result = _make_research(brief, rebuild.audit_input(folder / 'audit.json'), discovery, config, observations)
        rebuild.save(folder / 'research.json', result)
        complete = result['coverage']['complete']
        return ('succeeded' if complete else 'partial'), (None if complete else 'Research coverage is incomplete; inspect research.json and use resume --refresh to retry')
    research = rebuild.load_research(folder / 'research.json')
    if name == 'analysis':
        try:
            result = rebuild_analysis.analyze(brief, research, config.get('analysis'))
            note = None
        except rebuild_analysis.NeedsInput as exc:
            result = rebuild_analysis.analyze(brief, research, None)
            note = str(exc)
        rebuild.save(folder / 'analysis.json', result)
        return ('needs_input' if note else 'succeeded' if result['status'] == 'complete' else 'partial'), note or ('; '.join(result.get('warnings', [])[:3]) if result['status'] == 'partial' else None)
    audit = rebuild.audit_input(folder / 'audit.json')
    analysis = rebuild.read_json(folder / 'analysis.json')
    observations, import_warnings, excluded = _observations(brief, config) if 'cycle' in config else ([], [], [])
    plan = rebuild_plan.build_plan(audit, brief, observations, research, discovery=discovery)
    plan = rebuild_analysis.enrich_plan(plan, analysis)
    plan.setdefault('input_provenance', {})
    if 'cycle' in config:
        cycle = _cycle_config(config)
        protocol, queue_path = _ensure_protocol_queue(folder, config)
        unsupported_providers = rebuild.read_json(folder / 'browser-protocol.json').get('browser_excluded_providers', [])
        queue_stale = queue_path.is_file() and _queue_stale(queue_path, folder / 'browser-protocol.json')
        queue_counts = rebuild_browser.status(queue_path)['counts'] if queue_path.is_file() and not queue_stale else None
        review_state = {'status': 'pending', 'next_action': 'Supply cycle.review_file with completed copy.'}
        if cycle.get('review_file'):
            review_input, review_hash = rebuild_review.load_input(cycle['review_file'])
            page_ids = {page['id'] for page in plan['page_blueprints']}
            supplied_ids = {page['id'] for page in review_input['pages']}
            if not supplied_ids.issubset(page_ids):
                raise ValueError('Content review page IDs must match current plan page IDs')
            review_result = rebuild_review.review(review_input, review_hash)
            review_result['plan_sha256'] = _digest({'page_blueprints': plan['page_blueprints'],
                'source_hashes': {name: _file_hash(folder / (name + '.json')) for name in
                                  ('brief', 'audit', 'research', 'analysis', 'discovery')}})
            rebuild.save(folder / 'review.json', review_result)
            _write_text(folder / 'review.md', rebuild_review.markdown(review_result))
            _write_text(folder / 'review.html', rebuild_review.render_html(review_result))
            review_state = {'status': 'partial' if page_ids - supplied_ids else 'human_review_required',
                            'page_ids': sorted(supplied_ids),
                            'missing_page_ids': sorted(page_ids - supplied_ids),
                            'input_sha256': review_hash, 'plan_sha256': review_result['plan_sha256'],
                            'next_action': 'Editor must resolve manual review findings.'}
        measurement_status = ('blocked' if queue_stale or unsupported_providers or queue_counts and queue_counts['blocked'] else
                              'complete' if queue_counts and queue_counts['complete'] == len(protocol['slots']) and
                              not protocol['query_classification_needs_review'] else
                              'partial' if observations or (queue_counts and queue_counts['complete']) else 'pending')
        cycle_report = {'schema': 'su-presence/rebuild-cycle-report/1',
                        'measurement': {'status': measurement_status,
                                        'observed_rows': sum(row['outcome'] == 'observed' for row in observations),
                                        'imported_rows': len(observations),
                                        'pending_slots': (queue_counts['pending'] if queue_counts else len(protocol['slots'])),
                                        'queue_counts': queue_counts,
                                        'queue_stale': queue_stale,
                                        'browser_unsupported_providers': unsupported_providers,
                                        'completed_slots': queue_counts['complete'] if queue_counts else 0,
                                        'zero_citation_completed_slots': (sum(slot['status'] == 'complete' and not slot['result'].get('cited_urls')
                                            for slot in rebuild_browser._queue(queue_path)['slots']) if queue_counts else 0),
                                        'query_classification_needs_review': protocol['query_classification_needs_review'],
                                        'excluded_brand_query_ids': protocol['excluded_brand_query_ids'],
                                        'excluded_import_rows': excluded, 'warnings': import_warnings,
                                        'next_action': 'Record actual results for pending protocol slots; classify unspecified queries.'},
                        'review': review_state,
                        'research': {'status': state['stages']['research']['status'],
                                     'coverage': research.get('coverage')},
                        'deployment': {'status': 'excluded', 'reason': 'This cycle only prepares evidence and drafts.'}}
        rebuild.save(folder / 'cycle-report.json', cycle_report)
        plan['cycle'] = cycle_report
        plan.setdefault('limits', []).extend(import_warnings)
        if not observations:
            plan['limits'].append('No validated visibility observations imported; measurement remains pending.')
        if excluded:
            plan['limits'].append('%d observation rows excluded because query type is brand or unclassified.' % len(excluded))
        plan['input_provenance'].update({key: {'path': cycle[field], 'sha256': _file_hash(cycle[field])}
                                          for key, field in [('observations', 'observations_file'),
                                                             ('log', 'log_file'), ('review_input', 'review_file'),
                                                             ('browser_queue', 'queue_file')]
                                          if cycle.get(field) and Path(cycle[field]).is_file()})
    # Discovery hits are candidate sources, not proven search visibility observations.
    plan['discovery'] = {'provider': discovery.get('provider'), 'status': discovery.get('status'),
                         'candidate_count': len(discovery.get('candidates', [])),
                         'warnings': discovery.get('warnings', [])}
    plan['input_provenance'].update({key: {'path': str(folder / filename), 'sha256': _file_hash(folder / filename)}
                                for key, filename in [('audit', 'audit.json'), ('brief', 'brief.json'),
                                                      ('discovery', 'discovery.json'), ('research', 'research.json'),
                                                      ('analysis', 'analysis.json')]})
    if discovery.get('status') != 'succeeded':
        plan.setdefault('limits', []).append('Discovery is %s; search visibility and competitor coverage are incomplete.' % discovery.get('status'))
    if state['stages']['analysis']['status'] == 'needs_input':
        plan.setdefault('limits', []).append('Semantic analysis needs input; the available comparison is structural only.')
    plan['execution'] = {'run_id': state['run_id'], 'stage_statuses': {stage: state['stages'][stage]['status']
                           for stage in STAGES if stage != 'plan'},
                         'next_actions': [state['stages'][stage]['error'] for stage in STAGES
                                          if state['stages'][stage].get('error')]}
    rebuild.save(folder / 'plan.json', plan)
    _write_text(folder / 'guide.md', rebuild.markdown(plan))
    _write_text(folder / 'report.html', rebuild.render_html(plan))
    pages = folder / 'pages'
    if pages.is_symlink():
        raise ValueError('Pages directory cannot be a symlink')
    if not _within(pages, folder):
        raise ValueError('Pages directory escapes the run')
    pages.mkdir(exist_ok=True)
    for page in plan['page_blueprints']:
        stem = pages / rebuild.safe_name(page['id'])
        rebuild.save(stem.with_suffix('.json'), page)
        _write_text(stem.with_suffix('.md'), rebuild.page_draft(page, plan))
        _write_text(stem.with_suffix('.html'), rebuild.page_html(page, plan))
    return 'succeeded', None


def _overall(state):
    statuses = [state['stages'][name]['status'] for name in STAGES]
    if 'failed' in statuses:
        return 'failed'
    if 'needs_input' in statuses:
        return 'needs_input'
    if 'pending' in statuses or 'running' in statuses or 'skipped' in statuses:
        return 'partial'
    if 'partial' in statuses:
        return 'partial'
    if 'cycle' in state.get('config_snapshot', {}):
        # Automated copy review still requires human approval; this runner has
        # no approval transition, so a measured cycle is not overall complete.
        return 'partial'
    return 'complete'


def _safe_error(exc):
    if isinstance(exc, rebuild_analysis.AnalysisError):
        return 'AnalysisError: provider request or output failed; see provider configuration and retry'
    if isinstance(exc, OSError):
        return '%s: file or network operation failed' % type(exc).__name__
    message = str(exc)
    if len(message) > 300:
        message = message[:300] + '…'
    return '%s: %s' % (type(exc).__name__, message)


def _drive(folder, state, config, refresh=False):
    config = _cycle_with_queue(folder, config)
    for name in STAGES:
        stage = state['stages'][name]
        if any(state['stages'][dep]['status'] in ('failed', 'pending', 'running', 'skipped') for dep in DEPENDENCIES[name]):
            stage.update(status='skipped', error='Upstream stage unavailable')
            _write_state(folder, state)
            continue
        if name == 'research' and 'cycle' in config and state['stages']['brief']['status'] in ('succeeded', 'partial'):
            _ensure_protocol_queue(folder, config)
        fingerprint = _stage_fingerprint(name, state, config)
        force = refresh and name in ('discovery', 'research', 'analysis', 'plan')
        if refresh and name in ('audit', 'brief') and stage['status'] == 'partial':
            force = True
        if name == 'discovery' and stage.get('status') == 'succeeded' and config.get('discovery', {}).get('provider') == 'brave':
            max_age = config.get('discovery', {}).get('max_age_hours', 24)
            if type(max_age) not in (int, float) or not 1 <= max_age <= 720:
                raise ValueError('discovery.max_age_hours must be from 1 to 720')
            try:
                age = time.time() - datetime.fromisoformat(stage['ended_at']).timestamp()
                force = force or age >= max_age * 3600
            except (KeyError, ValueError, TypeError):
                force = True
        if (not force and stage['status'] in ('succeeded', 'partial')
                and stage.get('fingerprint') == fingerprint and _outputs_valid(folder, name, stage)):
            continue
        _archive_existing(folder, name, stage)
        if stage.get('attempts', 0):
            stage.setdefault('history', []).append({key: stage.get(key) for key in
                ('status', 'attempts', 'started_at', 'ended_at', 'fingerprint', 'outputs', 'error')})
        stage.update(status='running', started_at=_stamp(), ended_at=None, error=None,
                     attempts=stage.get('attempts', 0) + 1, fingerprint=fingerprint, outputs={})
        _write_state(folder, state)
        try:
            status, message = _execute(name, folder, state, config)
            stage.update(status=status, ended_at=_stamp(), error=message,
                         outputs={filename: _file_hash(folder / filename)
                                  for filename in _expected_outputs(folder, name)})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            stage.update(status='failed', ended_at=_stamp(), error=_safe_error(exc), outputs={})
        _write_state(folder, state)
    state['status'] = _overall(state)
    _write_state(folder, state)
    return state['status']


def run(args):
    if not evidence.public_url(args.site_url):
        raise ValueError('Site must be a public HTTP URL')
    if not isinstance(args.topic, str) or not args.topic.strip():
        raise ValueError('A topic is required')
    config, config_path = _config(args.config)
    parent = Path(args.out or 'out').resolve()
    folder = rebuild.new_run(parent)
    state = {'schema': SCHEMA, 'run_id': folder.name, 'created_at': _stamp(), 'updated_at': _stamp(),
             'site_url': args.site_url, 'topic': args.topic, 'locale': args.locale,
             'config_path': config_path, 'config_snapshot': config, 'status': 'pending',
             'stages': {name: {'status': 'pending', 'attempts': 0, 'outputs': {},
                               'dependencies': list(DEPENDENCIES[name])} for name in STAGES}}
    lock = _lock(folder)
    try:
        _write_state(folder, state)
        result = _drive(folder, state, config)
    finally:
        _unlock(lock)
    print('%s\nStatus: %s' % (folder, result))
    return 0 if result == 'complete' else 2


def resume(args):
    folder, state = _load(args.folder)
    source_path = args.config or state.get('config_path')
    if source_path and Path(source_path).is_file():
        config, config_path = _config(source_path)
    elif args.config:
        raise ValueError('Requested config file does not exist')
    else:
        config, config_path = state.get('config_snapshot', {}), source_path
    state['config_path'] = config_path
    state['config_snapshot'] = config
    lock = _lock(folder, args.recover_lock)
    try:
        result = _drive(folder, state, config, args.refresh)
    finally:
        _unlock(lock)
    print('%s\nStatus: %s' % (folder, result))
    return 0 if result == 'complete' else 2


def status(args):
    folder, state = _load(args.folder)
    output = {'schema': SCHEMA, 'run_id': state.get('run_id'), 'path': str(folder),
              'status': _overall(state), 'stages': {}}
    invalid = False
    for name in STAGES:
        item = state['stages'][name]
        valid = _outputs_valid(folder, name, item) if item.get('outputs') else False
        invalid = invalid or (item['status'] in ('succeeded', 'partial', 'needs_input') and not valid)
        output['stages'][name] = {'status': item['status'], 'attempts': item.get('attempts', 0),
                                  'outputs_valid': valid,
                                  'next_action': item.get('error')}
    if 'cycle' in state.get('config_snapshot', {}) and (folder / 'cycle-report.json').is_file():
        output['cycle'] = rebuild.read_json(folder / 'cycle-report.json')
        cycle_path = folder / 'browser-queue.json'
        if cycle_path.is_file():
            output['cycle']['live_browser_queue'] = rebuild_browser.status(cycle_path)
    if invalid:
        output['status'] = 'invalid_artifacts'
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if invalid else 0


def add_parsers(sub):
    p = sub.add_parser('run', help='Discover, research, compare and design pages in one durable run')
    p.add_argument('site_url'); p.add_argument('--topic', required=True); p.add_argument('--locale', default='ko-KR')
    p.add_argument('--config'); p.add_argument('--out'); p.set_defaults(func=run)
    p = sub.add_parser('resume', help='Resume a saved run using validated stage artifacts')
    p.add_argument('folder'); p.add_argument('--config'); p.add_argument('--refresh', action='store_true')
    p.add_argument('--recover-lock', action='store_true'); p.set_defaults(func=resume)
    p = sub.add_parser('status', help='Inspect a saved rebuild run')
    p.add_argument('folder'); p.set_defaults(func=status)
