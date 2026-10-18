"""Direct provider PDFs. No AADE credentials, challenge solving or URL guessing."""
from datetime import date
import hashlib
import ipaddress
import os
from pathlib import Path
import socket
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from .models import ApplicationError

MAX_BYTES = 40 * 1024 * 1024
LABELS = {
    'saved_unverified': 'PDF αποθηκευμένο — προς έλεγχο',
    'challenge': 'Εκκρεμεί: προστασία παρόχου / CAPTCHA',
    'html_page': 'Εκκρεμεί: ο σύνδεσμος επιστρέφει ιστοσελίδα',
    'http_error': 'Εκκρεμεί: απόρριψη από πάροχο',
    'invalid_pdf': 'Εκκρεμεί: μη έγκυρο ή κρυπτογραφημένο PDF',
    'too_large': 'Εκκρεμεί: αρχείο άνω των 40 MB',
    'network_error': 'Εκκρεμεί: πρόβλημα δικτύου',
    'unsafe_url': 'Εκκρεμεί: μη επιτρεπτός σύνδεσμος',
    'storage_error': 'Εκκρεμεί: αποτυχία αποθήκευσης',
}

class Pending(Exception):
    def __init__(self, state):
        self.state = state


def validate_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or any(ord(c) < 33 for c in url)):
            raise ValueError()
        addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise Pending('unsafe_url') from None


class SafeRedirect(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return Request(newurl, headers={'Accept': 'application/pdf', 'User-Agent': 'myInvoices/0.4'})


def valid_pdf(path):
    try:
        from pypdf import PdfReader
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                return False
            stream.seek(0)
            reader = PdfReader(stream, strict=True)
            return not reader.is_encrypted and 0 < len(reader.pages) <= 500
    except ImportError:
        raise ApplicationError('Εκτελέστε πρώτα INSTALL.bat για εγκατάσταση του ελέγχου PDF.') from None
    except Exception:
        return False


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class PDFDownloader:
    def __init__(self, database, opener=None):
        self.db = database
        self.opener = opener or build_opener(SafeRedirect())

    def run(self, client, destination, cancel, progress=lambda done, total: None):
        rows = self.db.download_candidates(client.id)
        result = dict(saved=0, skipped=0, pending=0, cancelled=False)
        for index, row in enumerate(rows):
            if cancel.is_set():
                result['cancelled'] = True
                break
            source_hash = hashlib.sha256(row['download_url'].encode()).hexdigest()
            old = self.db.saved_pdf(client.id, row['mark'], source_hash)
            if old:
                path = Path(old['path'])
                try:
                    if not path.is_symlink() and path.stat().st_size == old['byte_count'] and digest_file(path) == old['sha256']:
                        result['skipped'] += 1
                        progress(index + 1, len(rows))
                        continue
                except OSError:
                    pass
            output = None
            try:
                output = self._download(client, row, Path(destination), cancel)
                if output is None:
                    result['cancelled'] = True
                    break
                path, checksum, size = output
                self.db.record_pdf(client.id, row['mark'], 'saved_unverified', source_hash,
                                   str(path), checksum, size)
                result['saved'] += 1
            except Pending as error:
                self.db.record_pdf(client.id, row['mark'], error.state, source_hash)
                result['pending'] += 1
            except Exception:
                if output:
                    output[0].unlink(missing_ok=True)
                raise
            progress(index + 1, len(rows))
        return result

    def _download(self, client, row, root, cancel):
        validate_url(row['download_url'])
        request = Request(row['download_url'], headers={'Accept': 'application/pdf', 'User-Agent': 'myInvoices/0.4'})
        temporary = None
        final = None
        try:
            try:
                response = self.opener.open(request, timeout=30)
            except HTTPError as error:
                try:
                    state = 'challenge' if error.headers.get('cf-mitigated') == 'challenge' else 'http_error'
                finally:
                    error.close()
                raise Pending(state) from None
            with response:
                if response.headers.get('cf-mitigated') == 'challenge':
                    raise Pending('challenge')
                if getattr(response, 'status', 200) != 200:
                    raise Pending('http_error')
                prefix = response.read(65536)
                lower = prefix.lower()
                if any(marker in lower for marker in (b'cf-chl-', b'challenge-platform', b'captcha', b'cf-turnstile')) and not prefix.startswith(b'%PDF-'):
                    raise Pending('challenge')
                if not prefix.startswith(b'%PDF-'):
                    raise Pending('html_page' if b'<html' in lower or b'<!doctype html' in lower else 'invalid_pdf')
                try:
                    issued = date.fromisoformat(row['issue_date'])
                    year, month = str(issued.year), f'{issued.month:02}'
                except ValueError:
                    year, month = 'Αγνωστη_ημερομηνία', '00'
                supplier = ''.join(c for c in (row['issuer_vat'] or '') if c.isascii() and c.isalnum())[:32] or 'Αγνωστος'
                folder = root / f'{client.vat}_{client.environment}' / 'ΠΡΟΣ_ΕΛΕΓΧΟ' / year / month / supplier
                folder.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=folder, suffix='.part', delete=False) as stream:
                    temporary = Path(stream.name)
                    size = 0
                    chunk = prefix
                    deadline = time.monotonic() + 90
                    while chunk:
                        if cancel.is_set():
                            return None
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise Pending('too_large')
                        if time.monotonic() > deadline:
                            raise Pending('network_error')
                        stream.write(chunk)
                        chunk = response.read(65536)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not valid_pdf(temporary):
                    raise Pending('invalid_pdf')
                checksum = digest_file(temporary)
                if cancel.is_set():
                    return None
                final = folder / f'{row["mark"]}_{uuid.uuid4().hex}.pdf'
                # Exclusive creation works on NTFS and removable FAT/exFAT disks too.
                created = False
                interrupted = False
                try:
                    with final.open('xb') as target, temporary.open('rb') as source:
                        created = True
                        while chunk := source.read(65536):
                            if cancel.is_set():
                                interrupted = True
                                break
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    if interrupted:
                        final.unlink(missing_ok=True)
                        return None
                except Exception:
                    if created:
                        final.unlink(missing_ok=True)
                    raise
                return final, checksum, size
        except Pending:
            raise
        except (URLError, TimeoutError, ConnectionError):
            raise Pending('network_error') from None
        except OSError:
            raise Pending('storage_error') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
