import asyncio
from contextlib import nullcontext
import hashlib
import html as html_tools
import importlib.util
import io
import os
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, Mock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

import test_pdf_downloads as helpers
from myinvoices.browser_recovery import RecoveryPDFDownloader
from myinvoices.date_range import DateRange
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.pdf_downloads import Pending, archive_pdf_bytes
from myinvoices.xml_parser import parse_response


def text_pdf(mark):
    writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),
                           NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject()
    stream.set_data((f'BT /F1 12 Tf 20 750 Td (INVOICE MARK {mark}) Tj '
                     '0 -20 Td (Seller VAT 111111111. Customer VAT 000000000.) Tj '
                     '0 -20 Td (Date 2026-10-01. Consulting services. Total 124.12 EUR.) Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(stream)
    data=io.BytesIO();writer.write(data)
    return data.getvalue()


def invoice_html(mark,extra=''):
    return f'''<html><head><title>Invoice</title><style>@page {{size:A4;margin:15mm}}
      .next {{break-before:page}}</style></head><body><h1>INVOICE</h1>
      <p>MARK {mark}. Seller VAT 111111111. Customer VAT 000000000.</p>
      <p>Date 2026-10-01. Series DEMO Number {mark}. Total 124.12 EUR.</p>
      <p>Detailed invoice lines: consulting services, quantity one, net 100.10 EUR.</p>
      {extra}<section class="next">END OF SECOND INVOICE PAGE.</section></body></html>'''


class RecoveryQueueTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)

    def attempt(self,mark,state):
        row=self.db.invoice(self.client.id,mark)
        self.db.record_pdf(self.client.id,mark,state,hashlib.sha256(row['download_url'].encode()).hexdigest())

    def test_queue_excludes_saved_unattempted_stale_sources_and_other_periods(self):
        self.attempt(101,'challenge')
        self.assertEqual([r['mark'] for r in self.db.failed_download_candidates(self.client.id)],[101])
        self.assertEqual(self.db.failed_download_candidates(self.client.id,DateRange.parse('01/09/2026','30/09/2026')),[])
        other=self.db.add_client('Other','222222222')
        self.assertEqual(self.db.failed_download_candidates(other.id),[])
        with self.db.connection() as conn:
            conn.execute("UPDATE invoices SET download_url='https://provider.example/new' WHERE mark=101")
        self.assertEqual(self.db.failed_download_candidates(self.client.id),[])
        self.attempt(101,'html_page')
        service=RecoveryPDFDownloader(self.db)
        with patch.object(service,'session',return_value=nullcontext()), \
                patch.object(service,'_download',side_effect=lambda client,row,root,cancel:archive_pdf_bytes(self.pdf,client,row,root,cancel)) as fetch:
            result=service.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],1)
        self.assertEqual(fetch.call_args.args[1]['mark'],101)
        self.assertEqual(self.db.failed_download_candidates(self.client.id),[])
        with patch.object(service,'session',side_effect=AssertionError('No failed records must not launch')):
            self.assertEqual(service.run(self.client,self.root/'archive',threading.Event())['saved'],0)

    def test_reset_does_not_recover_unattempted_invoices(self):
        self.attempt(101,'challenge')
        self.db.reset_downloads(self.client.id)
        self.assertEqual(self.db.failed_download_candidates(self.client.id),[])

    def test_explicit_selection_stays_within_failed_client_records(self):
        self.attempt(101,'challenge');self.attempt(102,'html_page')
        service=RecoveryPDFDownloader(self.db,marks=[102,999])
        self.assertEqual([r['mark'] for r in service._candidates(self.client.id)],[102])


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Optional Playwright is not installed')
class ObservedPDFRequestTests(unittest.IsolatedAsyncioTestCase):
    def response(self,status=200,headers=None,data=None):
        return SimpleNamespace(status=status,headers=headers or {},body=AsyncMock(return_value=data or text_pdf(101)),dispose=AsyncMock())

    async def call(self,responses,validate=None):
        service=RecoveryPDFDownloader(Mock())
        get=AsyncMock(side_effect=responses)
        service.context=SimpleNamespace(request=SimpleNamespace(get=get))
        owner=SimpleNamespace(evaluate=AsyncMock(return_value=None))
        async def wait(awaitable):return await awaitable
        with patch('myinvoices.browser_recovery.validate_url',side_effect=validate):
            result=await service._fetch_observed_pdf(owner,'https://provider.example/observed.pdf',wait)
        return result,get

    async def test_cross_origin_fallback_follows_only_validated_redirects(self):
        first=self.response(302,{'location':'https://pdf.example/document.pdf'})
        second=self.response()
        validated=[]
        data,get=await self.call([first,second],validate=validated.append)
        self.assertEqual(data,text_pdf(101))
        self.assertIn('https://pdf.example/document.pdf',validated)
        self.assertEqual(get.call_args.kwargs['headers'],{'Accept':'application/pdf'})
        self.assertEqual(get.call_args.kwargs['max_redirects'],0)
        first.dispose.assert_awaited_once();second.dispose.assert_awaited_once()

    async def test_unsafe_redirect_is_never_requested(self):
        first=self.response(302,{'location':'https://127.0.0.1/private'})
        def validate(url):
            if '127.0.0.1' in url:raise Pending('unsafe_url')
        with self.assertRaises(Pending) as caught:
            await self.call([first],validate=validate)
        self.assertEqual(caught.exception.state,'unsafe_url')
        first.dispose.assert_awaited_once()

    async def test_status_size_and_html_do_not_become_a_pdf(self):
        denied=self.response(403)
        self.assertIsNone((await self.call([denied]))[0])
        large=self.response(headers={'content-length':str(41*1024*1024)})
        with self.assertRaises(Pending):await self.call([large])
        large.body.assert_not_awaited();large.dispose.assert_awaited_once()
        page=self.response(data=b'<html>not a PDF</html>')
        self.assertIsNone((await self.call([page]))[0])
        rate=self.response(429)
        with self.assertRaises(Pending) as caught:await self.call([rate])
        self.assertEqual(caught.exception.state,'http_429')


@unittest.skipUnless(importlib.util.find_spec('playwright') and shutil.which('chromium') and os.environ.get('DISPLAY'),
                     'Visible recovery integration needs Playwright, Chromium and a display')
class BrowserRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)
        with self.db.connection() as conn:
            conn.execute("UPDATE invoices SET download_url='https://provider.example/invoice/'||mark")
        row=self.db.invoice(self.client.id,101)
        self.db.record_pdf(self.client.id,101,'challenge',hashlib.sha256(row['download_url'].encode()).hexdigest())
        self.validation=patch('myinvoices.browser_recovery.validate_url')
        self.validation.start();self.addCleanup(self.validation.stop)
        self.original_validation=patch('myinvoices.browser_downloads.validate_url')
        self.original_validation.start();self.addCleanup(self.original_validation.stop)
        self.requests=[]
        self.cookie_checks=[]

    def service(self,mode,timeout=8):
        test=self
        class SyntheticRecovery(RecoveryPDFDownloader):
            async def _start(inner):
                await super()._start()
                if mode=='session':
                    cookies=await inner.context.cookies('https://provider.example')
                    test.cookie_checks.append(any(c['name']=='synthetic-recovery-session' for c in cookies))
                    await inner.context.add_cookies([dict(name='synthetic-recovery-session',value='fixture',
                                                         domain='provider.example',path='/',expires=time.time()+3600)])
                async def serve(route):
                    path=urlsplit(route.request.url).path
                    test.requests.append((path,route.request.headers))
                    mark=path.rsplit('/',1)[-1].removesuffix('.pdf')
                    if path.startswith('/file/'):
                        await route.fulfill(status=200,content_type='application/pdf',body=text_pdf(mark));return
                    if path.startswith('/print/') or path.startswith('/embedded/') or path.startswith('/ready/'):
                        html=invoice_html(mark)
                    elif path.startswith('/invoice/'):
                        if mode=='direct':
                            await route.fulfill(status=200,content_type='application/pdf',body=text_pdf(mark));return
                        if mode=='iframe':
                            html=f'<html><body><iframe style="width:700px;height:200px" src="/embedded/{mark}"></iframe></body></html>'
                        elif mode=='srcdoc':
                            html=f'<html><body><iframe style="width:700px;height:200px" srcdoc="{html_tools.escape(invoice_html(mark),quote=True)}"></iframe></body></html>'
                        elif mode=='popup':
                            html=invoice_html(mark,f'<button onclick="window.open(\'/print/{mark}\')">Print</button>')
                        elif mode=='popup_pdf':
                            html=invoice_html(mark,f'<button onclick="window.open(\'/file/{mark}.pdf\')">Print</button>')
                        elif mode=='challenge':
                            html=f'''<html><head><title>Just a moment</title></head><body><div class="cf-turnstile">Synthetic challenge</div>
                               <button id="human" onclick="location.href='/ready/{mark}'">Human fixture action</button></body></html>'''
                        elif mode=='print_close':
                            html=invoice_html(mark,'<button onclick="document.body.dataset.prepared=1;window.print();window.close()">Print</button>')
                        elif mode=='close':
                            html='<html><body>Waiting for a document</body></html>'
                        elif mode=='bad':
                            html='<html><body>Access denied</body></html>'
                        else:
                            html=invoice_html(mark)
                    else:
                        await route.abort();return
                    await route.fulfill(status=200,content_type='text/html',body=html)
                await inner.context.route('**/*',serve)
                if mode=='challenge':
                    async def human(page):
                        try:
                            await page.locator('#human').wait_for(timeout=3000)
                            # A test-only actor on a synthetic page, never production CAPTCHA automation.
                            await page.locator('#human').click()
                        except Exception:pass
                    inner.context.on('page',human)
                if mode=='close':
                    async def close(page):
                        await asyncio.sleep(0.2)
                        await page.close()
                    inner.context.on('page',close)
        return SyntheticRecovery(self.db,timeout=timeout)

    def test_visible_html_recovery_saves_without_reset_and_skips_saved(self):
        service=self.service('html')
        result=service.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed'],result['pending']),(1,1,0))
        self.assertEqual(self.db.count(self.client.id,bucket='success'),1)
        self.assertEqual(self.db.count(self.client.id,bucket='failure'),0)
        self.assertEqual(len(PdfReader(next((self.root/'archive').rglob('*.pdf'))).pages),2)
        self.assertTrue(service.profile_directory.is_dir())
        self.assertEqual(service.run(self.client,self.root/'archive',threading.Event())['saved'],0)
        self.assertTrue(all('aade-user-id' not in headers and 'ocp-apim-subscription-key' not in headers for path,headers in self.requests))

    def test_clipped_html_iframe_is_printed_in_full(self):
        result=self.service('iframe').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,1))
        reader=PdfReader(next((self.root/'archive').rglob('*.pdf')))
        self.assertEqual(len(reader.pages),2)
        self.assertIn('END OF SECOND INVOICE PAGE',reader.pages[-1].extract_text())

    def test_srcdoc_invoice_frame_is_printed_in_full(self):
        result=self.service('srcdoc').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,1))
        reader=PdfReader(next((self.root/'archive').rglob('*.pdf')))
        self.assertEqual(len(reader.pages),2)

    def test_cookie_session_is_persistent_and_separate_per_client(self):
        service=self.service('session')
        service.run(self.client,self.root/'archive',threading.Event())
        source=hashlib.sha256(self.db.invoice(self.client.id,101)['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,101,'artifact_missing',source)
        service.run(self.client,self.root/'archive',threading.Event())
        other=self.db.add_client('Other','222222222')
        self.db.commit_page(self.db.checkpoint(other.id),parse_response(xml_page([xml_invoice(201,recipient=other.vat)])))
        source=hashlib.sha256(self.db.invoice(other.id,201)['download_url'].encode()).hexdigest()
        self.db.record_pdf(other.id,201,'html_page',source)
        original_profile=service.profile_directory
        service.run(other,self.root/'archive',threading.Event())
        self.assertEqual(self.cookie_checks,[False,True,False])
        self.assertNotEqual(original_profile,service.profile_directory)
        production=self.db.add_client('Same VAT production','000000000','production')
        self.db.commit_page(self.db.checkpoint(production.id),parse_response(xml_page([xml_invoice(202,recipient=production.vat)])))
        source=hashlib.sha256(self.db.invoice(production.id,202)['download_url'].encode()).hexdigest()
        self.db.record_pdf(production.id,202,'html_page',source)
        service.run(production,self.root/'archive',threading.Event())
        self.assertEqual(self.cookie_checks,[False,True,False,False])
        self.assertNotEqual(original_profile,service.profile_directory)

    def test_print_button_popup_is_captured(self):
        result=self.service('popup').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,1))
        self.assertTrue(any(path=='/print/101' for path,headers in self.requests))

    def test_original_pdf_from_print_popup_is_preferred(self):
        result=self.service('popup_pdf').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,0))
        self.assertEqual(next((self.root/'archive').rglob('*.pdf')).read_bytes(),text_pdf(101))

    def test_print_close_button_does_not_close_before_capture(self):
        result=self.service('print_close').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,1))

    def test_synthetic_human_completion_resumes_without_solver(self):
        result=self.service('challenge').run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['printed']),(1,1))

    def test_closing_invoice_stops_batch_without_reopening(self):
        row=self.db.invoice(self.client.id,102)
        self.db.record_pdf(self.client.id,102,'challenge',hashlib.sha256(row['download_url'].encode()).hexdigest())
        result=self.service('close').run(self.client,self.root/'archive',threading.Event())
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['saved'],0)
        self.assertEqual([path for path,headers in self.requests if path.startswith('/invoice/')],['/invoice/101'])

    def test_error_page_times_out_without_a_false_success(self):
        result=self.service('bad',timeout=1).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['pending']),(0,1))
        self.assertFalse(list((self.root/'archive').rglob('*.pdf')))
