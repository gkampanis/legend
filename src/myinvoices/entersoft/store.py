"""Independent local store. Main myInvoices schema/cursors remain untouched."""
from contextlib import contextmanager
from datetime import datetime,timezone
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from ..models import ApplicationError
from ..table_export import safe_cell
from .domain import Mapping,State,REASONS,STATE_LABELS

APP_ID=0x45425331
SCHEMA='''
CREATE TABLE scopes(id TEXT PRIMARY KEY,client_id TEXT NOT NULL,vat TEXT NOT NULL,environment TEXT NOT NULL,company TEXT NOT NULL);
CREATE TABLE supplier_mappings(scope TEXT NOT NULL REFERENCES scopes(id),supplier_vat TEXT NOT NULL,account TEXT NOT NULL,supplier_code TEXT NOT NULL,expense_item TEXT NOT NULL,PRIMARY KEY(scope,supplier_vat));
CREATE TABLE overrides(scope TEXT NOT NULL REFERENCES scopes(id),mark INTEGER NOT NULL,account TEXT NOT NULL,supplier_code TEXT NOT NULL,expense_item TEXT NOT NULL,PRIMARY KEY(scope,mark));
CREATE TABLE jobs(scope TEXT NOT NULL REFERENCES scopes(id),mark INTEGER NOT NULL,fingerprint TEXT NOT NULL,
 snapshot TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','processing','completed','failed','manual_review','skipped')),
 reasons TEXT NOT NULL,account TEXT NOT NULL DEFAULT '',ebs_reference TEXT,review_lock INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL,identity_hash TEXT NOT NULL DEFAULT '',PRIMARY KEY(scope,mark));
CREATE INDEX jobs_identity_idx ON jobs(scope,identity_hash);
CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL REFERENCES scopes(id),mark INTEGER,event TEXT NOT NULL,
 state TEXT,snapshot TEXT NOT NULL,reasons TEXT NOT NULL,account TEXT NOT NULL,ebs_reference TEXT,at TEXT NOT NULL);
CREATE INDEX audit_scope_idx ON audit(scope,id DESC);
PRAGMA application_id=1161974577;
PRAGMA user_version=1;
'''


def now():return datetime.now(timezone.utc).isoformat()

def identity_hash(row):
    if not all(row.get(k) for k in ('issuer_vat','number','issue_date')):return ''
    return hashlib.sha256(json.dumps([row.get('issuer_vat'),row.get('series') or '',row.get('number'),row.get('issue_date')],ensure_ascii=False).encode()).hexdigest()

def snapshot(row):
    return json.dumps({k:row.get(k) for k in ('mark','issuer_vat','recipient_vat','issue_date','invoice_type','series','number','net','vat','total','vat_categories','supplier_code','expense_item')},ensure_ascii=False,default=str)


class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as conn:
            tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not tables:conn.executescript('BEGIN IMMEDIATE;'+SCHEMA+'COMMIT;')
            self._check(conn)
    @staticmethod
    def _check(conn):
        if conn.execute('PRAGMA application_id').fetchone()[0]!=APP_ID or conn.execute('PRAGMA user_version').fetchone()[0]!=1:
            raise ApplicationError('Η βάση δεν ανήκει στην επέκταση Entersoft. Δεν έγινε αντικατάσταση.')
    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.path,timeout=5,isolation_level=None);conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA synchronous=FULL')
        try:yield conn
        finally:
            if conn.in_transaction:conn.rollback()
            conn.close()
    def bind(self,scope):
        with self.connection() as conn:
            conn.execute('INSERT OR IGNORE INTO scopes VALUES(?,?,?,?,?)',(scope.key,scope.client_id,scope.client_vat,scope.environment,scope.company))
    def _event(self,conn,scope,mark,event,state=None,row=None,reasons=(),account='',reference=None):
        conn.execute('INSERT INTO audit(scope,mark,event,state,snapshot,reasons,account,ebs_reference,at) VALUES(?,?,?,?,?,?,?,?,?)',
            (scope.key,mark,event,state,snapshot(row or {}),json.dumps(tuple(reasons)),account,reference,now()))
    def set_mapping(self,scope,supplier,mapping,mark=None):
        if len(supplier)!=9 or not supplier.isascii() or not supplier.isdigit():raise ApplicationError('Το ΑΦΜ προμηθευτή πρέπει να έχει 9 ψηφία.')
        self.bind(scope)
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if mark is None:
                conn.execute('INSERT INTO supplier_mappings VALUES(?,?,?,?,?) ON CONFLICT(scope,supplier_vat) DO UPDATE SET account=excluded.account,supplier_code=excluded.supplier_code,expense_item=excluded.expense_item',
                    (scope.key,supplier,mapping.account,mapping.supplier_code,mapping.expense_item))
            else:
                conn.execute('INSERT INTO overrides VALUES(?,?,?,?,?) ON CONFLICT(scope,mark) DO UPDATE SET account=excluded.account,supplier_code=excluded.supplier_code,expense_item=excluded.expense_item',
                    (scope.key,mark,mapping.account,mapping.supplier_code,mapping.expense_item))
            self._event(conn,scope,mark,'invoice_override_set' if mark else 'supplier_mapping_set',row={'issuer_vat':supplier},account=mapping.account)
            conn.commit()
    def clear_override(self,scope,mark):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');conn.execute('DELETE FROM overrides WHERE scope=? AND mark=?',(scope.key,mark))
            self._event(conn,scope,mark,'invoice_override_removed');conn.commit()
    def mapping(self,scope,supplier,mark):
        with self.connection() as conn:
            row=conn.execute('SELECT account,supplier_code,expense_item FROM overrides WHERE scope=? AND mark=?',(scope.key,mark)).fetchone()
            if not row:row=conn.execute('SELECT account,supplier_code,expense_item FROM supplier_mappings WHERE scope=? AND supplier_vat=?',(scope.key,supplier)).fetchone()
            return Mapping(*row) if row else None
    def job(self,scope,mark):
        with self.connection() as conn:
            row=conn.execute('SELECT * FROM jobs WHERE scope=? AND mark=?',(scope.key,mark)).fetchone()
            return dict(row) if row else None
    def jobs(self,scope):
        with self.connection() as conn:return {r['mark']:dict(r) for r in conn.execute('SELECT * FROM jobs WHERE scope=? ORDER BY mark',(scope.key,))}
    def recover_interrupted(self,scope):
        self.bind(scope)
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows=conn.execute("SELECT * FROM jobs WHERE scope=? AND state='processing'",(scope.key,)).fetchall()
            for row in rows:
                conn.execute("UPDATE jobs SET state='manual_review',reasons=?,review_lock=1,updated_at=? WHERE scope=? AND mark=?",(json.dumps(['interrupted']),now(),scope.key,row['mark']))
                self._event(conn,scope,row['mark'],'interrupted_review',State.MANUAL_REVIEW.value,json.loads(row['snapshot']),('interrupted',),row['account'],row['ebs_reference'])
            conn.commit();return len(rows)
    def prepare(self,scope,preparation):
        """Atomic idempotent local preparation. Never creates Completed TED state."""
        self.bind(scope);row=dict(preparation.invoice);mark=row['mark'];account=preparation.mapping.account if preparation.mapping else ''
        row['vat_categories']=preparation.vat_categories
        row['supplier_code']=preparation.mapping.supplier_code if preparation.mapping else ''
        row['expense_item']=preparation.mapping.expense_item if preparation.mapping else ''
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT * FROM jobs WHERE scope=? AND mark=?',(scope.key,mark)).fetchone()
            state,reasons=preparation.state,preparation.reasons
            if old and old['state']==State.COMPLETED.value:
                if old['fingerprint']==preparation.fingerprint:
                    self._event(conn,scope,mark,'completed_not_retried',State.SKIPPED.value,row,('already_completed',),old['account'],old['ebs_reference'])
                    conn.commit();return State.SKIPPED.value,('already_completed',)
                state,reasons=State.MANUAL_REVIEW.value,('source_changed',)
            elif old and old['review_lock']:
                locked_reasons=tuple(json.loads(old['reasons'])) or ('interrupted',)
                self._event(conn,scope,mark,'review_still_required',State.MANUAL_REVIEW.value,row,locked_reasons,old['account'],old['ebs_reference'])
                conn.commit();return State.MANUAL_REVIEW.value,locked_reasons
            key=identity_hash(row)
            duplicate=conn.execute('SELECT mark FROM jobs WHERE scope=? AND mark<>? AND identity_hash=? LIMIT 1',(scope.key,mark,key)).fetchone() if key else None
            if duplicate:
                state=State.MANUAL_REVIEW.value
                reasons=tuple(dict.fromkeys((*reasons,'duplicate_reference')))
            locked=int(bool(old and old['state']==State.COMPLETED.value))
            conn.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,mark) DO UPDATE SET
                fingerprint=excluded.fingerprint,snapshot=excluded.snapshot,state=excluded.state,reasons=excluded.reasons,
                account=excluded.account,review_lock=excluded.review_lock,updated_at=excluded.updated_at,identity_hash=excluded.identity_hash''',
                (scope.key,mark,preparation.fingerprint,snapshot(row),state,json.dumps(reasons),account,None,locked,now(),key))
            self._event(conn,scope,mark,'stage_a_prepared',state,row,reasons,account)
            conn.commit();return state,reasons
    def companies(self,client):
        with self.connection() as conn:
            return [r[0] for r in conn.execute('SELECT DISTINCT company FROM scopes WHERE client_id=? AND vat=? AND environment=? ORDER BY company',(client.id,client.vat,client.environment))]
    def mappings(self,scope):
        with self.connection() as conn:
            suppliers={r['supplier_vat']:Mapping(r['account'],r['supplier_code'],r['expense_item']) for r in conn.execute('SELECT * FROM supplier_mappings WHERE scope=?',(scope.key,))}
            overrides={r['mark']:Mapping(r['account'],r['supplier_code'],r['expense_item']) for r in conn.execute('SELECT * FROM overrides WHERE scope=?',(scope.key,))}
            return suppliers,overrides
    def fail(self,scope,row):
        self.bind(scope)
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT state FROM jobs WHERE scope=? AND mark=?',(scope.key,row['mark'])).fetchone()
            if not old or old['state'] not in ('completed','processing','manual_review'):
                conn.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,mark) DO UPDATE SET
                    state='failed',reasons=excluded.reasons,updated_at=excluded.updated_at''',
                    (scope.key,row['mark'],'',snapshot(row),'failed',json.dumps(['internal_error']),'',None,0,now(),identity_hash(row)))
            self._event(conn,scope,row.get('mark'),'preparation_error',State.FAILED.value,row,('internal_error',))
            conn.commit()
    def audit(self,scope,limit=300):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM audit WHERE scope=? ORDER BY id DESC LIMIT ?',(scope.key,limit))]
    def export_audit(self,scope,path):
        created=False
        try:
            with Path(path).open('x',encoding='utf-8-sig',newline='') as file,self.connection() as conn:
                created=True;writer=csv.writer(file,delimiter=';')
                writer.writerow(('Χρόνος UTC','MARK','ΑΦΜ προμηθευτή','Σειρά','Αρχικός αριθμός','Ημερομηνία','Σύνολο','Λογαριασμός','Κατάσταση','Ενέργεια','Αιτία','Αναφορά EBS'))
                count=0
                for record in conn.execute('SELECT * FROM audit WHERE scope=? ORDER BY id',(scope.key,)):
                    row=json.loads(record['snapshot'])
                    data=(record['at'],record['mark'] or '',row.get('issuer_vat') or '',row.get('series') or '',row.get('number') or '',row.get('issue_date') or '',row.get('total') or '',record['account'],STATE_LABELS.get(record['state'],''),record['event'], ' / '.join(REASONS.get(code,'Χειροκίνητος έλεγχος') for code in json.loads(record['reasons'])),record['ebs_reference'] or '')
                    writer.writerow([safe_cell(value) for value in data]);count+=1
                file.flush();os.fsync(file.fileno());return count
        except (OSError,ValueError,sqlite3.Error):
            if created:Path(path).unlink(missing_ok=True)
            raise ApplicationError('Δεν εξήχθη το ιστορικό. Επιλέξτε νέο αρχείο CSV.') from None
    def export_excel(self,scope,path):
        from .excel import write_excel
        headers=('Χρόνος UTC','MARK','ΑΦΜ προμηθευτή','Σειρά','Αρχικός αριθμός','Ημερομηνία','Καθαρή αξία','ΦΠΑ','Σύνολο','Κατηγορία ΦΠΑ XML','Λογαριασμός','Κωδικός προμηθευτή EBS','Κωδικός δαπάνης EBS','Κατάσταση','Ενέργεια','Αιτία','Αναφορά EBS')
        def rows():
            with self.connection() as conn:
                for record in conn.execute('SELECT * FROM audit WHERE scope=? ORDER BY id',(scope.key,)):
                    row=json.loads(record['snapshot'])
                    yield (record['at'],record['mark'] or '',row.get('issuer_vat') or '',row.get('series') or '',row.get('number') or '',row.get('issue_date') or '',row.get('net') or '',row.get('vat') or '',row.get('total') or '',
                        ' / '.join(row.get('vat_categories') or []),record['account'],row.get('supplier_code') or '',row.get('expense_item') or '',
                        STATE_LABELS.get(record['state'],''),record['event'],' / '.join(REASONS.get(code,'Χειροκίνητος έλεγχος') for code in json.loads(record['reasons'])),record['ebs_reference'] or '')
        return write_excel(path,headers,rows())

    def backup(self,destination):
        output=Path(destination)/('entersoft-agent-'+uuid.uuid4().hex+'.sqlite3')
        try:
            with output.open('xb'):pass
            with self.connection() as source,sqlite3.connect(output) as target:
                source.backup(target)
                self._check(target)
                if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise sqlite3.DatabaseError()
            return output
        except (OSError,sqlite3.Error,ApplicationError):
            output.unlink(missing_ok=True)
            raise ApplicationError('Δεν δημιουργήθηκε το αντίγραφο της επέκτασης.') from None
