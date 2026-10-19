"""Conservative HTML invoice printing after original PDF capture has failed.

This does not print PDF viewers, solve challenges, or use OCR. A browser print is
an explicitly different artifact from the provider's original PDF.
"""
import asyncio
from contextlib import suppress
from datetime import date
import io
import re
import time
import unicodedata

from .pdf_downloads import MAX_BYTES, Pending, archive_pdf_bytes

PRINT_TIMEOUT_SECONDS = 20
STABILITY_INTERVAL = 0.2

_SNAPSHOT = """() => {
 const visible = e => !!e.getClientRects().length &&
   getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
 const any = selector => [...document.querySelectorAll(selector)].some(visible);
 const text = document.body?.innerText || '';
 const completed = selector => [...document.querySelectorAll(selector)]
   .some(e=>typeof e.value==='string' && e.value.trim().length>0);
 return {
   text: text.slice(0,250001), title: document.title, type: document.contentType,
   viewer: any('iframe,embed,object,.pdfViewer,[data-pdfjsprinting]') ||
     [...document.querySelectorAll('canvas')].some(e => visible(e) &&
       e.getBoundingClientRect().width >= 400 && e.getBoundingClientRect().height >= 300) ||
     !!window.PDFViewerApplication,
   challenge: any('iframe[src*="challenges.cloudflare.com"],.cf-turnstile,[id^="cf-chl-"]') && !completed('input[name="cf-turnstile-response"]') ||
     any('iframe[src*="recaptcha"]') && !completed('textarea[name="g-recaptcha-response"]') ||
     any('iframe[src*="hcaptcha"]') && !completed('textarea[name="h-captcha-response"],input[name="h-captcha-response"]'),
   login: any('input[type="password"]'),
   blocked: any('[aria-modal="true"],dialog[open],.modal.show'),
   ready: (!document.fonts || document.fonts.status === 'loaded') &&
     [...document.images].filter(visible).every(i => i.complete && i.naturalWidth > 0),
   shape: [document.body?.scrollWidth || 0,document.body?.scrollHeight || 0],
   complex: document.querySelectorAll('*').length > 10000
 };
}"""

_READY = """async () => {
 if (document.fonts) await document.fonts.ready;
 await Promise.all([...document.images].filter(i => i.getClientRects().length).map(i =>
   i.complete ? Promise.resolve() : new Promise(resolve => {
     i.addEventListener('load',resolve,{once:true});
     i.addEventListener('error',resolve,{once:true});
   })));
}"""


class _PrintCancelled(Exception):
    pass


async def _bounded(awaitable, cancel, deadline):
    """Keep cancellation responsive even during a browser protocol operation."""
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            if cancel.is_set():
                raise _PrintCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Pending('print_timeout')
            await asyncio.wait({task}, timeout=min(0.1, remaining))
        if cancel.is_set():
            raise _PrintCancelled()
        return await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


def _normal(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).casefold()
                   if not unicodedata.combining(c))


def _contains(text, value):
    value = _normal(value).strip()
    return bool(value) and bool(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text))


def _vat(text, value):
    value = re.sub(r'\D', '', str(value or ''))
    return len(value) == 9 and bool(re.search(r'(?<!\d)(?:el\s*)?' + value + r'(?!\d)', text))


def _matches_invoice(text, client, invoice):
    """Match known metadata, rather than treating every printable page as success."""
    text = _normal(text)
    if len(text.strip()) < 80 or not re.search(r'\b(invoice|τιμολογιο|παραστατικο|πιστωτικο|αποδειξη)\b', text):
        return False
    mark = str(invoice.get('mark', ''))
    # Explicitly labelled competing MARKs are an invoice list, not one document.
    marks = set(re.findall(r'\b(?:mark|μαρκ)\s*[:：#]?\s*(\d{10,20})\b', text))
    if marks and marks != {mark}:
        return False
    issuer = _vat(text, invoice.get('issuer_vat'))
    recipient = _vat(text, client.vat)
    if _contains(text, mark) and (issuer or recipient):
        return True
    try:
        issued = date.fromisoformat(str(invoice.get('issue_date', '')))
    except ValueError:
        return False
    dates = (issued.isoformat(), issued.strftime('%d/%m/%Y'),
             issued.strftime('%d-%m-%Y'), issued.strftime('%d.%m.%Y'))
    number = str(invoice.get('number') or '').strip()
    series = str(invoice.get('series') or '').strip()
    identity = _contains(text, number) and (not series or _contains(text, series))
    return bool(issuer and recipient and identity and any(_contains(text, d) for d in dates))


def _guard(snapshot, client, invoice):
    title, text = _normal(snapshot.get('title', '')), _normal(snapshot.get('text', ''))
    challenge = r'just a moment|attention required|verify (?:that )?you are human|checking your browser|security verification|επαληθευση ανθρωπου'
    if snapshot.get('challenge') or re.search(challenge, title + '\n' + text[:2000]):
        raise Pending('challenge')
    if snapshot.get('viewer') or snapshot.get('type') == 'application/pdf':
        raise Pending('print_viewer_unsupported')
    error = r'\b(access denied|forbidden|not found|service unavailable|error|σφαλμα|δεν βρεθηκε|συνδεση χρηστη)\b'
    if (snapshot.get('login') or snapshot.get('blocked') or snapshot.get('complex') or len(text) > 250000 or
            re.search(error, title) or re.match(r'^\s*(?:error|access denied|forbidden|404\b)', text)):
        raise Pending('print_not_invoice')
    if not _matches_invoice(text, client, invoice):
        raise Pending('print_not_invoice')


def _valid_print(data, client, invoice):
    """Structural validity alone would accept blank pages or challenge printouts."""
    if len(data) > MAX_BYTES or not data.startswith(b'%PDF-'):
        return False
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 500:
            return False
        texts = []
        length = 0
        for page in reader.pages:
            text = page.extract_text() or ''
            length += len(text)
            if length > 1000000:
                return False
            texts.append(text)
        return _matches_invoice('\n'.join(texts), client, invoice)
    except Exception:
        return False


async def print_invoice_page(page, client, invoice, root, cancel):
    """Print a matching complete HTML invoice; return PDFArtifact or cancellation.

    Caller must first attempt original PDF retrieval. No DB writes or secret-bearing
    diagnostics occur here. The caller records the distinct printed artifact state.
    """
    deadline = time.monotonic() + PRINT_TIMEOUT_SECONDS
    try:
        if cancel.is_set():
            return None
        previous = await _bounded(page.evaluate(_SNAPSHOT), cancel, deadline)
        _guard(previous, client, invoice)
        await _bounded(page.evaluate(_READY), cancel, deadline)
        while True:
            await _bounded(asyncio.sleep(STABILITY_INTERVAL), cancel, deadline)
            current = await _bounded(page.evaluate(_SNAPSHOT), cancel, deadline)
            _guard(current, client, invoice)
            if current.get('ready') and all(current.get(key) == previous.get(key)
                                            for key in ('text', 'shape')):
                break
            previous = current
        data = await _bounded(page.pdf(print_background=True, prefer_css_page_size=True), cancel, deadline)
        if not _valid_print(data, client, invoice):
            raise Pending('print_not_invoice')
        if cancel.is_set():
            return None
        from .pdf_downloads import PDFArtifact
        result = archive_pdf_bytes(data, client, invoice, root, cancel)
        if result is None:
            return None
        if cancel.is_set():
            result[0].unlink(missing_ok=True)
            return None
        return PDFArtifact(*result, 'printed_unverified')
    except _PrintCancelled:
        return None
    except Pending:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise Pending('print_timeout') from None
    except Exception:
        if cancel.is_set():
            return None
        raise Pending('print_failed') from None
