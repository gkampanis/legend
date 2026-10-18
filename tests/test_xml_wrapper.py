from html import escape
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
import io

from myinvoices.api import APIError, Environment, RequestDocsAPI
from myinvoices.database import Database
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.models import Credentials, ParseError
from myinvoices.sync import Synchronizer
from myinvoices.xml_parser import parse_response, read_envelope


def wrapped(data):
    return ('<string xmlns="http://schemas.microsoft.com/2003/10/Serialization/">'
            + escape(data.decode("utf-8")) + '</string>').encode()


class WrapperTests(unittest.TestCase):
    def test_dotnet_string_with_requested_doc_is_unwrapped_and_parsed(self):
        direct = xml_page([xml_invoice(42)])
        self.assertEqual(parse_response(wrapped(direct)), parse_response(direct))

    def test_nested_wrapper_is_bounded(self):
        direct = xml_page([xml_invoice(42)])
        self.assertEqual(parse_response(wrapped(wrapped(direct))).invoices[0].mark, 42)
        with self.assertRaises(ParseError):
            parse_response(wrapped(wrapped(wrapped(direct))))

    def test_text_or_empty_string_is_not_a_successful_empty_page(self):
        for body, marker in [(b'<string/>', 'EMPTY'),
                             (b'<string>SYNTHETIC-PRIVATE-DATA</string>', 'TEXT')]:
            with self.subTest(marker=marker), self.assertRaises(ParseError) as raised:
                read_envelope(body)
            self.assertIn('XML_STRING=' + marker, str(raised.exception))
            self.assertNotIn('SYNTHETIC-PRIVATE-DATA', str(raised.exception))

    def test_wrapped_dtd_and_malformed_xml_are_rejected(self):
        for body in [b'<!DOCTYPE x [<!ENTITY secret "sensitive">]><RequestedDoc/>', b'<RequestedDoc>']:
            with self.subTest(body=body), self.assertRaises(ParseError):
                parse_response(wrapped(body))

    def test_wrapped_service_error_is_not_accepted_as_invoice_data(self):
        body = b'<ResponseDoc><response><errors><error><code>101</code><message>SYNTHETIC-PRIVATE</message></error></errors></response></ResponseDoc>'
        with self.assertRaises(ParseError) as raised:
            read_envelope(wrapped(body))
        self.assertIn('XML_ROOT=ResponseDoc', str(raised.exception))
        self.assertIn('101', str(raised.exception))
        self.assertNotIn('SYNTHETIC-PRIVATE', str(raised.exception))
        opener = Mock()
        opener.open.side_effect = HTTPError('https://sensitive.example', 403, 'private', {}, io.BytesIO(wrapped(body)))
        with self.assertRaises(APIError) as api_error:
            RequestDocsAPI(Credentials('synthetic-user', 'synthetic-key'), opener=opener).fetch(0)
        self.assertEqual(api_error.exception.service_codes, ('101',))

    def test_full_sync_handles_wrapped_pages_without_changing_continuation_rules(self):
        class API:
            environment = Environment.DEVELOPMENT
            def __init__(self):
                self.calls = []
            def fetch(self, mark, token=None):
                self.calls.append((mark, token))
                if token:
                    return wrapped(xml_page([xml_invoice(43)]))
                return wrapped(xml_page([xml_invoice(42)], ('p', 'r')))
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / 'sync.sqlite3')
            client = db.add_client('Synthetic', '000000000')
            api = API()
            result = Synchronizer(db, api).run(client.id)
            self.assertEqual((result.pages, result.records), (2, 2))
            self.assertEqual(api.calls, [(0, None), (0, ('p', 'r'))])
            self.assertEqual(db.count(client.id), 2)
            self.assertEqual(db.checkpoint(client.id).cursor, 43)
