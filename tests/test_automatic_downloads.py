from contextlib import contextmanager
import threading
import unittest
from unittest.mock import Mock

import test_pdf_downloads as pdf_helpers
from myinvoices.automatic_downloads import AutomaticPDFDownloader
from myinvoices.pdf_downloads import PDFDownloader, Pending
from myinvoices.models import ApplicationError


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        pdf_helpers.PDFTests.setUp(self)

    def test_page_fallback_records_one_final_result_and_shares_session(self):
        from myinvoices.demo import xml_invoice, xml_page
        from myinvoices.xml_parser import parse_response
        self.db.commit_page(self.db.checkpoint(self.client.id),parse_response(xml_page([xml_invoice(103)])))
        class MixedOpener:
            def __init__(inner): inner.calls=0
            def open(inner,request,timeout):
                inner.calls+=1
                return pdf_helpers.Response(self.pdf if inner.calls==1 else b'<html>dynamic application</html>')
        direct=PDFDownloader(self.db,MixedOpener())
        browser=PDFDownloader(self.db,pdf_helpers.Opener(self.pdf))
        opened=[]
        @contextmanager
        def session():
            opened.append('open')
            try: yield browser
            finally: opened.append('close')
        browser.session=session
        factory=Mock(return_value=browser)
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=factory).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['pending']),(3,0))
        self.assertEqual(dict(result['reasons']),{})
        factory.assert_called_once()
        self.assertEqual(opened,['open','close'])
        self.assertEqual({r['pdf_state'] for r in self.db.invoices(self.client.id)},{'saved_unverified'})
        self.assertEqual(len(list((self.root/'archive').rglob('*.pdf'))),3)

    def test_direct_success_does_not_start_browser(self):
        factory=Mock(side_effect=AssertionError('Browser should not start'))
        direct=PDFDownloader(self.db,pdf_helpers.Opener(self.pdf))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=factory).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
        factory.assert_not_called()

    def test_missing_browser_is_pending_without_repeated_startup(self):
        factory=Mock(side_effect=ApplicationError('Synthetic missing browser'))
        direct=PDFDownloader(self.db,pdf_helpers.Opener(b'<html>application</html>'))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=factory).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['pending']),(0,2))
        self.assertEqual(dict(result['reasons']),{'browser_unavailable':2})
        factory.assert_called_once()

    def test_malformed_pdf_does_not_trigger_unrelated_browser_retry(self):
        factory=Mock(side_effect=AssertionError('Browser should not start'))
        direct=PDFDownloader(self.db,pdf_helpers.Opener(b'%PDF-invalid'))
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=factory).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['pending'],2)
        self.assertEqual(dict(result['reasons']),{'invalid_pdf':2})
        factory.assert_not_called()

    def test_known_challenge_is_skipped_without_request_or_browser(self):
        import hashlib
        for row in self.db.download_candidates(self.client.id):
            self.db.record_pdf(self.client.id,row['mark'],'challenge',hashlib.sha256(row['download_url'].encode()).hexdigest())
        direct=Mock()
        factory=Mock()
        result=AutomaticPDFDownloader(self.db,direct=direct,browser_factory=factory).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(dict(result['reasons']),{'challenge':2})
        direct._download.assert_not_called()
        factory.assert_not_called()

    def test_default_dynamic_loader_is_headless(self):
        from unittest.mock import patch
        direct=PDFDownloader(self.db,pdf_helpers.Opener(b'<html>application</html>'))
        browser=PDFDownloader(self.db,pdf_helpers.Opener(self.pdf))
        @contextmanager
        def session(): yield browser
        browser.session=session
        with patch('myinvoices.automatic_downloads.BrowserPDFDownloader',return_value=browser) as factory:
            result=AutomaticPDFDownloader(self.db,direct=direct).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
        self.assertTrue(factory.call_args.kwargs['headless'])
        self.assertTrue(factory.call_args.kwargs['allow_print'])
