from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from myinvoices.database import Database
from myinvoices.presentation import TableQuery, PROVENANCE
from myinvoices.settings import UISettings, COLUMNS
from myinvoices.table_export import export_csv
from myinvoices.models import ApplicationError
from test_pdf_classification import add_invoices, record_attempt


class TableTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=Database(self.root/'db')
        self.client=self.db.add_client('Πελάτης','000000000')
        add_invoices(self.db,self.client,[2,10,100])
        with self.db.connection() as c:
            c.execute("UPDATE invoices SET issue_date='2026-09-30',total='100.2',series='Α',number='0010',issuer_vat='123456789' WHERE mark=10")
            c.execute("UPDATE invoices SET total='9.5',number='0002' WHERE mark=2")
            c.execute("UPDATE invoices SET total='20',number='0100' WHERE mark=100")

    def marks(self,table,**kwargs):return [r['mark'] for r in self.db.invoices(self.client.id,table=table,**kwargs)]

    def test_numeric_and_date_sorting_precede_pagination(self):
        self.assertEqual(self.marks(TableQuery(sort='total',descending=False)),[2,100,10])
        self.assertEqual(self.marks(TableQuery(sort='mark',descending=False),limit=2),[2,10])
        self.assertEqual(self.marks(TableQuery(sort='date',descending=False))[0],10)

    def test_combined_filters_search_and_no_raw_mutation(self):
        record_attempt(self.db,self.client,10,'challenge')
        record_attempt(self.db,self.client,100,'printed_unverified')
        query=TableQuery(search='0010',supplier='123456789',reason='ACCESS_MANUAL',status='failed')
        self.assertEqual(self.marks(query),[10]);self.assertEqual(self.db.count(self.client.id,table=query),1)
        self.assertEqual(self.db.recovery_count(self.client.id,table=query),1)
        self.assertEqual(self.marks(TableQuery(search='Α/0010')),[10])
        self.assertEqual(self.marks(TableQuery(search='123456789')),[10])
        self.assertEqual(self.marks(TableQuery(provenance='printed_unverified')),[100])
        self.assertEqual(self.marks(TableQuery(search="%' OR 1=1 --")),[])
        self.assertEqual(self.db.count(self.client.id),3)
        self.assertEqual(self.db.invoice(self.client.id,10)['total'],'100.2')

    def test_no_url_group_and_client_environment_isolation(self):
        add_invoices(self.db,self.client,[200],url=False)
        self.assertEqual(self.marks(TableQuery(reason='NO_URL')),[200])
        other=self.db.add_client('Ίδιο ΑΦΜ παραγωγή','000000000','production')
        add_invoices(self.db,other,[10])
        self.assertEqual(self.db.count(other.id),1)
        self.assertEqual(self.db.recovery_count(other.id),0)

    def test_csv_has_bom_greek_quoting_no_secrets_and_no_overwrite(self):
        with self.db.connection() as c:
            c.execute("UPDATE invoices SET series=?,download_url=? WHERE mark=10",('=HYPERLINK("x");\nδοκιμή','https://provider.example/private?token=SYNTHETIC-SECRET'))
        path=self.root/'out.csv'
        self.assertEqual(export_csv(path,self.db.export_rows(self.client.id,table=TableQuery(search='0010'))),1)
        data=path.read_bytes();self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        text=data.decode('utf-8-sig');self.assertIn('Ημερομηνία',text);self.assertIn('δοκιμή',text)
        self.assertIn("'=HYPERLINK",text);self.assertNotIn('SYNTHETIC-SECRET',text)
        import csv
        with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f,delimiter=';'))
        self.assertEqual(len(rows),2);self.assertEqual(len(rows[1]),8)
        with self.assertRaises(ApplicationError):export_csv(path,[])
        self.assertEqual(path.read_bytes(),data)

    def test_cancelled_export_removes_partial_output(self):
        cancel=threading.Event();cancel.set();path=self.root/'cancel.csv'
        with self.assertRaises(ApplicationError):export_csv(path,self.db.export_rows(self.client.id),cancel)
        self.assertFalse(path.exists())

    def test_many_records_queries_return_bounded_pages(self):
        # Bulk synthetic insertion, no network or private data.
        with self.db.connection() as c:
            row=dict(c.execute('SELECT * FROM invoices LIMIT 1').fetchone())
            columns=list(row);sql='INSERT INTO invoices('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
            c.execute('BEGIN')
            for mark in range(1000,11000):
                row['mark']=mark;row['total']=str(mark%300)
                c.execute(sql,[row[k] for k in columns])
            c.commit()
        before=time.perf_counter()
        rows=self.db.invoices(self.client.id,limit=200,table=TableQuery(search='10',sort='total',descending=False))
        elapsed=time.perf_counter()-before
        self.assertLessEqual(len(rows),200)
        self.assertEqual([float(r['total']) for r in rows],sorted(float(r['total']) for r in rows))
        print(f'\nSynthetic table 10,003 rows, filtered/sorted page: {elapsed:.4f}s')


class SettingsTests(unittest.TestCase):
    def test_only_ui_preferences_survive_and_invalid_paths_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);settings=UISettings(folder/'ui.json');client='00000000-0000-0000-0000-000000000001'
            settings.save(dict(widths={'mark':180,'secret':150,'total':-10},order=list(reversed(COLUMNS)),
                destinations={client:str(folder),'bad-id':str(folder)},api_key='SYNTHETIC-SECRET'))
            result=settings.load()
            self.assertEqual(result['widths'],{'mark':180});self.assertEqual(result['order'],list(reversed(COLUMNS)))
            self.assertEqual(result['destinations'],{client:str(folder)})
            self.assertNotIn('SYNTHETIC-SECRET',settings.path.read_text())
            settings.path.write_text('{broken');self.assertEqual(settings.load(),{})

    def test_missing_destination_falls_back_without_recreating_it(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'gone';client='00000000-0000-0000-0000-000000000001'
            self.assertEqual(UISettings.clean(dict(destinations={client:str(path)}))['destinations'],{})
            self.assertFalse(path.exists())
