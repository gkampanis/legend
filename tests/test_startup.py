from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from myinvoices.__main__ import main
from myinvoices.database import Database


class StartupTests(unittest.TestCase):
    def test_default_opens_normal_mode_with_no_synthetic_client(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normal.sqlite3"
            with patch("tkinter.Tk"), patch("myinvoices.gui.App") as app:
                self.assertEqual(main(["--db", str(path)]), 0)
            self.assertFalse(app.call_args.kwargs["demo"])
            self.assertEqual(Database(path).clients(), [])

    def test_demo_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite3"
            with patch("tkinter.Tk"), patch("myinvoices.gui.App") as app:
                self.assertEqual(main(["--demo", "--db", str(path)]), 0)
            self.assertTrue(app.call_args.kwargs["demo"])
            self.assertEqual(Database(path).clients()[0].vat, "000000000")
