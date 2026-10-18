"""Narrow UTF-8 XML parser; no DTDs, entities, or guessed provider URLs."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from .models import DocumentChange, Invoice, Page, ParseError

MAX_RESPONSE_BYTES = 16 * 1024 * 1024

# Only known technical root names can appear in diagnostic messages, never arbitrary XML names/values.
DIAGNOSTIC_ROOTS = {"RequestedDoc", "RequestedDocs", "requestedDoc", "requestedDocs",
                    "InvoicesDoc", "ResponseDoc", "Response", "Error", "error", "ErrorResponse",
                    "string", "RequestDocsResponse", "RequestedDocResponse", "Envelope", "html", "HTML"}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def child(element, name):
    if element is None:
        return None
    return next((e for e in element if local_name(e.tag) == name), None)


def text(element, name):
    e = child(element, name)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def positive_mark(value, label="invoice MARK"):
    if not value or not value.isascii() or not value.isdigit():
        raise ParseError(f"Λείπει ή δεν είναι έγκυρο το {label}.")
    mark = int(value)
    if not 0 < mark <= 9223372036854775807:
        raise ParseError(f"Μη έγκυρο {label}.")
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


def parse_xml(data: bytes):
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
    except ET.ParseError as error:
        line, column = error.position
        raise ParseError(f"Η απόκριση δεν είναι έγκυρο XML (γραμμή {line}, στήλη {column}).") from None
    except ValueError:
        raise ParseError("Μη υποστηριζόμενη δήλωση κωδικοποίησης XML.") from None
    return root


def numeric_error_codes(root):
    return tuple(sorted({e.text.strip() for e in root.iter()
        if local_name(e.tag) in {"code", "Code", "statusCode", "StatusCode"} and e.text
        and 0 < len(e.text.strip()) <= 6 and e.text.strip().isascii() and e.text.strip().isdigit()}))


def unwrap_xml(data: bytes):
    """Decode bounded .NET <string> envelopes, then validate the actual XML root."""
    for depth in range(3):
        try:
            root = parse_xml(data)
        except ParseError as error:
            if depth:
                raise ParseError("XML μέσα σε string: " + str(error), error.root_kind or "string") from None
            raise
        if local_name(root.tag) != "string":
            return root
        if len(root):
            raise ParseError("Μη αναμενόμενη απόκριση [XML_ROOT=string, XML_STRING=NESTED_ELEMENT]. Το string δεν περιέχει serialized XML κειμένου.", "string")
        value = (root.text or "").lstrip("\ufeff \t\r\n")
        if not value:
            raise ParseError("Κενή απόκριση [XML_ROOT=string, XML_STRING=EMPTY]. Δεν επιβεβαιώθηκε λήψη παραστατικών και δεν προχώρησε η πρόοδος.", "string")
        if not value.startswith("<"):
            raise ParseError("Απάντηση απλού κειμένου [XML_ROOT=string, XML_STRING=TEXT], όχι XML παραστατικών. Το περιεχόμενο δεν εμφανίζεται για προστασία δεδομένων.", "string")
        if depth == 2:
            raise ParseError("Υπερβολικά εμφωλευμένη απόκριση XML_ROOT=string. Δεν προχώρησε η πρόοδος.", "string")
        data = value.encode("utf-8")


def service_error_codes(data: bytes):
    """Bounded, non-raising HTTP-error inspection without exposing the response text."""
    if len(data) > 65536:
        return ()
    try:
        root = unwrap_xml(data)
    except ParseError:
        return ()
    if local_name(root.tag) not in {"ResponseDoc", "Error", "error", "ErrorResponse", "Response"}:
        return ()
    return numeric_error_codes(root)


def read_envelope(data: bytes):
    root = unwrap_xml(data)
    if local_name(root.tag) != "RequestedDoc":
        if local_name(root.tag) in {"ResponseDoc", "Error", "error", "ErrorResponse", "Response"}:
            codes = numeric_error_codes(root)
            suffix = (" Κωδικοί: " + ", ".join(codes[:10])) if codes else ""
            label = local_name(root.tag)
            raise ParseError(f"Απάντηση XML σφάλματος/κατάστασης [XML_ROOT={label}] αντί για RequestedDoc. Δεν αποθηκεύτηκε πρόοδος." + suffix, label)
        if local_name(root.tag).lower() == "html":
            raise ParseError("Επιστράφηκε HTML αντί για XML παραστατικών, πιθανώς από σελίδα πρόσβασης ή proxy.")
        root_name = local_name(root.tag)
        label = root_name if root_name in DIAGNOSTIC_ROOTS else "UNKNOWN"
        raise ParseError(f"Μη αναμενόμενη απόκριση RequestDocs [XML_ROOT={label}]. Αναμενόταν RequestedDoc. Δεν αποθηκεύτηκε πρόοδος.", label)
    return root


# Names and mark fields follow RequestedDoc and its referenced AADE types.
CHANGE_SECTIONS = {
    "cancelledInvoicesDoc": ("cancelledInvoice", "cancellationMark", "cancellation"),
    "incomeClassificationsDoc": ("incomeInvoiceClassification", "classificationMark", "income_classification"),
    "expensesClassificationsDoc": ("expensesInvoiceClassification", "classificationMark", "expense_classification"),
    "paymentMethodsDoc": ("paymentMethods", "paymentMethodMark", "payment"),
}


def parse_change(element, event_field, kind):
    invoice_mark = positive_mark(text(element, "invoiceMark"), "invoiceMark μεταβολής")
    event_value = text(element, event_field)
    event_mark = positive_mark(event_value, event_field) if event_value is not None else None
    cancelled_on = None
    if kind == "cancellation":
        if event_mark is None:
            raise ParseError("Λείπει το cancellationMark ακύρωσης.")
        cancelled_on = text(element, "cancellationDate")
        try:
            if not cancelled_on or date.fromisoformat(cancelled_on).isoformat() != cancelled_on:
                raise ValueError
        except ValueError:
            raise ParseError("Μη έγκυρη cancellationDate ακύρωσης.") from None
    return DocumentChange(kind, event_mark, invoice_mark,
                          ET.tostring(element, encoding="unicode"), cancelled_on)


def parse_response(data: bytes) -> Page:
    root = read_envelope(data)
    # Unknown payloads must not be silently accepted as a completed empty sync.
    allowed = {"continuationToken", "invoicesDoc", "cancelledInvoicesDoc",
               "incomeClassificationsDoc", "expensesClassificationsDoc",
               "paymentMethodsDoc"}
    if any(local_name(e.tag) not in allowed for e in root):
        raise ParseError("Η απόκριση περιέχει μη υποστηριζόμενο τμήμα XML.")
    changes = []
    for section in root:
        definition = CHANGE_SECTIONS.get(local_name(section.tag))
        if definition is None:
            continue
        element_name, event_field, kind = definition
        for element in section:
            if local_name(element.tag) != element_name:
                raise ParseError("Μη αναμενόμενη δομή ενότητας μεταβολών RequestDocs.")
            changes.append(parse_change(element, event_field, kind))
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
    return Page(tuple(invoices), continuation, tuple(changes))
