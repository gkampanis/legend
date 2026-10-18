import io
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from pypdf import PdfWriter

from myinvoices.database import Database
from myinvoices.demo import DemoAPI
from myinvoices.sync import Synchronizer
from myinvoices.pdf_downloads import PDFDownloader, Pending, validate_url, SafeRedirect
from urllib.request import Request


class Response(io.BytesIO):
    headers = {}
    status = 200


class Opener:
    def __init__(self, body):
        self.body, self.requests = body, []
    def open(self, request, timeout):
        self.requests.append(request)
        return Response(self.body)


class PDFTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / 'data.sqlite3')
        self.client = self.db.add_client('Synthetic', '000000000')
        Synchronizer(self.db, DemoAPI()).run(self.client.id, threading.Event())
        with self.db.connection() as conn:
            conn.execute("UPDATE invoices SET download_url='https://provider.example/pdf',status='url_available' WHERE client_id=?", (self.client.id,))
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = io.BytesIO()
        writer.write(stream)
        self.pdf = stream.getvalue()
        self.dns = patch('myinvoices.pdf_downloads.socket.getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
        self.dns.start()
        self.addCleanup(self.dns.stop)

    def run_download(self, body, cancel=None):
        opener = Opener(body)
        result = PDFDownloader(self.db, opener).run(self.client, self.root / 'output', cancel or threading.Event())
        return result, opener

    def test_pdf_is_unverified_and_repeat_checks_hash(self):
        result, opener = self.run_download(self.pdf)
        self.assertEqual(result['saved'], 2)
        self.assertEqual(len(list(self.root.rglob('*.pdf'))), 2)
        self.assertEqual({r['pdf_state'] for r in self.db.invoices(self.client.id)}, {'saved_unverified'})
        self.assertEqual(self.run_download(self.pdf)[0]['skipped'], 2)
        path = next(self.root.rglob('*.pdf'))
        path.write_bytes(b'changed')
        self.assertEqual(self.run_download(self.pdf)[0]['saved'], 1)
        self.assertEqual(path.read_bytes(), b'changed')
        for request in opener.requests:
            self.assertEqual(set(dict(request.header_items())), {'Accept', 'User-agent'})
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_html_and_challenge_are_pending_not_files(self):
        for body, expected in [(b'<html>landing</html>', 'html_page'), (b'<html>captcha</html>', 'challenge'), (b'%PDF-fake', 'invalid_pdf')]:
            self.assertEqual(self.run_download(body)[0]['pending'], 2)
            self.assertEqual({r['pdf_state'] for r in self.db.invoices(self.client.id)}, {expected})
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_cancellation_and_size_limit(self):
        cancel = threading.Event()
        cancel.set()
        result, opener = self.run_download(self.pdf, cancel)
        self.assertTrue(result['cancelled'])
        self.assertEqual(opener.requests, [])
        with patch('myinvoices.pdf_downloads.MAX_BYTES', 10):
            self.assertEqual(self.run_download(self.pdf)[0]['pending'], 2)
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_private_dns_and_redirect_are_rejected(self):
        with patch('myinvoices.pdf_downloads.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(Pending):
                validate_url('https://localhost/pdf')
        for url in ('http://provider.example/pdf', 'https://user:pass@provider.example/pdf', 'https://provider.example:444/pdf'):
            with self.assertRaises(Pending):
                SafeRedirect().redirect_request(Request('https://provider.example'), None, 302, '', {}, url)

    def test_missing_saved_file_downloads_again(self):
        self.run_download(self.pdf)
        next(self.root.rglob('*.pdf')).unlink()
        result, _ = self.run_download(self.pdf)
        self.assertEqual((result['saved'], result['skipped']), (1,1))

    def test_schema_two_upgrade_preserves_records_and_backup(self):
        with self.db.connection() as conn:
            conn.execute('DROP TABLE pdf_attempts')
            conn.execute('PRAGMA user_version=2')
        upgraded = Database(self.db.path)
        self.assertEqual(upgraded.count(self.client.id), 2)
        self.assertEqual(upgraded.client(self.client.id), self.client)
        self.assertEqual(len(list(self.root.glob('*schema2-backup*'))), 1)
        self.assertEqual(self.run_download(self.pdf)[0]['saved'], 2)

    def test_database_failure_removes_new_file(self):
        with patch.object(self.db, 'record_pdf', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.run_download(self.pdf)
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_cancel_during_stream_cleans_partial(self):
        cancel = threading.Event()
        class CancelResponse(Response):
            def read(self, size=-1):
                chunk = super().read(size)
                cancel.set()
                return chunk
        opener = Opener(self.pdf)
        opener.open = lambda *args, **kwargs: CancelResponse(self.pdf)
        result = PDFDownloader(self.db, opener).run(self.client, self.root / 'output', cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_candidates_exclude_cancelled_and_other_clients(self):
        other = self.db.add_client('Other', '111111111')
        self.assertEqual(self.db.download_candidates(other.id), [])
        mark = self.db.download_candidates(self.client.id)[0]['mark']
        with self.db.connection() as conn:
            conn.execute("INSERT INTO document_changes VALUES (?,?,?,?,?,?,?,?)", (self.client.id,'cancellation','synthetic',999,mark,None,'<cancel/>','now'))
        self.assertEqual(len(self.db.download_candidates(self.client.id)), 1)

    def test_http_error_is_pending_and_stream_closed(self):
        from urllib.error import HTTPError
        body = io.BytesIO(b'secret provider response')
        error = HTTPError('https://provider.example/token',403,'Forbidden',{'cf-mitigated':'challenge'},body)
        opener = Opener(b'')
        opener.open = lambda *args, **kwargs: (_ for _ in ()).throw(error)
        result = PDFDownloader(self.db, opener).run(self.client,self.root/'output',threading.Event())
        self.assertEqual(result['pending'],2)
        self.assertTrue(body.closed)
        self.assertFalse(list(self.root.rglob('*.pdf')))
