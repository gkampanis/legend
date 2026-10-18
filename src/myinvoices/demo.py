"""Synthetic demonstration. Never connects to a network or downloads PDFs."""

from .api import Environment

DEMO_VAT = "000000000"


def xml_invoice(mark, *, recipient=DEMO_VAT, url=True):
    link = (f"<downloadingInvoiceUrl>https://provider.example/invoice/{mark}</downloadingInvoiceUrl>"
            if url else "")
    return f"""<invoice>
      <uid>SYNTHETIC-{mark}</uid><mark>{mark}</mark>
      <issuer><vatNumber>111111111</vatNumber><country>GR</country></issuer>
      <counterpart><vatNumber>{recipient}</vatNumber><country>GR</country></counterpart>
      <invoiceHeader><series>DEMO</series><aa>{mark}</aa><issueDate>2026-10-01</issueDate>
        <invoiceType>1.1</invoiceType></invoiceHeader>
      <invoiceSummary><totalNetValue>100.10</totalNetValue><totalVatAmount>24.02</totalVatAmount>
        <totalGrossValue>124.12</totalGrossValue></invoiceSummary>{link}
    </invoice>"""


def xml_page(invoices=(), token=None):
    continuation = (f"<continuationToken><nextPartitionKey>{token[0]}</nextPartitionKey>"
                    f"<nextRowKey>{token[1]}</nextRowKey></continuationToken>" if token else "")
    return (f'<RequestedDoc xmlns="http://www.aade.gr/myDATA/doc/v1.0">{continuation}'
            '<invoicesDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0">'
            + "".join(invoices) + "</invoicesDoc></RequestedDoc>").encode()


class DemoAPI:
    environment = Environment.DEVELOPMENT

    def fetch(self, mark, continuation=None):
        if continuation == ("demo-partition", "demo-row"):
            return xml_page([xml_invoice(102, url=False)])
        if mark >= 102:
            return xml_page()
        return xml_page([xml_invoice(101)], ("demo-partition", "demo-row"))
