"""Resolve only explicit document references. No provider endpoint guessing or JS execution."""
import base64
import binascii
from html.parser import HTMLParser
import json
from urllib.parse import urljoin, urlsplit, parse_qs

MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 40 * 1024 * 1024
URL_KEYS = {'pdfurl', 'pdf_url', 'documenturl', 'document_url', 'downloadurl', 'download_url'}
BASE64_KEYS = {'pdfbase64', 'pdf_base64'}


class ResolutionError(Exception):
    def __init__(self, state):
        self.state = state


def decode_pdf_data(value):
    if not value.startswith('data:application/pdf;base64,'):
        raise ResolutionError('invalid_pdf')
    encoded = value.split(',', 1)[1]
    if len(encoded) > ((MAX_PDF_BYTES + 2) // 3) * 4:
        raise ResolutionError('too_large')
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ResolutionError('invalid_pdf') from None
    if len(decoded) > MAX_PDF_BYTES:
        raise ResolutionError('too_large')
    if not decoded.startswith(b'%PDF-'):
        raise ResolutionError('invalid_pdf')
    return decoded


def document_reference(value, base):
    value = value.strip()
    if value.startswith('data:application/pdf;base64,'):
        return decode_pdf_data(value)
    resolved = urljoin(base, value)
    parts = urlsplit(resolved)
    # PDF.js explicitly declares the document through its standard file parameter.
    if parts.path.endswith('/web/viewer.html'):
        files = parse_qs(parts.query).get('file', [])
        if len(files) == 1:
            resolved = urljoin(resolved, files[0])
    if urlsplit(resolved).scheme != 'https':
        raise ResolutionError('html_page')
    return resolved


def json_references(data):
    results = []
    budget = [2000]
    def walk(node, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 8:
            raise ResolutionError('descriptor_limit')
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and value.strip():
                    if key.lower() in URL_KEYS:
                        results.append(value)
                    elif key.lower() in BASE64_KEYS:
                        results.append('data:application/pdf;base64,' + value)
                elif isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)
    walk(data)
    return results


class DocumentHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.json_text = None
        self.json_blocks = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        style = values.get('style', '').lower().replace(' ', '')
        hidden = 'hidden' in values or 'display:none' in style or 'visibility:hidden' in style
        if not hidden:
            if tag in ('iframe', 'embed') and values.get('src'):
                self.references.append(values['src'])
            elif tag == 'object' and values.get('data'):
                self.references.append(values['data'])
            elif tag == 'a' and values.get('type', '').lower() == 'application/pdf' and values.get('href'):
                self.references.append(values['href'])
        if tag == 'script' and values.get('type', '').lower() == 'application/json':
            self.json_text = []

    def handle_data(self, data):
        if self.json_text is not None:
            self.json_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.json_text is not None:
            self.json_blocks.append(''.join(self.json_text))
            self.json_text = None


def resolve_document(body, base_url, content_type=''):
    if len(body) > MAX_DESCRIPTOR_BYTES:
        raise ResolutionError('descriptor_limit')
    text = body.decode('utf-8-sig', errors='replace')
    if 'json' in content_type.lower() or text.lstrip().startswith(('{', '[')):
        try:
            references = json_references(json.loads(text))
        except (ValueError, RecursionError):
            raise ResolutionError('invalid_descriptor') from None
    else:
        parser = DocumentHTML()
        parser.feed(text)
        references = list(parser.references)
        for block in parser.json_blocks:
            try:
                references.extend(json_references(json.loads(block)))
            except (ValueError, RecursionError):
                continue
    resolved = []
    for ref in references:
        try:
            value = document_reference(ref, base_url)
        except (ValueError, UnicodeError):
            raise ResolutionError('invalid_descriptor') from None
        except ResolutionError as error:
            if error.state == 'html_page':
                continue
            raise
        if value not in resolved:
            resolved.append(value)
    if len(resolved) > 1:
        raise ResolutionError('ambiguous_document')
    if not resolved:
        raise ResolutionError('html_page')
    return resolved[0]
