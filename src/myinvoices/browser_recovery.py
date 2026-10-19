"""Explicit second pass for failed invoices in a per-client visible browser."""
import asyncio
import hashlib
import re
import time
from contextlib import suppress
from urllib.parse import urldefrag, urljoin

from .browser_downloads import (BrowserPDFDownloader, VIEWER_SNAPSHOT,
                                browser_launch_choices, browser_launch_options)
from .models import ApplicationError
from .pdf_downloads import PDFDownloader, Pending, MAX_BYTES, validate_url
from .provider_documents import decode_pdf_data, ResolutionError
from .print_fallback import (_SNAPSHOT, _guard, _bounded, _PrintCancelled,
                             _valid_print, print_invoice_page)

PRINT_LABELS=re.compile(r'^\s*(?:Εκτ[υύ]πωση(?: παραστατικού)?|Print(?: invoice| document)?|Λ[ηή]ψη PDF|Download PDF)\s*$',re.I)

PRINT_BRIDGE="""() => {
 window.__myinvoicesPrintRequested=false;
 const close=window.close;
 window.print=()=>{window.__myinvoicesPrintRequested=true;};
 window.close=()=>{if(!window.__myinvoicesPrintRequested) close.call(window);};
}"""

READ_BROWSER_PDF="""async ({url,limit}) => {
 try {
   const response=await fetch(url,{credentials:'include',redirect:'error',headers:{Accept:'application/pdf'}});
   if(!response.ok) return null;
   if(Number(response.headers.get('content-length'))>limit) return {large:true};
   const reader=response.body.getReader();const chunks=[];let size=0;
   while(true) {
     const {done,value}=await reader.read();if(done) break;
     size+=value.length;if(size>limit) {await reader.cancel();return {large:true};}
     chunks.push(value);
   }
   const bytes=new Uint8Array(size);let offset=0;
   for(const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.length;}
   if(String.fromCharCode(...bytes.subarray(0,5))!=='%PDF-') return null;
   let binary='';for(let i=0;i<size;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
   return {encoded:'data:application/pdf;base64,'+btoa(binary)};
 } catch {return null;}
}"""

SERIALIZE_FRAME="""(fallback) => {
 const clone=document.documentElement.cloneNode(true);
 const canvases=[...document.querySelectorAll('canvas')];
 for(const [i,copy] of [...clone.querySelectorAll('canvas')].entries()) {
   const original=canvases[i];
   if(!original.getClientRects().length) {copy.remove();continue;}
   const img=document.createElement('img');
   try {img.src=original.toDataURL('image/png');} catch {return null;}
   for(const a of [...copy.attributes]) img.setAttribute(a.name,a.value);
   img.src=original.toDataURL('image/png');copy.replaceWith(img);
 }
 clone.querySelectorAll('script,noscript,iframe,embed,object,base,input[type=hidden],input[type=password],.cf-turnstile,meta[http-equiv],link[as=script],link[rel=modulepreload]').forEach(e=>e.remove());
 for(const e of clone.querySelectorAll('*')) for(const a of [...e.attributes]) {
   if(a.name.toLowerCase().startsWith('on') || /^javascript:/i.test(a.value.trim())) e.removeAttribute(a.name);
 }
 const base=document.createElement('base');base.href=document.baseURI.startsWith('https:') ? document.baseURI : fallback;
 clone.querySelector('head').prepend(base);
 const html='<!doctype html>'+clone.outerHTML;
 return html.length<=2000000 ? {html,base:base.href} : null;
}"""


class RecoveryPDFDownloader(BrowserPDFDownloader):
    def __init__(self,database,*,notice=lambda message:None,timeout=120,headless=False,marks=None):
        super().__init__(database,notice=notice,timeout=timeout,headless=headless,allow_print=True)
        self.profile_directory=None
        self.marks=None if marks is None else frozenset(int(mark) for mark in marks)

    def _candidates(self,client_id,dates=None):
        rows=self.db.failed_download_candidates(client_id,dates)
        return rows if self.marks is None else [row for row in rows if row['mark'] in self.marks]

    def run(self,client,destination,cancel,progress=lambda done,total:None,dates=None):
        if not self._candidates(client.id,dates) or cancel.is_set():
            return PDFDownloader.run(self,client,destination,cancel,progress,dates)
        key=hashlib.sha256(f'{client.id}|{client.environment}'.encode()).hexdigest()
        self.profile_directory=self.db.path.parent/'provider-browser'/key
        self.profile_directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.session():
            return PDFDownloader.run(self,client,destination,cancel,progress,dates)

    async def _start(self):
        from playwright.async_api import async_playwright,Error
        self.playwright=await async_playwright().start()
        for choice in browser_launch_choices():
            try:
                self.context=await self.playwright.chromium.launch_persistent_context(
                    str(self.profile_directory),accept_downloads=True,
                    **browser_launch_options(self.headless),**choice)
                self.browser=self.context.browser
                await self.context.add_init_script(PRINT_BRIDGE)
                return
            except Error:
                if self.context:
                    await self.context.close()
                    self.context=None
        raise ApplicationError('Δεν ξεκίνησε ο browser ανάκτησης. Κλείστε άλλη ανάκτηση και τρέξτε INSTALL_BROWSER.bat.')

    def _download(self,client,row,root,cancel):
        from playwright.async_api import Error
        try:
            return super()._download(client,row,root,cancel)
        except Error:
            # Closing the managed browser stops the batch; it must not reopen it.
            return None

    async def _capture(self,client,row,root,cancel):
        from playwright.async_api import Error
        validate_url(row['download_url'])
        if cancel.is_set():
            return None
        page=await self.context.new_page()
        pages=[];finished={};downloads=[];handlers=[]
        deadline=time.monotonic()+self.timeout
        invoice=self.db.invoice(client.id,row['mark'])
        async def wait(awaitable):
            try:
                return await _bounded(awaitable,cancel,deadline)
            except Pending:
                raise Pending(last_state) from None
        def track(owner):
            if owner in pages:
                return
            pages.append(owner)
            async def record(request):
                try:
                    response=await request.response()
                    if response and response.status==200 and len(finished)<16:
                        mime=response.headers.get('content-type','').split(';')[0].strip().lower()
                        if mime in ('application/pdf','application/octet-stream'):
                            finished[response.url]=(owner,response)
                except Error:
                    pass
            def download(value): downloads.append((owner,value))
            owner.on('requestfinished',record)
            owner.on('download',download)
            owner.on('popup',track)
            handlers.append((owner,record,download))
        track(page)
        async def record_popup_response(request):
            # A popup's first navigation may finish before Page.on('popup').
            try:
                response=await request.response()
                if not response or response.status!=200 or len(finished)>=16:
                    return
                mime=response.headers.get('content-type','').split(';')[0].strip().lower()
                if mime not in ('application/pdf','application/octet-stream'):
                    return
                owner=request.frame.page
                if owner not in pages:
                    if await owner.opener() not in pages:
                        return
                    track(owner)
                finished[response.url]=(owner,response)
            except Error:
                pass
        self.context.on('requestfinished',record_popup_response)
        clicked=set();seen={};refetched=set();grace=min(3,self.timeout/3)
        print_after=time.monotonic()+grace
        last_state='recovery_timeout'
        checked={row['download_url']}
        def checked_url(value):
            if value not in checked:
                validate_url(value);checked.add(value)
        self.notice(f"Ανάκτηση MARK {row['mark']}: ολοκληρώστε τυχόν έλεγχο στον ορατό browser. "
                    'Η εφαρμογή θα παραλάβει ή θα εκτυπώσει το παραστατικό. Η Διακοπή ή το κλείσιμο του browser σταματά την παρτίδα.')
        def archive(data,owner):
            if owner is not page and not _valid_print(data,client,invoice):
                raise Pending('recovery_pdf_mismatch')
            return self._archive(data,client,row,root,cancel)
        try:
            try:
                navigation=await wait(page.goto(row['download_url'],wait_until='domcontentloaded',timeout=min(15000,self.timeout*1000)))
                if navigation and navigation.status==429:
                    raise Pending('http_429')
            except Error:
                if page.is_closed() and not any(not p.is_closed() for p in pages):
                    return None
            while time.monotonic()<deadline:
                if cancel.is_set() or not any(not p.is_closed() for p in pages):
                    return None
                if len(pages)>5 or len(downloads)>1:
                    raise Pending('ambiguous_document')
                if downloads:
                    owner,download=downloads[0]
                    output=await self._download_event(download,client,row,root,cancel,deadline)
                    if output and owner is not page and not _valid_print(output[0].read_bytes(),client,invoice):
                        output[0].unlink(missing_ok=True)
                        raise Pending('recovery_pdf_mismatch')
                    return output
                sources=set();embedded=[];candidates=[];viewers=False
                for owner in list(pages):
                    if owner.is_closed():
                        continue
                    if owner.url.startswith('https:'):
                        checked_url(owner.url)
                    elif owner.url!='about:blank':
                        raise Pending('unsafe_url')
                    for frame in owner.frames[:8]:
                        try:
                            if frame is not owner.main_frame:
                                if frame.url.startswith('https:'):
                                    checked_url(frame.url)
                                elif frame.url.startswith('blob:https:'):
                                    checked_url(frame.url[5:])
                                elif frame.url not in ('about:blank','about:srcdoc') and not frame.url.startswith('blob:https:'):
                                    continue
                                element=await wait(frame.frame_element())
                                if not await wait(element.is_visible()):
                                    continue
                            snapshot=await wait(frame.evaluate(VIEWER_SNAPSHOT,{'limit':MAX_BYTES}))
                            if snapshot.get('large'):
                                raise Pending('too_large')
                            if snapshot.get('ambiguous'):
                                raise Pending('ambiguous_document')
                            sources.update(urldefrag(s)[0] for s in snapshot.get('sources',[]))
                            viewers|=bool(snapshot.get('viewer'))
                            if snapshot.get('encoded'):
                                try: data=decode_pdf_data(snapshot['encoded'])
                                except ResolutionError as error: raise Pending(error.state) from None
                                if not any(data==old[0] for old in embedded): embedded.append((data,owner))
                            screen=await wait(frame.evaluate(_SNAPSHOT))
                            try:
                                _guard(screen,client,invoice)
                            except Pending as error:
                                if error.state=='challenge':last_state='recovery_challenge'
                                continue
                            last_state='recovery_timeout'
                            signature=(screen.get('text'),str(screen.get('shape')),screen.get('ready'))
                            old=seen.get(frame)
                            if old is None or old[0]!=signature:
                                seen[frame]=(signature,time.monotonic())
                            elif screen.get('ready') and time.monotonic()-old[1]>=0.5:
                                candidates.append((owner,frame))
                        except Error:
                            continue
                if len(embedded)>1:
                    raise Pending('ambiguous_document')
                if embedded:
                    return archive(*embedded[0])
                matches=[value for url,value in finished.items() if urldefrag(url)[0] in sources]
                if not matches:
                    matches=[value for value in finished.values() if value[1].request.is_navigation_request()]
                    if not matches and viewers and len(finished)==1:
                        matches=list(finished.values())
                if len(matches)>1:
                    raise Pending('ambiguous_document')
                if matches:
                    owner,response=matches[0]
                    checked_url(response.url)
                    length=response.headers.get('content-length','')
                    if length.isdigit() and int(length)>MAX_BYTES:
                        raise Pending('too_large')
                    data=await wait(response.body())
                    if not data.startswith(b'%PDF-') and response.url not in refetched:
                        refetched.add(response.url)
                        data=await self._fetch_observed_pdf(owner,response.url,wait)
                    if data and data.startswith(b'%PDF-'):
                        return archive(data,owner)
                preferred=[c for c in candidates if c[0] is not page] or candidates
                if preferred and time.monotonic()>=print_after:
                    if len(preferred)>1:
                        raise Pending('ambiguous_document')
                    owner,frame=preferred[0]
                    if frame not in clicked:
                        clicked.add(frame)
                        controls=frame.get_by_role('button',name=PRINT_LABELS).or_(frame.get_by_role('link',name=PRINT_LABELS))
                        if await wait(controls.count())==1 and await wait(controls.is_visible()):
                            with suppress(Error): await wait(controls.click(timeout=1500))
                            print_after=time.monotonic()+grace
                            continue
                    try:
                        self.notice(f"MARK {row['mark']}: αποθήκευση φορτωμένου παραστατικού σε PDF.")
                        if frame is owner.main_frame:
                            return await print_invoice_page(owner,client,invoice,root,cancel)
                        base_url=page.url if page.url.startswith('https:') else row['download_url']
                        return await self._print_frame(frame,client,invoice,root,cancel,wait,base_url)
                    except Pending as error:
                        last_state=error.state
                        print_after=time.monotonic()+1
                await wait(asyncio.sleep(0.2))
            raise Pending(last_state)
        except _PrintCancelled:
            return None
        except Error:
            if cancel.is_set() or not any(not p.is_closed() for p in pages):return None
            raise Pending('recovery_timeout') from None
        finally:
            self.context.remove_listener('requestfinished',record_popup_response)
            for owner,record,download in handlers:
                owner.remove_listener('requestfinished',record)
                owner.remove_listener('download',download)
                owner.remove_listener('popup',track)
            for owner in reversed(pages):
                with suppress(Error):await owner.close()

    async def _print_frame(self,frame,client,invoice,root,cancel,wait,base_url):
        # Copy an already matched HTML document, not the clipped outer iframe.
        await wait(frame.evaluate("() => window.dispatchEvent(new Event('beforeprint'))"))
        rendered=await wait(frame.evaluate(SERIALIZE_FRAME,base_url))
        if not rendered:
            raise Pending('print_viewer_unsupported')
        validate_url(rendered['base'])
        printable=await self.context.new_page()
        try:
            await wait(printable.set_content(rendered['html'],wait_until='load'))
            return await print_invoice_page(printable,client,invoice,root,cancel)
        finally:
            with suppress(Exception):await printable.close()

    async def _fetch_observed_pdf(self,owner,url,wait):
        """Native PDF viewers can expose wrapper HTML as the navigation body."""
        from playwright.async_api import Error
        validate_url(url)
        try:
            result=await wait(owner.evaluate(READ_BROWSER_PDF,{'url':url,'limit':MAX_BYTES}))
            if result and result.get('large'):
                raise Pending('too_large')
            if result and result.get('encoded'):
                return decode_pdf_data(result['encoded'])
        except (Error,ResolutionError):
            pass
        # Cross-origin embedded PDFs may disallow DOM fetch; this request shares
        # only the provider browser's cookies, never the AADE API headers.
        for _ in range(5):
            validate_url(url)
            response=None
            try:
                response=await wait(self.context.request.get(url,headers={'Accept':'application/pdf'},
                                                             max_redirects=0,timeout=15000))
                if response.status in (301,302,303,307,308):
                    location=response.headers.get('location')
                    if not location:return None
                    url=urljoin(url,location)
                    continue
                if response.status==429:
                    raise Pending('http_429')
                if response.status!=200:return None
                length=response.headers.get('content-length','')
                if length.isdigit() and int(length)>MAX_BYTES:raise Pending('too_large')
                data=await wait(response.body())
                if len(data)>MAX_BYTES:raise Pending('too_large')
                return data if data.startswith(b'%PDF-') else None
            except Error:
                return None
            finally:
                if response:
                    with suppress(Error):await response.dispose()
        return None
