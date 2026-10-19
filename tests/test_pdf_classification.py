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

    def test_human_print_confirmation_preserves_provenance_and_moves_tabs(self):
        import io
        from unittest.mock import patch
        from pypdf import PdfWriter
        from myinvoices.gui import App
        add_invoices(self.db,self.client,[100])
        record_attempt(self.db,self.client,100,'challenge')
        self.app = App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        self.assertEqual(self.app.failure_tree.get_children(),('100',))
        writer=PdfWriter()
        writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO()
        writer.write(stream)
        downloads=self.directory/'Downloads'
        downloads.mkdir()
        self.app.browser_download_folder=str(downloads)
        self.app.download_roots[self.client.id]=str(self.directory/'archive')
        def printed(*args,**kwargs):
            (downloads/'printed.pdf').write_bytes(stream.getvalue())
        with patch('myinvoices.interactive_downloads.open_provider',side_effect=printed), patch.object(self.app,'_ask_pdf_provenance',return_value='printed') as confirm:
            self.app.browser_pdf_button.invoke()
            self.wait()
            confirm.assert_called_once()
        self.assertEqual(self.app.success_tree.get_children(),('100',))
        self.assertEqual(self.app.success_tree.set('100','method'),'Εκτύπωση σε PDF')
        self.assertEqual(self.app.failure_tree.get_children(),())
        self.assertEqual(self.db.invoices(self.client.id)[0]['pdf_state'],'printed_unverified')
        self.assertIn('Από εκτύπωση: 1',self.app.message_text.get('1.0','end'))
        self.assertTrue((downloads/'printed.pdf').is_file())

    def test_print_choice_dialog_returns_printed_without_boolean_coercion(self):
        import tkinter as tk
        from tkinter import ttk
        from myinvoices.gui import App
        self.app = App(self.root,self.db,today=date(2026,11,1))
        self.wait()
        def choose_printed():
            def visit(widget):
                for child in widget.winfo_children():
                    if isinstance(child,ttk.Button) and child.cget('text')=='PDF από εκτύπωση':
                        child.invoke()
                        return True
                    if visit(child):
                        return True
                return False
            self.assertTrue(any(visit(dialog) for dialog in self.root.winfo_children() if isinstance(dialog,tk.Toplevel)))
        self.root.after(50,choose_printed)
        self.assertEqual(self.app._ask_pdf_provenance({'mark':100},Path('printed.pdf')),'printed')
        self.assertEqual(self.errors,[])
