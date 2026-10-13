# -*- coding: utf-8 -*-
"""Offline provider, provenance and bounds tests for rebuild discovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import rebuild_discovery as discovery  # noqa: E402


def brief():
    return {'schema': 'su-presence/rebuild-brief/1', 'site_url': 'https://example.com/',
            'queries': [{'id': 'q1', 'text': 'sample price', 'intent_id': 'pricing',
                         'terms': [], 'required_sections': [], 'page_type': 'guide'},
                        {'id': 'q2', 'text': 'sample features', 'intent_id': 'features',
                         'terms': [], 'required_sections': [], 'page_type': 'guide'}],
            'competitors': ['https://competitor.example/'], 'channels': ['google'],
            'facts': [], 'pages': []}


class DiscoveryTests(unittest.TestCase):
    def test_missing_provider_or_key_requires_input(self):
        self.assertEqual(discovery.discover(brief())['status'], 'needs_input')
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': ''}):
            result = discovery.discover(brief(), {'provider': 'brave'})
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['candidates'], [])
        self.assertNotIn('secret-value', json.dumps(result))

    def test_import_validates_query_role_context_and_deduplicates(self):
        rows = [{'query_id': 'q1', 'query_text': 'sample price', 'url': 'https://competitor.example/a',
                 'title': 'Comparison', 'provider': 'host_search', 'locale': 'ko-KR',
                 'position': 1, 'observed_at': '2026-10-13T10:00:00+09:00'},
                {'query_id': 'q1', 'query_text': 'sample price', 'url': 'https://competitor.example/a',
                 'title': 'Comparison', 'provider': 'host_search', 'locale': 'ko-KR',
                 'position': 2, 'observed_at': '2026-10-13T10:00:00+09:00'}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'found.json'
            path.write_text(json.dumps({'schema': discovery.SCHEMA, 'candidates': rows}), encoding='utf-8')
            result = discovery.discover(brief(), {'provider': 'import', 'file': str(path),
                                                  'locale': 'ko-KR'})
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(len(result['candidates']), 1)
            self.assertEqual(result['candidates'][0]['role'], 'declared_competitor')
            self.assertEqual(result['coverage']['failed_queries'], ['q2'])
            rows[0]['url'] = 'http://localhost/'
            path.write_text(json.dumps({'schema': discovery.SCHEMA, 'candidates': rows}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'public HTTP URL'):
                discovery.discover(brief(), {'provider': 'import', 'file': str(path), 'locale': 'ko-KR'})

    def test_brave_requests_are_bounded_and_failures_partial(self):
        calls = []
        def fake(query, locale, count, key):
            calls.append((query, locale, count, key))
            if query == 'sample features':
                raise TimeoutError()
            return [{'url': 'https://publisher.example/a', 'title': 'A'},
                    {'url': 'http://127.0.0.1/', 'title': 'Local'}]
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'secret-value'}), \
                patch.object(discovery, '_brave_search', side_effect=fake):
            result = discovery.discover(brief(), {'provider': 'brave', 'locale': 'ko-KR',
                                                  'max_requests': 2, 'max_results': 2})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['candidates'][0]['role'], 'unknown')
        self.assertEqual(result['coverage']['failed_queries'], ['q2'])
        self.assertEqual(result['coverage']['invalid_results'], 1)
        self.assertNotIn('secret-value', json.dumps(result))
        for value in (True, 0, 51):
            with self.assertRaises(ValueError):
                discovery.discover(brief(), {'provider': 'import', 'max_requests': value})

    def test_import_rejects_stale_query_or_locale(self):
        row = {'query_id': 'q1', 'query_text': 'old question', 'url': 'https://other.example/a',
               'title': 'A', 'position': 1, 'locale': 'en-US', 'provider': 'host_search',
               'observed_at': '2026-10-13T10:00:00+09:00'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'found.json'
            path.write_text(json.dumps({'schema': discovery.SCHEMA, 'candidates': [row]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'query_text'):
                discovery.discover(brief(), {'provider': 'import', 'file': str(path)})
            row['query_text'] = 'sample price'
            with self.assertRaisesRegex(ValueError, 'locale'):
                discovery.discover(brief(), {'provider': 'import', 'file': str(path), 'locale': 'ko-KR'})

    def test_import_size_limit_and_omitted_known_query(self):
        row = {'query_id': 'q2', 'query_text': 'sample features', 'url': 'https://other.example/a',
               'title': 'A', 'position': 1, 'locale': 'en-US', 'provider': 'host_search',
               'observed_at': '2026-10-13T10:00:00+09:00'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'found.json'
            path.write_text(json.dumps({'schema': discovery.SCHEMA, 'candidates': [row]}), encoding='utf-8')
            result = discovery.discover(brief(), {'provider': 'import', 'file': str(path),
                                                  'max_requests': 1})
            self.assertEqual(result['coverage']['omitted_queries'], 1)
            self.assertEqual(result['candidates'], [])
            path.write_text('x' * (discovery.MAX_IMPORT_BYTES + 1), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'size limit'):
                discovery.discover(brief(), {'provider': 'import', 'file': str(path)})

    def test_brave_empty_result_and_malformed_result_are_distinct(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, n): return self.payload[:n]
        class Opener:
            def __init__(self, payload): self.payload = payload
            def open(self, request, timeout): return Response(self.payload)
        with patch.object(discovery, 'build_opener', return_value=Opener(b'{}')):
            self.assertEqual(discovery._brave_search('query', 'ko-KR', 2, 'secret-value'), [])
        with patch.object(discovery, 'build_opener', return_value=Opener(b'{"web": []}')):
            with self.assertRaisesRegex(ValueError, 'valid web results'):
                discovery._brave_search('query', 'ko-KR', 2, 'secret-value')
        oversized = b' ' * (discovery.MAX_RESPONSE_BYTES + 1)
        with patch.object(discovery, 'build_opener', return_value=Opener(oversized)):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                discovery._brave_search('query', 'ko-KR', 2, 'secret-value')

    def test_provider_failure_redacts_credentials_and_known_hosts_are_hints(self):
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'secret-value'}), \
                patch.object(discovery, '_brave_search', side_effect=OSError('secret-value')):
            result = discovery.discover(brief(), {'provider': 'brave', 'max_requests': 1})
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertEqual(discovery._role('https://www.youtube.com/watch?v=1', brief()), 'video')
        self.assertEqual(discovery._role('https://notyoutube.com/', brief()), 'unknown')


if __name__ == '__main__':
    unittest.main()
