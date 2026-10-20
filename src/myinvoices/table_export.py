"""Export only visible accounting columns, without URLs, XML or local paths."""
import csv
import os
from pathlib import Path
from .models import ApplicationError
from .presentation import PROVENANCE, display_date, display_amount, guidance

HEADERS = ('Ημερομηνία','MARK','ΑΦΜ προμηθευτή','Σειρά / Αριθμός','Σύνολο','Προέλευση PDF','Κατάσταση PDF','Αιτία / επόμενη ενέργεια')


def values(row):
    from .pdf_downloads import state_label
    state = row.get('pdf_state')
    status = ('Ακυρωμένο (myDATA)' if row.get('cancelled') else state_label(state) if row.get('pdf_saved')
              else 'Εκκρεμεί' if row.get('pdf_attempted') else 'Υπάρχει link — PDF ανεξέλεγκτο' if row.get('download_url') else 'Χωρίς link PDF')
    reason, action = guidance(row)
    return (display_date(row.get('issue_date')),str(row['mark']),row.get('issuer_vat') or '—',
            f"{row.get('series') or ''} / {row.get('number') or ''}",display_amount(row.get('total')),
            PROVENANCE.get(state,'—'),status,'' if row.get('pdf_saved') else reason+' · '+action)


def safe_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=','+','-','@')) else text


def export_csv(path, rows, cancel=None):
    path = Path(path)
    created = False
    try:
        # Exclusive creation: never overwrites an existing document.
        with path.open('x',encoding='utf-8-sig',newline='') as file:
            created = True
            writer = csv.writer(file,delimiter=';')
            writer.writerow(HEADERS)
            count = 0
            for row in rows:
                if cancel and cancel.is_set():
                    raise ApplicationError('Η εξαγωγή διακόπηκε. Δεν δημιουργήθηκε μερικό CSV.')
                writer.writerow([safe_cell(v) for v in values(row)]);count += 1
            file.flush();os.fsync(file.fileno())
        return count
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise ApplicationError('Δεν αποθηκεύτηκε το CSV. Επιλέξτε νέο όνομα και διαθέσιμο φάκελο.') from None
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
