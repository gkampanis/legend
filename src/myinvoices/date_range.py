"""Validated inclusive invoice issue-date ranges, independent of GUI widgets."""
from dataclasses import dataclass
from datetime import date, datetime
from .models import ApplicationError


@dataclass(frozen=True)
class DateRange:
    start: date | None = None
    end: date | None = None

    def __post_init__(self):
        if self.start and self.end and self.start > self.end:
            raise ApplicationError('Η ημερομηνία Από πρέπει να είναι πριν ή ίδια με την Έως.')

    @classmethod
    def parse(cls, start='', end=''):
        def parse_one(value):
            if not value.strip():
                return None
            for pattern in ('%d/%m/%Y', '%Y-%m-%d'):
                try:
                    return datetime.strptime(value.strip(), pattern).date()
                except ValueError:
                    pass
            raise ApplicationError('Δώστε ημερομηνίες ΗΗ/ΜΜ/ΕΕΕΕ, π.χ. 01/01/2026.')
        return cls(parse_one(start), parse_one(end))

    def sql(self, column='issue_date'):
        clauses, values = [], []
        for bound, operator in ((self.start, '>='), (self.end, '<=')):
            if bound:
                clauses.append(f'{column}{operator}?')
                values.append(bound.isoformat())
        return ((' AND ' + ' AND '.join(clauses)) if clauses else ''), values

    @property
    def period(self):
        if not self.start and not self.end:
            return None
        if not self.start or not self.end:
            raise ApplicationError('Για άντληση ΑΑΔΕ περιόδου συμπληρώστε και Από και Έως.')
        return self.start.isoformat(), self.end.isoformat()
