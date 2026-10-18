import base64
import importlib.util
import io
import json
import shutil
import threading
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

import test_pdf_downloads as pdf_helpers
from myinvoices.browser_downloads import BrowserPDFDownloader


@unittest.skipUnless(importlib.util.find_spec('playwright') and shutil.which('chromium'),
                     'Optional browser integration requires Playwright and system Chromium')
class BrowserDownloadTests(unittest.TestCase):
    def setUp(self):
        pdf_helpers.PDFTests.setUp(self)
        self.browser_validation = patch('myinvoices.browser_downloads.validate_url')
        self.browser_validation.start()
        self.addCleanup(self.browser_validation.stop)

    def downloader(self, html, routes=None, timeout=10):
        routes=routes or {}
        class SyntheticBrowser(BrowserPDFDownloader):
            async def _new_context(inner):
                context=await super()._new_context()
                async def serve(route):
                    path=urlsplit(route.request.url).path
                    if path=='/pdf':
                        await route.fulfill(status=200,content_type='text/html',body=html)
                    elif path in routes:
                        body,mime=routes[path]
                        await route.fulfill(status=200,content_type=mime,body=body)
                    else:
                        await route.abort()
                await context.route('**/*',serve)
                return context
        return SyntheticBrowser(self.db,headless=True,timeout=timeout)

    def test_javascript_json_blob_is_captured_and_deduplicated(self):
        html='''<html><body><iframe id="invoice" style="width:800px;height:600px"></iframe>
        <script>fetch('/api/invoice').then(r=>r.json()).then(j=>{
        const bytes=Uint8Array.from(atob(j.pdfBase64),c=>c.charCodeAt(0));
        document.querySelector('#invoice').src=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));
        });</script></body></html>'''
        api=json.dumps({'pdfBase64':base64.b64encode(self.pdf).decode()})
        downloader=self.downloader(html,{'/api/invoice':(api,'application/json')})
        result=downloader.run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
        files=list((self.root/'archive').rglob('*.pdf'))
        self.assertEqual(len(files),2)
        self.assertEqual(files[0].read_bytes(),self.pdf)
        repeat=self.downloader(html,{'/api/invoice':(api,'application/json')})
        self.assertEqual(repeat.run(self.client,self.root/'archive',threading.Event())['skipped'],2)
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_canvas_viewer_network_pdf_is_captured(self):
        html='<html><body><canvas></canvas><script>fetch("/invoice.pdf").then(r=>r.arrayBuffer())</script></body></html>'
        result=self.downloader(html,{'/invoice.pdf':(self.pdf,'application/pdf')}).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)

    def test_multiple_pdfs_are_not_selected_arbitrarily(self):
        html='''<html><body><iframe src="/one.pdf"></iframe><iframe src="/two.pdf"></iframe></body></html>'''
        result=self.downloader(html,{'/one.pdf':(self.pdf,'application/pdf'),'/two.pdf':(self.pdf+b'\n','application/pdf')},timeout=3).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],0)
        self.assertEqual(result['pending'],2)
        self.assertFalse(list(self.root.rglob('*.pdf')))

    def test_ordinary_html_times_out_without_archiving(self):
        result=self.downloader('<html>Not an invoice viewer</html>',timeout=1).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],0)
        self.assertEqual(dict(result['reasons']),{'browser_timeout':2})
        self.assertFalse(list(self.root.rglob('*.pdf')))

    def test_download_event_is_archived_without_manual_import(self):
        encoded=base64.b64encode(self.pdf).decode()
        html="""<html><body><a id="save" download="invoice.pdf">PDF</a><script>
        const bytes=Uint8Array.from(atob('"""+encoded+"""'),c=>c.charCodeAt(0));
        const a=document.querySelector('#save');a.href=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));
        setTimeout(()=>a.click(),100);</script></body></html>"""
        result=self.downloader(html).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
        self.assertEqual(len(list((self.root/'archive').rglob('*.pdf'))),2)
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_cancel_closes_browser_and_leaves_no_archive(self):
        cancel=threading.Event()
        downloader=self.downloader('<html><body>Waiting for document</body></html>')
        downloader.notice=lambda _message: cancel.set()
        result=downloader.run(self.client,self.root/'archive',cancel)
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['saved'],0)
        self.assertIsNone(downloader.browser)
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))
