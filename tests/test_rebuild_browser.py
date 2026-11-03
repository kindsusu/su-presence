import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rebuild_browser as browser
from rebuild_protocol import SCHEMA as PROTOCOL_SCHEMA


def protocol():
    query = 'Which service fits a small team?'
    return {'schema': PROTOCOL_SCHEMA, 'slots': [
        {'id': 'q1:chatgpt:1', 'query_id': 'q1', 'query_text': query,
         'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
         'provider': 'chatgpt', 'repetition': 1, 'status': 'pending'},
        {'id': 'q1:claude:1', 'query_id': 'q1', 'query_text': query,
         'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
         'provider': 'claude', 'repetition': 1, 'status': 'pending'},
    ]}


def result(job, session='https://chatgpt.com/c/example', citation_state='complete'):
    now = datetime.now(timezone.utc)
    return {'run_id': job['run_id'], 'slot_id': job['slot_id'],
            'dispatch_id': job['dispatch_id'], 'query_text': job['query_text'],
            'query_sha256': job['query_sha256'], 'provider': job['provider'],
            'observed_at': now.isoformat(), 'session_evidence': session,
            'raw_response_reference': 'capture://synthetic-answer-1',
            'conditions': {'product': job['provider'], 'model': 'model shown in UI',
                           'measurement_date': now.date().isoformat(), 'locale': 'en-US',
                           'web_search': True, 'session_mode': 'fresh'},
            'answer_complete': True, 'brand_mentioned': False,
            'citation_coverage': citation_state,
            'cited_urls': []}


class FakeBridge:
    def __init__(self):
        self.jobs = []

    def execute(self, job, queue_path):
        self.jobs.append(job)
        session = 'https://chatgpt.com/c/example'
        browser.mark_submitted(queue_path, job['slot_id'], job['dispatch_id'], session)
        return result(job, session)

    def reconcile(self, job, queue_path):
        self.jobs.append(job)
        self.session_seen = job['session_evidence']
        return result(job, 'session-1')

    def available(self):
        return True


class BrowserQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'protocol.json'
        self.queue = self.root / 'queue.json'
        self.source.write_text(json.dumps(protocol()), encoding='utf-8')

    def test_fake_bridge_executes_exact_query_and_resume_does_not_duplicate(self):
        browser.initialize(self.source, self.queue)
        bridge = FakeBridge()
        browser.dispatch_one(self.queue, bridge)
        self.assertEqual(bridge.jobs[0]['query_text'], protocol()['slots'][0]['query_text'])
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)
        browser.dispatch_one(self.queue, bridge)
        self.assertEqual(len(bridge.jobs), 2)
        self.assertEqual({job['slot_id'] for job in bridge.jobs},
                         {'q1:chatgpt:1', 'q1:claude:1'})
        self.assertIsNone(browser.dispatch_one(self.queue, bridge))
        self.assertEqual(len(bridge.jobs), 2)
        with self.assertRaises(ValueError):
            browser.initialize(self.source, self.queue)

    def test_claim_and_submitted_are_not_retried_after_resume(self):
        browser.initialize(self.source, self.queue)
        first = browser.claim_next(self.queue)
        second = browser.claim_next(self.queue)
        self.assertNotEqual(first['slot_id'], second['slot_id'])
        browser.mark_submitted(self.queue, second['slot_id'], second['dispatch_id'], 'session-2')
        self.assertIsNone(browser.claim_next(self.queue))
        counts = browser.status(self.queue)['counts']
        self.assertEqual((counts['claimed'], counts['submitted']), (1, 1))
        self.assertEqual(len(browser.status(self.queue)['needs_reconciliation']), 2)

    def test_reconcile_existing_submission_without_resending(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        bridge = FakeBridge()
        browser.reconcile_one(self.queue, bridge)
        self.assertEqual(bridge.jobs[0]['dispatch_id'], job['dispatch_id'])
        self.assertEqual(bridge.session_seen, 'session-1')
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)
        self.assertIsNone(browser.reconcile_one(self.queue, bridge))

    def test_select_bridge_requires_explicit_aside_capability(self):
        native, aside = FakeBridge(), FakeBridge()
        self.assertIs(browser.select_bridge(native, aside, aside_selected=True,
                                            aside_logged_in=True), native)
        self.assertIs(browser.select_bridge(None, aside, aside_selected=True,
                                            aside_logged_in=True), aside)
        with self.assertRaises(browser.BrowserBlocked):
            browser.select_bridge(None, aside, aside_selected=True, aside_logged_in=False)

    def test_invalid_result_never_completes_and_can_be_reconciled(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        observed = result(job, 'session-1')
        observed['query_sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            browser.record_result(self.queue, observed)
        self.assertEqual(browser.status(self.queue)['counts']['submitted'], 1)
        observed['query_sha256'] = job['query_sha256']
        browser.record_result(self.queue, observed)
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)
        browser.record_result(self.queue, observed)
        with self.assertRaises(ValueError):
            browser.record_result(self.queue, dict(observed, brand_mentioned=True))

    def test_partial_citations_remain_incomplete(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        partial = result(job, 'session-1', 'partial')
        browser.record_result(self.queue, partial)
        self.assertEqual(browser.status(self.queue)['counts']['incomplete'], 1)
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 0)
        browser.record_result(self.queue, dict(partial, citation_coverage='complete'))
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)

    def test_unfinished_answer_is_incomplete_and_private_citation_rejected(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        partial = result(job, 'session-1', 'unknown')
        partial['answer_complete'] = False
        partial['brand_mentioned'] = None
        browser.record_result(self.queue, partial)
        self.assertEqual(browser.status(self.queue)['counts']['incomplete'], 1)
        complete = result(job, 'session-1')
        complete['cited_urls'] = ['http://localhost/private']
        with self.assertRaises(ValueError):
            browser.record_result(self.queue, complete)
        complete['cited_urls'] = []
        browser.record_result(self.queue, complete)
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)

    def test_blocked_auth_does_not_consume_other_pending_slot(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_blocked(self.queue, job['slot_id'], job['dispatch_id'], 'auth', 'Sign-in required')
        self.assertEqual(browser.status(self.queue)['counts']['blocked'], 1)
        self.assertEqual(browser.claim_next(self.queue)['provider'], 'claude')

    def test_requeue_requires_explicit_no_submission_reconciliation(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        with self.assertRaises(ValueError):
            browser.requeue_unsubmitted(self.queue, job['slot_id'], job['dispatch_id'], 'yes')
        browser.requeue_unsubmitted(self.queue, job['slot_id'], job['dispatch_id'],
                                    'confirmed_not_submitted')
        renewed = browser.claim_next(self.queue)
        self.assertEqual(renewed['slot_id'], job['slot_id'])
        self.assertNotEqual(renewed['dispatch_id'], job['dispatch_id'])
        browser.mark_submitted(self.queue, renewed['slot_id'], renewed['dispatch_id'], 'session-1')
        with self.assertRaises(ValueError):
            browser.requeue_unsubmitted(self.queue, renewed['slot_id'], renewed['dispatch_id'],
                                        'confirmed_not_submitted')

    def test_protocol_validation_rejects_tampering_and_unsupported_ui(self):
        altered = protocol()
        altered['slots'][0]['query_sha256'] = '0' * 64
        self.source.write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaises(ValueError):
            browser.initialize(self.source, self.queue)
        self.assertFalse(self.queue.exists())
        altered = protocol()
        altered['slots'][0]['provider'] = 'unknown'
        self.source.write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaises(ValueError):
            browser.initialize(self.source, self.queue)

    def test_saved_queue_rejects_forged_identity_result_and_dispatch(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        browser.record_result(self.queue, result(job, 'session-1'))
        baseline = json.loads(self.queue.read_text(encoding='utf-8'))
        mutations = (
            lambda q: q['slots'][0].update(query_text='different'),
            lambda q: q['slots'][0]['result'].update(query_sha256='wrong'),
            lambda q: q['slots'][0]['result'].update(session_evidence='forged'),
            lambda q: q['slots'][0]['result'].update(answer_complete=False),
            lambda q: q['slots'][1].update(id=q['slots'][0]['id']),
            lambda q: q['slots'][0]['dispatch'].update(id=''),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                altered = json.loads(json.dumps(baseline))
                mutate(altered)
                self.queue.write_text(json.dumps(altered), encoding='utf-8')
                with self.assertRaises(ValueError):
                    browser.status(self.queue)
        self.queue.write_text(json.dumps(baseline), encoding='utf-8')
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)

    def test_reconcile_block_preserves_partial_result(self):
        browser.initialize(self.source, self.queue)
        job = browser.claim_next(self.queue)
        browser.mark_submitted(self.queue, job['slot_id'], job['dispatch_id'], 'session-1')
        browser.record_result(self.queue, result(job, 'session-1', 'partial'))

        class BlockedBridge(FakeBridge):
            def reconcile(self, job, queue_path):
                raise browser.BrowserBlocked('quota', 'Daily limit reached')

        browser.reconcile_one(self.queue, BlockedBridge())
        self.assertEqual(browser.status(self.queue)['counts']['blocked'], 1)
        saved = json.loads(self.queue.read_text(encoding='utf-8'))['slots'][0]
        self.assertEqual(saved['result']['citation_coverage'], 'partial')
        self.assertEqual(saved['block']['reason'], 'quota')
        browser.reconcile_one(self.queue, FakeBridge())
        self.assertEqual(browser.status(self.queue)['counts']['complete'], 1)
        saved = json.loads(self.queue.read_text(encoding='utf-8'))['slots'][0]
        self.assertIsNone(saved['block'])


if __name__ == '__main__':
    unittest.main()
