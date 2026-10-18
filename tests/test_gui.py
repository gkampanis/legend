import os
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
                app = App(root, database)
                deadline = time.monotonic() + 10
                while app.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertFalse(app.busy)
                self.assertEqual(len(app.changes_tree.get_children()), 4)
                invoice_row = app.document_tree.get_children()[0]
                self.assertEqual(app.document_tree.item(invoice_row, "values")[-1], "Ακυρωμένο (myDATA)")
                from unittest.mock import patch
                from myinvoices.models import ParseError
                def failed_check(*_args, **kwargs):
                    kwargs["stage"]("xml")
                    raise ParseError("Μη αναμενόμενη απόκριση RequestDocs [XML_ROOT=Error].", "Error")
                with patch("myinvoices.gui.check_client", side_effect=failed_check):
                    app.check_button.invoke()
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy)
                self.assertIn("[xml]", app.last_error)
                self.assertIn("XML_ROOT=Error", app.message_text.get("1.0", "end"))
                app.copy_error_button.invoke()
                self.assertEqual(root.clipboard_get(), app.last_error)
                self.assertTrue((Path(directory) / "diagnostics.jsonl").exists())
                message = "[xml] Μη αναμενόμενη απόκριση RequestDocs [XML_ROOT=Error]."
                app._message(message, error=True)
                app._message("Εργασία ολοκληρώθηκε")
                app.copy_error_button.invoke()
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
                self.assertEqual(str(app.copy_error_button.cget("state")), "disabled")
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
                self.assertEqual(app.edit_button.cget("text"), "Στοιχεία πελάτη")
                app._sync()
                wait_for(lambda: not app.busy)
                self.assertEqual(app.last_result.pages, 2)
                self.assertEqual(app.total, 2)
                rows = app.document_tree.get_children()
                self.assertEqual(len(rows), 2)
                statuses = {app.document_tree.item(row, "values")[-1] for row in rows}
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
                self.assertEqual(app.total,2)
                app.date_from.set('01/10/2026')
                app.date_to.set('31/10/2026')
                app.dates_button.invoke()
                wait()
                self.assertEqual(app.total,1)
                self.assertEqual(app.document_tree.get_children(),('101',))
                app.document_tree.selection_set('101')
                with patch('myinvoices.gui.open_provider') as browser:
                    app.browser_button.invoke()
                    wait()
                    browser.assert_called_once_with(db,client,101)
                with patch('myinvoices.gui.filedialog.askopenfilename',return_value=str(source)), patch('myinvoices.gui.messagebox.askyesno',return_value=True), patch('myinvoices.gui.filedialog.askdirectory',return_value=str(folder/'archive')):
                    app.import_button.invoke()
                    wait()
                self.assertIn('PDF από browser',app.document_tree.item('101','values')[-1])
                self.assertEqual(source.read_bytes(),stream.getvalue())
                with patch('myinvoices.gui.filedialog.askdirectory',return_value=str(folder/'archive')), patch('myinvoices.gui.PDFDownloader',side_effect=lambda database: PDFDownloader(database,Opener(stream.getvalue()))):
                    app.pdf_button.invoke()
                    wait()
                self.assertIn('Ήδη αποθηκευμένα: 1',app.status.get())
                app.date_from.set('31/02/2026')
                app.dates_button.invoke()
                self.assertIn('ΗΗ/ΜΜ/ΕΕΕΕ',app.last_error)
                app.clear_dates_button.invoke()
                wait()
                self.assertEqual(app.total,2)
            finally:
                root.destroy()
