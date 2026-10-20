from datetime import date
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from test_pdf_classification import add_invoices,record_attempt


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','GUI needs display')
class WorkspaceGUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from myinvoices.database import Database
        from myinvoices.gui import App
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.db=Database(self.directory/'db')
        self.client=self.db.add_client('Συνθετικός πελάτης','000000000')
        add_invoices(self.db,self.client,[100,101,102,103])
        record_attempt(self.db,self.client,100,'challenge');record_attempt(self.db,self.client,101,'network_error')
        self.root=tk.Tk();self.root.tk.call('tk','scaling',96/72);self.root.geometry('1366x768');self.errors=[]
        self.root.report_callback_exception=lambda *e:self.errors.append(e)
        self.app=App(self.root,self.db,today=date(2026,11,1));self.root.geometry('1366x768')
        self.addCleanup(self.root.destroy);self.wait()

    def wait(self,predicate=None):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            self.root.update()
            if predicate() if predicate else not self.app.busy:break
            time.sleep(.01)
        self.assertFalse(self.app.busy);self.assertFalse(self.errors)
        if predicate:self.assertTrue(predicate())

    def test_failure_scope_selection_counts_and_details_are_contextual(self):
        app=self.app;app.document_book.select(app.failure_tree.master);self.root.update()
        self.assertEqual(app.recovery_button.cget('text'),'Ανάκτηση όλων των 2 εκκρεμών')
        app.failure_tree.selection_set('100');self.root.update()
        self.wait(lambda:'Cloudflare' in app.details.text.get('1.0','end'))
        self.assertEqual(app.recovery_button.cget('text'),'Ανάκτηση 1 επιλεγμένων')
        self.assertIn('ολοκληρώστε',app.details.text.get('1.0','end'))
        app.failure_tree.selection_set(('100','101'));self.root.update()
        self.assertEqual(app.selection_text.get(),'2 επιλεγμένα')
        app._clear_table_selection();self.root.update()
        self.assertEqual(app.recovery_button.cget('text'),'Ανάκτηση όλων των 2 εκκρεμών')
        with patch('myinvoices.gui.messagebox.askyesno',return_value=False),patch('myinvoices.gui.RecoveryPDFDownloader') as service:
            app.recovery_button.invoke();service.assert_not_called()

    def test_selected_recovery_submits_exact_marks_and_scope(self):
        from myinvoices.pdf_downloads import PDFDownloader,archive_pdf_bytes
        from myinvoices.browser_recovery import RecoveryPDFDownloader
        import io
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);stream=io.BytesIO();writer.write(stream)
        seen=[]
        class Synthetic(RecoveryPDFDownloader):
            def run(inner,client,root,cancel,progress,dates=None):
                seen.extend(row['mark'] for row in inner._candidates(client.id,dates))
                return dict(saved=0,printed=0,skipped=0,pending=0,cancelled=False,reasons={})
        app=self.app;app.document_book.select(app.failure_tree.master);self.root.update()
        app.failure_tree.selection_set('101');self.root.update()
        app.download_roots[self.client.id]=str(self.directory)
        with patch('myinvoices.gui.RecoveryPDFDownloader',side_effect=lambda db,**kwargs:Synthetic(db,**kwargs)):
            app.recovery_button.invoke();self.wait()
        self.assertEqual(seen,[101]);self.assertEqual(self.db.count(self.client.id,bucket='failure'),2)
        self.assertIn('Ολοκληρώθηκε',app.batch_summary.get())

    def test_combined_dynamic_filters_sort_and_copy_use_active_rows(self):
        app=self.app;app.invoice_search.set('10');app.reason_filter.set('Χρειάζεται παρέμβαση στον browser')
        self.wait(lambda:app.filter_timer is None and not app.busy)
        self.assertEqual(app.document_tree.get_children(),('100',))
        app.document_tree.selection_set('100');self.root.update();app._copy_rows()
        self.assertIn('01/10/2026',self.root.clipboard_get())
        self.assertNotIn('https://',self.root.clipboard_get())
        app.clicked_cell=(app.document_tree,'100','#2');app._copy_cell()
        self.assertEqual(self.root.clipboard_get(),'100')
        app._reset_filters();self.wait(lambda:app.filter_timer is None and not app.busy)
        app._sort_table('mark');self.wait()
        self.assertEqual(app.document_tree.get_children(),('100','101','102','103'))
        self.assertEqual(self.db.count(self.client.id),4)

    def test_settings_save_width_order_destination_and_survive_reopen(self):
        app=self.app;app.document_tree.column('mark',width=185)
        app.download_roots[self.client.id]=str(self.directory)
        app.clicked_cell=(app.document_tree,'100','#2');app._move_column(-1)
        app._save_preferences();self.wait()
        self.assertEqual(app.settings.load()['widths']['mark'],185)
        self.assertEqual(app.settings.load()['order'][0],'mark')
        app.download_roots.clear();app.settings_loaded=False;app.refresh();self.wait()
        self.assertEqual(app.download_roots[self.client.id],str(self.directory))
        self.assertEqual(app.document_tree.column('mark','width'),185)

    def test_client_change_invalidates_previous_details_and_environment_is_explicit(self):
        app=self.app
        other=self.db.add_client('Παραγωγή','000000000','production')
        app.document_tree.selection_set('100');self.root.update()
        app.refresh();self.wait();app.client_tree.selection_set(other.id);app._select();self.wait()
        self.assertIn('Παραγωγή ΑΑΔΕ',app.summary.get());self.assertNotIn('MARK 100',app.details.text.get('1.0','end'))
        self.assertEqual(app.recovery_total,0)
        app.client_tree.selection_set(self.client.id);app._select();self.wait()
        self.assertIn('[ΔΟΚΙΜΑΣΤΙΚΟ ΑΑΔΕ]',app.summary.get())

    def test_main_controls_are_accessible_at_desktop_sizes_and_scaling(self):
        import tkinter as tk
        from myinvoices.gui import App
        self.root.withdraw()
        for width,height in ((1366,768),(1920,1080)):
            for scale in (96/72,120/72,144/72):
                self.root.tk.call('tk','scaling',scale)
                window=tk.Toplevel(self.root)
                try:
                    app=App(window,self.db,today=date(2026,11,1))
                    window.geometry(f'{width}x{height}+0+0')
                    deadline=time.monotonic()+5
                    while app.busy and time.monotonic()<deadline:
                        self.root.update();time.sleep(.01)
                    self.root.update()
                    for control in (app.sync_button,app.pdf_button,app.browser_pdf_button,app.export_button,app.progress,app.details.text):
                        self.assertTrue(control.winfo_ismapped(),(width,height,scale,str(control)))
                        x=control.winfo_rootx()-window.winfo_rootx();y=control.winfo_rooty()-window.winfo_rooty()
                        self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
                        self.assertLessEqual(x+control.winfo_width(),width)
                        self.assertLessEqual(y+control.winfo_height(),height)
                    self.assertGreaterEqual(app.document_tree.winfo_height(),100,(width,height,scale,'table'))
                    self.assertGreaterEqual(app.details.text.winfo_height(),40,(width,height,scale,'details'))
                    self.assertEqual(str(app.document_tree.cget('selectmode')),'extended')
                    self.assertTrue(app.invoice_search_entry.cget('takefocus')!='0')
                    self.assertIn('Αποθηκευμένα',[app.document_book.tab(t,'text') for t in app.document_book.tabs()])
                finally:window.destroy()

    def test_close_persists_settings_without_callbacks_after_destroy(self):
        import tkinter as tk
        from myinvoices.gui import App
        window=tk.Toplevel(self.root);app=App(window,self.db,today=date(2026,11,1))
        deadline=time.monotonic()+5
        while app.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(.01)
        app._close()
        while not app.closed and time.monotonic()<deadline:
            self.root.update();time.sleep(.01)
        self.assertTrue(app.closed);self.assertFalse(self.errors)
        self.assertTrue(app.settings.path.is_file())
