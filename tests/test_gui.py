import os
from pathlib import Path
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "GUI requires a display (or Xvfb)")
class GUITests(unittest.TestCase):
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
