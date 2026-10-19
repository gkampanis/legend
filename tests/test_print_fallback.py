import asyncio
import importlib.util
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from myinvoices.pdf_downloads import Pending
from myinvoices.print_fallback import print_invoice_page


@unittest.skipUnless(importlib.util.find_spec('playwright') and shutil.which('chromium'),
                     'Optional browser integration requires Playwright and system Chromium')
class PrintFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = SimpleNamespace(vat='123456789', environment='production')
        self.invoice = dict(mark=400000000000001, issuer_vat='987654321',
                            recipient_vat='123456789', issue_date='2026-10-01',
                            series='INV', number='12345')
        self.cancel = threading.Event()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=shutil.which('chromium'), headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.html = ''

        async def serve(route):
            await route.fulfill(status=200, content_type='text/html', body=self.html)

        await self.context.route('**/*', serve)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.temp.cleanup()

    def invoice_html(self, *, with_mark=True):
        mark = f'<p>MARK: {self.invoice["mark"]}</p>' if with_mark else ''
        return '''<!doctype html><html><head><title>Invoice</title><style>
          @page {size:A4;margin:15mm} .next {break-before:page}
          </style></head><body><h1>INVOICE</h1>''' + mark + '''
          <p>Supplier VAT: 987654321</p><p>Customer VAT: 123456789</p>
          <p>Issue date: 01/10/2026</p><p>Invoice identity: INV / 12345</p>
          <p>Products and services supplied to the customer. Net value 100.00; Total 124.00.</p>
          <section class="next"><h2>Invoice continuation</h2>
          <p>Final line item and document closing notes. END OF INVOICE PAGE TWO.</p></section>
          </body></html>'''

    async def navigate(self, html):
        self.html = html
        await self.page.goto('https://synthetic.example/invoice', wait_until='load')

    async def capture(self):
        return await print_invoice_page(self.page, self.client, self.invoice, self.root, self.cancel)

    async def assert_pending(self, html, state):
        await self.navigate(html)
        with self.assertRaises(Pending) as caught:
            await self.capture()
        self.assertEqual(caught.exception.state, state)
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))

    async def test_matching_html_prints_both_pages_and_distinct_state(self):
        await self.navigate(self.invoice_html())
        result = await self.capture()
        self.assertEqual(result.state, 'printed_unverified')
        self.assertTrue(result.path.is_file())
        reader = PdfReader(result.path)
        self.assertEqual(len(reader.pages), 2)
        self.assertIn('END OF INVOICE PAGE TWO', reader.pages[1].extract_text())
        self.assertFalse(list(self.root.rglob('*.part')))

    async def test_metadata_identity_can_match_without_mark(self):
        await self.navigate(self.invoice_html(with_mark=False))
        result = await self.capture()
        self.assertEqual(result.state, 'printed_unverified')

    async def test_mismatched_metadata_and_invoice_lists_are_rejected(self):
        html = self.invoice_html().replace('400000000000001', '400000000000002')
        await self.assert_pending(html, 'print_not_invoice')
        html = self.invoice_html() + '<p>MARK: 400000000000002</p>'
        await self.assert_pending(html, 'print_not_invoice')
        html = self.invoice_html(with_mark=False).replace('123456789', '111111111')
        await self.assert_pending(html, 'print_not_invoice')

    async def test_blank_error_and_login_are_not_archived(self):
        for html in ('<html><body></body></html>',
                     self.invoice_html().replace('<title>Invoice</title>', '<title>Access denied</title>'),
                     self.invoice_html() + '<input type="password">'):
            await self.assert_pending(html, 'print_not_invoice')

    async def test_challenge_is_not_printed_even_with_matching_metadata(self):
        html = self.invoice_html().replace('<title>Invoice</title>', '<title>Just a moment...</title>')
        await self.assert_pending(html, 'challenge')
        await self.assert_pending(self.invoice_html() + '<div class="cf-turnstile">Verify you are human</div>', 'challenge')

    async def test_visible_viewers_are_not_printed_as_outer_pages(self):
        for viewer in ('<iframe src="about:blank"></iframe>', '<canvas width="900" height="1200"></canvas>',
                       '<object data="about:blank"></object>', '<embed src="about:blank">',
                       '<div class="pdfViewer">Rendered document</div>'):
            await self.assert_pending(self.invoice_html() + viewer, 'print_viewer_unsupported')

    async def test_html_invoice_with_small_barcode_canvas_is_printable(self):
        html=self.invoice_html()+'<canvas id="barcode" width="200" height="80"></canvas>'
        await self.navigate(html)
        result=await self.capture()
        self.assertEqual(result.state,'printed_unverified')
        self.assertEqual(len(PdfReader(result.path).pages),2)

    async def test_print_button_page_uses_native_beforeprint_layout(self):
        html=self.invoice_html()+'''<button onclick="window.print()">Εκτύπωση</button>
          <p id="prepared"></p><script>window.addEventListener('beforeprint',()=>{
            document.getElementById('prepared').textContent='PRINT PREPARATION COMPLETE';
          });</script>'''
        await self.navigate(html)
        result=await self.capture()
        text='\n'.join(page.extract_text() for page in PdfReader(result.path).pages)
        self.assertIn('PRINT PREPARATION COMPLETE',text)

    async def test_print_css_hiding_the_invoice_is_not_success(self):
        html = self.invoice_html().replace('</style>', '@media print {body {display:none}}</style>')
        await self.assert_pending(html, 'print_not_invoice')

    async def test_cancel_before_capture_has_no_file(self):
        await self.navigate(self.invoice_html())
        self.cancel.set()
        self.assertIsNone(await self.capture())
        self.assertFalse(list(self.root.rglob('*.pdf')))

    async def test_cancel_while_waiting_for_readiness_has_no_file(self):
        await self.navigate(self.invoice_html())

        async def cancel_later():
            await asyncio.sleep(0.05)
            self.cancel.set()

        task = asyncio.create_task(cancel_later())
        self.assertIsNone(await self.capture())
        await task
        self.assertFalse(list(self.root.rglob('*.pdf')))

    async def test_timeout_is_pending_not_success(self):
        await self.navigate(self.invoice_html())
        with patch('myinvoices.print_fallback.PRINT_TIMEOUT_SECONDS', 0.01):
            with self.assertRaises(Pending) as caught:
                await self.capture()
        self.assertEqual(caught.exception.state, 'print_timeout')
        self.assertFalse(list(self.root.rglob('*.pdf')))

    async def test_cancel_during_browser_print_has_no_published_file(self):
        await self.navigate(self.invoice_html())

        async def slow_print(**_options):
            self.cancel.set()
            await asyncio.sleep(5)

        with patch.object(self.page, 'pdf', side_effect=slow_print):
            self.assertIsNone(await self.capture())
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))

    async def test_hidden_metadata_does_not_identify_a_page(self):
        html = '<html><body>Welcome to our provider portal.' + \
            '<div style="display:none">' + self.invoice_html() + '</div></body></html>'
        await self.assert_pending(html, 'print_not_invoice')

    async def test_browser_print_failure_is_safe(self):
        await self.navigate(self.invoice_html())
        with patch.object(self.page, 'pdf', side_effect=RuntimeError('private token must not escape')):
            with self.assertRaises(Pending) as caught:
                await self.capture()
        self.assertEqual(caught.exception.state, 'print_failed')
        self.assertNotIn('private', str(caught.exception))
        self.assertFalse(list(self.root.rglob('*.pdf')))


if __name__ == '__main__':
    unittest.main()
