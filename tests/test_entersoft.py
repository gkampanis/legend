from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from myinvoices.database import Database
from myinvoices.demo import xml_invoice,xml_page
from myinvoices.xml_parser import parse_response
from myinvoices.models import ApplicationError
from myinvoices.date_range import DateRange
from myinvoices.entersoft.domain import Scope,Mapping,validate_invoice,require_write_access,money
from myinvoices.entersoft.source import Source
from myinvoices.entersoft.store import Store
from myinvoices.entersoft.service import Preparer,Control


def invoice(mark,*,recipient='000000000',number='0000123',lines=None):
    xml=xml_invoice(mark,recipient=recipient).replace(f'<aa>{mark}</aa>',f'<aa>{number}</aa>')
    lines=lines if lines is not None else '<invoiceDetails><lineNumber>1</lineNumber><netValue>100.10</netValue><vatCategory>1</vatCategory><vatAmount>24.02</vatAmount></invoiceDetails>'
    return xml.replace('<invoiceSummary>',lines+'<invoiceSummary>')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.db=Database(self.root/'main.sqlite3');self.client=self.db.add_client('Synthetic','000000000')
        self.db.commit_page(self.db.checkpoint(self.client.id),parse_response(xml_page([invoice(101),invoice(102,number='0000124')])))
        self.scope=Scope(self.client.id,self.client.vat,self.client.environment,'USER-DEFINED-COMPANY')
        self.source=Source(self.db.path,self.client);self.store=Store(self.root/'entersoft'/'agent.sqlite3')
        self.mapping=Mapping('USER_DEFINED_ACCOUNT','USER_VENDOR','USER_EXPENSE')
    def row(self):return self.source.invoice(101)
    def plan(self,row=None):return validate_invoice(row or self.row(),self.scope,self.mapping)


class ValidationTests(Fixture):
    def test_standard_preparation_preserves_number_and_never_enables_writes(self):
        plan=self.plan();self.assertEqual(plan.state,'pending');self.assertFalse(plan.ready_to_write)
        self.assertEqual(plan.invoice['number'],'0000123');self.assertEqual(plan.vat_categories,('1',))
        with self.assertRaises(ApplicationError):require_write_access(stage='D',approved=True)

    def test_no_auto_account_decision(self):
        plan=validate_invoice(self.row(),self.scope)
        self.assertIn('mapping_missing',plan.reasons);self.assertEqual(plan.state,'manual_review')
        with self.assertRaises(ApplicationError):Mapping('')

    def test_decimal_safe_processing_rejects_float_nonfinite_and_wrong_totals(self):
        self.assertEqual(money('0.10')+money('0.20'),Decimal('0.30'))
        for value in (0.1,'NaN','Infinity','0.001',None,-1):
            with self.assertRaises(ValueError):money(value)
        row=self.row();row['total']='124.13'
        self.assertIn('amounts',self.plan(row).reasons)

    def test_recipient_issuer_and_xml_mismatch_are_reviewed(self):
        for name,value in (('recipient_vat','222222222'),('issuer_vat','000000000'),('number','different')):
            row=self.row();row[name]=value
            self.assertIn('identity',self.plan(row).reasons)

    def test_missing_xml_categories_are_not_inferred_from_totals(self):
        row=self.row();row['raw_xml']=xml_invoice(101).replace('<aa>101</aa>','<aa>0000123</aa>')
        self.assertIn('xml_details_missing',self.plan(row).reasons)

    def test_later_classification_change_and_foreign_currency_require_review(self):
        row=self.row();row['classification_update']=1
        self.assertIn('classification_update',self.plan(row).reasons)
        row=self.row();row['raw_xml']=invoice(101).replace('<invoiceHeader>','<invoiceHeader><currency>USD</currency>')
        self.assertIn('special_treatment',self.plan(row).reasons)

    def test_multiple_vat_or_classifications_do_not_get_guessed(self):
        row=self.row();row['raw_xml']=invoice(101).replace('<invoiceDetails>','<invoiceDetails><expensesClassification><classificationType>A</classificationType></expensesClassification><expensesClassification><classificationType>B</classificationType></expensesClassification>')
        self.assertIn('multiple_classifications',self.plan(row).reasons)
        row=self.row();row['raw_xml']=invoice(101).replace('</invoiceDetails>','</invoiceDetails><invoiceDetails><netValue>0</netValue><vatAmount>0</vatAmount><vatCategory>2</vatCategory></invoiceDetails>')
        self.assertIn('multiple_vat',self.plan(row).reasons)

    def test_credit_special_vat_adjustments_and_cancelled_are_reviewed(self):
        for edit in ('credit','category','adjustment','cancelled'):
            row=self.row();row['raw_xml']=invoice(101)
            if edit=='credit':row['invoice_type']='5.1';row['raw_xml']=row['raw_xml'].replace('<invoiceType>1.1','<invoiceType>5.1')
            if edit=='category':row['raw_xml']=row['raw_xml'].replace('<vatCategory>1','<vatCategory>9')
            if edit=='adjustment':row['raw_xml']=row['raw_xml'].replace('<invoiceSummary>','<invoiceSummary><totalFeesAmount>1</totalFeesAmount>')
            if edit=='cancelled':row['cancelled']=1
            self.assertIn('cancelled' if edit=='cancelled' else 'special_treatment',self.plan(row).reasons)


class StoreTests(Fixture):
    def test_scopes_include_client_vat_environment_and_company(self):
        self.store.set_mapping(self.scope,'111111111',self.mapping)
        for other in (replace(self.scope,environment='production'),replace(self.scope,company='OTHER'),replace(self.scope,client_id='other'),replace(self.scope,client_vat='222222222')):
            self.assertIsNone(self.store.mapping(other,'111111111',101))
        reopened=Store(self.store.path)
        self.assertEqual(reopened.mapping(self.scope,'111111111',101),self.mapping)

    def test_invoice_override_precedence_and_removal_are_audited(self):
        self.store.set_mapping(self.scope,'111111111',self.mapping)
        override=Mapping('USER_EXCEPTION')
        self.store.set_mapping(self.scope,'111111111',override,101)
        self.assertEqual(self.store.mapping(self.scope,'111111111',101),override)
        self.assertEqual(self.store.mapping(self.scope,'111111111',102),self.mapping)
        self.store.clear_override(self.scope,101)
        self.assertEqual(self.store.mapping(self.scope,'111111111',101),self.mapping)
        self.assertEqual(len(self.store.audit(self.scope)),3)

    def test_main_database_and_archive_schema_are_unchanged(self):
        before=self.db.path.read_bytes();checkpoint=self.db.checkpoint(self.client.id)
        self.store.prepare(self.scope,self.plan())
        self.assertEqual(self.db.path.read_bytes(),before)
        self.assertEqual(self.db.checkpoint(self.client.id),checkpoint)
        with self.db.connection() as conn:self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],4)
        with self.assertRaises(ApplicationError):Store(self.db.path)
        self.assertEqual(self.db.path.read_bytes(),before)

    def test_completed_history_never_causes_another_submission(self):
        plan=self.plan();self.store.prepare(self.scope,plan)
        with self.store.connection() as conn:conn.execute("UPDATE jobs SET state='completed',ebs_reference='SYNTHETIC-RECEIPT'")
        state,reasons=self.store.prepare(self.scope,plan)
        self.assertEqual((state,reasons),('skipped',('already_completed',)))
        self.assertEqual(self.store.job(self.scope,101)['state'],'completed')
        row=self.row();row['number']='changed'
        state,reasons=self.store.prepare(self.scope,self.plan(row))
        self.assertEqual((state,reasons),('manual_review',('source_changed',)))
        self.assertEqual(self.store.job(self.scope,101)['ebs_reference'],'SYNTHETIC-RECEIPT')

    def test_restart_turns_uncertain_processing_into_locked_review(self):
        self.store.prepare(self.scope,self.plan())
        with self.store.connection() as conn:conn.execute("UPDATE jobs SET state='processing'")
        self.assertEqual(self.store.recover_interrupted(self.scope),1)
        self.assertEqual(self.store.prepare(self.scope,self.plan())[0],'manual_review')
        self.assertEqual(self.store.job(self.scope,101)['review_lock'],1)

    def test_excel_compatible_audit_excludes_urls_xml_and_secrets(self):
        row=self.row();row['raw_xml']=invoice(101).replace('</invoice>','<private>SYNTHETIC-SECRET</private></invoice>')
        self.store.prepare(self.scope,self.plan(row));path=self.root/'audit.csv'
        self.assertEqual(self.store.export_audit(self.scope,path),1)
        data=path.read_bytes();self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        text=data.decode('utf-8-sig');self.assertIn('0000123',text);self.assertNotIn('https://',text);self.assertNotIn('SYNTHETIC-SECRET',text)
        with self.assertRaises(ApplicationError):self.store.export_audit(self.scope,path)

    def test_separate_backup_roundtrip(self):
        self.store.prepare(self.scope,self.plan());path=self.store.backup(self.root)
        copy=Store(path);self.assertEqual(copy.job(self.scope,101)['snapshot'],self.store.job(self.scope,101)['snapshot'])

    def test_real_xlsx_preserves_identity_strings_without_formulas(self):
        import zipfile
        import xml.etree.ElementTree as ET
        self.store.prepare(self.scope,self.plan());path=self.root/'audit.xlsx'
        self.assertEqual(self.store.export_excel(self.scope,path),1)
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            self.assertEqual(root.find(".//s:c[@r='E2']/s:is/s:t",ns).text,'0000123')
            self.assertEqual(root.find(".//s:c[@r='B2']",ns).get('t'),'inlineStr')
            self.assertEqual(root.findall('.//s:f',ns),[])
            self.assertNotIn(b'https://',archive.read('xl/worksheets/sheet1.xml'))
        before=path.read_bytes()
        with self.assertRaises(ApplicationError):self.store.export_excel(self.scope,path)
        self.assertEqual(path.read_bytes(),before)

    def test_atomic_job_and_audit_rollback(self):
        with patch.object(self.store,'_event',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.store.prepare(self.scope,self.plan())
        self.assertIsNone(self.store.job(self.scope,101))


class SourceTests(Fixture):
    def test_source_is_readonly_and_filters_missing_accounts_before_pagination(self):
        self.store.set_mapping(self.scope,'111111111',self.mapping,101)
        before=self.db.path.read_bytes()
        rows,total=self.source.rows(DateRange.parse('01/10/2026','31/10/2026'),'111111111',0,1,missing_only=True,scope=self.scope,mappings_path=self.store.path)
        self.assertEqual((total,[r['mark'] for r in rows]),(1,[102]))
        with self.source.connection() as conn:
            with self.assertRaises(sqlite3.OperationalError):conn.execute('DELETE FROM invoices')
        self.assertEqual(self.db.path.read_bytes(),before)
        self.assertEqual(self.source.rows(DateRange.parse('01/09/2026','30/09/2026'))[1],0)

    def test_duplicate_reference_different_mark_is_manual_review(self):
        with self.db.connection() as conn:
            conn.execute('UPDATE invoices SET number=?,raw_xml=? WHERE mark=102',('0000123',invoice(102)))
        self.assertEqual(self.source.invoice(101)['potential_duplicate'],1)
        result=Preparer(self.source,self.store).run(self.scope,[101,102],Control())
        self.assertEqual(result['counts'],{'manual_review':2})
        for job in self.store.jobs(self.scope).values():self.assertIn('duplicate_reference',json.loads(job['reasons']))

    def test_cross_client_or_changed_environment_is_not_read_as_current_customer(self):
        other=self.db.add_client('Other','222222222')
        with self.assertRaises(ApplicationError):Source(self.db.path,other).invoice(101)
        with self.assertRaises(ApplicationError):Source(self.db.path,replace(self.client,environment='production')).rows()


class RunnerTests(Fixture):
    def test_testmode_is_sequential_idempotent_and_never_completes_ted(self):
        self.store.set_mapping(self.scope,'111111111',self.mapping)
        before=self.db.path.read_bytes();events=[]
        result=Preparer(self.source,self.store).run(self.scope,[101,101,102],Control(),events.append)
        self.assertEqual((result['total'],result['processed'],result['ebs_writes']),(2,2,0))
        self.assertEqual(result['counts'],{'pending':2})
        self.assertEqual(set(self.store.jobs(self.scope)),{101,102})
        self.assertNotIn('completed',result['counts']);self.assertEqual(self.db.path.read_bytes(),before)
        self.assertEqual([e.mark for e in events if e.processed==0],[101])

    def test_runner_refuses_different_client_scope_before_queue_writes(self):
        with self.assertRaises(ApplicationError):Preparer(self.source,self.store).run(replace(self.scope,environment='production'),[101],Control())
        self.assertEqual(self.store.jobs(self.scope),{})

    def test_stop_between_invoices_preserves_first_and_leaves_remaining(self):
        control=Control()
        result=Preparer(self.source,self.store).run(self.scope,[101,102],control,lambda event:control.stop() if event.processed==1 else None)
        self.assertEqual((result['processed'],result['remaining'],result['stopped']),(1,1,True))
        self.assertIsNotNone(self.store.job(self.scope,101));self.assertIsNone(self.store.job(self.scope,102))

    def test_pause_then_resume_observes_condition_without_ui_actions(self):
        control=Control();control.pause();paused=threading.Event();done=threading.Event();results=[]
        def work():
            results.append(Preparer(self.source,self.store).run(self.scope,[101],control,lambda event:paused.set() if event.paused else None));done.set()
        thread=threading.Thread(target=work);thread.start()
        try:
            self.assertTrue(paused.wait(2));self.assertIsNone(self.store.job(self.scope,101))
            control.resume();self.assertTrue(done.wait(2));self.assertEqual(results[0]['processed'],1)
        finally:control.stop();thread.join(3)

    def test_stop_while_paused_and_failed_source_are_persisted_safely(self):
        control=Control();control.pause();control.stop()
        self.assertEqual(Preparer(self.source,self.store).run(self.scope,[101],control)['processed'],0)
        with patch.object(self.source,'invoice',side_effect=ApplicationError('Synthetic')):
            result=Preparer(self.source,self.store).run(self.scope,[101],Control())
        self.assertEqual(result['counts'],{'failed':1});self.assertEqual(self.store.job(self.scope,101)['state'],'failed')


class DesktopTests(unittest.TestCase):
    def test_linux_detection_is_explicitly_unavailable(self):
        from myinvoices.entersoft import desktop
        with patch.object(desktop.sys,'platform','linux'):
            with self.assertRaisesRegex(ApplicationError,'Windows'):desktop.windows()

    def test_uia_inspection_reads_identifiers_but_never_values_or_clicks(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from myinvoices.entersoft import desktop
        class Node:
            def __init__(self,name,kind='Edit',password=False):
                self.element_info=SimpleNamespace(name=name,control_type=kind,automation_id='fixture-control',class_name='Fixture',is_password=password,process_id=999999)
            def children(self):return []
            def click(self):raise AssertionError('No UI mutation')
            def get_value(self):raise AssertionError('No values')
        parent=Node('EBS ERP fixture','Window');parent.children=lambda:[Node('Σχετικά παρ/κά'),Node('SYNTHETIC-SECRET'),Node('password',password=True)]
        class Spec:
            def wrapper_object(self):return parent
        @contextmanager
        def fake():yield SimpleNamespace(window=lambda **_:Spec())
        with patch.object(desktop,'_desktop',fake):
            result=desktop.inspect(desktop.Window(123,999999,'EBS ERP fixture'))
        serialized=json.dumps(result,ensure_ascii=False)
        self.assertIn('Σχετικά παρ/κά',serialized);self.assertNotIn('SYNTHETIC-SECRET',serialized)
        self.assertFalse(result['fields_verified']);self.assertFalse(result['save_verified'])
