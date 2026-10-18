from pathlib import Path
import sqlite3
import tempfile
import unittest

from myinvoices.database import Database, SCHEMA
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.models import ParseError
from myinvoices.xml_parser import parse_response, read_envelope


def mixed_page(*, payment_mark=True):
    income = """<incomeClassificationsDoc xmlns:ic='https://www.aade.gr/myDATA/incomeClassificaton/v1.0'>
        <ic:incomeInvoiceClassification><ic:invoiceMark>10</ic:invoiceMark>
        <ic:classificationMark>23</ic:classificationMark></ic:incomeInvoiceClassification></incomeClassificationsDoc>"""
    expenses = """<expensesClassificationsDoc><expensesInvoiceClassification>
        <invoiceMark>10</invoiceMark><classificationMark>24</classificationMark>
        </expensesInvoiceClassification></expensesClassificationsDoc>"""
    payment = ("<paymentMethodsDoc><paymentMethods><invoiceMark>10</invoiceMark>"
               + ("<paymentMethodMark>25</paymentMethodMark>" if payment_mark else "")
               + "<paymentMethodDetails><type>7</type><amount>124.12</amount></paymentMethodDetails></paymentMethods></paymentMethodsDoc>")
    cancellation = """<cancelledInvoicesDoc><cancelledInvoice><invoiceMark>10</invoiceMark>
        <cancellationMark>22</cancellationMark><cancellationDate>2026-10-01</cancellationDate>
        </cancelledInvoice></cancelledInvoicesDoc>"""
    return xml_page([xml_invoice(10)]).replace(b"</RequestedDoc>",
        (cancellation + income + expenses + payment + "</RequestedDoc>").encode())


class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "db.sqlite3"
        self.db = Database(self.path)
        self.client = self.db.add_client("Synthetic", "000000000")

    def test_mixed_response_preserves_all_changes_and_correct_marks(self):
        page = parse_response(mixed_page())
        self.assertEqual(len(page.invoices), 1)
        self.assertEqual({c.kind for c in page.changes},
                         {"cancellation", "income_classification", "expense_classification", "payment"})
        self.assertEqual([c.event_mark for c in page.changes], [22, 23, 24, 25])
        self.assertTrue(all(c.invoice_mark == 10 for c in page.changes))
        self.db.commit_page(self.db.checkpoint(self.client.id), page)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor, 25)
        self.assertEqual(self.db.change_count(self.client.id), 4)
        self.assertEqual(self.db.invoices(self.client.id)[0]["cancelled"], 1)
        self.assertEqual(self.db.events(self.client.id)[0]["record_count"], 5)

    def test_changes_and_documents_are_idempotent_and_client_isolated(self):
        page = parse_response(mixed_page())
        self.db.commit_page(self.db.checkpoint(self.client.id), page)
        self.db.commit_page(self.db.checkpoint(self.client.id), page)
        self.assertEqual(self.db.change_count(self.client.id), 4)
        self.assertEqual(self.db.count(self.client.id), 1)
        other = self.db.add_client("Other", "111111111")
        self.assertEqual(self.db.change_count(other.id), 0)

    def test_missing_optional_event_mark_keeps_raw_record_without_guessing_mark(self):
        page = parse_response(mixed_page(payment_mark=False))
        self.assertIsNone(page.changes[-1].event_mark)
        self.db.commit_page(self.db.checkpoint(self.client.id), page)
        self.db.commit_page(self.db.checkpoint(self.client.id), page)
        self.assertEqual(self.db.change_count(self.client.id), 4)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor, 24)
        payment = next(c for c in self.db.changes(self.client.id) if c["kind"] == "payment")
        self.assertIn("paymentMethodDetails", payment["raw_xml"])

    def test_checkpoint_failure_rolls_back_changes_as_well_as_invoices(self):
        with self.db.connection() as conn:
            conn.execute("""CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON checkpoints
                BEGIN SELECT RAISE(ABORT,'synthetic'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.commit_page(self.db.checkpoint(self.client.id), parse_response(mixed_page()))
        self.assertEqual(self.db.count(self.client.id), 0)
        self.assertEqual(self.db.change_count(self.client.id), 0)
        self.assertEqual(self.db.checkpoint(self.client.id).revision, 0)

    def test_changes_only_page_updates_highwater_from_event_not_invoice_reference(self):
        data = b"""<RequestedDoc><cancelledInvoicesDoc><cancelledInvoice><invoiceMark>999</invoiceMark>
            <cancellationMark>1001</cancellationMark><cancellationDate>2026-10-01</cancellationDate>
            </cancelledInvoice></cancelledInvoicesDoc></RequestedDoc>"""
        self.db.commit_page(self.db.checkpoint(self.client.id), parse_response(data))
        self.assertEqual(self.db.count(self.client.id), 0)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor, 1001)

    def test_additive_migration_backs_up_v1_and_preserves_existing_data(self):
        legacy = self.path.parent / "v1.sqlite3"
        with sqlite3.connect(legacy) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO clients VALUES ('existing','Existing','000000000','production')")
            conn.execute("INSERT INTO checkpoints(client_id,cursor,highwater) VALUES ('existing',42,42)")
            conn.execute("""INSERT INTO invoices(client_id,mark,issue_date,raw_xml,status,updated_at)
                VALUES ('existing',41,'2026-10-01','<invoice/>','no_url','2026-10-01')""")
        upgraded = Database(legacy)
        self.assertEqual(upgraded.client("existing").name, "Existing")
        self.assertEqual(upgraded.checkpoint("existing").cursor, 42)
        self.assertEqual(upgraded.invoices("existing")[0]["mark"], 41)
        self.assertEqual(upgraded.change_count("existing"), 0)
        backups = list(self.path.parent.glob("v1-schema1-backup-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT cursor FROM checkpoints").fetchone()[0], 42)
            self.assertEqual(conn.execute("SELECT mark FROM invoices").fetchone()[0], 41)
        Database(legacy)
        self.assertEqual(len(list(self.path.parent.glob("v1-schema1-backup-*.sqlite3"))), 1)

    def test_unexpected_root_is_identified_without_accepting_it_as_success(self):
        for name in ("Error", "RequestedDocs", "string"):
            with self.subTest(name=name), self.assertRaises(ParseError) as raised:
                read_envelope(f"<{name}><message>SYNTHETIC-SECRET</message></{name}>".encode())
            self.assertIn("XML_ROOT=" + name, str(raised.exception))
            self.assertNotIn("SYNTHETIC-SECRET", str(raised.exception))
        with self.assertRaises(ParseError) as raised:
            read_envelope(b"<SYNTHETIC_SECRET_ROOT/>")
        self.assertIn("XML_ROOT=UNKNOWN", str(raised.exception))
        self.assertNotIn("SYNTHETIC_SECRET_ROOT", str(raised.exception))

    def test_xml_service_error_exposes_only_numeric_codes(self):
        data = b"""<Error><Code>101</Code><Message>SYNTHETIC-PRIVATE-DATA</Message>
            <statusCode>403</statusCode></Error>"""
        with self.assertRaises(ParseError) as raised:
            read_envelope(data)
        self.assertIn("101", str(raised.exception))
        self.assertIn("403", str(raised.exception))
        self.assertIn("XML_ROOT=Error", str(raised.exception))
        self.assertNotIn("SYNTHETIC-PRIVATE-DATA", str(raised.exception))
