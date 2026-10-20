"""Developer-only synthetic Stage A captures. No real API or EBS calls."""
from datetime import date
from pathlib import Path
import tempfile
import time
import tkinter as tk
from PIL import ImageGrab
from myinvoices.demo import xml_invoice,xml_page
from myinvoices.xml_parser import parse_response
from myinvoices.database import Database
from myinvoices.gui import App
from myinvoices.entersoft.ui import open_extension


def synthetic(mark,number):
    xml=xml_invoice(mark).replace(f'<aa>{mark}</aa>',f'<aa>{number}</aa>')
    return xml.replace('<invoiceSummary>','<invoiceDetails><lineNumber>1</lineNumber><netValue>100.10</netValue><vatCategory>1</vatCategory><vatAmount>24.02</vatAmount></invoiceDetails><invoiceSummary>')


def main():
    output=Path('docs/screenshots');output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='entersoft-synthetic-') as folder:
        db=Database(Path(folder)/'main.sqlite3');client=db.add_client('ΣΥΝΘΕΤΙΚΟΣ ΠΕΛΑΤΗΣ','000000000')
        db.commit_page(db.checkpoint(client.id),parse_response(xml_page([synthetic(101,'0000123'),synthetic(102,'0000124'),xml_invoice(103)])))
        root=tk.Tk();root.tk.call('tk','scaling',96/72);app=App(root,db,today=date(2026,11,1))
        def wait(predicate):
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                root.update()
                if predicate():
                    for _ in range(8):root.update();time.sleep(.01)
                    return
                time.sleep(.01)
            raise RuntimeError('Synthetic UI did not finish')
        wait(lambda:not app.busy)
        extension=open_extension(app);extension.window.geometry('1366x768+0+0');wait(lambda:not extension.busy)
        extension.company.set('ΣΥΝΘΕΤΙΚΗ ΕΤΑΙΡΕΙΑ EBS');extension.load(reset=True);wait(lambda:not extension.busy)
        extension.tree.selection_set('101');root.update();extension.account.set('USER_DEFINED_ACCOUNT');extension.save_supplier();wait(lambda:not extension.busy)
        extension.tree.selection_set(extension.tree.get_children());root.update();extension.start();wait(lambda:not extension.busy)
        extension.tree.selection_set('103');root.update()
        def capture(name):
            x,y=extension.window.winfo_rootx(),extension.window.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+extension.window.winfo_width(),y+extension.window.winfo_height()),xdisplay=__import__('os').environ.get('DISPLAY')).save(output/name)
        capture('entersoft-stage-a.png')
        extension.book.select(1);root.update();capture('entersoft-audit.png')
        assert extension.start_button.winfo_ismapped()
        root.destroy()


if __name__=='__main__':main()
