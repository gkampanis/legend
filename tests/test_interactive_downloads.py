import threading
import time
import unittest
from unittest.mock import Mock
import test_pdf_downloads as helpers
from myinvoices.interactive_downloads import InteractivePDFDownloader
from myinvoices.pdf_downloads import Pending


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)
        self.downloads=self.root/'Downloads'
        self.downloads.mkdir()
        for row in self.db.download_candidates(self.client.id):
            import hashlib
            self.db.record_pdf(self.client.id,row['mark'],'challenge',hashlib.sha256(row['download_url'].encode()).hexdigest())

    def service(self, browser, confirm=None, timeout=2):
        return InteractivePDFDownloader(self.db,self.downloads,browser=browser,confirm=confirm or (lambda row,path:True),
                                        timeout=timeout,stable_seconds=0.02,poll_seconds=0.01)

    def test_real_browser_boundary_and_completed_new_files_only(self):
        old=self.downloads/'old.pdf'
        old.write_bytes(self.pdf)
        calls=[]
        def browser(url,new):
            calls.append(url)
            (self.downloads/f'invoice-{len(calls)}.PDF').write_bytes(self.pdf)
            return True
        confirm=Mock(return_value=True)
        result=self.service(browser,confirm).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual((result['saved'],result['pending']),(2,0))
        self.assertEqual(len(calls),2)
        self.assertEqual(confirm.call_count,2)
        self.assertEqual(old.read_bytes(),self.pdf)
        self.assertEqual(len(list((self.root/'archive').rglob('*.pdf'))),2)
        repeat=self.service(browser).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(repeat['saved'],0)
        self.assertEqual(len(calls),2)

    def test_pending_queue_excludes_unattempted_other_clients_and_old_sources(self):
        other=self.db.add_client('Other','111111111')
        self.assertEqual(self.db.pending_candidates(other.id),[])
        marks=[r['mark'] for r in self.db.pending_candidates(self.client.id)]
        with self.db.connection() as conn:
            conn.execute('UPDATE invoices SET download_url=? WHERE mark=?',('https://provider.example/new',marks[0]))
        self.assertEqual(len(self.db.pending_candidates(self.client.id)),1)
        self.db.record_pdf(self.client.id,marks[1],'saved_unverified','synthetic','/synthetic',None,0)
        # An unrelated source must not suppress the current source's pending work.
        self.assertEqual(len(self.db.pending_candidates(self.client.id)),1)
        import hashlib
        source=hashlib.sha256(self.db.invoice(self.client.id,marks[1])['download_url'].encode()).hexdigest()
        self.db.record_pdf(self.client.id,marks[1],'saved_unverified',source,'/synthetic',None,0)
        self.assertEqual(self.db.pending_candidates(self.client.id),[])

    def test_unrelated_or_multiple_downloads_need_confirmation(self):
        def browser(*args,**kwargs):
            (self.downloads/'a.pdf').write_bytes(self.pdf)
            (self.downloads/'b.pdf').write_bytes(self.pdf)
            return True
        confirm=Mock(return_value=True)
        result=self.service(browser,confirm,timeout=0.1).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],0)
        confirm.assert_not_called()
        self.assertFalse(list((self.root/'archive').rglob('*.pdf')))

    def test_confirmation_rejection_does_not_archive(self):
        counter=[0]
        def browser(*args,**kwargs):
            counter[0]+=1
            (self.downloads/f'{counter[0]}.pdf').write_bytes(self.pdf)
            return True
        result=self.service(browser,lambda row,path:False).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(dict(result['reasons']),{'browser_confirmation':2})
        self.assertFalse(list((self.root/'archive').rglob('*.pdf')))

    def test_partial_download_and_cancel_are_not_archived(self):
        cancel=threading.Event()
        def browser(*args,**kwargs):
            (self.downloads/'invoice.pdf.crdownload').write_bytes(self.pdf)
            cancel.set()
            return True
        result=self.service(browser).run(self.client,self.root/'archive',cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(list((self.root/'archive').rglob('*.pdf')))
