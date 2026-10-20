"""Developer-only synthetic UI captures. Requires display and Pillow.

No real API requests, customer data or credentials. The before view executes the
unchanged v0.12 GUI from its recorded Git revision against synthetic fixtures.
"""
from datetime import date
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
import time
import tkinter as tk
import types
from unittest.mock import patch

from PIL import ImageGrab
from pypdf import PdfWriter
from myinvoices.database import Database
from myinvoices.demo import xml_invoice,xml_page
from myinvoices.xml_parser import parse_response
from myinvoices.gui import App
from myinvoices.pdf_downloads import PDFDownloader,archive_pdf_bytes

BASELINE='f669efa73b1bc1251db0439f1c0d94819f82898e'
OUTPUT=Path('docs/screenshots')


def pump(root,predicate=lambda:True):
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        root.update()
        if predicate():
            for _ in range(8):root.update();time.sleep(.01)
            return
        time.sleep(.01)
    raise RuntimeError('Synthetic GUI did not settle')


def capture(root,name):
    root.update()
    ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height()),
                   xdisplay=__import__('os').environ.get('DISPLAY')).save(OUTPUT/name)


def main():
    OUTPUT.mkdir(exist_ok=True,parents=True)
    with tempfile.TemporaryDirectory(prefix='myinvoices-synthetic-capture-') as directory:
        folder=Path(directory);db=Database(folder/'demo.sqlite3')
        client=db.add_client('ΣΥΝΘΕΤΙΚΟΣ ΠΕΛΑΤΗΣ','000000000')
        db.add_client('Συνθετική παραγωγή','000000000','production')
        db.commit_page(db.checkpoint(client.id),parse_response(xml_page([xml_invoice(mark,url=mark!=106) for mark in range(100,107)])))
        writer=PdfWriter();writer.add_blank_page(width=600,height=800);stream=io.BytesIO();writer.write(stream);pdf=stream.getvalue()
        for mark,state in [(100,'challenge'),(101,'network_error'),(102,'saved_unverified'),(103,'printed_unverified'),(104,'imported_unverified')]:
            row=db.invoice(client.id,mark);source=hashlib.sha256((row['download_url'] or '').encode()).hexdigest()
            if state.endswith('_unverified'):
                path,checksum,size=archive_pdf_bytes(pdf,client,row,folder/'Archive',__import__('threading').Event())
                db.record_pdf(client.id,mark,state,source,str(path),checksum,size)
            else:db.record_pdf(client.id,mark,state,source)
        baseline=types.ModuleType('myinvoices.baseline_gui')
        source=subprocess.run(['git','show',BASELINE+':src/myinvoices/gui.py'],check=True,capture_output=True,text=True).stdout
        # The Xvfb core Helvetica font lacks correct Greek glyph fallback.
        # Override only the platform font, keeping the baseline layout/behavior.
        source=source.replace("else 'Helvetica'","else 'DejaVu Sans'")
        exec(compile(source,'v012-baseline-gui.py','exec'),baseline.__dict__)
        baseline.__dict__['__version__']='0.12.0'
        root=tk.Tk();root.tk.call('tk','scaling',96/72)
        app=baseline.App(root,db,today=date(2026,11,1));root.geometry('1366x768+0+0');pump(root,lambda:not app.busy)
        capture(root,'v012-before.png');root.destroy()
        root=tk.Tk();root.tk.call('tk','scaling',96/72)
        app=App(root,db,today=date(2026,11,1));root.geometry('1366x768+0+0');pump(root,lambda:not app.busy)
        app.download_roots[client.id]=str(folder/'Archive');app.refresh();pump(root,lambda:not app.busy)
        capture(root,'v013-main.png')
        app.document_book.select(app.failure_tree.master);root.update();app.failure_tree.selection_set('100')
        pump(root,lambda:'Cloudflare' in app.details.text.get('1.0','end'))
        capture(root,'v013-failures-details.png')
        class Synthetic(PDFDownloader):
            def _download(self,client,row,destination,cancel):
                time.sleep(.6)
                return archive_pdf_bytes(pdf,client,row,destination,cancel)
        with patch('myinvoices.gui.AutomaticPDFDownloader',side_effect=lambda db,**_:Synthetic(db)):
            app.pdf_button.invoke();pump(root,lambda:'MARK' in app.status.get())
            capture(root,'v013-batch-progress.png')
            pump(root,lambda:not app.busy)
        app.document_book.select(app.success_tree.master);pump(root)
        capture(root,'v013-batch-summary.png');root.destroy()
        root=tk.Tk();root.tk.call('tk','scaling',144/72)
        app=App(root,db,today=date(2026,11,1));root.geometry('1366x768+0+0');pump(root,lambda:not app.busy)
        capture(root,'v013-1366x768-scale150.png');root.destroy()


if __name__=='__main__':main()
