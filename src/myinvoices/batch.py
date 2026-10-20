"""Small immutable progress records, independent from Tk and transports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchProgress:
    stage: str
    completed: int
    total: int | None
    mark: int | None = None
    saved: int = 0
    printed: int = 0
    failed: int = 0
    skipped: int = 0

    def text(self):
        count = f'{self.completed} / {self.total}' if self.total is not None else f'{self.completed} · σύνολο άγνωστο'
        item = f' · MARK {self.mark}' if self.mark is not None else ''
        return (f'{self.stage} · {count}{item} · Αποθηκευμένα: {self.saved} '
                f'(εκτύπωση: {self.printed}) · Εκκρεμή: {self.failed} · Ήδη αποθηκευμένα: {self.skipped}')


def save_batch(directory,client_id,operation,result):
    """Append fixed counters only; no URLs, provider content or exception text."""
    import json
    from datetime import datetime,timezone
    from pathlib import Path
    record=dict(client_id=client_id,operation=operation,at=datetime.now(timezone.utc).isoformat())
    for key in ('saved','printed','skipped','pending','cancelled','pages','records'):
        value=result.get(key)
        if isinstance(value,(int,bool)) and value>=0:record[key]=value
    try:
        with (Path(directory)/'batches.jsonl').open('a',encoding='utf-8') as file:
            file.write(json.dumps(record)+'\n')
    except OSError:
        return None
    return record


def last_batch(directory,client_id):
    import json
    from pathlib import Path
    found=None
    try:
        with (Path(directory)/'batches.jsonl').open('rb') as file:
            file.seek(0,2);size=file.tell();file.seek(max(0,size-1024*1024))
            lines=file.read().decode('utf-8',errors='replace').splitlines()
            for line in lines:
                try:
                    record=json.loads(line)
                    if (record.get('client_id')==client_id and record.get('operation') in ('sync','download','recovery')
                            and isinstance(record.get('at'),str)):
                        found=record
                except (ValueError,AttributeError):continue
    except OSError:pass
    return found


def batch_description(record):
    if not record:return 'Δεν υπάρχει καταγεγραμμένη παρτίδα για αυτόν τον πελάτη.'
    if record['operation']=='sync':
        counts=f"Σελίδες: {record.get('pages',0)} · Εγγραφές: {record.get('records',0)}"
    else:
        counts=f"Νέα PDF: {record.get('saved',0)} · Εκτύπωση: {record.get('printed',0)} · Εκκρεμή: {record.get('pending',0)} · Ήδη αποθηκευμένα: {record.get('skipped',0)}"
    return f"Τελευταία παρτίδα · {'Διακόπηκε' if record.get('cancelled') else 'Ολοκληρώθηκε'} · {counts}"
