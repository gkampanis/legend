import os
from datetime import date
from pathlib import Path
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "GUI requires a display (or Xvfb)")
class GUITests(unittest.TestCase):
    def test_copy_error_history_and_filter_clients(self):
        import tkinter as tk
        from myinvoices.database import Database
        from myinvoices.gui import App
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "gui.sqlite3")
            first = database.add_client("Αθήνα", "000000000")
            second = database.add_client("Πάτρα", "111111111")
            from test_changes import mixed_page
            from myinvoices.xml_parser import parse_response
            database.commit_page(database.checkpoint(first.id), parse_response(mixed_page()))
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, database,today=date(2026,11,1))
                deadline = time.monotonic() + 10
                while app.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertFalse(app.busy)
                self.assertEqual(len(app.changes_tree.get_children()), 4)
                invoice_row = app.document_tree.get_children()[0]
                self.assertEqual(app.document_tree.set(invoice_row, "status"), "Ακυρωμένο (myDATA)")
                from unittest.mock import patch
                from myinvoices.models import ParseError
                def failed_check(*_args, **kwargs):
                    kwargs["stage"]("xml")
                    raise ParseError("Μη αναμενόμενη απόκριση RequestDocs [XML_ROOT=Error].", "Error")
                with patch("myinvoices.gui.check_client", side_effect=failed_check):
                    app._check_api()
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy)
                self.assertIn("[xml]", app.last_error)
                self.assertIn("XML_ROOT=Error", app.message_text.get("1.0", "end"))
                app._copy_error()
                self.assertEqual(root.clipboard_get(), app.last_error)
                self.assertTrue((Path(directory) / "diagnostics.jsonl").exists())
                message = "[xml] Μη αναμενόμενη απόκριση RequestDocs [XML_ROOT=Error]."
                app._message(message, error=True)
                app._message("Εργασία ολοκληρώθηκε")
                app._copy_error()
                self.assertEqual(root.clipboard_get(), message)
                app._copy_messages()
                self.assertIn(message, root.clipboard_get())
                self.assertIn("Εργασία ολοκληρώθηκε", root.clipboard_get())
                self.assertEqual(str(app.message_text.cget("state")), "disabled")
                app._select_messages()
                self.assertTrue(app.message_text.tag_ranges("sel"))
                app.client_filter.set("ΠΑΤΡ")
                self.assertEqual(app.client_tree.get_children(), (second.id,))
                app.client_filter.set("000000000")
                self.assertEqual(app.client_tree.get_children(), (first.id,))
                app._clear_messages()
                self.assertEqual(app.message_text.get("1.0", "end-1c"), "")
                self.assertEqual(app.last_error, "")
                self.assertEqual(str(app.messages_menu.entrycget(0,"state")), "disabled")
            finally:
                root.destroy()

    def test_client_form_loads_and_saves_only_selected_credentials(self):
        import tkinter as tk
        from tkinter import ttk
        from myinvoices.database import Database
        from myinvoices.credential_store import CredentialStore
        from myinvoices.models import Credentials
        from myinvoices.gui import App

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "gui.sqlite3")
            first = database.add_client("A", "000000000")
            second = database.add_client("B", "111111111")
            store = CredentialStore(Path(directory) / "credentials")
            store.set(first, Credentials("FIRST-USER", "FIRST-KEY"))
            store.set(second, Credentials("SECOND-USER", "SECOND-KEY"))
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)
            try:
                app = App(root, database, credential_store=store)
                self.assertFalse(app.demo)
                def wait_for(predicate):
                    deadline = time.monotonic() + 10
                    while not predicate() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertTrue(predicate())
                    self.assertEqual(errors, [])
                wait_for(lambda: not app.busy)
                self.assertEqual(app.selected, first.id)
                app._edit_client()
                wait_for(lambda: not app.busy)
                window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                frame = window.winfo_children()[0]
                entries = {int(w.grid_info()["row"]): w for w in frame.winfo_children() if isinstance(w, ttk.Entry)}
                self.assertEqual(entries[2].get(), "FIRST-USER")
                self.assertEqual(entries[3].get(), "FIRST-KEY")
                self.assertEqual(str(entries[2].cget("state")), "normal")
                self.assertEqual(str(entries[3].cget("state")), "normal")
                self.assertEqual(entries[3].cget("show"), "•")
                self.assertEqual(str(entries[1].cget("state")), "readonly")
                entries[0].delete(0, "end")
                entries[0].insert(0, "A renamed")
                entries[3].delete(0, "end")
                entries[3].insert(0, "UPDATED-KEY")
                button = next(w for w in frame.winfo_children() if isinstance(w, ttk.Button))
                button.invoke()
                wait_for(lambda: not app.busy and not window.winfo_exists())
                self.assertEqual(store.get(first).subscription_key, "UPDATED-KEY")
                self.assertEqual(store.get(second).subscription_key, "SECOND-KEY")
                self.assertEqual(database.client(first.id).name, "A renamed")
                self.assertEqual(errors, [])
            finally:
                root.destroy()

    def test_demo_refresh_synchronize_and_repeat(self):
        import tkinter as tk
        from myinvoices.database import Database
        from myinvoices.__main__ import prepare_demo
        from myinvoices.gui import App

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "gui.sqlite3")
            client = prepare_demo(database)
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)
            try:
                app = App(root, database, demo=True)
                def wait_for(predicate):
                    deadline = time.monotonic() + 10
                    while not predicate() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertTrue(predicate(), "GUI worker did not finish")
                    self.assertEqual(errors, [])
                wait_for(lambda: not app.busy)
                self.assertEqual(app.selected, client.id)
                self.assertFalse(hasattr(app, "user_entry"))
                self.assertFalse(hasattr(app, "key_entry"))
                self.assertEqual(app.sync_button.cget("text"), "1. Εύρεση παραστατικών")
                app._sync()
                wait_for(lambda: not app.busy)
                self.assertEqual(app.last_result.pages, 2)
                self.assertEqual(app.total, 2)
                rows = app.document_tree.get_children()
                self.assertEqual(len(rows), 2)
                statuses = {app.document_tree.set(row, "status") for row in rows}
                self.assertEqual(statuses, {"Χωρίς link PDF", "Υπάρχει link — PDF ανεξέλεγκτο"})
                app._sync()
                wait_for(lambda: not app.busy)
                self.assertEqual(app.total, 2)
                self.assertEqual(app.last_result.records, 0)
                other = database.add_client("Άλλος demo πελάτης", "111111111")
                app.refresh()
                wait_for(lambda: not app.busy)
                app.client_tree.selection_set(other.id)
                app._select()
                wait_for(lambda: not app.busy)
                app._sync()
                self.assertIn("μόνο για τον συνθετικό πελάτη", app.status.get())
                self.assertEqual(errors, [])
            finally:
                root.destroy()

    def test_date_filter_browser_and_pdf_import_workflow(self):
        import io
        import threading
        import tkinter as tk
        from unittest.mock import patch
        from pypdf import PdfWriter
        from myinvoices.database import Database
        from myinvoices.demo import DemoAPI
        from myinvoices.sync import Synchronizer
        from myinvoices.gui import App
        from myinvoices.pdf_downloads import PDFDownloader
        from test_pdf_downloads import Opener
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            db = Database(folder/'gui.sqlite3')
            client = db.add_client('Synthetic','000000000')
            Synchronizer(db,DemoAPI()).run(client.id,threading.Event())
            with db.connection() as conn:
                conn.execute("UPDATE invoices SET issue_date='2025-01-01' WHERE mark=102")
            writer = PdfWriter()
            writer.add_blank_page(width=100,height=100)
            stream = io.BytesIO()
            writer.write(stream)
            source = folder/'browser.pdf'
            source.write_bytes(stream.getvalue())
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)
            try:
                app = App(root,db)
                def wait():
                    deadline = time.monotonic()+10
                    while app.busy and time.monotonic()<deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy)
                    self.assertEqual(errors,[])
                wait()
                app._clear_dates()
                wait()
                self.assertEqual(app.total,2)
                app.date_from.set('01/10/2026')
                app.date_to.set('31/10/2026')
                app._apply_dates()
                wait()
                self.assertEqual(app.total,1)
                self.assertEqual(app.document_tree.get_children(),('101',))
                app.document_tree.selection_set('101')
                app.download_roots[client.id]=str(folder/'archive')
                with patch('myinvoices.gui.open_provider') as browser:
                    app._open_provider()
                    wait()
                    browser.assert_called_once_with(db,client,101)
                with patch('myinvoices.gui.filedialog.askopenfilename',return_value=str(source)), patch('myinvoices.gui.messagebox.askyesno',return_value=True), patch('myinvoices.gui.filedialog.askdirectory',return_value=str(folder/'archive')):
                    app._import_pdf()
                    wait()
                self.assertIn('PDF αποθηκευμένο',app.document_tree.set('101','status'))
                self.assertEqual(source.read_bytes(),stream.getvalue())
                with patch('myinvoices.gui.filedialog.askdirectory',return_value=str(folder/'archive')), patch('myinvoices.gui.PDFDownloader',side_effect=lambda database: PDFDownloader(database,Opener(stream.getvalue()))):
                    app.pdf_button.invoke()
                    wait()
                self.assertIn('Ήδη αποθηκευμένα: 1',app.status.get())
                app.document_tree.selection_set('101')
                with patch('myinvoices.gui.open_provider') as browser:
                    app.browser_pdf_button.invoke()
                    wait()
                    browser.assert_called_once_with(db,client,101)
                self.assertIn('Άνοιξε ο σύνδεσμος',app.status.get())
                app.date_from.set('31/02/2026')
                app._apply_dates()
                self.assertIn('ΗΗ/ΜΜ/ΕΕΕΕ',app.last_error)
                app._clear_dates()
                wait()
                app._clear_dates()
                wait()
                self.assertEqual(app.total,2)
            finally:
                root.destroy()

    def test_three_primary_steps_and_previous_month_default(self):
        import tkinter as tk
        from myinvoices.database import Database
        from myinvoices.gui import App
        with tempfile.TemporaryDirectory() as directory:
            db=Database(Path(directory)/'gui.sqlite3')
            db.add_client('Synthetic','000000000')
            root=tk.Tk()
            root.withdraw()
            try:
                app=App(root,db,today=date(2026,10,19))
                deadline=time.monotonic()+10
                while app.busy and time.monotonic()<deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertFalse(app.busy)
                self.assertEqual((app.date_from.get(),app.date_to.get()),('01/09/2026','30/09/2026'))
                self.assertEqual([button.cget('text') for button in (app.sync_button,app.pdf_button,app.browser_pdf_button)],
                                 ['1. Εύρεση παραστατικών','2. Λήψη PDF','3. Άνοιγμα στον browser'])
                self.assertEqual((app.stat_total.get(),app.stat_saved.get(),app.stat_pending.get()),('0','0','0'))
                self.assertEqual(app.stop_button.winfo_manager(),'')
                self.assertFalse(hasattr(app,'add_button'))
            finally:
                root.destroy()

    def test_browser_button_opens_one_exact_link_without_download_loop(self):
        import hashlib, io, threading
        import tkinter as tk
        from unittest.mock import patch
        from pypdf import PdfWriter
        from myinvoices.database import Database
        from myinvoices.demo import DemoAPI
        from myinvoices.sync import Synchronizer
        from myinvoices.gui import App
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            db=Database(directory/'gui.sqlite3')
            client=db.add_client('Synthetic','000000000')
            Synchronizer(db,DemoAPI()).run(client.id,threading.Event())
            link='https://different-provider.example/102?view=invoice#original'
            with db.connection() as conn:
                conn.execute("UPDATE invoices SET download_url=?,status='url_available' WHERE mark=102",(link,))
            source=hashlib.sha256(link.encode()).hexdigest()
            db.record_pdf(client.id,102,'challenge',source)
            root=tk.Tk();root.withdraw()
            errors=[]
            root.report_callback_exception=lambda *error:errors.append(error)
            try:
                app=App(root,db,today=date(2026,11,1))
                def wait():
                    deadline=time.monotonic()+10
                    while app.busy and time.monotonic()<deadline:
                        root.update();time.sleep(0.01)
                    root.update()
                    self.assertFalse(app.busy)
                    self.assertEqual(errors,[])
                wait()
                app.document_book.select(app.failure_tree.master)
                root.update()
                app.failure_tree.selection_set('102')
                with patch('myinvoices.pdf_downloads.webbrowser.open',return_value=True) as browser, \
                        patch('myinvoices.pdf_downloads.socket.getaddrinfo',side_effect=AssertionError('No DNS preflight')), \
                        patch('myinvoices.gui.filedialog.askdirectory',side_effect=AssertionError('No folder picker')), \
                        patch('myinvoices.gui.AutomaticPDFDownloader',side_effect=AssertionError('No downloader')):
                    app.browser_pdf_button.invoke()
                    wait()
                    for _ in range(10):
                        root.update();time.sleep(0.01)
                    browser.assert_called_once_with(link,new=2)
                    app.browser_pdf_button.invoke()
                    wait()
                    self.assertEqual(browser.call_count,2)
                self.assertFalse(hasattr(app,'browser_download_folder'))
                with db.connection() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) FROM pdf_attempts').fetchone()[0],1)
                self.assertEqual(app.failure_tree.get_children(),('102',))
                self.assertFalse(list(directory.rglob('*.pdf')))
            finally:
                root.destroy()
