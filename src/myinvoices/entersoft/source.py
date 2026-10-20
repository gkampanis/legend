"""Read-only adapter to the existing myInvoices schema. No API/PDF operations."""
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from ..models import ApplicationError


class Source:
    def __init__(self,path,client):self.path,self.client=Path(path),client
    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            client=conn.execute('SELECT vat,environment FROM clients WHERE id=?',(self.client.id,)).fetchone()
            if not client or tuple(client)!=(self.client.vat,self.client.environment):
                raise ApplicationError('Το πλαίσιο πελάτη άλλαξε. Ανοίξτε ξανά την ενότητα Entersoft.')
            yield conn
        except sqlite3.Error:
            raise ApplicationError('Δεν διαβάστηκαν τα τοπικά στοιχεία myDATA. Δεν έγινε αλλαγή στην κύρια βάση.') from None
        finally:conn.close()
    def rows(self,dates=None,supplier='',offset=0,limit=200,*,missing_only=False,scope=None,mappings_path=None):
        from ..date_range import DateRange
        clause,values=(dates or DateRange()).sql('i.issue_date')
        query=' FROM invoices i WHERE i.client_id=?'+clause
        args=[self.client.id,*values]
        if supplier:
            query+=' AND i.issuer_vat=?';args.append(supplier)
        with self.connection() as conn:
            if missing_only and scope and mappings_path:
                conn.execute('ATTACH DATABASE ? AS agent',(Path(mappings_path).resolve().as_uri()+'?mode=ro',))
                query+=' AND NOT EXISTS(SELECT 1 FROM agent.supplier_mappings m WHERE m.scope=? AND m.supplier_vat=i.issuer_vat) AND NOT EXISTS(SELECT 1 FROM agent.overrides o WHERE o.scope=? AND o.mark=i.mark)'
                args.extend((scope.key,scope.key))
            total=conn.execute('SELECT count(*)'+query,args).fetchone()[0]
            rows=conn.execute('''SELECT i.*,EXISTS(SELECT 1 FROM document_changes c WHERE c.client_id=i.client_id
                AND c.invoice_mark=i.mark AND c.kind='cancellation') AS cancelled,
                EXISTS(SELECT 1 FROM document_changes c WHERE c.client_id=i.client_id AND c.invoice_mark=i.mark AND c.kind='expense_classification') AS classification_update,
                EXISTS(SELECT 1 FROM invoices other WHERE other.client_id=i.client_id AND other.mark<>i.mark
                    AND other.issuer_vat=i.issuer_vat AND COALESCE(other.series,'')=COALESCE(i.series,'')
                    AND other.number=i.number AND other.issue_date=i.issue_date) AS potential_duplicate'''+query+' ORDER BY issue_date DESC,mark DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
            return [dict(r) for r in rows],total
    def invoice(self,mark):
        with self.connection() as conn:
            row=conn.execute('''SELECT i.*,EXISTS(SELECT 1 FROM document_changes c WHERE c.client_id=i.client_id
                AND c.invoice_mark=i.mark AND c.kind='cancellation') AS cancelled,
                EXISTS(SELECT 1 FROM document_changes c WHERE c.client_id=i.client_id AND c.invoice_mark=i.mark AND c.kind='expense_classification') AS classification_update,
                EXISTS(SELECT 1 FROM invoices other WHERE other.client_id=i.client_id AND other.mark<>i.mark
                    AND other.issuer_vat=i.issuer_vat AND COALESCE(other.series,'')=COALESCE(i.series,'')
                    AND other.number=i.number AND other.issue_date=i.issue_date) AS potential_duplicate FROM invoices i WHERE i.client_id=? AND i.mark=?''',(self.client.id,mark)).fetchone()
            if row is None:raise ApplicationError('Το επιλεγμένο παραστατικό δεν βρέθηκε στον συγκεκριμένο πελάτη.')
            return dict(row)
