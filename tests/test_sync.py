from contextlib import contextmanager
from decimal import Decimal
import io
import logging
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from myinvoices.api import APIError, Environment, NoRedirect, RequestDocsAPI
from myinvoices.database import Database
from myinvoices.demo import DemoAPI, xml_invoice, xml_page
from myinvoices.models import ApplicationError, Credentials, ParseError, SyncError
from myinvoices.sync import Synchronizer
from myinvoices.xml_parser import MAX_RESPONSE_BYTES, parse_response


class ScriptedAPI:
    environment = Environment.DEVELOPMENT

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def fetch(self, mark, continuation=None):
        self.calls.append((mark, continuation))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


class ParserTests(unittest.TestCase):
    def test_metadata_and_decimal_amounts(self):
        invoice = parse_response(xml_page([xml_invoice(42)])).invoices[0]
        self.assertEqual(invoice.mark, 42)
        self.assertEqual(invoice.uid, "SYNTHETIC-42")
        self.assertEqual(invoice.issuer_vat, "111111111")
        self.assertEqual(invoice.recipient_vat, "000000000")
        self.assertEqual(invoice.issue_date, "2026-10-01")
        self.assertEqual((invoice.invoice_type, invoice.series, invoice.number), ("1.1", "DEMO", "42"))
        self.assertEqual((invoice.net, invoice.vat, invoice.total),
                         (Decimal("100.10"), Decimal("24.02"), Decimal("124.12")))
        self.assertIn("invoiceHeader", invoice.raw_xml)

    def test_url_is_only_official_field(self):
        invoice = parse_response(xml_page([xml_invoice(42)])).invoices[0]
        self.assertEqual(invoice.download_url, "https://provider.example/invoice/42")
        raw = xml_invoice(42, url=False).replace("</invoice>", "<qrCodeUrl>https://qr.example</qrCodeUrl></invoice>")
        self.assertIsNone(parse_response(xml_page([raw])).invoices[0].download_url)

    def test_url_nested_in_header_and_xml_escaping(self):
        raw = xml_invoice(42, url=False).replace("</invoiceHeader>",
            "<downloadingInvoiceUrl>https://provider.example/?a=1&amp;b=2</downloadingInvoiceUrl></invoiceHeader>")
        self.assertEqual(parse_response(xml_page([raw])).invoices[0].download_url,
                         "https://provider.example/?a=1&b=2")

    def test_empty_page_and_continuation(self):
        self.assertEqual(parse_response(xml_page()).invoices, ())
        self.assertEqual(parse_response(xml_page(token=("p", "r"))).continuation, ("p", "r"))

    def test_rejects_malformed_and_unexpected_xml(self):
        for data in [b"<", b"<html/>", b"", b"<RequestedDoc><error>secret</error></RequestedDoc>"]:
            with self.subTest(data=data), self.assertRaises(ParseError):
                parse_response(data)

    def test_dtd_entity_and_utf16_rejected(self):
        for data in [b'<!DOCTYPE x [<!ENTITY a "x">]><RequestedDoc/>',
                     "<RequestedDoc/>".encode("utf-16")]:
            with self.subTest(data=data), self.assertRaises(ParseError):
                parse_response(data)

    def test_invalid_invoice_does_not_get_synthetic_mark(self):
        base = xml_invoice(42)
        for original, replacement in [("<mark>42</mark>", ""), ("<mark>42</mark>", "<mark>NaN</mark>"),
                                      ("2026-10-01", "2026-02-31"), ("100.10", "NaN")]:
            with self.subTest(replacement=replacement), self.assertRaises(ParseError):
                parse_response(xml_page([base.replace(original, replacement)]))

    def test_incomplete_token_rejected(self):
        with self.assertRaises(ParseError):
            parse_response(b"<RequestedDoc><continuationToken><nextRowKey>r</nextRowKey></continuationToken></RequestedDoc>")

    def test_incomplete_change_fails_closed(self):
        with self.assertRaises(ParseError):
            parse_response(b"<RequestedDoc><cancelledInvoicesDoc><cancelledInvoice/></cancelledInvoicesDoc></RequestedDoc>")

    def test_unsafe_and_conflicting_urls_rejected(self):
        for url in ["http://provider.example/invoice/42", "https://u:password@provider.example/invoice/42"]:
            raw = xml_invoice(42).replace("https://provider.example/invoice/42", url)
            with self.subTest(url=url), self.assertRaises(ParseError):
                parse_response(xml_page([raw]))
        raw = xml_invoice(42).replace("</invoice>",
            "<downloadingInvoiceUrl>https://other.example</downloadingInvoiceUrl></invoice>")
        with self.assertRaises(ParseError):
            parse_response(xml_page([raw]))


class DatabaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.path)
        self.client = self.db.add_client("Δοκιμαστικός πελάτης", "000000000")

    def test_single_page_and_idempotency(self):
        api = ScriptedAPI(xml_page([xml_invoice(42)]), xml_page([xml_invoice(42)]))
        first = Synchronizer(self.db, api).run(self.client.id)
        self.assertEqual((first.pages, first.records), (1, 1))
        Synchronizer(self.db, api).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 1)
        self.assertEqual(api.calls, [(0, None), (42, None)])

    def test_pagination_uses_original_mark_and_stores_cursor_at_end(self):
        api = ScriptedAPI(xml_page([xml_invoice(10)], ("p", "r")), xml_page([xml_invoice(12)]))
        states = []
        result = Synchronizer(self.db, api).run(self.client.id,
            progress=lambda *_: states.append(self.db.checkpoint(self.client.id)))
        self.assertEqual(result.pages, 2)
        self.assertEqual(api.calls, [(0, None), (0, ("p", "r"))])
        self.assertEqual((states[0].cursor, states[0].highwater, states[0].active), (0, 10, True))
        self.assertEqual((states[1].cursor, states[1].active), (12, False))

    def test_highest_mark_is_not_last_mark_in_page(self):
        Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(12), xml_invoice(10)]))).run(self.client.id)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor, 12)

    def test_client_isolation_same_mark(self):
        second = self.db.add_client("Άλλος πελάτης", "222222222")
        Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(42)]))).run(self.client.id)
        Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(42, recipient=second.vat)]))).run(second.id)
        self.assertEqual(self.db.invoices(self.client.id)[0]["recipient_vat"], self.client.vat)
        self.assertEqual(self.db.invoices(second.id)[0]["recipient_vat"], second.vat)
        self.assertEqual(self.db.count(second.id), 1)

    def test_recipient_mismatch_rolls_back_entire_page(self):
        before = self.db.checkpoint(self.client.id)
        raw = xml_page([xml_invoice(10), xml_invoice(11, recipient="222222222")])
        with self.assertRaises(SyncError):
            Synchronizer(self.db, ScriptedAPI(raw)).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 0)
        self.assertEqual(self.db.checkpoint(self.client.id), before)
        self.assertEqual(self.db.events(self.client.id), [])

    def test_missing_url_is_not_downloaded_or_verified(self):
        Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(42, url=False)]))).run(self.client.id)
        invoice = self.db.invoices(self.client.id)[0]
        self.assertIsNone(invoice["download_url"])
        self.assertEqual(invoice["status"], "no_url")

    def test_available_url_is_not_verified_pdf(self):
        Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(42)]))).run(self.client.id)
        self.assertEqual(self.db.invoices(self.client.id)[0]["status"], "url_available")

    def test_interrupt_and_resume_with_new_database_instance(self):
        first = ScriptedAPI(xml_page([xml_invoice(10)], ("p", "r")), APIError("Διακοπή"))
        with self.assertRaises(APIError):
            Synchronizer(self.db, first).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 1)
        reopened = Database(self.path)
        second = ScriptedAPI(xml_page([xml_invoice(10), xml_invoice(11)]))
        Synchronizer(reopened, second).run(self.client.id)
        self.assertEqual(second.calls, [(0, ("p", "r"))])
        self.assertEqual(reopened.count(self.client.id), 2)
        self.assertEqual(reopened.checkpoint(self.client.id).cursor, 11)

    def test_database_failure_never_advances_cursor(self):
        with self.db.connection() as conn:
            conn.execute("""CREATE TRIGGER injected_failure BEFORE INSERT ON invoices
                WHEN NEW.mark=11 BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(10), xml_invoice(11)]))).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 0)
        self.assertEqual(self.db.checkpoint(self.client.id).revision, 0)
        self.assertEqual(self.db.events(self.client.id), [])

    def test_failure_writing_checkpoint_rolls_back_invoices(self):
        with self.db.connection() as conn:
            conn.execute("""CREATE TRIGGER injected_failure BEFORE UPDATE ON checkpoints
                BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(10)]))).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 0)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor, 0)

    def test_cursor_is_invisible_to_other_connection_before_commit(self):
        observations = []
        db = self.db
        original = db.connection
        class ObservingConnection:
            def __init__(self, conn): self.conn = conn
            def __getattr__(self, name): return getattr(self.conn, name)
            def commit(self):
                with original() as observer:
                    observations.append((
                        observer.execute("SELECT cursor FROM checkpoints WHERE client_id=?", (self_id,)).fetchone()[0],
                        observer.execute("SELECT count(*) FROM invoices").fetchone()[0]))
                self.conn.commit()
        self_id = self.client.id
        @contextmanager
        def wrapped():
            with original() as conn:
                yield ObservingConnection(conn)
        db.connection = wrapped
        Synchronizer(db, ScriptedAPI(xml_page([xml_invoice(42)]))).run(self.client.id)
        self.assertEqual(observations, [(0, 0)])
        self.assertEqual(db.count(self.client.id), 1)
        self.assertEqual(db.checkpoint(self.client.id).cursor, 42)

    def test_stale_writer_cannot_commit(self):
        state = self.db.checkpoint(self.client.id)
        self.db.commit_page(state, parse_response(xml_page([xml_invoice(10)])))
        with self.assertRaises(SyncError):
            self.db.commit_page(state, parse_response(xml_page([xml_invoice(11)])))
        self.assertEqual(self.db.count(self.client.id), 1)

    def test_malformed_later_page_preserves_checkpoint(self):
        api = ScriptedAPI(xml_page([xml_invoice(10)], ("p", "r")), b"invalid")
        with self.assertRaises(ParseError):
            Synchronizer(self.db, api).run(self.client.id)
        self.assertEqual(self.db.checkpoint(self.client.id).continuation, ("p", "r"))
        self.assertEqual(self.db.count(self.client.id), 1)

    def test_repeated_token_is_detected_across_restarts(self):
        with self.assertRaises(APIError):
            Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(10)], ("p", "r")), APIError("stop"))).run(self.client.id)
        with self.assertRaises(SyncError):
            Synchronizer(Database(self.path), ScriptedAPI(xml_page([xml_invoice(11)], ("p", "r")))).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 1)

    def test_cancel_before_request_and_after_response(self):
        event = threading.Event()
        event.set()
        api = ScriptedAPI()
        self.assertTrue(Synchronizer(self.db, api).run(self.client.id, event).cancelled)
        self.assertEqual(api.calls, [])
        event.clear()
        api = ScriptedAPI(xml_page([xml_invoice(10)]))
        fetch = api.fetch
        def cancelling(*args):
            event.set()
            return fetch(*args)
        api.fetch = cancelling
        result = Synchronizer(self.db, api).run(self.client.id, event)
        self.assertTrue(result.cancelled)
        self.assertEqual(self.db.checkpoint(self.client.id).revision, 0)

    def test_page_limit_leaves_resumable_progress(self):
        with self.assertRaises(SyncError):
            Synchronizer(self.db, ScriptedAPI(xml_page([xml_invoice(10)], ("p", "r"))), max_pages=1).run(self.client.id)
        self.assertTrue(self.db.checkpoint(self.client.id).active)

    def test_environment_mismatch_is_blocked_without_http(self):
        api = ScriptedAPI()
        api.environment = Environment.PRODUCTION
        with self.assertRaises(SyncError):
            Synchronizer(self.db, api).run(self.client.id)
        self.assertEqual(api.calls, [])

    def test_same_vat_allowed_only_in_different_environment(self):
        with self.assertRaises(ApplicationError):
            self.db.add_client("Duplicate", self.client.vat)
        second = self.db.add_client("Production", self.client.vat, "production")
        self.assertNotEqual(second.id, self.client.id)

    def test_demo_is_offline_and_repeatable(self):
        result = Synchronizer(self.db, DemoAPI()).run(self.client.id)
        self.assertEqual((result.pages, result.records), (2, 2))
        Synchronizer(self.db, DemoAPI()).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id), 2)


class APITests(unittest.TestCase):
    def setUp(self):
        self.creds = Credentials("SYNTHETIC-USER", "SYNTHETIC-KEY")

    def test_request_headers_parameters_timeout_and_environment(self):
        opener = Mock()
        response = Mock(status=200)
        response.read.return_value = xml_page()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        api = RequestDocsAPI(self.creds, Environment.PRODUCTION, timeout=17, opener=opener)
        self.assertEqual(api.fetch(42, ("p+1", "r/2")), xml_page())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith("https://mydatapi.aade.gr/myDATA/RequestDocs?"))
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),
            {"mark": ["42"], "nextPartitionKey": ["p+1"], "nextRowKey": ["r/2"]})
        self.assertEqual(request.get_header("Aade-user-id"), self.creds.user_id)
        self.assertEqual(request.get_header("Ocp-apim-subscription-key"), self.creds.subscription_key)
        self.assertEqual(request.get_header("Content-type"), "text/xml")
        self.assertEqual(request.get_header("Accept"), "application/xml, text/xml")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 17)

    def test_http_errors_are_structured_and_do_not_leak_secrets(self):
        for status in (301, 401, 403, 429, 500):
            opener = Mock()
            opener.open.side_effect = HTTPError("https://secret.example", status,
                self.creds.subscription_key, {}, io.BytesIO(b"sensitive response"))
            with self.subTest(status=status), self.assertRaises(APIError) as raised:
                RequestDocsAPI(self.creds, opener=opener).fetch(0)
            self.assertEqual(raised.exception.status, status)
            self.assertNotIn(self.creds.subscription_key, str(raised.exception))

    def test_timeout_and_network_errors_are_safe(self):
        for error in (TimeoutError(self.creds.subscription_key), URLError(self.creds.user_id)):
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(APIError) as raised:
                RequestDocsAPI(self.creds, opener=opener).fetch(0)
            self.assertNotIn(self.creds.subscription_key, str(raised.exception))
            self.assertNotIn(self.creds.user_id, str(raised.exception))

    def test_no_credentials_in_application_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        opener = Mock()
        opener.open.side_effect = URLError(self.creds.subscription_key)
        with self.assertRaises(APIError):
            RequestDocsAPI(self.creds, opener=opener).fetch(0)
        self.assertNotIn(self.creds.subscription_key, output.getvalue())
        self.assertNotIn(self.creds.user_id, output.getvalue())
        self.assertNotIn(self.creds.user_id, repr(self.creds))

    def test_redirects_refused(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://provider.example"))

    def test_invalid_credentials_and_endpoint_rejected(self):
        for user, key in [("", "key"), ("user", ""), ("user\r\n", "key")]:
            with self.subTest(user=user), self.assertRaises(ApplicationError):
                Credentials(user, key)
        with self.assertRaises(ApplicationError):
            RequestDocsAPI(self.creds, environment="https://arbitrary.example")

    def test_large_response_rejected(self):
        opener = Mock()
        response = Mock(status=200)
        response.read.return_value = b"x" * (MAX_RESPONSE_BYTES + 1)
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(APIError):
            RequestDocsAPI(self.creds, opener=opener).fetch(0)


if __name__ == "__main__":
    unittest.main()
