from contextlib import redirect_stdout
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from myinvoices.api import APIError, Environment
from myinvoices.credential_store import CredentialStore
from myinvoices.database import Database
from myinvoices.local_check import check_client, main
from myinvoices.models import ApplicationError, Credentials


class LocalCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local.sqlite3"
        self.database = Database(self.path)
        self.client = self.database.add_client("Synthetic", "000000000", "production")
        self.store = CredentialStore(self.path.parent / "credentials")
        self.secret = Credentials("SYNTHETIC-USER", "SYNTHETIC-KEY")
        self.store.set(self.client, self.secret)

    def test_uses_saved_credentials_and_environment_without_changing_checkpoint(self):
        factory = Mock()
        factory.return_value.fetch.return_value = b"<RequestedDoc/>"
        checkpoint = self.database.checkpoint(self.client.id)
        result = check_client(self.database, self.store, self.client.id, factory)
        factory.assert_called_once_with(self.secret, Environment.PRODUCTION)
        factory.return_value.fetch.assert_called_once_with(0, date_from=date.today(), date_to=date.today())
        self.assertIn("HTTP 200", result)
        self.assertNotIn("SYNTHETIC", result)
        self.assertEqual(self.database.checkpoint(self.client.id), checkpoint)
        self.assertEqual(self.database.count(self.client.id), 0)

    def test_missing_credentials_prevent_request(self):
        factory = Mock()
        with self.assertRaises(ApplicationError):
            check_client(self.database, CredentialStore(self.path.parent), self.client.id, factory)
        factory.assert_not_called()

    def test_http_status_is_preserved(self):
        factory = Mock()
        factory.return_value.fetch.side_effect = APIError("HTTP 403", 403)
        with self.assertRaises(APIError) as raised:
            check_client(self.database, self.store, self.client.id, factory)
        self.assertEqual(raised.exception.status, 403)

    def test_missing_database_does_not_create_files_or_request_secrets(self):
        path = self.path.parent / "absent.sqlite3"
        with redirect_stdout(io.StringIO()), patch("builtins.input") as prompt:
            self.assertEqual(main(["--db", str(path)]), 2)
        prompt.assert_not_called()
        self.assertFalse(path.exists())

    def test_cancellation_before_request(self):
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=KeyboardInterrupt), \
                patch("myinvoices.local_check.default_store") as store:
            self.assertEqual(main(["--db", str(self.path)]), 2)
        store.assert_not_called()
