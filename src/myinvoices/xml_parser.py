"""Narrow UTF-8 XML parser; no DTDs, entities, or guessed provider URLs."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from .models import Invoice, Page, ParseError

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def child(element, name):
    if element is None:
        return None
    return next((e for e in element if local_name(e.tag) == name), None)


def text(element, name):
    e = child(element, name)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def positive_mark(value):
    if not value or not value.isascii() or not value.isdigit():
        raise ParseError("Λείπει ή δεν είναι έγκυρο το invoice MARK.")
    mark = int(value)
    if not 0 < mark <= 9223372036854775807:
        raise ParseError("Μη έγκυρο invoice MARK.")
    return mark


def amount(element, name):
    value = text(element, name)
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ParseError("Μη έγκυρο χρηματικό ποσό στο XML.") from None
    if not number.is_finite():
        raise ParseError("Μη έγκυρο χρηματικό ποσό στο XML.")
    return number


def parse_invoice(element):
    header = child(element, "invoiceHeader")
    summary = child(element, "invoiceSummary")
    issue_date = text(header, "issueDate")
    try:
        if issue_date is None or date.fromisoformat(issue_date).isoformat() != issue_date:
            raise ValueError
    except ValueError:
        raise ParseError("Λείπει ή δεν είναι έγκυρη η ημερομηνία έκδοσης.") from None
    # Recognize only the explicit AADE field, never a generic URL or QR code.
    urls = {e.text.strip() for e in element.iter()
            if local_name(e.tag) == "downloadingInvoiceUrl" and e.text and e.text.strip()}
    if len(urls) > 1:
        raise ParseError("Αντικρουόμενα downloadingInvoiceUrl στο ίδιο παραστατικό.")
    url = next(iter(urls), None)
    if url:
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not any(c.isspace() or ord(c) < 32 for c in url))
        except ValueError:
            valid = False
        if not valid:
            raise ParseError("Μη ασφαλές downloadingInvoiceUrl στο XML.")
    return Invoice(
        mark=positive_mark(text(element, "mark")),
        uid=text(element, "uid"),
        issuer_vat=text(child(element, "issuer"), "vatNumber"),
        recipient_vat=text(child(element, "counterpart"), "vatNumber"),
        issue_date=issue_date,
        invoice_type=text(header, "invoiceType"),
        series=text(header, "series"),
        number=text(header, "aa"),
        net=amount(summary, "totalNetValue"),
        vat=amount(summary, "totalVatAmount"),
        total=amount(summary, "totalGrossValue"),
        download_url=url,
        raw_xml=ET.tostring(element, encoding="unicode"),
    )


def read_envelope(data: bytes):
    if len(data) > MAX_RESPONSE_BYTES:
        raise ParseError("Η απόκριση XML υπερβαίνει το επιτρεπόμενο μέγεθος.")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError("Αναμένεται XML UTF-8.") from None
    if any(marker in decoded.upper() for marker in ("<!DOCTYPE", "<!ENTITY")) or "\x00" in decoded:
        raise ParseError("Δεν επιτρέπονται DTD ή entities στο XML.")
    try:
        root = ET.fromstring(decoded)
    except (ET.ParseError, ValueError):
        raise ParseError("Η απόκριση δεν είναι έγκυρο XML.") from None
    if local_name(root.tag) != "RequestedDoc":
        if local_name(root.tag) == "ResponseDoc":
            raise ParseError("Η υπηρεσία επέστρεψε ResponseDoc αντί για παραστατικά. Η απόκριση δεν καταχωρήθηκε ως επιτυχής συγχρονισμός.")
        raise ParseError("Μη αναμενόμενη απόκριση RequestDocs.")
    return root


def parse_response(data: bytes) -> Page:
    root = read_envelope(data)
    # Unknown payloads must not be silently accepted as a completed empty sync.
    allowed = {"continuationToken", "invoicesDoc", "cancelledInvoicesDoc",
               "incomeClassificationsDoc", "expensesClassificationsDoc",
               "paymentMethodsDoc"}
    if any(local_name(e.tag) not in allowed for e in root):
        raise ParseError("Η απόκριση περιέχει μη υποστηριζόμενο τμήμα XML.")
    if any(len(e) for e in root
           if local_name(e.tag) not in {"continuationToken", "invoicesDoc"}):
        raise ParseError("Η ΑΑΔΕ επέστρεψε XML με ακυρώσεις/χαρακτηρισμούς/πληρωμές. Ο parser της εφαρμογής δεν τα υποστηρίζει ακόμη· δεν πρόκειται για έλεγχο λανθασμένων credentials. Η πρόοδος δεν προχώρησε.")
    containers = [e for e in root if local_name(e.tag) == "invoicesDoc"]
    invoices = []
    for container in containers:
        if any(local_name(e.tag) != "invoice" for e in container):
            raise ParseError("Μη αναμενόμενη δομή invoicesDoc.")
        invoices.extend(parse_invoice(e) for e in container)
    token_elements = [e for e in root if local_name(e.tag) == "continuationToken"]
    if len(token_elements) > 1:
        raise ParseError("Πολλαπλά continuation tokens.")
    continuation = None
    if token_elements:
        partition = text(token_elements[0], "nextPartitionKey")
        row = text(token_elements[0], "nextRowKey")
        if not partition or not row:
            raise ParseError("Ελλιπές continuation token· δεν αποθηκεύτηκε πρόοδος.")
        continuation = (partition, row)
    return Page(tuple(invoices), continuation)
