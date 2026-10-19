import hashlib
import importlib.util
from pathlib import Path
import shutil
import threading
import unittest
from urllib.parse import urlsplit
from unittest.mock import Mock, patch

import test_pdf_downloads as helpers
from myinvoices.pdf_downloads import PDFDownloader
from myinvoices.interactive_downloads import InteractivePDFDownloader
from myinvoices.automatic_downloads import AutomaticPDFDownloader
from myinvoices.browser_downloads import BrowserPDFDownloader


class RecoveryPersistenceTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)

    def test_printed_human_copy_retains_provenance_and_is_skipped(self):
        downloads=self.root/'Downloads';downloads.mkdir()
        for row in self.db.download_candidates(self.client.id):
            self.db.record_pdf(self.client.id,row['mark'],'challenge',hashlib.sha256(row['download_url'].encode()).hexdigest())
        count=[0]
        def browser(*args,**kwargs):
            count[0]+=1
            (downloads/f'printed-{count[0]}.pdf').write_bytes(self.pdf)
            return True
        service=InteractivePDFDownloader(self.db,downloads,browser=browser,confirm=lambda row,path:'printed',
                                         timeout=2,stable_seconds=0.01,poll_seconds=0.01)
        result=service.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(2,2))
        self.assertEqual({r['pdf_state'] for r in self.db.invoices(self.client.id)},{'printed_unverified'})
        again=PDFDownloader(self.db,helpers.Opener(b'unused')).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((again['skipped'],again['pending']),(2,0))

    def test_failed_redownload_of_deleted_file_is_not_a_success(self):
        PDFDownloader(self.db,helpers.Opener(self.pdf)).run(self.client,self.root/'archive',threading.Event())
        path=next((self.root/'archive').rglob('*.pdf'))
        mark=int(path.stem)
        path.unlink()
        result=PDFDownloader(self.db,helpers.Opener(b'%PDF-invalid')).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['skipped'],result['pending']),(1,1))
        self.assertEqual(self.db.count(self.client.id,bucket='success'),1)
        self.assertEqual([r['mark'] for r in self.db.invoices(self.client.id,bucket='failure')],[mark])


@unittest.skipUnless(importlib.util.find_spec('playwright') and shutil.which('chromium'),
                     'Optional recovery integration requires Playwright and system Chromium')
class AutomaticPrintPipelineTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)
        self.validation=patch('myinvoices.browser_downloads.validate_url')
        self.validation.start();self.addCleanup(self.validation.stop)
        with self.db.connection() as conn:
            conn.execute("UPDATE invoices SET download_url='https://provider.example/invoice/'||mark")

    def browser_factory(self,matching):
        class SyntheticBrowser(BrowserPDFDownloader):
            async def _new_context(inner):
                context=await super()._new_context()
                async def serve(route):
                    mark=urlsplit(route.request.url).path.rsplit('/',1)[-1]
                    if not mark.isdigit():
                        await route.abort();return
                    if matching:
                        text=(f'INVOICE MARK {mark}. Seller VAT 111111111. Customer VAT 000000000. '
                              f'Date 2026-10-01. Series DEMO Number {mark}. Total 124.12 EUR. '
                              + 'Detailed invoice line: consulting services, quantity one, net value 100.10 EUR. '*10)
                    else:
                        text='Welcome. This page contains no invoice document.'
                    await route.fulfill(status=200,content_type='text/html',body=f'<html><body><h1>Invoice</h1><main>{text}</main></body></html>')
                await context.route('**/*',serve)
                return context
        return lambda:SyntheticBrowser(self.db,headless=True,timeout=0.75,allow_print=True)

    def test_original_failure_then_print_success_classifies_and_skips(self):
        direct=PDFDownloader(self.db,helpers.Opener(b'<html>Dynamic invoice application</html>'))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=self.browser_factory(True)).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed'],result['pending']),(2,2,0))
        self.assertEqual(self.db.count(self.client.id,bucket='success'),2)
        self.assertEqual(self.db.count(self.client.id,bucket='failure'),0)
        never=Mock(side_effect=AssertionError('Printed success must not be downloaded again'))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=never).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['skipped'],2)
        never.assert_not_called()

    def test_both_attempts_fail_without_a_false_pdf(self):
        direct=PDFDownloader(self.db,helpers.Opener(b'<html>Dynamic application</html>'))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=self.browser_factory(False)).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['pending']),(0,2))
        self.assertEqual(self.db.count(self.client.id,bucket='failure'),2)
        self.assertFalse(list((self.root/'archive').rglob('*.pdf')))
