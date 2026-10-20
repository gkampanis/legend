import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
from myinvoices.presentation import reason_group, guidance, display_amount, display_date, PROVENANCE
from myinvoices.diagnostics import record_failure
from myinvoices.batch import BatchProgress
import test_pdf_downloads as helpers


class DomainTests(unittest.TestCase):
    def test_reason_groups_and_next_actions_preserve_provenance(self):
        expected={'challenge':'ACCESS_MANUAL','http_403':'ACCESS_MANUAL','http_503':'NETWORK_RETRYABLE',
                  'network_error':'NETWORK_RETRYABLE','print_viewer_unsupported':'UNSUPPORTED_VIEW',
                  'artifact_missing':'MISSING_LOCAL_FILE','storage_error':'OTHER'}
        for state,group in expected.items():
            self.assertEqual(reason_group(state),group)
            title,action=guidance(dict(pdf_state=state,download_url='https://synthetic.example',pdf_attempted=1))
            self.assertTrue(title);self.assertTrue(action)
        self.assertEqual(reason_group(None,False),'NO_URL')
        self.assertEqual(len(set(PROVENANCE.values())),3)
        for state in PROVENANCE:self.assertEqual(reason_group(state),'')

    def test_greek_display_does_not_change_raw_values(self):
        self.assertEqual(display_date('2026-10-01'),'01/10/2026')
        self.assertEqual(display_amount('1234.5'),'1.234,50 €')
        self.assertEqual(display_amount('NaN'),'—')

    def test_pdf_diagnostic_stages_are_not_lost_and_secrets_are_absent(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log'
            for stage in ('pdf','pdf_import','backup','export'):
                record_failure(path,ValueError('SYNTHETIC-SECRET'),stage)
            records=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['stage'] for r in records],['pdf','pdf_import','backup','export'])
            self.assertNotIn('SYNTHETIC-SECRET',path.read_text())

    def test_support_export_keeps_pdf_stage_without_personal_values(self):
        from myinvoices.diagnostics import export_report
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            (path/'diagnostics.jsonl').write_text(json.dumps(dict(stage='pdf',http_status=403,url='SYNTHETIC-SECRET',exception_type='SYNTHETIC-SECRET'))+'\n')
            self.assertEqual(export_report(path,path/'report.json'),1)
            report=json.loads((path/'report.json').read_text())
            self.assertEqual(report['diagnostics'],[{'stage':'pdf','http_status':403}])
            self.assertNotIn('SYNTHETIC-SECRET',(path/'report.json').read_text())

    def test_unknown_total_is_explicit(self):
        self.assertIn('σύνολο άγνωστο',BatchProgress('ΑΑΔΕ',2,None).text())


class ProgressTests(unittest.TestCase):
    def setUp(self):helpers.PDFTests.setUp(self)

    def test_saved_skipped_and_cancelled_counts_come_from_committed_work(self):
        from myinvoices.pdf_downloads import PDFDownloader
        service=PDFDownloader(self.db,helpers.Opener(self.pdf))
        events=[];service.observer=events.append
        result=service.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((events[-1].completed,events[-1].saved,events[-1].printed),(2,2,0))
        self.assertEqual(result['saved'],2)
        events.clear();service.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(events[-1].skipped,2)
        cancel=threading.Event();cancel.set();events.clear()
        result=service.run(self.client,self.root/'archive',cancel)
        self.assertTrue(result['cancelled']);self.assertEqual(events[-1].completed,0)
