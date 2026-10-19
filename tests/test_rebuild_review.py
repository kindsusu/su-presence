import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rebuild_review as review


class ContentReviewTests(unittest.TestCase):
    def example(self):
        return {'schema': review.INPUT_SCHEMA, 'identity': {'name': 'Acme  Co', 'category': 'Software'},
                'profiles': [{'url': 'https://profile.example.org/acme', 'name': 'acme co',
                              'category': 'Consulting'}],
                'pages': [{'id': 'guide-1', 'url': 'https://example.com/guide',
                           'intent': 'Choose a service',
                           'updated_at': '2026-10-02',
                           'body': '# What is included?\nThe service includes listed steps with a source.\n'
                                   '## Comparison\nA versus B has limitations.\n',
                           'claims': [{'text': 'The service includes listed steps with a source.',
                                       'source_url': 'https://sources.example.org/terms',
                                       'quote': 'listed steps', 'source_text': 'The listed steps are described here.',
                                       'source_date': '2026-10-01'}]}]}

    def test_literal_matches_and_human_review_boundary(self):
        output = review.review(self.example(), 'a' * 64)
        self.assertEqual(output['status'], 'human_review_required')
        checks = output['pages'][0]['checks']
        self.assertIn('literal_quote_match', [row['code'] for row in checks])
        self.assertIn('question_answer', [row['code'] for row in output['pages'][0]['manual_review']])
        self.assertIn('comparison_balance', [row['code'] for row in output['pages'][0]['manual_review']])
        self.assertEqual(output['identity_checks'][0]['state'], 'literal_match')
        self.assertEqual(output['identity_checks'][1]['state'], 'review')
        self.assertNotIn('source_text', json.dumps(output))

    def test_missing_claim_evidence_and_dates_are_flagged(self):
        obj = self.example()
        obj['pages'][0]['body'] = '# Draft\nTODO: Finish.\n- [ ] check source\n'
        obj['pages'][0]['claims'][0].update(quote='other', source_date='2026-02-30')
        output = review.review(obj)
        codes = [row['code'] for row in output['pages'][0]['checks']]
        self.assertTrue({'placeholder', 'unchecked_task', 'claim_absent',
                         'quote_absent', 'source_date_invalid'} <= set(codes))
        del obj['pages'][0]['claims'][0]['source_date']
        self.assertIn('source_date_missing', [r['code'] for r in review.review(obj)['pages'][0]['checks']])

    def test_missing_identity_is_unknown(self):
        obj = self.example()
        del obj['identity']['category']
        self.assertEqual(review.review(obj)['identity_checks'][1]['state'], 'unknown')
        obj['identity']['name'] = '   '
        self.assertEqual(review.review(obj)['identity_checks'][0]['state'], 'unknown')

    def test_page_date_claim_coverage_and_korean_placeholders(self):
        obj = self.example()
        del obj['pages'][0]['updated_at']
        obj['pages'][0]['claims'] = []
        obj['pages'][0]['body'] = '서비스 설명 [확인 필요] 작성 필요'
        result = review.review(obj)['pages'][0]
        self.assertIn('page_date_missing', [row['code'] for row in result['checks']])
        self.assertIn('placeholder', [row['code'] for row in result['checks']])
        self.assertIn('evidence_coverage', [row['code'] for row in result['manual_review']])
        self.assertIn('visible_date_author', [row['code'] for row in result['manual_review']])
        obj['pages'][0]['updated_at'] = '2026-02-30'
        self.assertIn('page_date_invalid', [row['code'] for row in review.review(obj)['pages'][0]['checks']])

    def test_reports_claim_and_profile_context_without_source_text(self):
        result = review.review(self.example())
        self.assertEqual(result['pages'][0]['claims'][0]['source_date'], '2026-10-01')
        self.assertEqual(result['identity_checks'][1]['expected'], 'Software')
        self.assertEqual(result['identity_checks'][1]['supplied'], 'Consulting')
        for report in (review.markdown(result), review.render_html(result)):
            self.assertIn('claim-1', report)
            self.assertIn('https://sources.example.org/terms', report)
            self.assertIn('Software', report)
            self.assertIn('Consulting', report)
            self.assertNotIn('The listed steps are described here.', report)

    def test_rejects_invalid_shape_and_private_url(self):
        obj = self.example()
        obj['pages'][0]['url'] = 'http://127.0.0.1/private'
        with self.assertRaisesRegex(ValueError, 'public HTTP URL'):
            review.validate_input(obj)
        obj = self.example()
        obj['pages'][0]['claims'] = 'not a list'
        with self.assertRaisesRegex(ValueError, 'claims'):
            review.validate_input(obj)
        obj = self.example()
        obj['pages'].append(dict(obj['pages'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate page id'):
            review.validate_input(obj)

    def test_rejects_oversize_input_before_parse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.json'
            path.write_bytes(b' ' * (review.MAX_FILE + 1))
            with self.assertRaisesRegex(ValueError, '2 MB'):
                review.load_input(path)

    def test_escapes_untrusted_html(self):
        obj = self.example()
        obj['pages'][0]['id'] = '<script>alert(1)</script>'
        obj['pages'][0]['intent'] = '<img src=x onerror=alert(1)>'
        output = review.review(obj)
        page = review.render_html(output)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('Content-Security-Policy', page)

    def test_command_creates_distinct_runs(self):
        class Args:
            pass
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'copy.json'
            source.write_text(json.dumps(self.example()), encoding='utf-8')
            args = Args()
            args.input = str(source)
            args.out = str(Path(folder) / 'reviews')
            self.assertEqual(review.command(args), 0)
            self.assertEqual(review.command(args), 0)
            runs = list(Path(args.out).glob('run-*'))
            self.assertEqual(len(runs), 2)
            self.assertTrue(all((run / 'review.json').is_file() and
                                (run / 'review.md').is_file() and
                                (run / 'review.html').is_file() for run in runs))


if __name__ == '__main__':
    unittest.main()
