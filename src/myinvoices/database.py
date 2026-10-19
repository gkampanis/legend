from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid
import xml.etree.ElementTree as ET

from .date_range import DateRange
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

EXTENSION = """
CREATE TABLE IF NOT EXISTS document_changes (
    client_id TEXT NOT NULL REFERENCES clients(id), kind TEXT NOT NULL,
    record_key TEXT NOT NULL, event_mark INTEGER, invoice_mark INTEGER NOT NULL,
    cancellation_date TEXT, raw_xml TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(client_id, kind, record_key)
);
CREATE INDEX IF NOT EXISTS changes_invoice_idx ON document_changes(client_id, invoice_mark, kind);
PRAGMA user_version = 2;
"""


PDF_SCHEMA = """
CREATE TABLE IF NOT EXISTS pdf_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 client_id TEXT NOT NULL, mark INTEGER NOT NULL, state TEXT NOT NULL,
 source_hash TEXT NOT NULL, path TEXT, sha256 TEXT, byte_count INTEGER NOT NULL DEFAULT 0,
 at TEXT NOT NULL, FOREIGN KEY(client_id,mark) REFERENCES invoices(client_id,mark)
);
CREATE INDEX IF NOT EXISTS pdf_invoice_idx ON pdf_attempts(client_id,mark,id DESC);
PRAGMA user_version=3;
"""


PERIOD_SCHEMA = """
CREATE TABLE IF NOT EXISTS period_checkpoints (
 client_id TEXT NOT NULL REFERENCES clients(id), date_from TEXT NOT NULL, date_to TEXT NOT NULL,
 cursor INTEGER NOT NULL DEFAULT 0, query_mark INTEGER NOT NULL DEFAULT 0,
 highwater INTEGER NOT NULL DEFAULT 0, partition_key TEXT, row_key TEXT,
 active INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(client_id,date_from,date_to)
);
CREATE TABLE IF NOT EXISTS period_continuation_history (
 client_id TEXT NOT NULL REFERENCES clients(id), date_from TEXT NOT NULL, date_to TEXT NOT NULL,
 token_hash TEXT NOT NULL, PRIMARY KEY(client_id,date_from,date_to,token_hash)
);
CREATE INDEX IF NOT EXISTS invoices_date_idx ON invoices(client_id,issue_date DESC,mark DESC);
PRAGMA user_version=4;
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
                conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + EXTENSION + PDF_SCHEMA + PERIOD_SCHEMA + "\nCOMMIT;")
            elif version in (1, 2, 3):
                # Additive migration only, with a separate consistent SQLite backup first.
                required = {"clients", "invoices", "checkpoints", "events", "continuation_history"}
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required <= tables:
                    raise ApplicationError("Μη αναμενόμενη δομή βάσης· δεν έγινε αναβάθμιση.")
                with tempfile.NamedTemporaryFile(prefix=self.path.stem + f"-schema{version}-backup-",
                                                  suffix=".sqlite3", dir=self.path.parent, delete=False) as file:
                    backup_path = Path(file.name)
                backup = sqlite3.connect(backup_path)
                try:
                    conn.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ApplicationError("Δεν επαληθεύτηκε το αντίγραφο ασφαλείας. Δεν έγινε αναβάθμιση.")
                finally:
                    backup.close()
                conn.executescript("BEGIN IMMEDIATE;\n" + EXTENSION + PDF_SCHEMA + PERIOD_SCHEMA + "\nCOMMIT;")
            elif version != 4:
                raise ApplicationError("Μη υποστηριζόμενη έκδοση βάσης δεδομένων.")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # Compare attempts to the current URL without persisting URLs a second time.
        conn.create_function("source_digest", 1,
                             lambda value: hashlib.sha256((value or "").encode()).hexdigest(),
                             deterministic=True)
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

    def rename_client(self, client_id, name):
        name = name.strip()
        if not name or len(name) > 200:
            raise ApplicationError("Συμπληρώστε όνομα πελάτη έως 200 χαρακτήρες.")
        with self.connection() as conn:
            result = conn.execute("UPDATE clients SET name=? WHERE id=?", (name, client_id))
            if result.rowcount != 1:
                raise ApplicationError("Ο πελάτης δεν βρέθηκε.")
        return self.client(client_id)

    def configure_client(self, client_id, name, environment):
        name = name.strip()
        if not name or len(name) > 200 or environment not in {"development", "production"}:
            raise ApplicationError("Μη έγκυρο όνομα ή περιβάλλον πελάτη.")
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
                if client is None:
                    raise ApplicationError("Ο πελάτης δεν βρέθηκε.")
                if client["environment"] != environment:
                    state = conn.execute("SELECT revision FROM checkpoints WHERE client_id=?", (client_id,)).fetchone()
                    count = conn.execute("SELECT count(*) FROM invoices WHERE client_id=?", (client_id,)).fetchone()[0]
                    periods = conn.execute("SELECT count(*) FROM period_checkpoints WHERE client_id=? AND revision>0", (client_id,)).fetchone()[0]
                    if count or state[0] or periods:
                        raise ApplicationError("Ο πελάτης έχει ήδη συγχρονισμένα δεδομένα. Προσθέστε ξεχωριστή εγγραφή για το άλλο περιβάλλον.")
                conn.execute("UPDATE clients SET name=?, environment=? WHERE id=?", (name, environment, client_id))
                conn.commit()
        except sqlite3.IntegrityError:
            raise ApplicationError("Υπάρχει ήδη αυτό το ΑΦΜ στο επιλεγμένο περιβάλλον.") from None
        return self.client(client_id)

    def client(self, client_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise ApplicationError("Ο πελάτης δεν βρέθηκε.")
        return Client(**dict(row))

    def checkpoint(self, client_id, period=None):
        with self.connection() as conn:
            if period:
                # Require canonical inclusive dates before creating a durable scope.
                parsed = DateRange.parse(*period)
                period = parsed.period
                conn.execute("""INSERT OR IGNORE INTO period_checkpoints(client_id,date_from,date_to)
                    VALUES (?,?,?)""", (client_id, *period))
                row = conn.execute("""SELECT * FROM period_checkpoints
                    WHERE client_id=? AND date_from=? AND date_to=?""", (client_id, *period)).fetchone()
            else:
                row = conn.execute("SELECT * FROM checkpoints WHERE client_id=?", (client_id,)).fetchone()
        if row is None:
            raise ApplicationError("Δεν βρέθηκε πρόοδος για τον πελάτη.")
        token = (row["partition_key"], row["row_key"]) if row["active"] else None
        return Checkpoint(client_id, row["cursor"], row["query_mark"], row["highwater"],
                          token, bool(row["active"]), row["revision"], period)

    def commit_page(self, checkpoint, page):
        """Invoice writes and the next cursor commit together, or neither does."""
        client = self.client(checkpoint.client_id)
        highwater = max([checkpoint.cursor, checkpoint.highwater]
                        + [invoice.mark for invoice in page.invoices]
                        + [change.event_mark for change in page.changes if change.event_mark is not None])
        table = 'period_checkpoints' if checkpoint.period else 'checkpoints'
        history = 'period_continuation_history' if checkpoint.period else 'continuation_history'
        where = 'client_id=?' + (' AND date_from=? AND date_to=?' if checkpoint.period else '')
        scope = (client.id, *(checkpoint.period or ()))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(f"SELECT revision FROM {table} WHERE {where}", scope).fetchone()
            if current is None or current[0] != checkpoint.revision:
                raise SyncError("Η πρόοδος άλλαξε από άλλη εργασία. Δοκιμάστε ξανά.")
            for change in page.changes:
                canonical = ET.canonicalize(change.raw_xml, strip_text=True, rewrite_prefixes=True)
                key = (str(change.event_mark) if change.event_mark is not None
                       else "xml:" + hashlib.sha256(canonical.encode()).hexdigest())
                conn.execute("""INSERT INTO document_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_id, kind, record_key) DO UPDATE SET
                    event_mark=excluded.event_mark, invoice_mark=excluded.invoice_mark,
                    cancellation_date=excluded.cancellation_date, raw_xml=excluded.raw_xml,
                    updated_at=excluded.updated_at""",
                    (client.id, change.kind, key, change.event_mark, change.invoice_mark,
                     change.cancellation_date, change.raw_xml, now()))
            if page.continuation:
                digest = hashlib.sha256(json.dumps(page.continuation).encode()).hexdigest()
                try:
                    conn.execute(f"INSERT INTO {history} VALUES ({','.join('?' for _ in (*scope, digest))})", (*scope, digest))
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
                conn.execute(f"DELETE FROM {history} WHERE {where}", scope)
            conn.execute(f"""UPDATE {table} SET cursor=?, query_mark=?, highwater=?,
                            partition_key=?, row_key=?, active=?, revision=revision+1 WHERE {where}""",
                         (cursor, query, highwater, partition, row, active, *scope))
            conn.execute("INSERT INTO events(client_id, at, kind, record_count) VALUES (?, ?, ?, ?)",
                         (client.id, now(), "period_page_committed" if checkpoint.period else "page_committed", len(page.invoices) + len(page.changes)))
            conn.commit()
        return self.checkpoint(client.id, checkpoint.period)

    @staticmethod
    def _invoice_scope(client_id, dates=None):
        """One classification shared by lists, counters and retry queues.

        A later failed attempt does not discard an archived artifact. An explicit
        missing/changed-artifact event invalidates older successes instead. Both
        attempts and successes must belong to the invoice's current source URL.
        """
        from .pdf_downloads import SAVED_STATES
        states = ",".join("?" for _ in SAVED_STATES)
        clause, values = (dates or DateRange()).sql("i.issue_date")
        query = f"""SELECT i.*,
            COALESCE(saved.state,attempt.state) AS pdf_state,
            COALESCE(saved.source_hash,attempt.source_hash) AS pdf_source_hash,
            saved.path AS pdf_path, saved.sha256 AS pdf_sha256,
            saved.byte_count AS pdf_byte_count,
            saved.id IS NOT NULL AS pdf_saved,
            attempt.id IS NOT NULL AS pdf_attempted,
            EXISTS(SELECT 1 FROM document_changes c WHERE c.client_id=i.client_id
                AND c.invoice_mark=i.mark AND c.kind='cancellation') AS cancelled
            FROM invoices i
            LEFT JOIN pdf_attempts saved ON saved.id=(
                SELECT p.id FROM pdf_attempts p
                WHERE p.client_id=i.client_id AND p.mark=i.mark
                AND p.source_hash=source_digest(i.download_url) AND p.state IN ({states})
                AND p.id>COALESCE((SELECT max(t.id) FROM pdf_attempts t
                    WHERE t.client_id=i.client_id AND t.mark=i.mark AND t.source_hash=p.source_hash
                    AND t.state IN ('artifact_missing','artifact_changed','downloads_reset')),0)
                ORDER BY p.id DESC LIMIT 1)
            LEFT JOIN pdf_attempts attempt ON attempt.id=(
                SELECT p.id FROM pdf_attempts p WHERE p.client_id=i.client_id AND p.mark=i.mark
                AND p.source_hash=source_digest(i.download_url) AND p.state<>'downloads_reset'
                AND p.id>COALESCE((SELECT max(t.id) FROM pdf_attempts t
                    WHERE t.client_id=p.client_id AND t.mark=p.mark AND t.source_hash=p.source_hash
                    AND t.state='downloads_reset'),0) ORDER BY p.id DESC LIMIT 1)
            WHERE i.client_id=?""" + clause
        return query, (*SAVED_STATES, client_id, *values)

    @staticmethod
    def _bucket_clause(bucket):
        if bucket == "all":
            return ""
        if bucket == "success":
            return " WHERE pdf_saved=1"
        if bucket == "failure":
            return " WHERE pdf_attempted=1 AND pdf_saved=0 AND cancelled=0"
        raise ApplicationError("Μη έγκυρη λίστα παραστατικών.")

    def invoices(self, client_id, offset=0, limit=200, dates=None, bucket="all"):
        query, values = self._invoice_scope(client_id, dates)
        query = "SELECT * FROM (" + query + ")" + self._bucket_clause(bucket)
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                query + " ORDER BY issue_date DESC,mark DESC LIMIT ? OFFSET ?",
                (*values, limit, offset))]

    def count(self, client_id, dates=None, bucket="all"):
        query, values = self._invoice_scope(client_id, dates)
        with self.connection() as conn:
            return conn.execute("SELECT count(*) FROM (" + query + ")" + self._bucket_clause(bucket), values).fetchone()[0]

    def changes(self, client_id, limit=200):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM document_changes WHERE client_id=? ORDER BY event_mark DESC, record_key LIMIT ?",
                (client_id, limit))]

    def change_count(self, client_id):
        with self.connection() as conn:
            return conn.execute("SELECT count(*) FROM document_changes WHERE client_id=?", (client_id,)).fetchone()[0]

    def events(self, client_id, limit=100):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE client_id=? ORDER BY id DESC LIMIT ?", (client_id, limit))]

    def download_candidates(self, client_id, dates=None):
        query, values = self._invoice_scope(client_id, dates)
        with self.connection() as conn:
            # Fetch only small metadata, not raw invoice XML, across a batch.
            return [dict(r) for r in conn.execute("""SELECT mark,issue_date,issuer_vat,download_url,
                pdf_state,pdf_source_hash FROM (""" + query + ") WHERE download_url IS NOT NULL AND cancelled=0 ORDER BY mark", values)]

    def saved_pdf(self, client_id, mark, source_hash):
        from .pdf_downloads import SAVED_STATES
        states = ",".join("?" for _ in SAVED_STATES)
        with self.connection() as conn:
            row = conn.execute(f"""SELECT * FROM pdf_attempts p WHERE client_id=? AND mark=?
                AND source_hash=? AND state IN ({states})
                AND id>COALESCE((SELECT max(t.id) FROM pdf_attempts t
                    WHERE t.client_id=p.client_id AND t.mark=p.mark AND t.source_hash=p.source_hash
                    AND t.state IN ('artifact_missing','artifact_changed','downloads_reset')),0)
                ORDER BY id DESC LIMIT 1""", (client_id, mark, source_hash, *SAVED_STATES)).fetchone()
            return dict(row) if row else None

    def record_pdf(self, client_id, mark, state, source_hash, path=None, sha256=None, byte_count=0):
        with self.connection() as conn:
            conn.execute("""INSERT INTO pdf_attempts(client_id,mark,state,source_hash,path,sha256,byte_count,at)
                VALUES (?,?,?,?,?,?,?,?)""", (client_id,mark,state,source_hash,path,sha256,byte_count,now()))

    def reset_downloads(self, client_id):
        """Append reset barriers for all this client's sources in one transaction.

        Invoice/API checkpoints, client/credentials and attempt history survive.
        Old successes/challenges cannot reappear if a source URL changes back.
        """
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM clients WHERE id=?',(client_id,)).fetchone() is None:
                raise ApplicationError('Επιλέξτε πελάτη για μηδενισμό λήψεων.')
            rows=conn.execute('''SELECT mark,source_digest(download_url) AS source_hash
                FROM invoices WHERE client_id=? UNION SELECT mark,source_hash FROM pdf_attempts
                WHERE client_id=?''',(client_id,client_id)).fetchall()
            at=now()
            conn.executemany('''INSERT INTO pdf_attempts(client_id,mark,state,source_hash,at)
                VALUES (?,?,'downloads_reset',?,?)''',[(client_id,r['mark'],r['source_hash'],at) for r in rows])
            count=conn.execute('SELECT count(*) FROM invoices WHERE client_id=?',(client_id,)).fetchone()[0]
            conn.execute('INSERT INTO events(client_id,at,kind,record_count) VALUES (?,?,?,?)',
                         (client_id,at,'pdf_downloads_reset',count))
            conn.commit()
        return count

    def invoice(self, client_id, mark):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE client_id=? AND mark=?", (client_id,mark)).fetchone()
            if row is None:
                raise ApplicationError('Επιλέξτε ένα παραστατικό.')
            return dict(row)

    def environment_editable(self, client_id):
        with self.connection() as conn:
            return not (conn.execute("SELECT revision FROM checkpoints WHERE client_id=?", (client_id,)).fetchone()[0]
                or conn.execute("SELECT count(*) FROM invoices WHERE client_id=?", (client_id,)).fetchone()[0]
                or conn.execute("SELECT count(*) FROM period_checkpoints WHERE client_id=? AND revision>0", (client_id,)).fetchone()[0])

    def pending_candidates(self, client_id, dates=None):
        from .pdf_downloads import INTERACTIVE_STATES
        return [row for row in self.download_candidates(client_id,dates)
                if row['pdf_state'] in INTERACTIVE_STATES and row['pdf_source_hash'] == hashlib.sha256(row['download_url'].encode()).hexdigest()]

    def pdf_stats(self, client_id, dates=None):
        query, values = self._invoice_scope(client_id, dates)
        with self.connection() as conn:
            row=conn.execute("""SELECT count(*) AS total,COALESCE(SUM(pdf_saved),0) AS saved,
                COALESCE(SUM(pdf_attempted=1 AND pdf_saved=0 AND cancelled=0),0) AS failed
                FROM (""" + query + ")", values).fetchone()
            result=dict(row)
        result['pending']=len(self.pending_candidates(client_id,dates))
        return result
