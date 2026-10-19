"""Per-client/current-source download classification and SQL pagination."""
import hashlib
import os
from datetime import date
from pathlib import Path
import tempfile
import unittest

from myinvoices.database import Database
from myinvoices.date_range import DateRange
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.models import ApplicationError, DocumentChange, Page
from myinvoices.pdf_downloads import SAVED_STATES
from myinvoices.xml_parser import parse_response


def add_invoices(database, client, marks, *, date='2026-10-01', url=True):
    body = xml_page([xml_invoice(mark, recipient=client.vat, url=url).replace('2026-10-01', date)
                     for mark in marks])
    database.commit_page(database.checkpoint(client.id), parse_response(body))


def record_attempt(database, client, mark, state):
    row = database.invoice(client.id, mark)
    source = hashlib.sha256((row['download_url'] or '').encode()).hexdigest()
    saved = state in SAVED_STATES
    database.record_pdf(client.id, mark, state, source,
                        f'/synthetic/{mark}.pdf' if saved else None,
                        'synthetic-checksum' if saved else None, 123 if saved else 0)
    return source


class PDFClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root/'data.sqlite3')
        self.client = self.db.add_client('Synthetic', '000000000')
        self.dates = DateRange.parse('01/10/2026', '31/10/2026')

    def test_provenance_buckets_do_not_count_unattempted_as_failures(self):
        add_invoices(self.db, self.client, range(100, 106))
        add_invoices(self.db, self.client, [106], url=False)
        for mark, state in zip(range(100, 103), SAVED_STATES):
            source = record_attempt(self.db, self.client, mark, state)
            self.assertEqual(self.db.saved_pdf(self.client.id, mark, source)['state'], state)
        record_attempt(self.db, self.client, 103, 'challenge')
        record_attempt(self.db, self.client, 104, 'print_not_invoice')
        self.assertEqual(self.db.count(self.client.id), 7)
        successes = self.db.invoices(self.client.id, bucket='success')
        self.assertEqual({r['pdf_state'] for r in successes}, set(SAVED_STATES))
        self.assertEqual({r['mark'] for r in self.db.invoices(self.client.id, bucket='failure')}, {103,104})
        self.assertEqual(self.db.pdf_stats(self.client.id), {'total':7,'saved':3,'failed':2,'pending':2})
        with self.assertRaises(ApplicationError):
            self.db.invoices(self.client.id, bucket='not-a-bucket')

    def test_current_source_and_tombstones_preserve_append_only_history(self):
        add_invoices(self.db, self.client, [100,101])
        checkpoint = self.db.checkpoint(self.client.id)
        source = record_attempt(self.db, self.client, 100, 'printed_unverified')
        record_attempt(self.db, self.client, 100, 'browser_timeout')
        self.assertEqual(self.db.count(self.client.id, bucket='success'), 1)
        self.assertEqual(self.db.invoices(self.client.id)[1]['pdf_state'], 'printed_unverified')
        record_attempt(self.db, self.client, 100, 'artifact_missing')
        self.assertIsNone(self.db.saved_pdf(self.client.id, 100, source))
        self.assertEqual(self.db.count(self.client.id, bucket='success'), 0)
        record_attempt(self.db, self.client, 100, 'network_error')
        self.assertEqual(self.db.count(self.client.id, bucket='failure'), 1)
        record_attempt(self.db, self.client, 100, 'saved_unverified')
        self.assertEqual(self.db.count(self.client.id, bucket='success'), 1)
        record_attempt(self.db, self.client, 100, 'artifact_changed')
        self.assertIsNone(self.db.saved_pdf(self.client.id, 100, source))
        record_attempt(self.db, self.client, 100, 'saved_unverified')
        record_attempt(self.db, self.client, 101, 'challenge')
        with self.db.connection() as conn:
            attempts = conn.execute('SELECT count(*) FROM pdf_attempts').fetchone()[0]
            conn.execute("UPDATE invoices SET download_url='https://provider.example/new' WHERE client_id=?", (self.client.id,))
        self.assertEqual(self.db.count(self.client.id, bucket='success'), 0)
        self.assertEqual(self.db.count(self.client.id, bucket='failure'), 0)
        self.assertEqual(self.db.pending_candidates(self.client.id), [])
        self.assertTrue(all(r['pdf_state'] is None for r in self.db.invoices(self.client.id)))
        reopened = Database(self.db.path)
        self.assertEqual(reopened.checkpoint(self.client.id), checkpoint)
        with reopened.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM pdf_attempts').fetchone()[0], attempts)
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 4)

    def test_filters_before_pagination_with_client_dates_and_cancellation(self):
        add_invoices(self.db, self.client, range(1000, 1500))
        for mark in range(1000, 1221):
            record_attempt(self.db, self.client, mark, 'printed_unverified')
        for mark in range(1221, 1442):
            record_attempt(self.db, self.client, mark, 'challenge')
        add_invoices(self.db, self.client, [1500], date='2026-09-30')
        record_attempt(self.db, self.client, 1500, 'challenge')
        other = self.db.add_client('Other', '222222222')
        add_invoices(self.db, other, [1000])
        record_attempt(self.db, other, 1000, 'saved_unverified')
        cancelled = DocumentChange('cancellation', 9001, 1441, '<cancellation/>')
        self.db.commit_page(self.db.checkpoint(self.client.id), Page((), changes=(cancelled,)))
        for bucket, expected in (('all',500),('success',221),('failure',220)):
            self.assertEqual(self.db.count(self.client.id, self.dates, bucket=bucket), expected)
            first = self.db.invoices(self.client.id, limit=200, dates=self.dates, bucket=bucket)
            second = self.db.invoices(self.client.id, offset=200, limit=200, dates=self.dates, bucket=bucket)
            self.assertEqual(len(first),200)
            self.assertEqual(len(second),min(expected-200,200))
            self.assertFalse({r['mark'] for r in first} & {r['mark'] for r in second})
        self.assertNotIn(1441, {r['mark'] for r in self.db.pending_candidates(self.client.id,self.dates)})
        self.assertEqual(len(self.db.pending_candidates(self.client.id,self.dates)),220)
        self.assertEqual(self.db.pdf_stats(self.client.id,self.dates), {'total':500,'saved':221,'failed':220,'pending':220})
        self.assertEqual(self.db.count(other.id, self.dates, bucket='success'),1)


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'GUI requires a display (or Xvfb)')
class PDFClassificationGUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = Database(self.directory/'gui.sqlite3')
        self.client = self.db.add_client('Synthetic', '000000000')
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)

    def wait(self):
        import time
        deadline = time.monotonic()+10
        while self.app.busy and time.monotonic()<deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.errors, [])

    def select_bucket(self, bucket):
        self.app.document_book.select(self.app.document_trees[bucket].master)
        self.root.update()
        self.assertEqual(self.app.document_bucket, bucket)

    def test_tabs_paginate_independently_and_commands_use_active_selection(self):
        from unittest.mock import patch
        from myinvoices.gui import App
        add_invoices(self.db, self.client, range(1000,1443))
        for mark in range(1000,1221):
            record_attempt(self.db, self.client, mark, 'printed_unverified')
        for mark in range(1221,1442):
            record_attempt(self.db, self.client, mark, 'challenge')
        self.app = App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        self.assertEqual(self.app.bucket_totals, {'all':443,'success':221,'failure':221})
        self.assertEqual(len(self.app.success_tree.get_children()),200)
        self.assertEqual(len(self.app.failure_tree.get_children()),200)
        self.assertEqual(self.app.success_tree.set('1220','method'),'Εκτύπωση σε PDF')
        self.assertIn('CAPTCHA',self.app.failure_tree.set('1441','reason'))
        self.app.document_tree.selection_set('1442')
        self.select_bucket('success')
        self.app._page(1)
        self.wait()
        self.assertEqual((self.app.offset,self.app.total),(200,221))
        self.assertEqual(len(self.app.success_tree.get_children()),21)
        self.select_bucket('failure')
        self.assertEqual(self.app.offset,0)
        self.app.failure_tree.selection_set('1441')
        with patch('myinvoices.gui.open_provider') as opened:
            self.app._open_provider()
            self.wait()
            opened.assert_called_once_with(self.db,self.client,1441)
        self.app._page(1)
        self.wait()
        self.assertEqual(self.app.offsets, {'all':0,'success':200,'failure':200})
        self.assertEqual(len(self.app.failure_tree.get_children()),21)
        self.select_bucket('success')
        self.assertEqual(self.app.offset,200)
        self.select_bucket('all')
        self.assertEqual((self.app.offset,self.app.total),(0,443))
        self.assertEqual(self.app._selected_document()['mark'],1442)
        self.app.date_from.set('01/09/2026')
        self.app.date_to.set('30/09/2026')
        self.app._apply_dates()
        self.wait()
        self.assertEqual(self.app.offsets, {'all':0,'success':0,'failure':0})
        self.assertTrue(all(not tree.get_children() for tree in self.app.document_trees.values()))

    def test_open_saved_pdf_from_success_tab_uses_legacy_location(self):
        import io, time
        from pypdf import PdfWriter
        from myinvoices.gui import App
        add_invoices(self.db,self.client,[100])
        legacy=self.directory/'previous-archive'/'100.pdf'
        legacy.parent.mkdir()
        writer=PdfWriter()
        writer.add_blank_page(width=100,height=160)
        writer.add_blank_page(width=160,height=100)
        stream=io.BytesIO();writer.write(stream)
        legacy.write_bytes(stream.getvalue())
        source=hashlib.sha256(self.db.invoice(self.client.id,100)['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,100,'printed_unverified',source,str(legacy),
                           hashlib.sha256(stream.getvalue()).hexdigest(),len(stream.getvalue()))
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        self.select_bucket('success')
        self.app.success_tree.selection_set('100')
        self.app.open_pdf_button.invoke()
        self.wait()
        preview=self.app.pdf_preview
        deadline=time.monotonic()+10
        while preview.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(0.01)
        self.assertFalse(preview.busy)
        self.assertEqual(preview.count,2)
        self.assertEqual(preview.path,legacy)
        self.assertIsNotNone(preview.photo)
        self.assertEqual(legacy.read_bytes(),stream.getvalue())
        self.assertEqual(self.db.count(self.client.id,bucket='success'),1)
        preview.window.destroy()
        self.assertTrue(preview.cancel.is_set())
        self.assertIsNone(preview.poll_id)

    def test_folder_button_uses_desktop_or_selected_client_override(self):
        from unittest.mock import patch
        from myinvoices.gui import App
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        desktop=self.directory/'OneDrive'/'Desktop'
        def mkdir(path):
            path.mkdir(parents=True,exist_ok=True)
        with patch('myinvoices.gui.desktop_directory',return_value=desktop), \
                patch('myinvoices.gui.open_directory',side_effect=mkdir) as opened:
            self.app.open_folder_button.invoke()
            self.wait()
            expected=desktop/'000000000_ΔΟΚΙΜΑΣΤΙΚΟ'
            opened.assert_called_once_with(expected)
            self.assertTrue(expected.is_dir())
            self.assertEqual(self.app.download_roots[self.client.id],str(desktop))
            custom=self.directory/'custom'
            self.app.download_roots[self.client.id]=str(custom)
            self.app.open_folder_button.invoke()
            self.wait()
            self.assertEqual(opened.call_args.args,(custom/'000000000_ΔΟΚΙΜΑΣΤΙΚΟ',))

    def test_download_defaults_to_desktop_and_keeps_invoice_months(self):
        import io, socket
        from unittest.mock import patch
        from pypdf import PdfWriter
        import test_pdf_downloads as helpers
        from myinvoices.gui import App
        from myinvoices.pdf_downloads import PDFDownloader
        add_invoices(self.db,self.client,[100])
        add_invoices(self.db,self.client,[101],date='2026-09-30')
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        data=io.BytesIO();writer.write(data)
        desktop=self.directory/'Desktop'
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        def downloader(database,**kwargs):
            return PDFDownloader(database,helpers.Opener(data.getvalue()))
        with patch('myinvoices.gui.desktop_directory',return_value=desktop), \
                patch('myinvoices.gui.AutomaticPDFDownloader',side_effect=downloader), \
                patch('myinvoices.pdf_downloads.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]), \
                patch('myinvoices.gui.filedialog.askdirectory',side_effect=AssertionError('Default must not prompt')):
            self.app.pdf_button.invoke();self.wait()
            self.assertEqual(len(list(desktop.rglob('*.pdf'))),1)
            self.app._clear_dates();self.wait()
            self.app.pdf_button.invoke();self.wait()
        paths={p.relative_to(desktop) for p in desktop.rglob('*.pdf')}
        self.assertEqual(paths,{
            Path('000000000_ΔΟΚΙΜΑΣΤΙΚΟ/2026-10/100.pdf'),
            Path('000000000_ΔΟΚΙΜΑΣΤΙΚΟ/2026-09/101.pdf')})
        self.assertEqual(self.app.bucket_totals['success'],2)

    def test_reset_button_requires_confirmation_and_clears_only_download_states(self):
        from unittest.mock import patch
        from myinvoices.gui import App
        add_invoices(self.db,self.client,[100,101])
        for mark in (100,101):
            record_attempt(self.db,self.client,mark,'printed_unverified')
        checkpoint=self.db.checkpoint(self.client.id)
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        with patch('myinvoices.gui.messagebox.askyesno',return_value=False):
            self.app.reset_downloads_button.invoke()
            self.wait()
        self.assertEqual(self.app.stat_saved.get(),'2')
        with patch('myinvoices.gui.messagebox.askyesno',return_value=True) as confirm:
            self.app.reset_downloads_button.invoke()
            self.wait()
        self.assertIn(self.client.vat,confirm.call_args.args[1])
        self.assertEqual(self.app.bucket_totals,dict(all=2,success=0,failure=0))
        self.assertEqual(self.db.checkpoint(self.client.id),checkpoint)
        first=self.app.history_tree.get_children()[0]
        self.assertEqual(self.app.history_tree.item(first,'values')[1],'Μηδενισμός λήψεων')

    def test_folder_delete_dialog_shows_exact_folder_and_cancel_is_non_destructive(self):
        import io, shutil
        from unittest.mock import patch
        from pypdf import PdfWriter
        from myinvoices.gui import App
        from myinvoices.archive_actions import trash_client_archive
        add_invoices(self.db,self.client,[100])
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO();writer.write(stream)
        desktop=self.directory/'Desktop'
        folder=desktop/'000000000_ΔΟΚΙΜΑΣΤΙΚΟ'
        file=folder/'2026-10'/'100.pdf'
        file.parent.mkdir(parents=True);file.write_bytes(stream.getvalue())
        source=hashlib.sha256(self.db.invoice(self.client.id,100)['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,100,'saved_unverified',source,str(file),
                           hashlib.sha256(stream.getvalue()).hexdigest(),len(stream.getvalue()))
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        self.app.download_roots[self.client.id]=str(desktop)
        with patch('myinvoices.gui.messagebox.askyesno',return_value=False), \
                patch('myinvoices.gui.trash_client_archive') as remove:
            self.app.delete_archive_button.invoke();self.wait()
            remove.assert_not_called()
        self.assertTrue(file.is_file())
        self.assertEqual(self.app.stat_saved.get(),'1')
        recycle=self.directory/'Recycle'
        def remove(database,client,root):
            return trash_client_archive(database,client,root,trash=lambda path:shutil.move(path,recycle))
        with patch('myinvoices.gui.messagebox.askyesno',return_value=True) as confirm, \
                patch('myinvoices.gui.trash_client_archive',side_effect=remove):
            self.app.delete_archive_button.invoke();self.wait()
        self.assertIn(str(folder),confirm.call_args.args[1])
        self.assertFalse(folder.exists())
        self.assertEqual(len(list(recycle.rglob('*.pdf'))),1)
        self.assertEqual(self.app.stat_saved.get(),'0')
        self.assertEqual(self.db.client(self.client.id),self.client)

    def test_recovery_button_processes_failed_rows_without_reset_or_manual_browser(self):
        import io
        from unittest.mock import patch
        from pypdf import PdfWriter
        from myinvoices.gui import App
        from myinvoices.pdf_downloads import PDFDownloader,PDFArtifact,archive_pdf_bytes
        add_invoices(self.db,self.client,[100,101,102])
        record_attempt(self.db,self.client,100,'saved_unverified')
        record_attempt(self.db,self.client,101,'challenge')
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        data=io.BytesIO();writer.write(data)
        recovered=[]
        class SyntheticRecovery(PDFDownloader):
            def _candidates(inner,client_id,dates=None):
                return inner.db.failed_download_candidates(client_id,dates)
            def _download(inner,client,row,root,cancel):
                recovered.append(row['mark'])
                return PDFArtifact(*archive_pdf_bytes(data.getvalue(),client,row,root,cancel),'printed_unverified')
        self.app=App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        self.select_bucket('failure')
        self.app.download_roots[self.client.id]=str(self.directory/'Desktop')
        with patch('myinvoices.gui.RecoveryPDFDownloader',side_effect=lambda db,**kwargs:SyntheticRecovery(db)), \
                patch('myinvoices.gui.open_provider',side_effect=AssertionError('Recovery must capture documents')), \
                patch.object(self.db,'reset_downloads',side_effect=AssertionError('No reset during recovery')):
            self.app.recovery_button.invoke();self.wait()
        self.assertEqual(recovered,[101])
        self.assertEqual(self.app.bucket_totals,dict(all=3,success=2,failure=0))
        self.assertEqual(self.app.success_tree.set('101','method'),'Εκτύπωση σε PDF')
        self.assertEqual(len(list((self.directory/'Desktop').rglob('*.pdf'))),1)
        self.assertIn('Η ανάκτηση ολοκληρώθηκε',self.app.message_text.get('1.0','end'))
