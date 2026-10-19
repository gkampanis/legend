from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from myinvoices.api import Environment
from myinvoices.database import Database
from myinvoices.date_range import DateRange
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.models import ApplicationError, SyncError
from myinvoices.sync import Synchronizer


class PeriodAPI:
    environment = Environment.DEVELOPMENT
    def __init__(self, *pages):
        self.pages = iter(pages)
        self.calls = []
    def fetch(self, mark, continuation=None, **kwargs):
        self.calls.append((mark, continuation, kwargs))
        return next(self.pages)


class DatePeriodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / 'data.sqlite3')
        self.client = self.db.add_client('Synthetic', '000000000')
        self.dates = DateRange.parse('01/10/2026', '31/10/2026')

    def test_validation_and_inclusive_queries(self):
        for start,end in [('31/02/2026',''), ('11/10/2026','10/10/2026'), ('x','')]:
            with self.assertRaises(ApplicationError):
                DateRange.parse(start,end)
        self.assertEqual(DateRange.parse('29/02/2024').start, date(2024,2,29))
        with self.assertRaises(ApplicationError):
            DateRange.parse('01/01/2026').period
        api = PeriodAPI(xml_page([xml_invoice(100), xml_invoice(101).replace('2026-10-01','2026-10-31'), xml_invoice(102).replace('2026-10-01','2026-11-01')]))
        Synchronizer(self.db,api).run(self.client.id)
        self.assertEqual(self.db.count(self.client.id,self.dates), 2)
        self.assertEqual({r['mark'] for r in self.db.invoices(self.client.id, dates=self.dates)}, {100,101})
        self.assertEqual(len(self.db.download_candidates(self.client.id,self.dates)), 2)
        self.assertEqual(self.db.count(self.client.id,DateRange.parse('', '01/10/2026')),1)
        self.assertEqual(len(self.db.invoices(self.client.id,limit=1,offset=1,dates=self.dates)),1)

    def test_period_pages_resume_without_advancing_full_sync(self):
        general_api = PeriodAPI(xml_page([xml_invoice(900)]))
        Synchronizer(self.db,general_api).run(self.client.id)
        general = self.db.checkpoint(self.client.id)
        api = PeriodAPI(xml_page([xml_invoice(100)],('part','row')))
        with self.assertRaises(SyncError):
            Synchronizer(self.db,api,max_pages=1).run(self.client.id,dates=self.dates)
        scoped = self.db.checkpoint(self.client.id,self.dates.period)
        self.assertEqual((scoped.cursor, scoped.highwater, scoped.continuation), (0,100,('part','row')))
        self.assertEqual(self.db.checkpoint(self.client.id),general)
        self.db = Database(self.db.path)
        resume = PeriodAPI(xml_page([xml_invoice(101)]))
        Synchronizer(self.db,resume).run(self.client.id,dates=self.dates)
        self.assertEqual(resume.calls, [(0,('part','row'),dict(date_from=date(2026,10,1),date_to=date(2026,10,31)))])
        self.assertEqual(self.db.checkpoint(self.client.id,self.dates.period).cursor,101)
        self.assertEqual(self.db.checkpoint(self.client.id),general)
        self.assertEqual(self.db.count(self.client.id),3)
        other = DateRange.parse('01/09/2026','30/09/2026')
        self.assertEqual(self.db.checkpoint(self.client.id,other.period).cursor,0)
        self.assertTrue(all(call[2]['date_from'] == self.dates.start for call in api.calls))

    def test_scope_rollback_and_repeated_tokens(self):
        api = PeriodAPI(xml_page([xml_invoice(100)],('same','token')),xml_page([xml_invoice(101)],('same','token')))
        with self.assertRaises(SyncError):
            Synchronizer(self.db,api).run(self.client.id,dates=self.dates)
        self.assertEqual(self.db.count(self.client.id),1)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor,0)
        self.assertEqual(self.db.checkpoint(self.client.id,self.dates.period).highwater,100)
        with self.db.connection() as conn:
            conn.execute("""CREATE TRIGGER reject_scope BEFORE UPDATE ON period_checkpoints
                BEGIN SELECT RAISE(ABORT,'synthetic'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(102)]))).run(self.client.id,dates=self.dates)
        self.assertEqual(self.db.count(self.client.id),1)

    def test_schema3_backup_keeps_documents_and_pdf_attempts(self):
        import hashlib
        Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(100)]))).run(self.client.id)
        source = hashlib.sha256(self.db.invoice(self.client.id,100)['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,100,'challenge',source)
        before = self.db.checkpoint(self.client.id)
        with self.db.connection() as conn:
            conn.execute('DROP TABLE period_checkpoints')
            conn.execute('DROP TABLE period_continuation_history')
            conn.execute('PRAGMA user_version=3')
        upgraded = Database(self.db.path)
        self.assertEqual(upgraded.checkpoint(self.client.id),before)
        self.assertEqual(upgraded.invoices(self.client.id)[0]['pdf_state'],'challenge')
        backup = next(self.root.glob('*schema3-backup*'))
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],3)
        Database(self.db.path)
        self.assertEqual(len(list(self.root.glob('*schema3-backup*'))),1)

    def test_empty_period_does_not_allow_environment_reassignment(self):
        Synchronizer(self.db,PeriodAPI(xml_page())).run(self.client.id,dates=self.dates)
        with self.assertRaises(ApplicationError):
            self.db.configure_client(self.client.id,'Synthetic','production')

    def test_general_continuation_is_unchanged_by_period_query(self):
        with self.assertRaises(SyncError):
            Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(700)],('same','token'))),max_pages=1).run(self.client.id)
        before = self.db.checkpoint(self.client.id)
        with self.assertRaises(SyncError):
            Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(100)],('same','token'))),max_pages=1).run(self.client.id,dates=self.dates)
        self.assertEqual(self.db.checkpoint(self.client.id),before)
        Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(101)]))).run(self.client.id,dates=self.dates)
        self.assertEqual(self.db.checkpoint(self.client.id),before)
        Synchronizer(self.db,PeriodAPI(xml_page([xml_invoice(701)]))).run(self.client.id)
        self.assertEqual(self.db.checkpoint(self.client.id).cursor,701)
        self.assertEqual(self.db.checkpoint(self.client.id,self.dates.period).cursor,101)

    def test_previous_month_handles_year_boundary_and_leap_year(self):
        from myinvoices.date_range import previous_month
        self.assertEqual(previous_month(date(2026,1,15)),DateRange(date(2025,12,1),date(2025,12,31)))
        self.assertEqual(previous_month(date(2024,3,1)),DateRange(date(2024,2,1),date(2024,2,29)))
