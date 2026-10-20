"""Non-destructive backup/restore-preview workflow."""
import tkinter as tk
from tkinter import ttk,filedialog
from .backup import create_backup,validate_backup


def show_backup(app):
    window=tk.Toplevel(app.root);window.title('Backup και προεπισκόπηση επαναφοράς');window.transient(app.root)
    frame=ttk.Frame(window,padding=16);frame.pack(fill='both',expand=True)
    include=tk.BooleanVar(value=False)
    ttk.Label(frame,text='Συνεπές αντίγραφο βάσης, προαιρετικά με τα καταγεγραμμένα PDF.\nΔεν περιλαμβάνονται API credentials ή συνεδρίες browser.\nΟ έλεγχος επαναφοράς χρησιμοποιεί προσωρινή βάση· δεν αντικαθιστά τα τρέχοντα δεδομένα.',wraplength=620).pack(anchor='w')
    ttk.Checkbutton(frame,text='Να αντιγραφούν και τα καταγεγραμμένα PDF (απαιτεί επιπλέον χώρο)',variable=include).pack(anchor='w',pady=10)
    output=tk.Text(frame,height=12,width=78,wrap='word',state='disabled');output.pack(fill='both',expand=True,pady=8)
    def report(text):
        if not window.winfo_exists():return
        output.configure(state='normal');output.delete('1.0','end');output.insert('end',text);output.configure(state='disabled')
    def create():
        if app.busy:return
        destination=filedialog.askdirectory(parent=window,title='Προορισμός νέου backup')
        if not destination:return
        pdfs=include.get();app.cancel.clear()
        def work():
            app.operation_stage='backup'
            return create_backup(app.db,destination,include_pdfs=pdfs,cancel=app.cancel,
                progress=lambda text:app.queue.put(('browser_notice',text)))
        def done(payload):
            path,manifest=payload
            message=(f'Backup: {path}\nΒάση: έγκυρη. Κατάσταση: '+('ΜΕΡΙΚΟ — ορισμένα PDF λείπουν ή άλλαξαν.' if manifest['issues'] else 'Ολοκληρώθηκε.')+
                f"\nPDF: {'περιλαμβάνονται όσα αντιγράφηκαν επιτυχώς' if pdfs else 'ΔΕΝ περιλαμβάνονται — μόνο metadata'}.\n"
                f"Παραστατικά: {manifest['counts']['invoices']} · PDF αντιγραμμένα: {sum(e['kind']=='pdf' for e in manifest['files'])} · Προβλήματα PDF: {len(manifest['issues'])}.\n"
                'Τα credentials απαιτούν νέα καταχώριση σε νέο υπολογιστή.')
            report(message);app._message('Το backup ελέγχθηκε με δοκιμαστική επαναφορά σε προσωρινό φάκελο.')
        app._submit(work,done,report);app.stop_button.configure(state='normal')
    def validate():
        if app.busy:return
        folder=filedialog.askdirectory(parent=window,title='Φάκελος backup με manifest.json')
        if not folder:return
        app.cancel.clear()
        def work():
            app.operation_stage='backup'
            return validate_backup(folder,cancel=app.cancel)
        def done(result):
            m=result['manifest']
            report(f"Έγκυρο backup · schema {m['schema']} · {m['counts']['clients']} πελάτες · {m['counts']['invoices']} παραστατικά.\n"
                   f"PDF: {result['pdf_count']} · Μέγεθος: {result['total_bytes']/1024/1024:.2f} MB.\n"
                   f"Ημερομηνία UTC: {m['created_at']}\nΕλλείψεις PDF κατά τη δημιουργία: {len(m['issues'])}.\n"
                   'Η βάση επαναφέρθηκε και ελέγχθηκε μόνο σε προσωρινό φάκελο.\n'
                   'Δεν έγινε αντικατάσταση της ενεργής βάσης. Η εφαρμογή δεν παρέχει ακόμη ενεργό restore.\n'
                   'Τα καταγεγραμμένα paths της βάσης διατηρούν τις αρχικές διαδρομές. Απαιτείται χωριστή διαδικασία επανατοποθέτησης PDF και επανεισαγωγής credentials για νέο PC.')
            app._message('Ο έλεγχος backup ολοκληρώθηκε χωρίς αλλαγή των τωρινών δεδομένων.')
        app._submit(work,done,report);app.stop_button.configure(state='normal')
    bar=ttk.Frame(frame);bar.pack(fill='x')
    ttk.Button(bar,text='Δημιουργία νέου backup',command=create).pack(side='left',padx=4)
    ttk.Button(bar,text='Έλεγχος / προεπισκόπηση επαναφοράς',command=validate).pack(side='left',padx=4)
    window.bind('<Escape>',lambda _e:window.destroy())
