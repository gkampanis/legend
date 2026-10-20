"""Strict, decimal-safe preparation without accounting decisions or UI writes."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json

from ..models import ApplicationError
from ..xml_parser import parse_xml,child,text,local_name


class State(str,Enum):
    PENDING='pending'
    PROCESSING='processing'
    COMPLETED='completed'
    FAILED='failed'
    MANUAL_REVIEW='manual_review'
    SKIPPED='skipped'


STATE_LABELS={State.PENDING.value:'Σε αναμονή — προετοιμασμένο',State.PROCESSING.value:'Έλεγχος προετοιμασίας',
    State.COMPLETED.value:'Ολοκληρωμένο (ιστορικό)',State.FAILED.value:'Αποτυχία προετοιμασίας',
    State.MANUAL_REVIEW.value:'Χειροκίνητος έλεγχος',State.SKIPPED.value:'Παραλείφθηκε'}
REASONS={
    'mapping_missing':'Λείπει λογαριασμός εξόδων που θα ορίσει ο χρήστης.',
    'identity':'Ασυμφωνία ή έλλειψη στοιχείων εκδότη/λήπτη/MARK στο αποθηκευμένο XML.',
    'cancelled':'Το παραστατικό έχει ακυρωθεί.',
    'invoice_reference':'Λείπει ή δεν είναι έγκυρη η αρχική σειρά/αρίθμηση/ημερομηνία.',
    'amounts':'Τα χρηματικά ποσά λείπουν, είναι μη έγκυρα ή δεν συμφωνούν.',
    'xml_details_missing':'Δεν υπάρχουν πλήρεις γραμμές ΦΠΑ στο αποθηκευμένο XML.',
    'multiple_vat':'Υπάρχουν πολλαπλές κατηγορίες ΦΠΑ.',
    'multiple_classifications':'Υπάρχουν πολλαπλοί χαρακτηρισμοί εξόδων.',
    'classification_update':'Υπάρχει χωριστή μεταβολή χαρακτηρισμού εξόδων που χρειάζεται έλεγχο.',
    'special_treatment':'Πιστωτικό, ειδικός ΦΠΑ/φόροι ή μη υποστηριζόμενη δομή: απαιτείται ρητός κανόνας.',
    'external_duplicate_unchecked':'Δεν έχει επιβεβαιωθεί έλεγχος διπλοτύπων στο EBS.',
    'duplicate_reference':'Υπάρχει πιθανό διπλότυπο με ίδιο προμηθευτή/σειρά/αριθμό/ημερομηνία και διαφορετικό MARK.',
    'already_completed':'Υπάρχει ολοκληρωμένη εγγραφή στο τοπικό ιστορικό· δεν επανυποβάλλεται.',
    'interrupted':'Προηγούμενος έλεγχος διακόπηκε. Απαιτείται χειροκίνητη εξέταση.',
    'source_changed':'Τα στοιχεία άλλαξαν μετά την προηγούμενη καταγραφή. Απαιτείται έλεγχος.',
    'internal_error':'Ο έλεγχος προετοιμασίας απέτυχε. Δεν έγινε εγγραφή στο EBS.',
}
GATES=('Επιβεβαίωση εταιρείας EBS','Μοναδική αναγνώριση προμηθευτή στο EBS','Έλεγχος διπλοτύπου στο EBS',
       'Επιβεβαίωση πεδίου αρχικής αναφοράς','Επιβεβαίωση controls/ΦΠΑ/γραμμής δαπάνης','Επιβεβαίωση αποτελέσματος Save')


@dataclass(frozen=True)
class Scope:
    client_id:str
    client_vat:str
    environment:str
    company:str
    def __post_init__(self):
        if self.environment not in ('production','development') or len(self.client_vat)!=9 or not self.client_vat.isascii() or not self.client_vat.isdigit():
            raise ApplicationError('Μη έγκυρο πλαίσιο πελάτη.')
        object.__setattr__(self,'company',self.company.strip())
        if not self.company.strip() or len(self.company)>100 or any(ord(c)<32 for c in self.company):
            raise ApplicationError('Ορίστε αναγνωριστικό/όνομα εταιρείας EBS για χωριστή τοπική προετοιμασία.')
    @property
    def key(self):
        return hashlib.sha256(json.dumps([self.client_id,self.client_vat,self.environment,self.company],ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Mapping:
    account:str
    supplier_code:str=''
    expense_item:str=''
    def __post_init__(self):
        for value in (self.account,self.supplier_code,self.expense_item):
            if not isinstance(value,str) or len(value)>100 or any(ord(c)<32 for c in value):
                raise ApplicationError('Μη έγκυρος λογαριασμός ή κωδικός Entersoft.')
        if not self.account.strip():raise ApplicationError('Τον λογαριασμό εξόδων πρέπει να τον ορίσει ο χρήστης.')


@dataclass(frozen=True)
class Preparation:
    invoice:dict
    mapping:Mapping|None
    reasons:tuple[str,...]
    vat_categories:tuple[str,...]
    @property
    def state(self):return State.MANUAL_REVIEW.value if self.reasons else State.PENDING.value
    @property
    def ready_to_write(self):return False
    @property
    def fingerprint(self):
        data={k:self.invoice.get(k) for k in ('mark','issuer_vat','recipient_vat','issue_date','invoice_type','series','number','net','vat','total')}
        data['vat_categories']=self.vat_categories
        # Include the bounded source XML digest, never the XML/URLs themselves.
        data['xml_digest']=hashlib.sha256((self.invoice.get('raw_xml') or '').encode()).hexdigest()
        return hashlib.sha256(json.dumps(data,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def money(value):
    if isinstance(value,(float,bool)) or value is None:raise ValueError()
    try:number=Decimal(str(value))
    except InvalidOperation:raise ValueError() from None
    if not number.is_finite() or number<0 or number>Decimal('999999999999.99') or number!=number.quantize(Decimal('.01')):raise ValueError()
    return number


def validate_invoice(row,scope,mapping=None):
    problems=[];categories=set();classifications=set()
    def issue(code):
        if code not in problems:problems.append(code)
    if not mapping:issue('mapping_missing')
    if row.get('cancelled'):issue('cancelled')
    if row.get('classification_update'):issue('classification_update')
    if row.get('potential_duplicate'):issue('duplicate_reference')
    if (row.get('recipient_vat')!=scope.client_vat or not str(row.get('issuer_vat') or '').isascii()
            or len(str(row.get('issuer_vat') or ''))!=9 or not str(row.get('issuer_vat') or '').isdigit()
            or row.get('issuer_vat')==scope.client_vat):issue('identity')
    try:
        date.fromisoformat(row['issue_date'])
        if not isinstance(row['number'],str) or not row['number'] or len(row['number'])>100 or any(ord(c)<32 for c in row['number']):raise ValueError()
        if row.get('series') is not None and (len(row['series'])>100 or any(ord(c)<32 for c in row['series'])):raise ValueError()
    except (ValueError,TypeError,KeyError):issue('invoice_reference')
    try:
        net,vat,total=(money(row.get(k)) for k in ('net','vat','total'))
        if net+vat!=total:issue('amounts')
    except ValueError:issue('amounts')
    if row.get('invoice_type') not in ('1.1','2.1'):issue('special_treatment')
    try:
        root=parse_xml((row.get('raw_xml') or '').encode())
        if local_name(root.tag)!='invoice':issue('identity')
        if (text(root,'mark')!=str(row.get('mark')) or text(child(root,'issuer'),'vatNumber')!=row.get('issuer_vat')
            or text(child(root,'counterpart'),'vatNumber')!=scope.client_vat):issue('identity')
        header=child(root,'invoiceHeader')
        if text(header,'currency') not in (None,'EUR') or text(child(root,'issuer'),'country') not in (None,'GR') or child(root,'taxesTotals') is not None:
            issue('special_treatment')
        if any((text(header,k) or '')!=(str(row.get(field) or '')) for k,field in
               (('series','series'),('aa','number'),('issueDate','issue_date'),('invoiceType','invoice_type'))):issue('identity')
        summary=child(root,'invoiceSummary')
        for name,field in [('totalNetValue','net'),('totalVatAmount','vat'),('totalGrossValue','total')]:
            if money(text(summary,name))!=money(row.get(field)):issue('amounts')
        for name in ('totalWithheldAmount','totalFeesAmount','totalStampDutyAmount','totalOtherTaxesAmount','totalDeductionsAmount'):
            if text(summary,name) is not None and money(text(summary,name))!=0:issue('special_treatment')
        lines=[e for e in root if local_name(e.tag)=='invoiceDetails']
        if not lines:issue('xml_details_missing')
        line_net=line_vat=Decimal('0')
        for line in lines:
            category=text(line,'vatCategory')
            if category is None:issue('xml_details_missing')
            else:categories.add(category)
            # Only ordinary categories are prepared; there is no guessed rate/account translation.
            if category not in ('1','2','3') or text(line,'vatExemptionCategory') or text(line,'invoiceDetailType'):
                issue('special_treatment')
            line_net+=money(text(line,'netValue'));line_vat+=money(text(line,'vatAmount'))
            for node in line.iter():
                if local_name(node.tag)=='expensesClassification':
                    classifications.add((text(node,'classificationType') or '',text(node,'classificationCategory') or ''))
            for name in ('withheldAmount','feesAmount','stampDutyAmount','otherTaxesAmount','deductionsAmount'):
                if text(line,name) is not None and money(text(line,name))!=0:issue('special_treatment')
        if len(categories)>1:issue('multiple_vat')
        # Summary classifications are also checked; no account inferred from them.
        if summary is not None:
            for node in summary:
                if local_name(node.tag)=='expensesClassification':
                    classifications.add((text(node,'classificationType') or '',text(node,'classificationCategory') or ''))
        if len(classifications)>1:issue('multiple_classifications')
        if lines and (line_net!=money(row.get('net')) or line_vat!=money(row.get('vat'))):issue('amounts')
    except (ApplicationError,ValueError,TypeError,KeyError,AttributeError):issue('xml_details_missing')
    return Preparation(dict(row),mapping,tuple(problems),tuple(sorted(categories)))


def require_write_access(*_args,**_kwargs):
    raise ApplicationError('Στάδιο Α: οι εγγραφές και η αποθήκευση στο Entersoft δεν έχουν ενεργοποιηθεί.')
