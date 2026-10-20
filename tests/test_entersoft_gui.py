from datetime import date
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from test_entersoft import invoice
from myinvoices.database import Database
from myinvoices.demo import xml_page
from myinvoices.xml_parser import parse_response


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','Tk needs display')
class ExtensionGUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from myinvoices.gui import App
        from myinvoices.entersoft.ui import open_extension
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.directory=Path(self.temp.name)
        self.db=Database(self.directory/'main.sqlite3');self.client=self.db.add_client('Synthetic','000000000')
        self.db.commit_page(self.db.checkpoint(self.client.id),parse_response(xml_page([invoice(101),invoice(102,number='0000124')])))
        self.root=tk.Tk();self.errors=[];self.root.report_callback_exception=lambda *e:self.errors.append(e)
        self.addCleanup(self.root.destroy)
        self.app=App(self.root,self.db,today=date(2026,11,1));self.wait(lambda:not self.app.busy)
        self.before=self.db.path.read_bytes()
        self.extension=open_extension(self.app);self.wait(lambda:not self.extension.busy)
        self.extension.company.set('USER-DEFINED-COMPANY');self.extension.load(reset=True);self.wait(lambda:not self.extension.busy)
    def wait(self,predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            self.root.update()
            if predicate():break
            time.sleep(.01)
        self.root.update();self.assertTrue(predicate());self.assertFalse(self.errors)

    def test_extension_is_optional_and_does_not_change_normal_workflow(self):
        from myinvoices.entersoft.ui import open_extension
        self.assertIs(open_extension(self.app),self.extension)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.pdf_button.cget('text'),'2. Λήψη PDF')
        self.assertEqual(self.app.browser_pdf_button.cget('text'),'Άνοιγμα συνδέσμου')
        self.assertEqual(self.db.path.read_bytes(),self.before)
        self.assertTrue(self.extension.test_mode.get())
        self.assertEqual(self.extension.tree.get_children(),('102','101'))

    def test_mapping_override_missing_filter_and_testmode_run(self):
        ext=self.extension;ext.tree.selection_set('101');self.root.update()
        ext.account.set('USER_ACCOUNT');ext.supplier_code.set('USER_VENDOR');ext.save_supplier();self.wait(lambda:not ext.busy)
        self.assertEqual(ext.tree.set('102','account'),'USER_ACCOUNT')
        ext.account.set('USER_OVERRIDE');ext.save_override();self.wait(lambda:not ext.busy)
        self.assertEqual(ext.tree.set('101','account'),'USER_OVERRIDE')
        ext.missing.set(True);ext.load(reset=True);self.wait(lambda:not ext.busy)
        self.assertEqual(ext.total,0)
        ext.missing.set(False);ext.load(reset=True);self.wait(lambda:not ext.busy)
        ext.tree.selection_set(('101','102'));self.root.update()
        with patch('myinvoices.gui.RequestDocsAPI',side_effect=AssertionError('No API in extension')),patch('myinvoices.gui.AutomaticPDFDownloader',side_effect=AssertionError('No PDF in extension')),patch('myinvoices.entersoft.desktop.windows',side_effect=AssertionError('No desktop action in preparation')):
            ext.start();self.wait(lambda:not ext.busy)
        self.assertEqual([job['state'] for job in ext.store.jobs(ext.scope).values()],['pending','pending'])
        self.assertIn('Νέες εγγραφές EBS: 0',ext.status.get())
        self.assertEqual(self.db.path.read_bytes(),self.before)
        self.assertFalse(list(self.directory.rglob('*.pdf')))

    def test_missing_account_is_manual_review_and_details_show_exact_reference(self):
        ext=self.extension;ext.tree.selection_set('101');self.root.update();ext.start();self.wait(lambda:not ext.busy)
        self.assertEqual(ext.store.job(ext.scope,101)['state'],'manual_review')
        self.assertIn('Λείπει λογαριασμός',ext.details.get('1.0','end'))
        self.assertIn('0000123',ext.details.get('1.0','end'))
        self.assertIn('Κατηγορία ΦΠΑ XML: 1',ext.details.get('1.0','end'))

    def test_readonly_probe_reports_linux_limit_and_never_changes_source(self):
        ext=self.extension
        with patch('myinvoices.entersoft.desktop.sys.platform','linux'):
            ext.detect();self.wait(lambda:not ext.busy)
        self.assertIn('Windows',ext.status.get());self.assertEqual(ext.detected,[])
        self.assertEqual(self.db.path.read_bytes(),self.before)

    def test_close_while_paused_signals_stop_and_cancels_tk_timer(self):
        ext=self.extension;ext.control.pause();ext.close();self.root.update()
        self.assertTrue(ext.closed);self.assertTrue(ext.control.stopped)
        self.assertFalse(self.errors)

    def test_extension_controls_and_real_viewport_fit_1366_by_768(self):
        ext=self.extension;ext.window.geometry('1366x768+0+0');self.root.update()
        for widget in (ext.start_button,ext.stop_button,ext.pause_button,ext.resume_button,ext.tree,ext.details):
            self.assertTrue(widget.winfo_ismapped(),str(widget))
            x=widget.winfo_rootx()-ext.window.winfo_rootx();y=widget.winfo_rooty()-ext.window.winfo_rooty()
            self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+widget.winfo_width(),1366)
            self.assertLessEqual(y+widget.winfo_height(),768)
        self.assertGreaterEqual(ext.tree.winfo_height(),100)
