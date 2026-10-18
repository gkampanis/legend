from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from myinvoices.api import RequestDocsAPI
from myinvoices.credential_store import CredentialStore, WindowsDPAPI
from myinvoices.database import Database
from myinvoices.models import ApplicationError, Credentials


class FakeEncryption:
    """Opaque fixture store, not encryption; used only for offline contract tests."""
    def __init__(self):
        self.values = {}

    def encrypt(self, plaintext):
        opaque = b"TEST-ONLY-OPAQUE-" + str(len(self.values)).encode()
        self.values[opaque] = plaintext
        return opaque

    def decrypt(self, opaque):
        return self.values[opaque]


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "test.sqlite3")
        self.a = self.db.add_client("A", "000000000")
        self.b = self.db.add_client("B", "111111111")
        self.secret = Credentials("SYNTHETIC-USER", "SYNTHETIC-KEY")

    def test_session_credentials_are_per_client_and_never_written(self):
        store = CredentialStore(self.root / "credentials")
        store.set(self.a, self.secret)
        self.assertEqual(store.get(self.a), self.secret)
        self.assertIsNone(store.get(self.b))
        self.assertFalse(store.directory.exists())
        self.assertIsNone(CredentialStore(store.directory).get(self.a))
        store.clear()
        self.assertIsNone(store.get(self.a))

    def test_encrypted_store_survives_restart_and_replaces_atomically(self):
        backend = FakeEncryption()
        store = CredentialStore(self.root / "credentials", backend)
        store.set(self.a, self.secret)
        again = CredentialStore(store.directory, backend)
        self.assertEqual(again.get(self.a), self.secret)
        second = Credentials("OTHER-USER", "OTHER-KEY", self.a.vat)
        again.set(self.a, second)
        self.assertEqual(store.get(self.a), second)
        files = list(store.directory.iterdir())
        self.assertEqual(len(files), 1)
        for file in files:
            self.assertNotIn(b"SYNTHETIC", file.read_bytes())
            self.assertNotIn(b"OTHER-KEY", file.read_bytes())
        self.assertNotIn(b"SYNTHETIC-KEY", self.db.path.read_bytes())

    def test_ciphertext_cannot_be_reassigned_to_other_client_or_environment(self):
        store = CredentialStore(self.root / "credentials", FakeEncryption())
        store.set(self.a, self.secret)
        store._path(self.b).write_bytes(store._path(self.a).read_bytes())
        with self.assertRaises(ApplicationError):
            store.get(self.b)
        with self.assertRaises(ApplicationError):
            store.get(replace(self.a, environment="production"))

    def test_encryption_failure_preserves_previous_credentials(self):
        backend = FakeEncryption()
        store = CredentialStore(self.root / "credentials", backend)
        store.set(self.a, self.secret)
        backend.encrypt = Mock(side_effect=RuntimeError("SYNTHETIC-KEY"))
        with self.assertRaises(ApplicationError) as raised:
            store.set(self.a, Credentials("new", "new"))
        self.assertNotIn("SYNTHETIC-KEY", str(raised.exception))
        self.assertEqual(store.get(self.a), self.secret)

    def test_wrong_representative_vat_and_unsafe_id_rejected(self):
        store = CredentialStore(self.root / "credentials")
        with self.assertRaises(ApplicationError):
            store.set(self.a, Credentials("u", "k", self.b.vat))
        with self.assertRaises(ApplicationError):
            store.get(replace(self.a, id="../../secret"))

    def test_rename_keeps_documents_checkpoint_and_credentials(self):
        from myinvoices.demo import DemoAPI
        from myinvoices.sync import Synchronizer
        store = CredentialStore(self.root / "credentials")
        store.set(self.a, self.secret)
        Synchronizer(self.db, DemoAPI()).run(self.a.id)
        state = self.db.checkpoint(self.a.id)
        renamed = self.db.rename_client(self.a.id, "Νέο όνομα")
        self.assertEqual(renamed.vat, self.a.vat)
        self.assertEqual(self.db.count(renamed.id), 2)
        self.assertEqual(self.db.checkpoint(renamed.id), state)
        self.assertEqual(store.get(renamed), self.secret)

    def test_representative_parameter_is_explicit_not_inferred(self):
        for entity_vat in (None, self.a.vat):
            opener = Mock()
            response = Mock(status=200)
            response.read.return_value = b"<RequestedDoc/>"
            opener.open.return_value.__enter__ = Mock(return_value=response)
            opener.open.return_value.__exit__ = Mock(return_value=False)
            RequestDocsAPI(Credentials("u", "k", entity_vat), opener=opener).fetch(0)
            params = parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
            if entity_vat:
                self.assertEqual(params["entityVatNumber"], [self.a.vat])
            else:
                self.assertNotIn("entityVatNumber", params)

    @unittest.skipUnless(os.name == "nt", "Native DPAPI must be tested on Windows")
    def test_native_windows_dpapi_roundtrip(self):
        backend = WindowsDPAPI()
        store = CredentialStore(self.root / "credentials", backend)
        store.set(self.a, self.secret)
        self.assertEqual(CredentialStore(store.directory, WindowsDPAPI()).get(self.a), self.secret)
        self.assertNotIn(b"SYNTHETIC-KEY", store._path(self.a).read_bytes())
