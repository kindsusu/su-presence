"""Durable browser measurement dispatch for an explicitly supplied browser host.

This module never logs in, acquires credentials, buys quota, or implements a browser.
The host owns the public product UI and implements BrowserBridge. A claimed job is
ambiguous after a crash and must be reconciled before any fresh submission.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from rebuild_protocol import SCHEMA as PROTOCOL_SCHEMA
from rebuild_evidence import public_url

SCHEMA = 'su-presence/rebuild-browser-queue/1'
MAX_BYTES = 4_000_000
MAX_TEXT = 100_000
MAX_SLOTS = 1000
LEASE_SECONDS = 1800

# These are host-facing instructions, not brittle DOM selectors. The bridge must
# verify what the selected public UI actually displays before recording results.
PRODUCTS = {
    'chatgpt': {'url': 'https://chatgpt.com/', 'new_session': 'Start a new chat',
                'completion': 'Wait until the answer stops generating',
                'citations': 'Inspect rendered source links and their final URLs'},
    'claude': {'url': 'https://claude.ai/new', 'new_session': 'Start a new chat',
               'completion': 'Wait until the answer stops generating',
               'citations': 'Inspect rendered source links if web search was used'},
    'gemini': {'url': 'https://gemini.google.com/app', 'new_session': 'Start a new chat',
               'completion': 'Wait until the answer stops generating',
               'citations': 'Inspect rendered source links and sources panel'},
    'perplexity': {'url': 'https://www.perplexity.ai/', 'new_session': 'Start a new thread',
                   'completion': 'Wait until the answer stops generating',
                   'citations': 'Inspect numbered citations and source links'},
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _bounded(value, name, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
        raise ValueError(f'{name} must be nonempty UTF-8 text of at most {limit} bytes')
    return value


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular JSON file')
    with path.open('rb') as file:
        payload = file.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('JSON file exceeds size limit')
    obj = json.loads(payload.decode('utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('Expected a JSON object')
    return obj, hashlib.sha256(payload).hexdigest()


def _write(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError('Queue path must be in a regular existing directory')
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(encoded) > MAX_BYTES:
        raise ValueError('Queue exceeds size limit')
    fd, temporary = tempfile.mkstemp(prefix='.browser-queue-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _Lock:
    def __init__(self, path):
        self.path = Path(str(path) + '.lock')

    def __enter__(self):
        if self.path.is_symlink():
            raise ValueError('Queue lock is a symbolic link')
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError('Queue is locked; retry later or inspect the lock')
        with os.fdopen(fd, 'w', encoding='ascii') as file:
            file.write(f'{os.getpid()} {_now()}\n')
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)


def _queue(path):
    queue, _ = _read(path)
    if queue.get('schema') != SCHEMA or not isinstance(queue.get('slots'), list):
        raise ValueError('Unsupported browser queue')
    if not 0 < len(queue['slots']) <= MAX_SLOTS:
        raise ValueError('Invalid queue length')
    _bounded(queue.get('run_id'), 'run ID', 64)
    _bounded(queue.get('protocol_sha256'), 'protocol digest', 64)
    source, digest = _read(_bounded(queue.get('protocol_path'), 'protocol path', 4096))
    if digest != queue['protocol_sha256'] or source.get('schema') != PROTOCOL_SCHEMA:
        raise ValueError('Queue protocol provenance mismatch')
    source_slots = source.get('slots')
    if not isinstance(source_slots, list) or len(source_slots) != len(queue['slots']):
        raise ValueError('Queue slot count differs from protocol')
    identities, dispatch_ids = set(), set()
    for slot, original in zip(queue['slots'], source_slots):
        if not isinstance(slot, dict) or not isinstance(original, dict):
            raise ValueError('Invalid saved slot')
        slot_id = _bounded(slot.get('id'), 'slot ID', 256)
        if slot_id in identities:
            raise ValueError('Duplicate saved slot')
        identities.add(slot_id)
        for field in ('id', 'query_id', 'query_text', 'query_sha256', 'provider', 'repetition'):
            if slot.get(field) != original.get(field):
                raise ValueError(f'Saved slot {field} differs from protocol')
        query = _bounded(slot.get('query_text'), 'query')
        if (slot.get('provider') not in PRODUCTS or
                slot.get('query_sha256') != hashlib.sha256(query.encode('utf-8')).hexdigest() or
                type(slot.get('repetition')) is not int or slot['repetition'] < 1 or
                type(slot.get('attempts')) is not int or slot['attempts'] < 0):
            raise ValueError('Invalid saved slot identity or attempts')
        state = slot.get('status')
        if state not in ('pending', 'claimed', 'submitted', 'incomplete', 'complete', 'blocked'):
            raise ValueError('Invalid saved slot state')
        dispatch = slot.get('dispatch')
        if state == 'pending':
            if dispatch is not None or slot.get('submitted_at') is not None or slot.get('result') is not None or slot.get('block') is not None:
                raise ValueError('Pending slot contains active evidence')
            continue
        if slot['attempts'] < 1 or not isinstance(dispatch, dict):
            raise ValueError('Active slot requires a dispatch')
        dispatch_id = _bounded(dispatch.get('id'), 'dispatch ID', 64)
        if dispatch_id in dispatch_ids:
            raise ValueError('Duplicate saved dispatch')
        dispatch_ids.add(dispatch_id)
        for key in ('claimed_at', 'lease_until'):
            _timestamp(dispatch.get(key), key)
        submitted = slot.get('submitted_at')
        if submitted is None:
            if state not in ('claimed', 'blocked') or 'session_evidence' in dispatch:
                raise ValueError('Unsubmitted slot contains submission evidence')
        else:
            _timestamp(submitted, 'submitted_at')
            _bounded(dispatch.get('session_evidence'), 'session evidence', 2048)
            if state == 'claimed':
                raise ValueError('Submitted slot cannot remain claimed')
        block = slot.get('block')
        if state == 'blocked':
            if not isinstance(block, dict) or block.get('reason') not in ('auth', 'quota', 'browser_unavailable', 'ui_changed', 'other'):
                raise ValueError('Invalid saved block')
            _bounded(block.get('detail'), 'block detail', 2048)
            _timestamp(block.get('at'), 'block time')
        elif block is not None:
            raise ValueError('Nonblocked slot contains a block')
        observed = slot.get('result')
        if observed is None:
            if state in ('complete', 'incomplete'):
                raise ValueError('Observed slot lacks result')
        else:
            if state not in ('complete', 'incomplete', 'blocked') or submitted is None:
                raise ValueError('Result is attached to an unsubmitted slot')
            job = _job(queue, slot)
            _result(job, observed)
            if observed['session_evidence'] != dispatch['session_evidence']:
                raise ValueError('Saved result session mismatch')
            is_complete = observed['answer_complete'] and observed['citation_coverage'] == 'complete'
            if state == 'complete' and not is_complete or state == 'incomplete' and is_complete:
                raise ValueError('Saved result completion mismatch')
    return queue


def _timestamp(value, name):
    text = _bounded(value, name, 64)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone missing')
    except ValueError as exc:
        raise ValueError(f'{name} needs timezone') from exc
    return parsed


def initialize(protocol_path, queue_path):
    """Create a queue once from immutable protocol slots."""
    protocol, digest = _read(protocol_path)
    if protocol.get('schema') != PROTOCOL_SCHEMA or not isinstance(protocol.get('slots'), list):
        raise ValueError('Expected rebuild protocol slots')
    if not 0 < len(protocol['slots']) <= MAX_SLOTS:
        raise ValueError('Protocol must contain 1..1000 slots')
    ids, slots = set(), []
    for source in protocol['slots']:
        if not isinstance(source, dict):
            raise ValueError('Invalid protocol slot')
        slot_id = _bounded(source.get('id'), 'slot ID', 256)
        query = _bounded(source.get('query_text'), 'query')
        provider = source.get('provider')
        if slot_id in ids or provider not in PRODUCTS or source.get('status') != 'pending':
            raise ValueError('Duplicate, unsupported, or nonpending protocol slot')
        ids.add(slot_id)
        if source.get('query_sha256') != hashlib.sha256(query.encode('utf-8')).hexdigest():
            raise ValueError('Protocol query hash mismatch')
        slots.append({'id': slot_id, 'query_id': _bounded(source.get('query_id'), 'query ID', 256),
                      'query_text': query, 'query_sha256': source['query_sha256'],
                      'provider': provider, 'repetition': source.get('repetition'),
                      'status': 'pending', 'attempts': 0, 'dispatch': None,
                      'submitted_at': None, 'result': None, 'block': None})
        if type(source.get('repetition')) is not int or source['repetition'] < 1:
            raise ValueError('Invalid repetition')
    path = Path(queue_path)
    with _Lock(path):
        if path.exists() or path.is_symlink():
            raise ValueError('Queue already exists')
        queue = {'schema': SCHEMA, 'run_id': str(uuid.uuid4()), 'protocol_sha256': digest,
                 'protocol_path': str(Path(protocol_path).resolve()), 'created_at': _now(),
                 'slots': slots}
        _write(path, queue)
    return queue


def _job(queue, slot):
    return {'run_id': queue['run_id'], 'slot_id': slot['id'],
            'dispatch_id': slot['dispatch']['id'], 'query_id': slot['query_id'],
            'query_text': slot['query_text'], 'query_sha256': slot['query_sha256'],
            'provider': slot['provider'], 'repetition': slot['repetition'],
            'product': PRODUCTS[slot['provider']],
            'session_evidence': slot['dispatch'].get('session_evidence'),
            'lease_until': slot['dispatch']['lease_until']}


def claim_next(queue_path):
    """Claim one pending slot. Expired claims stay claimed for reconciliation."""
    with _Lock(queue_path):
        queue = _queue(queue_path)
        for slot in queue['slots']:
            if slot['status'] == 'pending':
                slot['status'] = 'claimed'
                slot['attempts'] += 1
                slot['dispatch'] = {'id': str(uuid.uuid4()), 'claimed_at': _now(),
                                    'lease_until': datetime.fromtimestamp(time.time() + LEASE_SECONDS,
                                                                          timezone.utc).isoformat(timespec='seconds')}
                _write(queue_path, queue)
                return _job(queue, slot)
    return None


def _selected(queue, slot_id, dispatch_id):
    slot = next((row for row in queue['slots'] if row['id'] == slot_id), None)
    if slot is None or not slot['dispatch'] or slot['dispatch']['id'] != dispatch_id:
        raise ValueError('Unknown slot or stale dispatch')
    return slot


def mark_submitted(queue_path, slot_id, dispatch_id, session_evidence):
    """Host calls this immediately after submitting the exact query in a fresh UI session."""
    _bounded(session_evidence, 'session evidence', 2048)
    with _Lock(queue_path):
        queue = _queue(queue_path)
        slot = _selected(queue, slot_id, dispatch_id)
        if slot['status'] == 'submitted' and slot['dispatch'].get('session_evidence') == session_evidence:
            return slot
        if slot['status'] != 'claimed':
            raise ValueError('Slot is not claimed')
        slot['status'] = 'submitted'
        slot['submitted_at'] = _now()
        slot['dispatch']['session_evidence'] = session_evidence
        _write(queue_path, queue)
        return slot


def _result(job, result):
    if not isinstance(result, dict):
        raise ValueError('Result must be an object')
    for key in ('run_id', 'slot_id', 'dispatch_id', 'query_sha256', 'provider'):
        if result.get(key) != job[key]:
            raise ValueError(f'Result {key} does not match dispatch')
    if result.get('query_text') != job['query_text']:
        raise ValueError('Result must preserve exact query')
    conditions = result.get('conditions')
    if not isinstance(conditions, dict) or conditions.get('product') != job['provider']:
        raise ValueError('Result product mismatch')
    for key in ('model', 'locale'):
        _bounded(conditions.get(key), key, 256)
    if type(conditions.get('web_search')) is not bool or conditions.get('session_mode') != 'fresh':
        raise ValueError('Record actual web search setting and fresh session')
    parsed = _timestamp(result.get('observed_at'), 'observed_at')
    if conditions.get('measurement_date') != parsed.date().isoformat():
        raise ValueError('Measurement date mismatch')
    _bounded(result.get('session_evidence'), 'session evidence', 2048)
    _bounded(result.get('raw_response_reference'), 'raw response reference', 2048)
    answer_complete = result.get('answer_complete')
    if type(answer_complete) is not bool:
        raise ValueError('answer_complete must be observed boolean')
    if (answer_complete and type(result.get('brand_mentioned')) is not bool or
            not answer_complete and result.get('brand_mentioned') is not None):
        raise ValueError('brand_mentioned must be boolean only after answer completion')
    citation_state = result.get('citation_coverage')
    if citation_state not in ('complete', 'partial', 'unknown'):
        raise ValueError('citation_coverage must be complete, partial, or unknown')
    if not answer_complete and citation_state == 'complete':
        raise ValueError('Citation coverage cannot be complete before the answer completes')
    citations = result.get('cited_urls')
    if not isinstance(citations, list) or len(citations) > 100:
        raise ValueError('cited_urls must be a bounded list')
    for url in citations:
        _bounded(url, 'citation URL', 2048)
        if not public_url(url):
            raise ValueError('Citation must be a public HTTP URL')
    if citation_state != 'complete' and not citations:
        # Unknown citations remain unknown; never infer an absent citation.
        pass
    return result


def record_result(queue_path, result):
    """Store a completed or citation-incomplete observation after submission."""
    if not isinstance(result, dict):
        raise ValueError('Result must be an object')
    with _Lock(queue_path):
        queue = _queue(queue_path)
        slot = _selected(queue, result.get('slot_id'), result.get('dispatch_id'))
        job = _job(queue, slot)
        clean = _result(job, result)
        if slot['status'] in ('complete', 'incomplete') and slot['result'] == clean:
            return slot
        if slot['status'] not in ('submitted', 'incomplete', 'blocked') or slot['submitted_at'] is None:
            raise ValueError('Result requires submitted slot')
        if clean['session_evidence'] != slot['dispatch']['session_evidence']:
            raise ValueError('Session evidence mismatch')
        slot['result'] = clean
        slot['status'] = 'complete' if (clean['answer_complete'] and
                                       clean['citation_coverage'] == 'complete') else 'incomplete'
        slot['block'] = None
        _write(queue_path, queue)
        return slot


def mark_blocked(queue_path, slot_id, dispatch_id, reason, detail):
    """Record UI auth/quota/capability blocks without impersonating an observation."""
    if reason not in ('auth', 'quota', 'browser_unavailable', 'ui_changed', 'other'):
        raise ValueError('Unsupported block reason')
    _bounded(detail, 'block detail', 2048)
    with _Lock(queue_path):
        queue = _queue(queue_path)
        slot = _selected(queue, slot_id, dispatch_id)
        if slot['status'] not in ('claimed', 'submitted', 'incomplete', 'blocked'):
            raise ValueError('Only active slots can be blocked')
        slot['status'] = 'blocked'
        slot['block'] = {'reason': reason, 'detail': detail, 'at': _now()}
        _write(queue_path, queue)
        return slot


def requeue_unsubmitted(queue_path, slot_id, dispatch_id, confirmation):
    """Reconcile a crashed claim only after host confirms no query was submitted.

    Submitted and observed slots can never be requeued; they require result
    ingestion or a recorded block. The confirmation is an auditable assertion.
    """
    if confirmation != 'confirmed_not_submitted':
        raise ValueError('Explicit confirmed_not_submitted reconciliation required')
    with _Lock(queue_path):
        queue = _queue(queue_path)
        slot = _selected(queue, slot_id, dispatch_id)
        if slot['status'] not in ('claimed', 'blocked') or slot['submitted_at'] is not None:
            raise ValueError('Only unsubmitted claims can be requeued')
        slot['status'] = 'pending'
        slot['reconciled_at'] = _now()
        slot['dispatch'] = None
        slot['block'] = None
        _write(queue_path, queue)
        return slot


def status(queue_path):
    queue = _queue(queue_path)
    counts = {name: sum(row['status'] == name for row in queue['slots']) for name in
              ('pending', 'claimed', 'submitted', 'incomplete', 'complete', 'blocked')}
    return {'run_id': queue['run_id'], 'counts': counts,
            'needs_reconciliation': [row['id'] for row in queue['slots'] if row['status'] in
                                     ('claimed', 'submitted', 'incomplete', 'blocked')]}


class BrowserBridge(Protocol):
    """Explicit host capability. execute must call mark_submitted before returning a result.

    Host submits the exact query once, polls answer completion, inspects citations,
    and provides verifiable raw response and session references. It may return None
    after submission for later record_result ingestion, or raise BrowserBlocked.
    """
    def execute(self, job: dict, queue_path: Path) -> dict | None: ...

    def reconcile(self, job: dict, queue_path: Path) -> dict | None: ...


class BrowserBlocked(Exception):
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, detail
        super().__init__(detail)


def select_bridge(native=None, aside=None, *, aside_selected=False, aside_logged_in=False):
    """Prefer an available native browser; use Aside only when explicitly selected.

    The caller supplies browser-capable bridge objects. No credentials, tokens,
    or browser sessions are discovered or transferred by this function.
    """
    if native is not None and native.available():
        return native
    if (aside_selected and aside_logged_in and aside is not None and aside.available()):
        return aside
    raise BrowserBlocked('browser_unavailable', 'No selected browser-capable host is available')


def dispatch_one(queue_path, bridge: BrowserBridge):
    """Claim and execute one job with a browser-capable host; never resubmit claimed work."""
    job = claim_next(queue_path)
    if job is None:
        return None
    try:
        result = bridge.execute(job, Path(queue_path))
    except BrowserBlocked as exc:
        mark_blocked(queue_path, job['slot_id'], job['dispatch_id'], exc.reason, exc.detail)
        return status(queue_path)
    if result is not None:
        record_result(queue_path, result)
    return status(queue_path)


def reconcile_one(queue_path, bridge: BrowserBridge):
    """Poll one previously submitted session through its original host, without a resend.

    The bridge must reopen the saved session evidence and may return a revised
    result. Claimed slots lack proof of submission and require manual audit.
    """
    queue = _queue(queue_path)
    slot = next((row for row in queue['slots'] if row['status'] in ('submitted', 'incomplete') or
                 row['status'] == 'blocked' and row['submitted_at'] is not None), None)
    if slot is None:
        return None
    job = _job(queue, slot)
    try:
        result = bridge.reconcile(job, Path(queue_path))
    except BrowserBlocked as exc:
        mark_blocked(queue_path, job['slot_id'], job['dispatch_id'], exc.reason, exc.detail)
        return status(queue_path)
    if result is not None:
        record_result(queue_path, result)
    return status(queue_path)


def _cli(args):
    if args.browser_command == 'init':
        value = initialize(args.protocol, args.queue)
    elif args.browser_command == 'next':
        value = claim_next(args.queue)
    elif args.browser_command == 'submit':
        value = mark_submitted(args.queue, args.slot, args.dispatch, args.session_evidence)
    elif args.browser_command == 'record':
        value, _ = _read(args.result)
        value = record_result(args.queue, value)
    elif args.browser_command == 'blocked':
        value = mark_blocked(args.queue, args.slot, args.dispatch, args.reason, args.detail)
    elif args.browser_command == 'requeue':
        value = requeue_unsubmitted(args.queue, args.slot, args.dispatch, args.confirmation)
    else:
        value = status(args.queue)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def add_parsers(sub):
    browser = sub.add_parser('browser', help='Durable public UI measurement queue for an external browser host')
    commands = browser.add_subparsers(dest='browser_command', required=True)
    p = commands.add_parser('init'); p.add_argument('protocol'); p.add_argument('queue'); p.set_defaults(func=_cli)
    for name in ('next', 'status'):
        p = commands.add_parser(name); p.add_argument('queue'); p.set_defaults(func=_cli)
    p = commands.add_parser('submit'); p.add_argument('queue'); p.add_argument('slot')
    p.add_argument('dispatch'); p.add_argument('session_evidence'); p.set_defaults(func=_cli)
    p = commands.add_parser('record'); p.add_argument('queue'); p.add_argument('result'); p.set_defaults(func=_cli)
    p = commands.add_parser('blocked'); p.add_argument('queue'); p.add_argument('slot'); p.add_argument('dispatch')
    p.add_argument('reason', choices=('auth', 'quota', 'browser_unavailable', 'ui_changed', 'other'))
    p.add_argument('detail'); p.set_defaults(func=_cli)
    p = commands.add_parser('requeue'); p.add_argument('queue'); p.add_argument('slot'); p.add_argument('dispatch')
    p.add_argument('confirmation', choices=('confirmed_not_submitted',)); p.set_defaults(func=_cli)
