"""Visible browser document capture. No stealth, CAPTCHA clicks or guessed API paths."""
import asyncio
from contextlib import suppress, contextmanager
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit, unquote, urldefrag
from urllib.request import getproxies

from .models import ApplicationError
from .pdf_downloads import (PDFDownloader, Pending, MAX_BYTES, validate_url, archive_folder,
                            valid_pdf, digest_file, publish_pdf, archive_pdf_bytes)
from .provider_documents import decode_pdf_data, ResolutionError

# Read the PDF explicitly embedded in the visible document, including Blob and PDF.js.
# This neither searches arbitrary links nor executes actions/buttons on the page.
VIEWER_SNAPSHOT = """async ({limit}) => {
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const nodes = [...document.querySelectorAll('iframe[src],embed[src],object[data]')].filter(visible);
 const sources = nodes.map(e => e.src || e.data).filter(Boolean);
 const blobs = [...new Set(sources.filter(s => s.startsWith('blob:') || s.startsWith('data:application/pdf;base64,')))];
 if (blobs.length > 1) return {ambiguous:true,sources};
 let bytes;
 if (blobs.length === 1) {
   if (blobs[0].startsWith('data:')) return {encoded:blobs[0],sources};
   const response = await fetch(blobs[0]);
   const blob = await response.blob();
   if (blob.size > limit) return {large:true,sources};
   bytes = new Uint8Array(await blob.arrayBuffer());
 } else if (document.querySelector('canvas,.pdfViewer') && window.PDFViewerApplication?.pdfDocument) {
   bytes = await window.PDFViewerApplication.pdfDocument.getData();
 }
 if (bytes) {
   if (bytes.length > limit) return {large:true,sources};
   let binary = '';
   for (let i=0; i<bytes.length; i+=32768) binary += String.fromCharCode(...bytes.subarray(i,i+32768));
   return {encoded:'data:application/pdf;base64,'+btoa(binary),sources};
 }
 const viewer = [...document.querySelectorAll('iframe,embed,object,.pdfViewer')].some(visible) ||
   [...document.querySelectorAll('canvas')].some(e=>visible(e) &&
     e.getBoundingClientRect().width >= 400 && e.getBoundingClientRect().height >= 300);
 return {sources,viewer};
}"""


def browser_launch_choices():
    if sys.platform != 'win32':
        chromium=shutil.which('chromium')
        return ([{'executable_path':chromium}] if chromium else []) + [{}]
    choices=[{'channel':'msedge'},{'channel':'chrome'}]
    seen=set()
    for variable in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        root=os.environ.get(variable)
        if not root:
            continue
        for relative in ('BraveSoftware/Brave-Browser/Application/brave.exe',
                         'Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
            executable=Path(root)/relative
            if executable.is_file() and str(executable) not in seen:
                seen.add(str(executable))
                choices.append({'executable_path':str(executable)})
    choices.append({})  # Playwright's installed Chromium is the final fallback.
    return choices


def browser_launch_options(headless):
    options=dict(headless=headless)
    proxy=getproxies().get('https')
    if proxy:
        parsed=urlsplit(proxy)
        if parsed.hostname:
            options['proxy']={'server':f'{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}'}
            if parsed.username:
                options['proxy']['username']=unquote(parsed.username)
                options['proxy']['password']=unquote(parsed.password or '')
    return options


class BrowserPDFDownloader(PDFDownloader):
    def __init__(self, database, *, timeout=90, headless=False, notice=lambda message: None, allow_print=False):
        super().__init__(database)
        if not 0 < timeout <= 300:
            raise ValueError('Browser timeout must be between 0 and 300 seconds')
        self.timeout, self.headless, self.notice = timeout, headless, notice
        self.allow_print = allow_print
        self.context = self.browser = self.playwright = None

    def run(self, client, destination, cancel, progress=lambda done, total: None, dates=None):
        # No browser installation/startup is needed when there are no candidate links.
        if not self.db.download_candidates(client.id, dates):
            return super().run(client, destination, cancel, progress, dates)
        with self.session():
            return super().run(client, destination, cancel, progress, dates)

    @contextmanager
    def session(self):
        """Share a temporary browser across one batch, including lazy fallback calls."""
        try:
            from playwright.async_api import Error
        except ImportError:
            raise ApplicationError('Τρέξτε πρώτα INSTALL_BROWSER.bat για τη λήψη μέσω Edge/Chrome.') from None
        with asyncio.Runner() as runner:
            self.runner = runner
            try:
                try:
                    runner.run(self._start())
                except Error:
                    raise ApplicationError('Δεν ξεκίνησε η συνεδρία browser. Τρέξτε INSTALL_BROWSER.bat και δοκιμάστε ξανά.') from None
                yield self
            finally:
                runner.run(self._close())

    async def _start(self):
        from playwright.async_api import async_playwright, Error
        self.playwright = await async_playwright().start()
        options = browser_launch_options(self.headless)
        for choice in browser_launch_choices():
            try:
                self.browser = await self.playwright.chromium.launch(**options, **choice)
                break
            except Error:
                continue
        if self.browser is None:
            raise ApplicationError('Δεν ξεκίνησε Edge/Chrome/Brave ή Chromium. Τρέξτε INSTALL_BROWSER.bat και δοκιμάστε ξανά.')
        self.context = await self._new_context()

    async def _new_context(self):
        # Separate temporary profile; no AADE credentials or personal browser profile.
        return await self.browser.new_context(accept_downloads=True)

    async def _close(self):
        if self.context:
            with suppress(Exception):
                await self.context.close()
        if self.browser:
            with suppress(Exception):
                await self.browser.close()
        if self.playwright:
            with suppress(Exception):
                await self.playwright.stop()
        self.context = self.browser = self.playwright = None

    def _download(self, client, row, root, cancel):
        return self.runner.run(self._capture(client, row, root, cancel))

    def _archive(self, data, client, row, root, cancel):
        return archive_pdf_bytes(data,client,row,root,cancel)

    async def _download_event(self, download, client, row, root, cancel, deadline):
        from playwright.async_api import Error
        temporary = None
        try:
            url = download.url
            if url.startswith('data:application/pdf;base64,'):
                try:
                    data = decode_pdf_data(url)
                except ResolutionError as error:
                    raise Pending(error.state) from None
                return self._archive(data,client,row,root,cancel)
            validate_url(url[5:] if url.startswith('blob:') else url)
            folder = archive_folder(root,client,row)
            with tempfile.NamedTemporaryFile(dir=folder,suffix='.part',delete=False) as stream:
                temporary = Path(stream.name)
            task = asyncio.create_task(download.save_as(temporary))
            try:
                while not task.done():
                    if cancel.is_set() or time.monotonic() > deadline:
                        await download.cancel()
                        task.cancel()
                        with suppress(asyncio.CancelledError,Error):
                            await task
                        if cancel.is_set():
                            return None
                        raise Pending('browser_timeout')
                    await asyncio.wait({task},timeout=0.25)
                await task
                if temporary.stat().st_size > MAX_BYTES:
                    raise Pending('too_large')
                return self._archive(temporary.read_bytes(),client,row,root,cancel)
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError,Error):
                        await task
        except Error:
            if cancel.is_set():
                return None
            raise Pending('browser_download_failed') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    async def _capture(self, client, row, root, cancel):
        from playwright.async_api import Error, TimeoutError as BrowserTimeout
        validate_url(row['download_url'])
        if cancel.is_set():
            return None
        page = await self.context.new_page()
        page.set_default_timeout(15000)
        finished, downloads = {}, []
        deadline = time.monotonic() + self.timeout
        self.notice(f"MARK {row['mark']}: φόρτωση δυναμικού PDF στο παρασκήνιο." if self.headless else
                    f"MARK {row['mark']}: φόρτωση στον browser.")

        async def record(request):
            try:
                response = await request.response()
                if response is None or response.status != 200 or len(finished) >= 16:
                    return
                mime = response.headers.get('content-type','').split(';')[0].strip().lower()
                if mime in ('application/pdf','application/octet-stream'):
                    finished[response.url] = response
            except Error:
                pass
        page.on('requestfinished',record)
        page.on('download',lambda download: downloads.append(download))
        try:
            try:
                navigation = await page.goto(row['download_url'],wait_until='domcontentloaded',timeout=min(30000,self.timeout*1000))
                if self.headless and navigation:
                    if navigation.headers.get('cf-mitigated') == 'challenge' or navigation.status == 403:
                        raise Pending('challenge')
                    if navigation.status == 429:
                        raise Pending('http_429')
            except (BrowserTimeout,Error):
                # A direct PDF download can abort navigation while emitting download.
                if page.is_closed():
                    raise Pending('browser_closed') from None
            while time.monotonic() < deadline:
                if cancel.is_set():
                    return None
                if page.is_closed():
                    raise Pending('browser_closed')
                if page.url.startswith('https:'):
                    validate_url(page.url)
                elif page.url != 'about:blank':
                    raise Pending('unsafe_url')
                if len(downloads) > 1:
                    raise Pending('ambiguous_document')
                if downloads:
                    return await self._download_event(downloads[0],client,row,root,cancel,deadline)
                sources, has_viewer, embedded = set(), False, []
                for frame in page.frames[:8]:
                    try:
                        if frame != page.main_frame:
                            frame_url = frame.url[5:] if frame.url.startswith('blob:') else frame.url
                            if frame_url.startswith('https:'):
                                validate_url(frame_url)
                            elif frame_url != 'about:blank':
                                continue
                            element = await frame.frame_element()
                            if not await element.is_visible():
                                continue
                        snapshot = await asyncio.wait_for(frame.evaluate(VIEWER_SNAPSHOT,{'limit':MAX_BYTES}),timeout=5)
                    except (Error,TimeoutError):
                        continue
                    if snapshot.get('large'):
                        raise Pending('too_large')
                    if snapshot.get('ambiguous'):
                        raise Pending('ambiguous_document')
                    sources.update(urldefrag(url)[0] for url in snapshot.get('sources',[]))
                    has_viewer |= bool(snapshot.get('viewer'))
                    if snapshot.get('encoded'):
                        for source in snapshot.get('sources',[]):
                            if source.startswith('blob:'):
                                validate_url(source[5:])
                        try:
                            data = decode_pdf_data(snapshot['encoded'])
                        except ResolutionError as error:
                            raise Pending(error.state) from None
                        if data not in embedded:
                            embedded.append(data)
                if len(embedded) > 1:
                    raise Pending('ambiguous_document')
                if embedded:
                    return self._archive(embedded[0],client,row,root,cancel)
                matches = [response for url,response in finished.items() if urldefrag(url)[0] in sources]
                if not matches:
                    primary = [r for r in finished.values() if r.request.is_navigation_request()
                               and r.request.frame == page.main_frame]
                    matches = primary or (list(finished.values()) if has_viewer and len(finished)==1 else [])
                if len(matches) > 1:
                    raise Pending('ambiguous_document')
                if matches:
                    response = matches[0]
                    validate_url(response.url)
                    length = response.headers.get('content-length','')
                    if length.isdigit() and int(length) > MAX_BYTES:
                        raise Pending('too_large')
                    data = await asyncio.wait_for(response.body(),timeout=15)
                    return self._archive(data,client,row,root,cancel)
                if self.headless:
                    challenge = await page.evaluate("""() => {
                      const visible=e=>!!e.getClientRects().length;
                      return /just a moment|attention required/i.test(document.title) ||
                        [...document.querySelectorAll('iframe[src*="challenges.cloudflare.com"],.cf-turnstile,[id^="cf-chl-"]')].some(visible);
                    }""")
                    if challenge:
                        raise Pending('challenge')
                await asyncio.sleep(0.25)
            if cancel.is_set():
                return None
            if self.allow_print:
                from .print_fallback import print_invoice_page
                self.notice(f"MARK {row['mark']}: δεύτερη προσπάθεια, εκτύπωση φορτωμένου παραστατικού.")
                invoice = self.db.invoice(client.id,row['mark'])
                return await print_invoice_page(page,client,invoice,root,cancel)
            raise Pending('browser_timeout')
        except Pending:
            raise
        except (Error,TimeoutError):
            if cancel.is_set():
                return None
            raise Pending('browser_closed' if page.is_closed() else 'browser_timeout') from None
        finally:
            with suppress(Error):
                await page.close()
