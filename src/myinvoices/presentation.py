"""Typed, storage-compatible presentation rules. Never implies PDF verification."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum


class ReasonGroup(str, Enum):
    NETWORK_RETRYABLE = 'NETWORK_RETRYABLE'
    ACCESS_MANUAL = 'ACCESS_MANUAL'
    NO_URL = 'NO_URL'
    UNSUPPORTED_VIEW = 'UNSUPPORTED_VIEW'
    MISSING_LOCAL_FILE = 'MISSING_LOCAL_FILE'
    OTHER = 'OTHER'


REASONS = {
    ReasonGroup.NETWORK_RETRYABLE: ('Προσωρινό πρόβλημα σύνδεσης', 'Δοκιμάστε ξανά αργότερα με Λήψη PDF.'),
    ReasonGroup.ACCESS_MANUAL: ('Χρειάζεται παρέμβαση στον browser', 'Επιλέξτε Ανάκτηση με browser και ολοκληρώστε τυχόν έλεγχο πρόσβασης.'),
    ReasonGroup.NO_URL: ('Χωρίς σύνδεσμο από ΑΑΔΕ', 'Η ΑΑΔΕ δεν επέστρεψε σύνδεσμο. Μπορείτε να εισάγετε το σωστό PDF χειροκίνητα.'),
    ReasonGroup.UNSUPPORTED_VIEW: ('Μη υποστηριζόμενη προβολή', 'Δοκιμάστε ανάκτηση με browser ή ανοίξτε τον σύνδεσμο και εισάγετε το PDF.'),
    ReasonGroup.MISSING_LOCAL_FILE: ('Το τοπικό αρχείο λείπει ή άλλαξε', 'Πατήστε Λήψη PDF για νέα αποθήκευση. Το αλλαγμένο αρχείο διατηρείται.'),
    ReasonGroup.OTHER: ('Άλλη εκκρεμότητα', 'Δείτε την αιτία στις λεπτομέρειες. Δοκιμάστε ξανά ή αντιγράψτε το μήνυμα για υποστήριξη.'),
}
PROVENANCE = {'saved_unverified': 'Αρχικό PDF', 'printed_unverified': 'Εκτύπωση σε PDF',
              'imported_unverified': 'Εισαγωγή PDF'}


def reason_group(state, has_url=True):
    if state in PROVENANCE:
        return ''
    if not has_url:
        return ReasonGroup.NO_URL.value
    if state in {'network_error','browser_timeout','print_timeout','recovery_timeout','http_408','http_429'} or (state or '').startswith('http_5'):
        return ReasonGroup.NETWORK_RETRYABLE.value
    if state in {'challenge','http_401','http_403','recovery_challenge','browser_confirmation','browser_closed'}:
        return ReasonGroup.ACCESS_MANUAL.value
    if state in {'artifact_missing','artifact_changed'}:
        return ReasonGroup.MISSING_LOCAL_FILE.value
    if state in {'html_page','browser_required','descriptor_limit','invalid_descriptor','ambiguous_document',
                 'print_not_invoice','print_viewer_unsupported','print_failed','invalid_pdf','recovery_pdf_mismatch'}:
        return ReasonGroup.UNSUPPORTED_VIEW.value
    return ReasonGroup.OTHER.value


def guidance(row):
    group = reason_group(row.get('pdf_state'), bool(row.get('download_url')))
    if not group:
        return 'Αποθηκευμένο αρχείο', 'Άνοιγμα PDF. Η αποθήκευση δεν αποτελεί ανεξάρτητη επαλήθευση περιεχομένου.'
    if not row.get('pdf_attempted') and row.get('download_url'):
        return 'Δεν έχει δοκιμαστεί', 'Πατήστε Λήψη PDF.'
    return REASONS[ReasonGroup(group)]


def display_date(value):
    try:
        return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (ValueError, TypeError):
        return value or '—'


def display_amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return '—'
        return f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.') + ' €'
    except (InvalidOperation, TypeError, ValueError):
        return '—'


@dataclass(frozen=True)
class TableQuery:
    search: str = ''
    supplier: str = ''
    reason: str = ''
    provenance: str = ''
    status: str = ''
    sort: str = 'date'
    descending: bool = True

    def conditions(self):
        clauses, values = [], []
        def like(value):
            return '%' + value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        if self.search.strip():
            clauses.append("(CAST(mark AS TEXT) LIKE ? ESCAPE '\\' OR series LIKE ? ESCAPE '\\' OR number LIKE ? ESCAPE '\\' OR issuer_vat LIKE ? ESCAPE '\\' OR (COALESCE(series,'')||'/'||COALESCE(number,'')) LIKE ? ESCAPE '\\')")
            values.extend([like(self.search.strip())] * 5)
        if self.supplier.strip():
            clauses.append("issuer_vat LIKE ? ESCAPE '\\'")
            values.append(like(self.supplier.strip()))
        if self.reason in ReasonGroup._value2member_map_:
            clauses.append('reason_group(pdf_state,download_url IS NOT NULL)=?')
            values.append(self.reason)
        if self.provenance in PROVENANCE:
            clauses.append('pdf_state=?'); values.append(self.provenance)
        status = {'saved':'pdf_saved=1','failed':'pdf_attempted=1 AND pdf_saved=0 AND cancelled=0',
                  'new':'pdf_attempted=0 AND cancelled=0','cancelled':'cancelled=1'}
        if self.status in status:
            clauses.append(status[self.status])
        return clauses, values

    def order(self):
        columns = {'date':'issue_date','mark':'mark','supplier':'issuer_vat','identity':"COALESCE(series,'')||COALESCE(number,'')",
                   'total':'CAST(total AS NUMERIC)','method':'pdf_state','status':'pdf_saved','reason':'reason_group(pdf_state,download_url IS NOT NULL)'}
        return columns.get(self.sort,'issue_date') + (' DESC' if self.descending else ' ASC') + ',mark DESC'
