import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

import test_pdf_downloads as helpers
from myinvoices.models import ApplicationError
from myinvoices.pdf_downloads import PDFDownloader, Pending, archive_folder, open_provider, saved_pdf_path
from myinvoices.local_files import desktop_directory, open_directory


class LocalArchiveTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)

    def test_months_use_registered_client_vat_without_supplier_subfolders(self):
        with self.db.connection() as conn:
            conn.execute("UPDATE invoices SET issue_date='2025-12-31' WHERE mark=101")
            conn.execute("UPDATE invoices SET issue_date='2026-01-01' WHERE mark=102")
        PDFDownloader(self.db,helpers.Opener(self.pdf)).run(self.client,self.root/'Desktop',threading.Event())
        paths={p.relative_to(self.root/'Desktop') for p in (self.root/'Desktop').rglob('*.pdf')}
        self.assertEqual(paths,{
            Path('000000000_ΔΟΚΙΜΑΣΤΙΚΟ/2025-12/101.pdf'),
            Path('000000000_ΔΟΚΙΜΑΣΤΙΚΟ/2026-01/102.pdf')})
        production=SimpleNamespace(vat='123456789',environment='production')
        row=dict(issuer_vat='987654321',issue_date='2026-02-28')
        folder=archive_folder(self.root/'Desktop',production,row)
        self.assertEqual(folder,self.root/'Desktop/123456789/2026-02')
        for invalid in ('2026-02-30','../../other',''):
            with self.assertRaises(Pending):
                archive_folder(self.root/'Desktop',production,dict(row,issue_date=invalid))

    def test_open_link_is_exact_and_has_no_network_preflight_or_saved_state_requirement(self):
        for link in ('https://new-provider.example/view?q=1#page', 'http://other.example:8080/document?id=7'):
            with self.db.connection() as conn:
                conn.execute('UPDATE invoices SET download_url=? WHERE mark=101',(link,))
            browser=Mock(return_value=True)
            with patch('myinvoices.pdf_downloads.socket.getaddrinfo',side_effect=AssertionError('No preflight')):
                open_provider(self.db,self.client,101,browser=browser)
            browser.assert_called_once_with(link,new=2)
        for bad in ('file:///tmp/a.pdf','javascript:alert(1)','https://user:secret@host.example/x','https://host.example/\n'):
            with self.db.connection() as conn:
                conn.execute('UPDATE invoices SET download_url=? WHERE mark=101',(bad,))
            browser=Mock()
            with self.assertRaises(ApplicationError):
                open_provider(self.db,self.client,101,browser=browser)
            browser.assert_not_called()

    def test_saved_pdf_lookup_preserves_old_paths_and_reports_missing_or_changed_files(self):
        with self.assertRaises(ApplicationError):
            saved_pdf_path(self.db,self.client,101)
        legacy=self.root/'legacy'/'old.pdf'
        legacy.parent.mkdir();legacy.write_bytes(self.pdf)
        source=hashlib.sha256(self.db.invoice(self.client.id,101)['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,101,'printed_unverified',source,str(legacy),
                           hashlib.sha256(self.pdf).hexdigest(),len(self.pdf))
        self.assertEqual(saved_pdf_path(self.db,self.client,101),legacy)
        legacy.write_bytes(b'changed')
        with self.assertRaises(ApplicationError):
            saved_pdf_path(self.db,self.client,101)
        self.assertEqual(legacy.read_bytes(),b'changed')
        legacy.unlink()
        with self.assertRaises(ApplicationError):
            saved_pdf_path(self.db,self.client,101)
        other=self.db.add_client('Other','222222222')
        with self.assertRaises(ApplicationError):
            saved_pdf_path(self.db,other,101)

    def test_windows_desktop_respects_known_folder_and_reports_lookup_failure(self):
        redirected=self.root/'OneDrive'/'Επιφάνεια εργασίας'
        with patch('myinvoices.local_files.sys.platform','win32'), \
                patch('myinvoices.local_files._windows_desktop',return_value=redirected):
            self.assertEqual(desktop_directory(),redirected)
        with patch('myinvoices.local_files.sys.platform','win32'), \
                patch('myinvoices.local_files._windows_desktop',side_effect=OSError('private path')):
            with self.assertRaises(ApplicationError) as caught:
                desktop_directory()
            self.assertNotIn('private',str(caught.exception))

    def test_windows_folder_launch_uses_an_argument_without_shell_interpolation(self):
        folder=self.root/'Desktop'/'000000000 & invoices'
        with patch('myinvoices.local_files.sys.platform','win32'), \
                patch.object(os,'startfile',create=True) as opened:
            open_directory(folder)
            opened.assert_called_once_with(str(folder),'explore')
        self.assertTrue(folder.is_dir())


@unittest.skipUnless(os.name == 'nt','Native Windows Known Folder API')
class NativeDesktopTests(unittest.TestCase):
    def test_known_desktop_is_absolute(self):
        path=desktop_directory()
        self.assertTrue(path.is_absolute())
