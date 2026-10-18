from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from myinvoices.database import Database
from myinvoices.diagnostics import record_failure
from myinvoices.models import ApplicationError
from myinvoices.probe import main as probe


class DiagnosticTests(unittest.TestCase):
    def test_diagnostics_do_not_include_exception_text_or_local_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            try:
                raise ValueError("SYNTHETIC-SECRET-DO-NOT-LOG")
            except ValueError as error:
                self.assertTrue(record_failure(path, error, "credentials"))
            record = json.loads(path.read_text())
            self.assertEqual(record["stage"], "credentials")
            self.assertEqual(record["exception_type"], "ValueError")
            self.assertNotIn("SYNTHETIC-SECRET", path.read_text())

    def test_missing_secret_bindings_never_send_request(self):
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), patch("myinvoices.probe.RequestDocsAPI") as api, redirect_stdout(output):
            self.assertEqual(probe(), 2)
        api.assert_not_called()

    def test_probe_only_reads_today_without_printing_data(self):
        output = io.StringIO()
        with patch.dict("os.environ", {"MYINVOICES_AADE_USER_ID": "SYNTHETIC-USER",
                "MYINVOICES_AADE_SUBSCRIPTION_KEY": "SYNTHETIC-KEY"}), \
                patch("myinvoices.probe.RequestDocsAPI") as api, redirect_stdout(output):
            api.return_value.fetch.return_value = b"<RequestedDoc><private>INVOICE-SECRET</private></RequestedDoc>"
            self.assertEqual(probe(), 0)
            api.return_value.fetch.assert_called_once_with(0, date_from=date.today(), date_to=date.today())
        self.assertNotIn("SYNTHETIC", output.getvalue())
        self.assertNotIn("INVOICE-SECRET", output.getvalue())

    def test_environment_can_change_before_first_commit_only(self):
        from myinvoices.demo import DemoAPI
        from myinvoices.sync import Synchronizer
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            client = db.add_client("Test", "000000000")
            updated = db.configure_client(client.id, "Test", "production")
            self.assertEqual(updated.environment, "production")
            db.configure_client(client.id, "Test", "development")
            Synchronizer(db, DemoAPI()).run(client.id)
            with self.assertRaises(ApplicationError):
                db.configure_client(client.id, "Test", "production")
            self.assertEqual(db.count(client.id), 2)

    def test_connection_date_parameters_use_official_format(self):
        from myinvoices.api import RequestDocsAPI
        from myinvoices.models import Credentials
        from urllib.parse import parse_qs, urlsplit
        opener = Mock()
        response = Mock(status=200)
        response.read.return_value = b"<RequestedDoc/>"
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        RequestDocsAPI(Credentials("u", "k"), opener=opener).fetch(0,
            date_from=date(2026, 10, 18), date_to=date(2026, 10, 18))
        params = parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
        self.assertEqual(params["dateFrom"], ["18/10/2026"])
        self.assertEqual(params["dateTo"], ["18/10/2026"])
