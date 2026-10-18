import os
from pathlib import Path
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "GUI requires a display (or Xvfb)")
class GUITests(unittest.TestCase):
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
                self.assertEqual(errors, [])
            finally:
                root.destroy()
