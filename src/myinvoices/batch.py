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
