from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .models import ApplicationError, Checkpoint, Client, SyncError

SCHEMA = """
CREATE TABLE clients (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, vat TEXT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('development', 'production')),
    UNIQUE(vat, environment)
);
CREATE TABLE checkpoints (
    client_id TEXT PRIMARY KEY REFERENCES clients(id),
    cursor INTEGER NOT NULL DEFAULT 0, query_mark INTEGER NOT NULL DEFAULT 0,
    highwater INTEGER NOT NULL DEFAULT 0,
    partition_key TEXT, row_key TEXT, active INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE invoices (
    client_id TEXT NOT NULL REFERENCES clients(id), mark INTEGER NOT NULL,
    uid TEXT, issuer_vat TEXT, recipient_vat TEXT, issue_date TEXT NOT NULL,
    invoice_type TEXT, series TEXT, number TEXT,
    net TEXT, vat TEXT, total TEXT, download_url TEXT, raw_xml TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('url_available', 'no_url')),
    updated_at TEXT NOT NULL, PRIMARY KEY(client_id, mark)
);
CREATE TABLE continuation_history (
    client_id TEXT NOT NULL REFERENCES clients(id), token_hash TEXT NOT NULL,
    PRIMARY KEY(client_id, token_hash)
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT REFERENCES clients(id),
    at TEXT NOT NULL, kind TEXT NOT NULL, record_count INTEGER NOT NULL
);
PRAGMA user_version = 1;
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if tables:
                    raise ApplicationError("Η βάση δεν ανήκει σε αυτή την έκδοση του myInvoices.")
                conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            elif version != 1:
                raise ApplicationError("Μη υποστηριζόμενη έκδοση βάσης δεδομένων.")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def add_client(self, name, vat, environment="development"):
        name, vat = name.strip(), vat.strip()
        if not name or len(name) > 200:
            raise ApplicationError("Συμπληρώστε όνομα πελάτη έως 200 χαρακτήρες.")
        if len(vat) != 9 or not vat.isascii() or not vat.isdigit():
            raise ApplicationError("Το ΑΦΜ πρέπει να αποτελείται από 9 ψηφία.")
        if environment not in {"development", "production"}:
            raise ApplicationError("Μη έγκυρο περιβάλλον πελάτη.")
        client = Client(str(uuid.uuid4()), name, vat, environment)
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO clients VALUES (?, ?, ?, ?)",
                             (client.id, name, vat, environment))
                conn.execute("INSERT INTO checkpoints(client_id) VALUES (?)", (client.id,))
                conn.commit()
        except sqlite3.IntegrityError:
            raise ApplicationError("Υπάρχει ήδη αυτό το ΑΦΜ στο συγκεκριμένο περιβάλλον.") from None
        return client

    def clients(self):
        with self.connection() as conn:
            return [Client(**dict(r)) for r in conn.execute("SELECT * FROM clients ORDER BY name, vat")]

    def client(self, client_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise ApplicationError("Ο πελάτης δεν βρέθηκε.")
        return Client(**dict(row))

    def checkpoint(self, client_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM checkpoints WHERE client_id=?", (client_id,)).fetchone()
        if row is None:
            raise ApplicationError("Δεν βρέθηκε πρόοδος για τον πελάτη.")
        token = (row["partition_key"], row["row_key"]) if row["active"] else None
        return Checkpoint(client_id, row["cursor"], row["query_mark"], row["highwater"],
                          token, bool(row["active"]), row["revision"])

    def commit_page(self, checkpoint, page):
        """Invoice writes and the next cursor commit together, or neither does."""
        client = self.client(checkpoint.client_id)
        highwater = max([checkpoint.cursor, checkpoint.highwater]
                        + [invoice.mark for invoice in page.invoices])
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT revision FROM checkpoints WHERE client_id=?",
                                   (client.id,)).fetchone()
            if current is None or current[0] != checkpoint.revision:
                raise SyncError("Η πρόοδος άλλαξε από άλλη εργασία. Δοκιμάστε ξανά.")
            if page.continuation:
                digest = hashlib.sha256(json.dumps(page.continuation).encode()).hexdigest()
                try:
                    conn.execute("INSERT INTO continuation_history VALUES (?, ?)", (client.id, digest))
                except sqlite3.IntegrityError:
                    raise SyncError("Επαναλαμβανόμενο continuation token· ο συγχρονισμός σταμάτησε.") from None
            for invoice in page.invoices:
                # Missing counterpart is preserved as unknown; a known mismatch is rejected.
                if invoice.recipient_vat and invoice.recipient_vat != client.vat:
                    raise SyncError("Το ΑΦΜ παραλήπτη δεν συμφωνεί με τον επιλεγμένο πελάτη.")
                values = (
                    client.id, invoice.mark, invoice.uid, invoice.issuer_vat, invoice.recipient_vat,
                    invoice.issue_date, invoice.invoice_type, invoice.series, invoice.number,
                    *(str(v) if v is not None else None for v in (invoice.net, invoice.vat, invoice.total)),
                    invoice.download_url, invoice.raw_xml,
                    "url_available" if invoice.download_url else "no_url", now(),
                )
                conn.execute("""
                    INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_id, mark) DO UPDATE SET
                    uid=excluded.uid, issuer_vat=excluded.issuer_vat,
                    recipient_vat=excluded.recipient_vat, issue_date=excluded.issue_date,
                    invoice_type=excluded.invoice_type, series=excluded.series, number=excluded.number,
                    net=excluded.net, vat=excluded.vat, total=excluded.total,
                    download_url=excluded.download_url, raw_xml=excluded.raw_xml,
                    status=excluded.status, updated_at=excluded.updated_at
                """, values)
            if page.continuation:
                cursor, query, active = checkpoint.cursor, checkpoint.request_mark, 1
                partition, row = page.continuation
            else:
                cursor, query, active = highwater, 0, 0
                partition = row = None
                conn.execute("DELETE FROM continuation_history WHERE client_id=?", (client.id,))
            conn.execute("""UPDATE checkpoints SET cursor=?, query_mark=?, highwater=?,
                            partition_key=?, row_key=?, active=?, revision=revision+1 WHERE client_id=?""",
                         (cursor, query, highwater, partition, row, active, client.id))
            conn.execute("INSERT INTO events(client_id, at, kind, record_count) VALUES (?, ?, ?, ?)",
                         (client.id, now(), "page_committed", len(page.invoices)))
            conn.commit()
        return self.checkpoint(client.id)

    def invoices(self, client_id, offset=0, limit=200):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM invoices WHERE client_id=? ORDER BY issue_date DESC, mark DESC LIMIT ? OFFSET ?",
                (client_id, limit, offset))]

    def count(self, client_id):
        with self.connection() as conn:
            return conn.execute("SELECT count(*) FROM invoices WHERE client_id=?", (client_id,)).fetchone()[0]

    def events(self, client_id, limit=100):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE client_id=? ORDER BY id DESC LIMIT ?", (client_id, limit))]
