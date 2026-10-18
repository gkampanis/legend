from dataclasses import dataclass, field
from decimal import Decimal


class ApplicationError(Exception):
    """Only fixed, credential-free messages may cross the GUI boundary."""


class ParseError(ApplicationError):
    pass


class SyncError(ApplicationError):
    pass


class Cancelled(ApplicationError):
    pass


@dataclass(frozen=True)
class Credentials:
    user_id: str = field(repr=False)
    subscription_key: str = field(repr=False)

    def __post_init__(self):
        if not self.user_id.strip() or not self.subscription_key.strip():
            raise ApplicationError("Συμπληρώστε και τα δύο API credentials.")
        if any(c in value for value in (self.user_id, self.subscription_key)
               for c in ("\r", "\n", "\x00")):
            raise ApplicationError("Μη έγκυρα API credentials.")


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    vat: str
    environment: str = "development"


@dataclass(frozen=True)
class Invoice:
    mark: int
    uid: str | None
    issuer_vat: str | None
    recipient_vat: str | None
    issue_date: str
    invoice_type: str | None
    series: str | None
    number: str | None
    net: Decimal | None
    vat: Decimal | None
    total: Decimal | None
    download_url: str | None = field(repr=False)
    raw_xml: str = field(repr=False)


@dataclass(frozen=True)
class Page:
    invoices: tuple[Invoice, ...]
    continuation: tuple[str, str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Checkpoint:
    client_id: str
    cursor: int = 0
    query_mark: int = 0
    highwater: int = 0
    continuation: tuple[str, str] | None = field(default=None, repr=False)
    active: bool = False
    revision: int = 0

    @property
    def request_mark(self):
        return self.query_mark if self.active else self.cursor


@dataclass(frozen=True)
class SyncResult:
    pages: int
    records: int
    cancelled: bool = False
