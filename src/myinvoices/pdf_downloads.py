"""Direct provider PDFs. No AADE credentials, challenge solving or URL guessing."""
import io
import hashlib
from collections import Counter
import webbrowser
from http.client import HTTPException
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
from datetime import date
from typing import NamedTuple

from .models import ApplicationError
from .provider_documents import MAX_DESCRIPTOR_BYTES, ResolutionError, resolve_document
from .local_files import client_archive_folder

MAX_BYTES = 40 * 1024 * 1024
SAVED_STATES = ('saved_unverified','imported_unverified','printed_unverified')


class PDFArtifact(NamedTuple):
    path: Path
    checksum: str
    size: int
    state: str = 'saved_unverified'


INTERACTIVE_STATES = {'challenge','http_403','browser_required','browser_timeout','browser_closed',
    'browser_download_failed','browser_confirmation','html_page','descriptor_limit','invalid_descriptor','ambiguous_document','browser_unavailable','print_not_invoice','print_viewer_unsupported','print_timeout','print_failed'}

LABELS = {
    'archive_date_invalid': 'Δεν αποθηκεύτηκε PDF: μη έγκυρη ημερομηνία έκδοσης',
    'printed_unverified': 'PDF από εκτύπωση',
    'artifact_missing': 'Το αποθηκευμένο αρχείο δεν βρέθηκε',
    'artifact_changed': 'Το αποθηκευμένο αρχείο έχει αλλάξει',
    'print_not_invoice': 'Αποτυχία εκτύπωσης: δεν αναγνωρίστηκε φορτωμένο παραστατικό',
    'print_viewer_unsupported': 'Αποτυχία εκτύπωσης: ο προβολέας χρειάζεται χειροκίνητη εκτύπωση',
    'print_timeout': 'Αποτυχία εκτύπωσης: δεν ολοκληρώθηκε η φόρτωση',
    'print_failed': 'Αποτυχία δημιουργίας PDF εκτύπωσης',
    'browser_confirmation': 'Εκκρεμεί: δεν επιβεβαιώθηκε νέο PDF από τον browser',
    'challenge': 'Εκκρεμεί για δεύτερο πέρασμα: Cloudflare / CAPTCHA',
    'browser_unavailable': 'Δεν ξεκίνησε browser για λήψη/εκτύπωση — τρέξτε INSTALL_BROWSER.bat',
    'browser_required': 'Εκκρεμεί: χρειάζεται φόρτωση στον browser',
    'browser_timeout': 'Εκκρεμεί: ο browser δεν παρέδωσε PDF εγκαίρως',
    'browser_download_failed': 'Εκκρεμεί: απέτυχε το download στον browser',
    'browser_closed': 'Εκκρεμεί: έκλεισε ο browser',
    'ambiguous_document': 'Εκκρεμεί: περισσότερα από ένα πιθανά PDF',
    'descriptor_limit': 'Εκκρεμεί: μεγάλη ή σύνθετη σελίδα/JSON',
    'invalid_descriptor': 'Εκκρεμεί: μη έγκυρο JSON παρόχου',
    'imported_unverified': 'PDF αποθηκευμένο (εισαγωγή)',
    'saved_unverified': 'PDF αποθηκευμένο',
    'html_page': 'Εκκρεμεί: ο σύνδεσμος επιστρέφει ιστοσελίδα',
    'http_error': 'Εκκρεμεί: απόρριψη από πάροχο',
    'invalid_pdf': 'Εκκρεμεί: μη έγκυρο ή κρυπτογραφημένο PDF',
    'too_large': 'Εκκρεμεί: αρχείο άνω των 40 MB',
    'network_error': 'Εκκρεμεί: πρόβλημα δικτύου',
    'unsafe_url': 'Εκκρεμεί: μη επιτρεπτός σύνδεσμος',
    'storage_error': 'Εκκρεμεί: αποτυχία αποθήκευσης',
}


def state_label(state):
    if state and state.startswith('http_') and state[5:].isdigit():
        return f'Εκκρεμεί: HTTP {state[5:]} από τον πάροχο'
    return LABELS.get(state, 'Εκκρεμεί: άγνωστη κατάσταση')


def open_provider(database, client, mark, browser=None):
    row = database.invoice(client.id, mark)
    if not row['download_url']:
        raise ApplicationError('Το παραστατικό δεν έχει επίσημο σύνδεσμο παρόχου.')
    # This action only hands the exact AADE-returned link to the user's browser.
    # No DNS preflight, provider request, polling or download is performed here.
    try:
        parts = urlsplit(row['download_url'])
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                or parts.port == 0 or any(ord(c) < 33 for c in row['download_url'])):
            raise ValueError()
    except ValueError:
        raise ApplicationError('Ο σύνδεσμος του παραστατικού δεν είναι έγκυρος σύνδεσμος ιστοσελίδας.') from None
    if not (browser or webbrowser.open)(row['download_url'], new=2):
        raise ApplicationError('Δεν άνοιξε ο browser. Ελέγξτε τον προεπιλεγμένο browser των Windows.')


def archive_folder(root, client, row):
    try:
        month = date.fromisoformat(str(row['issue_date'])).strftime('%Y-%m')
    except (KeyError, ValueError, TypeError):
        raise Pending('archive_date_invalid') from None
    folder = client_archive_folder(root, client) / month
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def saved_pdf_path(database, client, mark):
    row = database.invoice(client.id, mark)
    source_hash = hashlib.sha256((row['download_url'] or '').encode()).hexdigest()
    saved = database.saved_pdf(client.id, mark, source_hash)
    if not saved or not saved['path']:
        raise ApplicationError('Δεν έχει αποθηκευτεί PDF για αυτό το παραστατικό.')
    path = Path(saved['path'])
    try:
        if (not path.is_symlink() and path.is_file() and path.stat().st_size == saved['byte_count']
                and digest_file(path) == saved['sha256']):
            return path
    except OSError:
        pass
    raise ApplicationError('Το αποθηκευμένο PDF λείπει ή έχει αλλάξει. Εκτελέστε ξανά Λήψη PDF.')


def publish_pdf(temporary, folder, mark, cancel):
    final = folder / f'{mark}.pdf'
    created = False
    interrupted = False
    try:
        try:
            target_stream = final.open('xb')
        except FileExistsError:
            final = folder / f'{mark}_{uuid.uuid4().hex}.pdf'
            target_stream = final.open('xb')
        with target_stream as target:
            created = True
            with temporary.open('rb') as source:
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
        return final
    except Exception:
        if created:
            final.unlink(missing_ok=True)
        raise


def import_browser_pdf(database, client, mark, source, destination, cancel):
    """User explicitly associates a locally downloaded PDF with the selected MARK."""
    row = database.invoice(client.id, mark)
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ApplicationError('Επιλέξτε ένα τοπικό αρχείο PDF, όχι σύνδεσμο ή φάκελο.')
    temporary = final = None
    try:
        folder = archive_folder(destination, client, row)
        with source.open('rb') as original, tempfile.NamedTemporaryFile(dir=folder, suffix='.part', delete=False) as target:
            temporary = Path(target.name)
            size = 0
            while chunk := original.read(65536):
                if cancel.is_set():
                    return None
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ApplicationError('Το PDF υπερβαίνει το όριο των 40 MB.')
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if not valid_pdf(temporary):
            raise ApplicationError('Το αρχείο δεν είναι έγκυρο PDF έως 500 σελίδες χωρίς κρυπτογράφηση.')
        checksum = digest_file(temporary)
        source_hash = hashlib.sha256((row['download_url'] or '').encode()).hexdigest()
        old = database.saved_pdf(client.id, mark, source_hash)
        if old and old['sha256'] == checksum:
            old_path = Path(old['path'])
            if old_path.is_file() and not old_path.is_symlink() and digest_file(old_path) == checksum:
                return old_path
        if cancel.is_set():
            return None
        final = publish_pdf(temporary, folder, mark, cancel)
        if final:
            database.record_pdf(client.id, mark, 'imported_unverified', source_hash, str(final), checksum, size)
        return final
    except OSError:
        if final:
            final.unlink(missing_ok=True)
        raise ApplicationError('Αποτυχία ανάγνωσης ή αποθήκευσης του PDF. Το αρχικό αρχείο διατηρείται.') from None
    except Exception:
        if final:
            final.unlink(missing_ok=True)
        raise
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)

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
    except socket.gaierror:
        raise Pending('network_error') from None
    except (ValueError, OSError):
        raise Pending('unsafe_url') from None


class SafeRedirect(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return Request(newurl, headers={'Accept': 'application/pdf', 'User-Agent': 'myInvoices/0.7'})


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

    def _candidates(self, client_id, dates=None):
        return self.db.download_candidates(client_id,dates)

    def run(self, client, destination, cancel, progress=lambda done, total: None, dates=None):
        rows = self._candidates(client.id, dates)
        result = dict(saved=0, printed=0, skipped=0, pending=0, cancelled=False, reasons=Counter())
        for index, row in enumerate(rows):
            if cancel.is_set():
                result['cancelled'] = True
                break
            source_hash = hashlib.sha256(row['download_url'].encode()).hexdigest()
            old = self.db.saved_pdf(client.id, row['mark'], source_hash)
            if old:
                path = Path(old['path'])
                invalidation = 'artifact_changed'
                try:
                    if not path.is_symlink() and path.stat().st_size == old['byte_count'] and digest_file(path) == old['sha256']:
                        result['skipped'] += 1
                        progress(index + 1, len(rows))
                        continue
                except FileNotFoundError:
                    invalidation = 'artifact_missing'
                except OSError:
                    invalidation = 'artifact_missing'
                self.db.record_pdf(client.id,row['mark'],invalidation,source_hash)
            output = None
            try:
                output = self._download(client, row, Path(destination), cancel)
                if output is None:
                    result['cancelled'] = True
                    break
                path, checksum, size = output[:3]
                state = getattr(output,'state','saved_unverified')
                if state not in SAVED_STATES:
                    raise ApplicationError('Μη αναμενόμενος τρόπος αποθήκευσης PDF.')
                self.db.record_pdf(client.id, row['mark'], state, source_hash,
                                   str(path), checksum, size)
                result['saved'] += 1
                result['printed'] += int(state == 'printed_unverified')
            except Pending as error:
                self.db.record_pdf(client.id, row['mark'], error.state, source_hash)
                result['pending'] += 1
                result['reasons'][error.state] += 1
            except Exception:
                if output:
                    output[0].unlink(missing_ok=True)
                raise
            progress(index + 1, len(rows))
        return result

    def _open_document(self, url, cancel):
        visited = set()
        for _step in range(4):
            if cancel.is_set():
                return None
            if url in visited:
                raise Pending('html_page')
            visited.add(url)
            validate_url(url)
            request = Request(url, headers={'Accept': 'application/pdf, application/json, text/html;q=0.8',
                                           'User-Agent': 'myInvoices/0.7'})
            try:
                response = self.opener.open(request, timeout=30)
            except HTTPError as error:
                try:
                    challenge = error.headers.get('cf-mitigated') == 'challenge'
                    if not challenge:
                        try:
                            prefix = error.read(65536).lower()
                        except (OSError, ValueError, HTTPException):
                            prefix = b''
                        challenge = any(marker in prefix for marker in
                            (b'cf-chl-', b'challenge-platform', b'captcha', b'cf-turnstile'))
                    state = 'challenge' if challenge else f'http_{error.code}'
                finally:
                    error.close()
                raise Pending(state) from None
            transferred = False
            try:
                if response.headers.get('cf-mitigated') == 'challenge':
                    raise Pending('challenge')
                if getattr(response, 'status', 200) != 200:
                    raise Pending(f'http_{response.status}')
                prefix = response.read(65536)
                if prefix.startswith(b'%PDF-'):
                    transferred = True
                    return response, prefix
                lower = prefix.lower()
                if any(marker in lower for marker in (b'cf-chl-', b'challenge-platform', b'captcha', b'cf-turnstile')):
                    raise Pending('challenge')
                is_descriptor = ('json' in response.headers.get('Content-Type', '').lower()
                    or b'<html' in lower or b'<!doctype html' in lower
                    or prefix.lstrip().startswith((b'{', b'[')))
                if not is_descriptor:
                    raise Pending('invalid_pdf')
                body = prefix + response.read(MAX_DESCRIPTOR_BYTES + 1 - len(prefix))
                final_url = response.geturl() if hasattr(response, 'geturl') else url
                reference = resolve_document(body, final_url, response.headers.get('Content-Type', ''))
            except ResolutionError as error:
                raise Pending(error.state) from None
            finally:
                # Direct PDF ownership is transferred to the streaming caller only.
                if not transferred:
                    response.close()
            if isinstance(reference, bytes):
                stream = io.BytesIO(reference)
                return stream, stream.read(65536)
            url = reference
        raise Pending('html_page')

    def _download(self, client, row, root, cancel):
        temporary = None
        try:
            opened = self._open_document(row['download_url'], cancel)
            if opened is None:
                return None
            response, prefix = opened
            with response:
                folder = archive_folder(root, client, row)
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
                final = publish_pdf(temporary, folder, row['mark'], cancel)
                if final is None:
                    return None
                return final, checksum, size
        except Pending:
            raise
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            raise Pending('network_error') from None
        except OSError:
            raise Pending('storage_error') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


def archive_pdf_bytes(data, client, row, root, cancel):
    if len(data) > MAX_BYTES:
        raise Pending('too_large')
    if not data.startswith(b'%PDF-'):
        raise Pending('invalid_pdf')
    temporary = None
    try:
        folder = archive_folder(root, client, row)
        with tempfile.NamedTemporaryFile(dir=folder,suffix='.part',delete=False) as stream:
            temporary = Path(stream.name)
            for offset in range(0,len(data),65536):
                if cancel.is_set():
                    return None
                stream.write(data[offset:offset+65536])
            stream.flush()
            os.fsync(stream.fileno())
        if not valid_pdf(temporary):
            raise Pending('invalid_pdf')
        checksum = digest_file(temporary)
        final = publish_pdf(temporary,folder,row['mark'],cancel)
        return (final,checksum,len(data)) if final else None
    except OSError:
        raise Pending('storage_error') from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
