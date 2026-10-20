"""Optional Greek Stage A window, independent of the existing invoice workflow."""
from datetime import date
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog

from ..models import ApplicationError
from ..date_range import DateRange
from ..presentation import display_date,display_amount
from . import STAGE
from .domain import Scope,Mapping,STATE_LABELS,REASONS,GATES
from .source import Source
from .store import Store
from .service import Control,Preparer
from . import desktop


def open_extension(app):
    if app.busy:return
    if not app.selected:
        app._message('Επιλέξτε πελάτη πριν ανοίξετε την ενότητα Entersoft.',error=True);return
    windows=getattr(app,'entersoft_windows',{})
    key=(app.selected,app.clients[app.selected].environment)
    if key in windows and not windows[key].closed:
        windows[key].window.lift();return windows[key]
    extension=Extension(app.root,app.db.path,app.clients[app.selected],app.dates)
    windows[key]=extension;app.entersoft_windows=windows
    return extension


class Extension:
    def __init__(self,parent,source_path,client,dates=None):
        self.client=client;self.source=Source(source_path,client)
        self.store_path=Path(source_path).parent/'entersoft'/'agent.sqlite3'
        self.store=None;self.rows={};self.queue=queue.Queue();self.control=Control()
        self.busy=False;self.preparing=False;self.closed=False;self.scope=None;self.offset=0;self.total=0
        self.window=tk.Toplevel(parent);self.window.title('Entersoft EBS — πρόσθετη ενότητα, Στάδιο Α')
        self.window.geometry('1280x800');self.window.minsize(1000,650)
        self.company=tk.StringVar();self.supplier=tk.StringVar();self.missing=tk.BooleanVar()
        self.start_date=tk.StringVar(value=display_date(str(dates.start)) if dates and dates.start else '')
        self.end_date=tk.StringVar(value=display_date(str(dates.end)) if dates and dates.end else '')
        self.status=tk.StringVar(value=STAGE);self.counts=tk.StringVar(value='Προετοιμασία 0 · Καταχωρίσεις EBS 0')
        self.account=tk.StringVar();self.supplier_code=tk.StringVar();self.expense_item=tk.StringVar();self.mapping_vat=tk.StringVar()
        self.test_mode=tk.BooleanVar(value=True);self.pid=tk.StringVar();self.detected=[]
        self._build();self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Destroy>',self._destroy,add='+')
        self.poll_id=self.window.after(50,self._poll)
        self.load()
    def _build(self):
        frame=ttk.Frame(self.window,padding=10);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=f'{self.client.name} · ΑΦΜ {self.client.vat} · {'[ΔΟΚΙΜΑΣΤΙΚΟ ΑΑΔΕ]' if self.client.environment=='development' else 'Παραγωγή ΑΑΔΕ'} · {STAGE}',font='TkHeadingFont',wraplength=1120).pack(anchor='w')
        ttk.Label(frame,text='Οι λήψεις PDF και οι υπάρχουσες λειτουργίες παραμένουν ξεχωριστές. Η εταιρεία παρακάτω ορίζει τοπικό πλαίσιο· δεν επιβεβαιώνει σύνδεση με EBS.',wraplength=1120).pack(anchor='w',pady=4)
        filters=ttk.Frame(frame);filters.pack(fill='x',pady=5)
        ttk.Label(filters,text='Εταιρεία EBS').grid(row=0,column=0)
        self.company_entry=ttk.Combobox(filters,textvariable=self.company,width=23);self.company_entry.grid(row=0,column=1,padx=4)
        ttk.Label(filters,text='Από / Έως').grid(row=0,column=2)
        self.from_entry=ttk.Entry(filters,textvariable=self.start_date,width=12);self.from_entry.grid(row=0,column=3,padx=4)
        self.to_entry=ttk.Entry(filters,textvariable=self.end_date,width=12);self.to_entry.grid(row=0,column=4,padx=4)
        ttk.Label(filters,text='ΑΦΜ προμηθευτή').grid(row=1,column=0)
        self.supplier_entry=ttk.Entry(filters,textvariable=self.supplier,width=20);self.supplier_entry.grid(row=1,column=1,padx=4)
        self.missing_check=ttk.Checkbutton(filters,text='Μόνο χωρίς λογαριασμό',variable=self.missing);self.missing_check.grid(row=1,column=2,columnspan=2)
        self.apply_button=ttk.Button(filters,text='Εφαρμογή φίλτρων',command=lambda:self.load(reset=True));self.apply_button.grid(row=1,column=4,padx=4)
        self.book=ttk.Notebook(frame);self.book.pack(fill='both',expand=True)
        records=ttk.Frame(self.book);audit=ttk.Frame(self.book);inspection=ttk.Frame(self.book)
        self.book.add(records,text='Παραστατικά και προετοιμασία');self.book.add(audit,text='Τοπικό ιστορικό');self.book.add(inspection,text='Ανίχνευση Windows — μόνο ανάγνωση')
        columns=('mark','supplier','number','date','net','vat','total','account','state')
        self.tree=ttk.Treeview(records,columns=columns,show='headings',selectmode='extended',height=8)
        for name,label,width in zip(columns,('MARK','ΑΦΜ προμηθευτή','Σειρά / αρχικός αριθμός','Ημερομηνία','Καθαρή αξία','ΦΠΑ','Σύνολο','Λογαριασμός','Κατάσταση προετοιμασίας'),(140,110,160,100,100,90,100,140,230)):
            self.tree.heading(name,text=label);self.tree.column(name,width=width,minwidth=70,anchor='e' if name in ('mark','net','vat','total') else 'w')
        vs=ttk.Scrollbar(records,command=self.tree.yview);hs=ttk.Scrollbar(records,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        self.tree.grid(row=0,column=0,sticky='nsew');vs.grid(row=0,column=1,sticky='ns');hs.grid(row=1,column=0,sticky='ew')
        self.details=tk.Text(records,height=4,wrap='word',state='disabled',font='TkTextFont')
        self.details.grid(row=2,column=0,columnspan=2,sticky='ew',pady=5)
        self.tree.bind('<<TreeviewSelect>>',self.selection)
        self.tree.bind('<Control-a>',self.select_all)
        records.rowconfigure(0,weight=1);records.columnconfigure(0,weight=1)
        mapping=ttk.LabelFrame(records,text='Αντιστοίχιση που ορίζει ο χρήστης',padding=6);mapping.grid(row=3,column=0,columnspan=2,sticky='ew')
        self.mapping_entries=[]
        for i,(label,variable) in enumerate((('ΑΦΜ προμηθευτή',self.mapping_vat),('Λογαριασμός εξόδων',self.account),('Κωδικός προμηθευτή EBS (προαιρετικό)',self.supplier_code),('Κωδικός δαπάνης EBS (προαιρετικό)',self.expense_item))):
            ttk.Label(mapping,text=label).grid(row=i//2,column=(i%2)*2,sticky='w')
            entry=ttk.Entry(mapping,textvariable=variable,width=23);entry.grid(row=i//2,column=(i%2)*2+1,padx=4,pady=3);self.mapping_entries.append(entry)
        bar=ttk.Frame(mapping);bar.grid(row=2,column=0,columnspan=4,sticky='w',pady=4)
        self.map_button=ttk.Button(bar,text='Αποθήκευση για προμηθευτή',command=self.save_supplier);self.map_button.pack(side='left')
        self.override_button=ttk.Button(bar,text='Εξαίρεση επιλεγμένου παραστατικού',command=self.save_override);self.override_button.pack(side='left',padx=4)
        self.clear_override_button=ttk.Button(bar,text='Αφαίρεση εξαίρεσης',command=self.clear_override);self.clear_override_button.pack(side='left')
        paging=ttk.Frame(records);paging.grid(row=4,column=0,columnspan=2,sticky='ew')
        self.previous=ttk.Button(paging,text='◀ Προηγούμενα',command=lambda:self.page(-1));self.previous.pack(side='left')
        self.next=ttk.Button(paging,text='Επόμενα ▶',command=lambda:self.page(1));self.next.pack(side='left',padx=5)
        self.page_text=tk.StringVar();ttk.Label(paging,textvariable=self.page_text).pack(side='right')
        self.audit_text=tk.Text(audit,wrap='word',state='disabled',font='TkTextFont');self.audit_text.pack(fill='both',expand=True)
        auditbar=ttk.Frame(audit);auditbar.pack(fill='x')
        self.export_button=ttk.Button(auditbar,text='Εξαγωγή ιστορικού / σφαλμάτων Excel…',command=self.export);self.export_button.pack(side='left')
        self.backup_button=ttk.Button(auditbar,text='Αντίγραφο βάσης επέκτασης',command=self.backup);self.backup_button.pack(side='left',padx=5)
        ttk.Label(inspection,text='Πρώτα επιλέξτε το σωστό παράθυρο. Δεν αλλάζει εστίαση, πεδία ή τιμές. Οι selectors/εταιρεία/Save παραμένουν ανεπιβεβαίωτα.',wraplength=1100).pack(anchor='w',pady=5)
        probe=ttk.Frame(inspection);probe.pack(fill='x')
        ttk.Label(probe,text='PID EBS (προαιρετικό)').pack(side='left');self.pid_entry=ttk.Entry(probe,textvariable=self.pid,width=10);self.pid_entry.pack(side='left',padx=5)
        self.detect_button=ttk.Button(probe,text='Ανίχνευση παραθύρων',command=self.detect);self.detect_button.pack(side='left')
        self.window_choice=ttk.Combobox(inspection,state='readonly',width=100);self.window_choice.pack(fill='x',pady=5)
        self.inspect_button=ttk.Button(inspection,text='Ανάγνωση controls επιλεγμένου παραθύρου',command=self.inspect);self.inspect_button.pack(anchor='w')
        self.inspection_text=tk.Text(inspection,wrap='word',state='disabled',font='TkTextFont');self.inspection_text.pack(fill='both',expand=True,pady=5)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=5)
        self.start_button=ttk.Button(actions,text='Έναρξη προετοιμασίας επιλεγμένων',command=self.start);self.start_button.pack(side='left')
        self.pause_button=ttk.Button(actions,text='Παύση',command=self.pause);self.pause_button.pack(side='left',padx=4)
        self.resume_button=ttk.Button(actions,text='Συνέχεια',command=self.resume);self.resume_button.pack(side='left')
        self.stop_button=ttk.Button(actions,text='Διακοπή',command=self.stop);self.stop_button.pack(side='left',padx=4)
        ttk.Checkbutton(actions,text='Test Mode — μόνο προετοιμασία',variable=self.test_mode,state='disabled').pack(side='right')
        ttk.Label(frame,textvariable=self.counts,wraplength=1100).pack(anchor='w')
        ttk.Label(frame,textvariable=self.status,wraplength=1100).pack(anchor='w',pady=4)
        for child in frame.winfo_children():
            if child.winfo_manager()=='pack':child.pack_forget()
        for index,child in enumerate(frame.winfo_children()):
            child.grid(row=index,column=0,sticky='nsew' if child is self.book else 'ew',pady=2)
            if child is self.book:frame.rowconfigure(index,weight=1)
        frame.columnconfigure(0,weight=1)
        self.window.bind('<Escape>',lambda _e:self.stop() if self.busy else self.close())
        self.mutable=[self.company_entry,self.from_entry,self.to_entry,self.supplier_entry,self.missing_check,self.apply_button,
            *self.mapping_entries,self.map_button,self.override_button,self.clear_override_button,self.export_button,self.backup_button,
            self.detect_button,self.inspect_button,self.pid_entry,self.previous,self.next,self.start_button]
    def _set_busy(self,value):
        self.busy=value
        for widget in self.mutable:widget.configure(state='disabled' if value else 'normal')
        self.pause_button.configure(state='normal' if value and self.preparing else 'disabled')
        self.resume_button.configure(state='normal' if value and self.preparing else 'disabled')
        self.stop_button.configure(state='normal' if value and self.preparing else 'disabled')
        if not value:
            self.previous.configure(state='normal' if self.offset else 'disabled')
            self.next.configure(state='normal' if self.offset+200<self.total else 'disabled')
    def _submit(self,work,done):
        if self.busy:return
        self._set_busy(True)
        def worker():
            try:
                if self.store is None:self.store=Store(self.store_path)
                result=work()
            except ApplicationError as error:self.queue.put(('error',str(error)))
            except Exception:self.queue.put(('error','Αποτυχία τοπικής προετοιμασίας. Δεν έγινε εγγραφή στο Entersoft.'))
            else:self.queue.put(('done',(done,result)))
        threading.Thread(target=worker,daemon=True).start()
    def _poll(self):
        try:
            for _ in range(100):
                kind,payload=self.queue.get_nowait()
                if kind=='progress':
                    event=payload
                    self.status.set('Σε παύση — Συνέχεια ή Διακοπή.' if event.paused else f'Έλεγχος {event.processed}/{event.total} · Προμηθευτής {event.supplier} · Αριθμός {event.number} · MARK {event.mark or "—"}')
                    self.counts.set(f"Σύνολο {event.total} · Ελεγμένα {event.processed} · Προετοιμασμένα {event.counts.get('pending',0)} · Αποτυχίες {event.counts.get('failed',0)} · Χειροκίνητος έλεγχος {event.counts.get('manual_review',0)} · Παραλείφθηκαν {event.counts.get('skipped',0)} · Υπόλοιπο {event.total-event.processed} · Νέες καταχωρίσεις EBS 0")
                elif kind=='error':self.preparing=False;self._set_busy(False);self.status.set(payload)
                else:
                    self._set_busy(False);callback,result=payload;callback(result)
        except queue.Empty:pass
        if not self.closed:self.poll_id=self.window.after(50,self._poll)
    def _scope(self):
        return Scope(self.client.id,self.client.vat,self.client.environment,self.company.get().strip())
    def _view(self,scope,dates,supplier,missing):
        if scope:self.store.bind(scope)
        rows,total=self.source.rows(dates,supplier,self.offset,200,missing_only=missing,scope=scope,mappings_path=self.store.path)
        mappings,overrides=self.store.mappings(scope) if scope else ({},{})
        jobs=self.store.jobs(scope) if scope else {}
        audit=self.store.audit(scope) if scope else []
        return rows,total,mappings,overrides,jobs,audit,self.store.companies(self.client)
    def load(self,reset=False):
        if self.busy:return
        try:
            scope=self._scope() if self.company.get().strip() else None
            dates=DateRange.parse(self.start_date.get(),self.end_date.get())
            supplier=self.supplier.get().strip();missing=self.missing.get()
            if supplier and (len(supplier)!=9 or not supplier.isascii() or not supplier.isdigit()):raise ApplicationError('Το φίλτρο ΑΦΜ χρειάζεται 9 ψηφία.')
            if missing and not scope:raise ApplicationError('Ορίστε εταιρεία EBS για έλεγχο ελλειπουσών αντιστοιχίσεων.')
        except ApplicationError as error:self.status.set(str(error));return
        if reset:self.offset=0
        self.scope=scope
        self._submit(lambda:self._view(scope,dates,supplier,missing),self._display)
    def _display(self,result):
        rows,self.total,self.mappings,self.overrides,self.jobs,audit,companies=result
        selected=self.tree.selection();self.tree.delete(*self.tree.get_children());self.rows={str(r['mark']):r for r in rows}
        for row in rows:
            key=str(row['mark']);mapping=self.overrides.get(row['mark']) or self.mappings.get(row.get('issuer_vat'))
            job=self.jobs.get(row['mark'])
            self.tree.insert('', 'end',iid=key,values=(key,row.get('issuer_vat') or '—',f"{row.get('series') or ''} / {row.get('number') or ''}",
                display_date(row['issue_date']),display_amount(row['net']),display_amount(row['vat']),display_amount(row['total']),mapping.account if mapping else 'Λείπει αντιστοίχιση',
                STATE_LABELS.get(job['state'],'Δεν έχει ελεγχθεί') if job else 'Δεν έχει ελεγχθεί'))
        self.tree.selection_set([key for key in selected if self.tree.exists(key)])
        self.page_text.set(f'{self.offset+1 if self.total else 0}–{min(self.offset+200,self.total)} από {self.total}')
        self.company_entry.configure(values=companies)
        lines=[]
        for event in audit:
            lines.append(f"{event['at']} · MARK {event['mark'] or '—'} · {event['event']} · {STATE_LABELS.get(event['state'],'')} · Λογαριασμός {event['account']} · "+' / '.join(REASONS.get(code,'Χειροκίνητος έλεγχος') for code in json.loads(event['reasons'])))
        self._text(self.audit_text,'\n'.join(lines) or 'Δεν υπάρχει τοπικό ιστορικό σε αυτό το πλαίσιο.')
        self._set_busy(False);self.selection()
    @staticmethod
    def _text(widget,text):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='disabled')
    def selection(self,_event=None):
        keys=self.tree.selection()
        if len(keys)!=1:
            self._text(self.details,f'{len(keys)} επιλεγμένα. Ορίστε εταιρεία/λογαριασμούς και ξεκινήστε δοκιμαστική προετοιμασία.');return
        row=self.rows[keys[0]];mapping=self.overrides.get(row['mark']) or self.mappings.get(row.get('issuer_vat'));job=self.jobs.get(row['mark'])
        self.mapping_vat.set(row.get('issuer_vat') or '')
        self.account.set(mapping.account if mapping else '');self.supplier_code.set(mapping.supplier_code if mapping else '');self.expense_item.set(mapping.expense_item if mapping else '')
        reasons=json.loads(job['reasons']) if job else []
        captured=json.loads(job['snapshot']) if job else {}
        vat_info=' / '.join(captured.get('vat_categories') or []) or 'δεν έχει ελεγχθεί'
        self._text(self.details,f"Κατηγορία ΦΠΑ XML: {vat_info} · MARK {row['mark']} · Αρχική σειρά {row.get('series') or '—'} · Αρχικός αριθμός {row.get('number') or '—'}\n"+
            ('\n'.join(REASONS.get(reason,'Χειροκίνητος έλεγχος') for reason in reasons) or 'Αναμονή ελέγχου προετοιμασίας.')+'\nΠριν από εγγραφή στο EBS εκκρεμούν: '+', '.join(GATES)+'.')
    def select_all(self,_event=None):self.tree.selection_set(self.tree.get_children());return 'break'
    def page(self,direction):
        if not self.busy:self.offset=max(0,self.offset+direction*200);self.load()
    def _mapping(self):return Mapping(self.account.get().strip(),self.supplier_code.get().strip(),self.expense_item.get().strip())
    def save_supplier(self):self._save_mapping(False)
    def save_override(self):self._save_mapping(True)
    def _save_mapping(self,override):
        if self.busy:return
        try:
            scope=self._scope();mapping=self._mapping();vat=self.mapping_vat.get().strip();marks=self.tree.selection()
            if override and len(marks)!=1:raise ApplicationError('Επιλέξτε ακριβώς ένα παραστατικό για εξαίρεση.')
            if override:vat=self.rows[marks[0]]['issuer_vat']
        except ApplicationError as error:self.status.set(str(error));return
        mark=int(marks[0]) if override else None
        self._submit(lambda:self.store.set_mapping(scope,vat,mapping,mark),lambda _:self.load())
    def clear_override(self):
        if self.busy:return
        try:
            scope=self._scope();marks=self.tree.selection()
            if len(marks)!=1:raise ApplicationError('Επιλέξτε ένα παραστατικό.')
        except ApplicationError as error:self.status.set(str(error));return
        self._submit(lambda:self.store.clear_override(scope,int(marks[0])),lambda _:self.load())
    def start(self):
        if self.busy:return
        try:
            scope=self._scope();marks=tuple(int(x) for x in self.tree.selection())
            if not marks:raise ApplicationError('Επιλέξτε παραστατικά για δοκιμαστική προετοιμασία.')
        except ApplicationError as error:self.status.set(str(error));return
        self.control=Control();self.preparing=True
        def work():return Preparer(self.source,self.store).run(scope,marks,self.control,lambda event:self.queue.put(('progress',event)))
        def done(result):
            self.preparing=False
            self.status.set(f"Η προετοιμασία {'διακόπηκε' if result['stopped'] else 'ολοκληρώθηκε'} · Ελεγμένα {result['processed']}/{result['total']} · Νέες εγγραφές EBS: 0.")
            self.load()
        self._submit(work,done)
    def pause(self):
        if self.busy:self.control.pause();self.status.set('Η παύση θα εφαρμοστεί πριν από το επόμενο βήμα προετοιμασίας.')
    def resume(self):
        if self.busy:self.control.resume();self.status.set('Συνέχιση προετοιμασίας…')
    def stop(self):
        if self.busy:self.control.stop();self.status.set('Ζητήθηκε διακοπή προετοιμασίας. Οι ήδη ελεγμένες εγγραφές διατηρούνται.')
    def export(self):
        if self.busy:return
        try:scope=self._scope()
        except ApplicationError as error:self.status.set(str(error));return
        path=filedialog.asksaveasfilename(parent=self.window,title='Νέο αρχείο ιστορικού Entersoft',defaultextension='.xlsx',filetypes=[('Excel','*.xlsx'),('CSV','*.csv')])
        if path:
            if Path(path).suffix.lower() not in ('.xlsx','.csv'):self.status.set('Επιλέξτε κατάληξη .xlsx ή .csv.');return
            export=self.store.export_excel if Path(path).suffix.lower()=='.xlsx' else self.store.export_audit
            self._submit(lambda:export(scope,path),lambda count:self.status.set(f'Εξήχθησαν {count} συμβάντα.'))
    def backup(self):
        if self.busy:return
        path=filedialog.askdirectory(parent=self.window,title='Αντίγραφο μόνο της βάσης επέκτασης Entersoft')
        if path:self._submit(lambda:self.store.backup(path),lambda output:self.status.set(f'Ελέγχθηκε αντίγραφο της επέκτασης: {output}'))
    def detect(self):
        if self.busy:return
        pid=self.pid.get().strip()
        if pid and (not pid.isascii() or not pid.isdigit() or int(pid)<=0):self.status.set('Μη έγκυρο PID.');return
        def done(windows):
            self.detected=windows;self.window_choice.configure(values=[f'PID {w.process_id} · HWND {w.handle} · {w.title}' for w in windows])
            if windows:self.window_choice.current(0)
            self.status.set(f'Βρέθηκαν {len(windows)} υποψήφια παράθυρα. Η εταιρεία και τα πεδία δεν έχουν επιβεβαιωθεί.')
        self._submit(lambda:desktop.windows(int(pid) if pid else None),done)
    def inspect(self):
        if self.busy:return
        index=self.window_choice.current()
        if not 0<=index<len(self.detected):self.status.set('Επιλέξτε παράθυρο μετά την ανίχνευση.');return
        self._submit(lambda:desktop.inspect(self.detected[index]),lambda result:self._text(self.inspection_text,json.dumps(result,ensure_ascii=False,indent=2)))
    def close(self):
        self.control.stop();self.window.destroy()
    def _destroy(self,event):
        if event.widget is self.window and not self.closed:
            self.closed=True;self.control.stop()
            if getattr(self,'poll_id',None):self.window.after_cancel(self.poll_id)
