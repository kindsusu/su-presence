"""Bounded search discovery for rebuild runs; search hits are not exposure observations."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import rebuild_evidence as evidence

SCHEMA = 'su-presence/rebuild-discovery/1'
BRAVE_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
MAX_RESPONSE_BYTES = 1_000_000
MAX_IMPORT_BYTES = 2_000_000
MAX_REQUESTS = 50
MAX_RESULTS = 20


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None
    except ValueError:
        return False


def _limit(value: object, default: int, ceiling: int, name: str) -> int:
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ValueError(f'{name} must be an integer from 1 to {ceiling}')
    return value


def _locale(value: object) -> str:
    if value is None:
        return 'en-US'
    if not isinstance(value, str) or not re.fullmatch(r'[a-z]{2,3}-[A-Z]{2}', value):
        raise ValueError('locale must be a language-country code such as ko-KR')
    return value


def _role(url: str, brief: dict) -> str:
    host = urlsplit(url).hostname.rstrip('.').lower()
    if host == urlsplit(brief['site_url']).hostname.rstrip('.').lower():
        return 'owned'
    if any(host == urlsplit(item).hostname.rstrip('.').lower() for item in brief['competitors']):
        return 'declared_competitor'
    hints = {
        'video': ('youtube.com', 'youtu.be', 'vimeo.com'),
        'community': ('reddit.com', 'quora.com'),
        'wiki': ('wikipedia.org', 'wikidata.org'),
        'news': ('news.google.com', 'news.naver.com'),
        'platform': ('medium.com', 'blog.naver.com', 'brunch.co.kr'),
    }
    for role, domains in hints.items():
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return role
    return 'unknown'


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname.rstrip('.').encode('idna').decode('ascii').lower()
    port = parsed.port
    authority_host = f'[{host}]' if ':' in host else host
    netloc = (authority_host if port in (None, 80 if parsed.scheme == 'http' else 443)
              else f'{authority_host}:{port}')
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or '/', parsed.query, ''))


def _candidate(row: dict, brief: dict, query: dict, *, default_provider: str,
               default_locale: str, default_time: str) -> dict:
    if not isinstance(row, dict):
        raise ValueError('Discovery candidate must be an object')
    url = row.get('url')
    if not evidence.public_url(url):
        raise ValueError('Discovery candidate must have a public HTTP URL')
    title = row.get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError('Discovery candidate title must be nonempty text up to 500 characters')
    provider = row.get('provider', default_provider)
    if not isinstance(provider, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}', provider):
        raise ValueError('Discovery candidate provider is invalid')
    position = row.get('position')
    if type(position) is not int or position < 1:
        raise ValueError('Discovery candidate position must be a positive integer')
    observed_at = row.get('observed_at', default_time)
    if not _timestamp(observed_at):
        raise ValueError('Discovery candidate observed_at must contain a timezone')
    locale = _locale(row.get('locale', default_locale))
    if locale != default_locale:
        raise ValueError('Discovery candidate locale differs from requested locale')
    if 'query_text' in row and row['query_text'] != query['text']:
        raise ValueError('Discovery candidate query_text differs from current brief')
    digest = hashlib.sha256(json.dumps([query['id'], _url_key(url), provider, locale],
                                     ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
    role = _role(url, brief)
    return {'id': 'discovery-' + digest, 'query_id': query['id'], 'query_text': query['text'],
            'url': url, 'title': title.strip(), 'provider': provider,
            'observed_at': observed_at, 'locale': locale, 'position': position,
            'role': role, 'role_basis': ('host_match' if role in ('owned', 'declared_competitor')
                                       else 'host_hint' if role != 'unknown' else 'unclassified')}


def _brave_search(query: str, locale: str, count: int, key: str) -> list[dict]:
    language, country = locale.split('-')
    url = BRAVE_ENDPOINT + '?' + urlencode({'q': query, 'country': country,
                                             'search_lang': language, 'count': count})
    request = Request(url, headers={'Accept': 'application/json', 'X-Subscription-Token': key,
                                    'User-Agent': 'su-presence-discovery/1'})
    # The endpoint is fixed; a redirect is never followed to another host with the key.
    with build_opener(_NoRedirect).open(request, timeout=12) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('Brave response exceeded size limit')
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        raise ValueError('Brave response must be an object')
    web = obj.get('web', {})
    rows = web.get('results', []) if isinstance(web, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Brave response has no valid web results list')
    return rows[:count]


def discover(brief: dict, config: dict | None = None) -> dict:
    """Return schema /1 candidates and coverage without treating hits as AI citations.

    ``config`` may be a discovery settings object or contain one under ``discovery``.
    import files use this same schema with a candidates list. Search credentials are
    read only from BRAVE_SEARCH_API_KEY and never copied to returned artifacts.
    """
    brief = evidence.validate_brief(brief)
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError('Discovery config must be an object')
    config = config.get('discovery', config)
    if not isinstance(config, dict):
        raise ValueError('Discovery settings must be an object')
    provider = config.get('provider')
    locale = _locale(config.get('locale'))
    max_requests = _limit(config.get('max_requests'), 10, MAX_REQUESTS, 'max_requests')
    max_results = _limit(config.get('max_results'), 10, MAX_RESULTS, 'max_results')
    queries = brief['queries'][:max_requests]
    result = {'schema': SCHEMA, 'generated_at': _stamp(), 'status': 'needs_input',
              'provider': provider, 'locale': locale, 'candidates': [], 'warnings': [],
              'coverage': {'requested_queries': len(brief['queries']), 'searched_queries': 0,
                           'omitted_queries': max(0, len(brief['queries']) - len(queries)),
                           'failed_queries': [], 'invalid_results': 0}}
    if result['coverage']['omitted_queries']:
        result['warnings'].append('Query request limit reached; some queries were not searched')
    if not queries:
        result['next_action'] = 'Add at least one query to the rebuild brief'
        return result
    if not provider:
        result['next_action'] = 'Configure discovery.provider as import or brave'
        return result
    if provider not in ('import', 'brave'):
        raise ValueError('Unsupported discovery provider')
    if provider == 'import':
        path = config.get('file')
        if not isinstance(path, str) or not path.strip():
            result['next_action'] = 'Set discovery.file to a discovery/1 JSON file'
            return result
        file = Path(path)
        if not file.is_file():
            result['next_action'] = 'Discovery import file does not exist'
            return result
        if file.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError('Discovery import exceeded size limit')
        with file.open(encoding='utf-8') as stream:
            payload = stream.read(MAX_IMPORT_BYTES + 1)
        if len(payload.encode('utf-8')) > MAX_IMPORT_BYTES:
            raise ValueError('Discovery import exceeded size limit')
        source = json.loads(payload)
        if not isinstance(source, dict) or source.get('schema') != SCHEMA or not isinstance(source.get('candidates'), list):
            raise ValueError('Discovery import must be a discovery/1 object with candidates list')
        allowed = {q['id']: q for q in queries}
        known = {q['id'] for q in brief['queries']}
        seen, per_query = set(), {}
        for row in source['candidates']:
            if not isinstance(row, dict) or row.get('query_id') not in known:
                raise ValueError('Discovery candidate query_id must refer to a brief query')
            if row['query_id'] not in allowed:
                continue
            if any(field not in row for field in ('query_text', 'provider', 'locale', 'observed_at', 'position')):
                raise ValueError('Imported candidate needs query_text, provider, locale, observed_at and position')
            qid = row['query_id']
            candidate = _candidate(row, brief, allowed[qid], default_provider='import',
                                   default_locale=locale, default_time=result['generated_at'])
            per_query[qid] = per_query.get(qid, 0) + 1
            if per_query[qid] > max_results:
                continue
            if candidate['id'] in seen:
                continue
            seen.add(candidate['id'])
            result['candidates'].append(candidate)
        result['coverage']['searched_queries'] = len(per_query)
        missing = [q['id'] for q in queries if q['id'] not in per_query]
        result['coverage']['failed_queries'] = missing
        if missing:
            result['warnings'].append('Import has no candidates for some selected queries')
    else:
        key = os.environ.get('BRAVE_SEARCH_API_KEY')
        if not key:
            result['next_action'] = 'Set BRAVE_SEARCH_API_KEY in the environment'
            return result
        seen = set()
        for query in queries:
            try:
                rows = _brave_search(query['text'], locale, max_results, key)
                result['coverage']['searched_queries'] += 1
                for position, row in enumerate(rows, 1):
                    try:
                        if not isinstance(row, dict):
                            raise ValueError('Invalid search result')
                        candidate = _candidate({**row, 'position': position, 'provider': 'brave',
                                                'locale': locale, 'observed_at': _stamp(),
                                                'query_text': query['text']}, brief, query,
                                               default_provider='brave', default_locale=locale,
                                               default_time=_stamp())
                    except ValueError:
                        result['coverage']['invalid_results'] += 1
                        result['warnings'].append('Skipped an invalid search result')
                        continue
                    if candidate['id'] not in seen:
                        seen.add(candidate['id'])
                        result['candidates'].append(candidate)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                # Do not include provider response bodies, request URLs or credentials.
                result['coverage']['failed_queries'].append(query['id'])
                result['warnings'].append(f'Search failed for {query["id"]}: {type(exc).__name__}')
    coverage = result['coverage']
    result['status'] = ('partial' if coverage['failed_queries'] or coverage['omitted_queries'] or coverage['invalid_results']
                        else 'succeeded')
    return result
