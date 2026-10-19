import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from myinvoices.models import ApplicationError
from myinvoices.pdf_preview import PDFPreview, render_pdf_page


def make_pdf(path):
    writer=PdfWriter()
    for width,height,color in ((101,200,'1 0 0'),(200,101,'0 0 1')):
        page=writer.add_blank_page(width=width,height=height)
        stream=DecodedStreamObject()
        stream.set_data(f'{color} rg 0 0 {width} {height} re f\n'.encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)


class PDFRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'synthetic.pdf'
        make_pdf(self.path)

    def test_actual_render_has_correct_rgb_pages_and_preserves_original(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        first=render_pdf_page(self.path)
        second=render_pdf_page(self.path,1,1.5)
        self.assertEqual((first.count,first.index,second.index),(2,0,1))
        self.assertEqual((first.width,first.height),(127,250))
        self.assertEqual((second.width,second.height),(375,190))
        self.assertEqual(first.ppm.split(b'\n',3)[3][:3],b'\xff\x00\x00')
        self.assertEqual(second.ppm.split(b'\n',3)[3][:3],b'\x00\x00\xff')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)

    def test_missing_invalid_encrypted_and_invalid_page_are_safe_errors(self):
        for path,index in ((self.path.with_name('missing.pdf'),0),(self.path,2)):
            with self.assertRaises(ApplicationError):
                render_pdf_page(path,index)
        self.path.write_bytes(b'private non PDF content')
        with self.assertRaises(ApplicationError) as caught:
            render_pdf_page(self.path)
        self.assertNotIn('private',str(caught.exception))
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('synthetic-password')
        with self.path.open('wb') as output:
            writer.write(output)
        with self.assertRaises(ApplicationError):
            render_pdf_page(self.path)

    def test_cancel_and_render_memory_limit(self):
        cancel=threading.Event();cancel.set()
        with patch('pypdfium2.PdfDocument',side_effect=AssertionError('Canceled render must not open PDF')):
            self.assertIsNone(render_pdf_page(self.path,cancel=cancel))
        with patch('myinvoices.pdf_preview.MAX_PIXELS',10000), patch('myinvoices.pdf_preview.MAX_SIDE',300):
            page=render_pdf_page(self.path,zoom=2.5)
            self.assertLessEqual(max(page.width,page.height),300)
            self.assertLessEqual(page.width*page.height,10300)

    def test_missing_renderer_has_install_guidance(self):
        with patch.dict('sys.modules',{'pypdfium2':None}):
            with self.assertRaises(ApplicationError) as caught:
                render_pdf_page(self.path)
        self.assertIn('INSTALL',str(caught.exception))


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','GUI requires a display (or Xvfb)')
class PDFPreviewGUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'synthetic.pdf'
        make_pdf(self.path)
        self.root=tk.Tk();self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors=[]
        self.root.report_callback_exception=lambda *error:self.errors.append(error)

    def wait(self,preview):
        deadline=time.monotonic()+10
        while preview.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(0.01)
        self.root.update()
        self.assertFalse(preview.busy)
        self.assertEqual(self.errors,[])

    def test_readonly_viewer_page_navigation_zoom_and_timer_cleanup(self):
        preview=PDFPreview(self.root,self.path,'Synthetic invoice')
        self.wait(preview)
        self.assertEqual(preview.photo.get(0,0),(255,0,0))
        self.assertEqual(str(preview.previous.cget('state')),'disabled')
        preview.next.invoke();self.wait(preview)
        self.assertEqual(preview.index,1)
        self.assertEqual(preview.photo.get(0,0),(0,0,255))
        self.assertEqual(str(preview.next.cget('state')),'disabled')
        width=preview.photo.width()
        preview.larger.invoke();self.wait(preview)
        self.assertGreater(preview.photo.width(),width)
        self.assertIn('125%',preview.status.get())
        preview.window.destroy()
        self.assertTrue(preview.closed)
        self.assertIsNone(preview.poll_id)
        self.assertTrue(preview.cancel.is_set())

    def test_closing_during_render_discards_worker_result(self):
        entered=threading.Event();release=threading.Event()
        def slow(*args,**kwargs):
            entered.set();release.wait(3)
            return None
        with patch('myinvoices.pdf_preview.render_pdf_page',side_effect=slow):
            preview=PDFPreview(self.root,self.path,'Synthetic invoice')
            self.assertTrue(entered.wait(2))
            preview.window.destroy()
            release.set()
            self.root.update()
        self.assertEqual(self.errors,[])
        self.assertIsNone(preview.poll_id)

    def test_preview_failure_reaches_copyable_application_message_callback(self):
        messages=[]
        preview=PDFPreview(self.root,self.path.with_name('missing.pdf'),'Synthetic',on_error=messages.append)
        self.wait(preview)
        self.assertEqual(len(messages),1)
        self.assertEqual(preview.status.get(),messages[0])
        self.assertIsNone(preview.photo)
