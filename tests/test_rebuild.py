import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rebuild
import measure
from test_rebuild_plan import inputs

class RebuildIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit, self.brief, self.research = inputs()
        self.brief['queries'][0]['text'] = '<script>alert(1)</script>'
        for name, obj in [('audit', self.audit), ('brief', self.brief), ('research', self.research)]:
            self.write(name, obj)

    def write(self, name, obj):
        path = self.root / (name + '.json')
        path.write_text(json.dumps(obj), encoding='utf-8')
        return str(path)

    def args(self):
        return ['plan', str(self.root/'audit.json'), '--brief', str(self.root/'brief.json'),
                '--research', str(self.root/'research.json'), '--out', str(self.root/'out')]

    def test_offline_end_to_end_preserves_runs_and_escapes_html(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')):
            self.assertEqual(rebuild.main(self.args()), 0)
            self.assertEqual(rebuild.main(self.args()), 0)
        runs = list((self.root/'out').iterdir())
        self.assertEqual(len(runs), 2)
        for run in runs:
            plan = json.loads((run/'plan.json').read_text(encoding='utf-8'))
            ids = {e['id'] for e in plan['evidence']}
            for page in plan['page_blueprints']:
                self.assertTrue(set(page['evidence_ids']).issubset(ids))
            self.assertEqual(len(list((run/'pages').glob('*.json'))),1)
            self.assertEqual(len(list((run/'pages').glob('*.md'))),1)
            draft = next((run/'pages').glob('*.html')).read_text(encoding='utf-8')
            self.assertNotIn('<script>',draft)
            self.assertIn('noindex',draft)
            self.assertNotIn('<script>', (run/'report.html').read_text(encoding='utf-8'))
            self.assertIn('sha256',plan['input_provenance']['brief'])

    def test_dispatch_subprocess(self):
        script = Path(__file__).resolve().parents[1]/'tools'/'seo_geo.py'
        proc = subprocess.run([sys.executable,str(script),'rebuild']+self.args(),capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)

    def test_invalid_urls_and_timezone_and_query_rejected(self):
        row = dict(id='o1', query_id='q1', query_text=self.brief['queries'][0]['text'], channel='google',
                   outcome='observed',observed_at='2026-10-01T00:00:00Z',url='https://rival.com/a',evidence_url='https://rival.com/a')
        for key,value in [('url','http://127.0.0.1'),('evidence_url','javascript:alert(1)'),('observed_at','2026-10-01'),('query_text','old question')]:
            with self.subTest(key=key):
                path=self.write('obs',dict(schema=rebuild.SCHEMA_OBS,observations=[dict(row,**{key:value})]))
                with self.assertRaises(ValueError): rebuild.observations(path,self.brief)

    def test_invalid_research_url(self):
        self.research['pages'][0]['url']='http://localhost'
        with self.assertRaises(ValueError): rebuild.load_research(self.write('research',self.research))

    def test_malformed_research_shapes(self):
        for key, value in [('headings', 123), ('features', {'table': 'false'}), ('h1', [123])]:
            with self.subTest(key=key):
                bad = dict(self.research, pages=[dict(self.research['pages'][0], **{key:value})])
                with self.assertRaises(ValueError): rebuild.load_research(self.write('bad-research', bad))

    def test_cross_site_and_empty_queries_fail_before_writes(self):
        self.brief['site_url']='https://elsewhere.com'
        self.write('brief',self.brief)
        self.assertEqual(rebuild.main(self.args()),2)
        self.brief['site_url']=self.audit['target']['base']; self.brief['queries']=[]
        self.write('brief',self.brief)
        self.assertEqual(rebuild.main(self.args()),2)
        self.assertFalse((self.root/'out').exists())

    def test_measurement_fingerprint_api_and_bad_citation(self):
        q=self.brief['queries'][0]
        base=dict(query_id=q['id'],query_fingerprint=measure.query_fingerprint(q),outcome='observed',
                  mode='manual',surface='google',date='2026-10-01',cited_urls=['https://rival.com/a','http://localhost'])
        log=self.root/'log.jsonl'; log.touch()
        with patch.object(measure,'load_log',return_value=[base,dict(base,mode='api'),dict(base,query_fingerprint='old')]):
            rows,warnings=rebuild.from_log(log,self.brief)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['url'],'https://rival.com/a')
        self.assertGreaterEqual(len(warnings),3)

    def test_init_no_overwrite(self):
        args=['init',str(self.root/'audit.json'),'--out',str(self.root/'init')]
        self.assertEqual(rebuild.main(args),0)
        p=self.root/'init'/'brief.json'; before=p.read_bytes()
        self.assertEqual(rebuild.main(args),2)
        self.assertEqual(p.read_bytes(),before)

    def test_bounded_research_and_partial_exit(self):
        with patch.object(rebuild.evidence,'fetch_page',return_value=dict(outcome='blocked',url='https://rival.com')) as fetch:
            code=rebuild.main(['research',str(self.root/'brief.json'),'--out',str(self.root/'research-out'),'--max-pages','1'])
        self.assertEqual(code,2); self.assertEqual(fetch.call_count,1)
        result=json.loads(next((self.root/'research-out').glob('*/research.json')).read_text(encoding='utf-8'))
        self.assertFalse(result['coverage']['complete'])

if __name__=='__main__': unittest.main()
