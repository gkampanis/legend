import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from organizer.core import scan, execute


class Application(tk.Tk):
    PAGE = 250

    def __init__(self):
        super().__init__()
        self.title('Οργάνωση αρχείων')
        self.geometry('1100x760')
        self.minsize(800, 600)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.plan = None
        self.busy = False
        self.page = 0
        self.destination = tk.StringVar()
        self.mode = tk.StringVar(value='copy')
        self.status = tk.StringVar(value='Προσθέστε φακέλους ή δίσκους και επιλέξτε προορισμό.')
        self.logs = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'FileOrganizer' / 'logs'
        top = ttk.Frame(self, padding=12)
        top.pack(fill='x')
        ttk.Label(top, text='Φάκελοι / δίσκοι προς σάρωση (με όλους τους υποφακέλους)').pack(anchor='w')
        self.roots = tk.Listbox(top, height=4, exportselection=False)
        self.roots.pack(fill='x', pady=5)
        row = ttk.Frame(top)
        row.pack(fill='x')
        self.controls = []
        self.button(row, 'Προσθήκη φακέλου ή δίσκου', self.add_root)
        self.button(row, 'Αφαίρεση επιλογής', self.remove_root)
        row = ttk.Frame(top)
        row.pack(fill='x', pady=8)
        ttk.Label(row, text='Προορισμός:').pack(side='left')
        ttk.Label(row, textvariable=self.destination).pack(side='left', padx=8)
        self.button(row, 'Επιλογή…', self.choose_destination)
        row = ttk.Frame(top)
        row.pack(fill='x')
        for text, value in [('Αντιγραφή (προεπιλογή)', 'copy'), ('Μετακίνηση', 'move')]:
            widget = ttk.Radiobutton(row, text=text, variable=self.mode, value=value)
            widget.pack(side='left', padx=5)
            self.controls.append(widget)
        self.button(row, 'Σάρωση / προεπισκόπηση', self.begin_scan)
        self.run_button = self.button(row, 'Εκτέλεση σχεδίου…', self.begin_execution)
        self.run_button.configure(state='disabled')
        self.cancel_button = ttk.Button(row, text='Ακύρωση', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=5)
        table_frame = ttk.Frame(self, padding=(12, 0))
        table_frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_frame, columns=('source', 'target', 'size'), show='headings')
        for key, title, width in [('source', 'Αρχείο προέλευσης', 420), ('target', 'Προτεινόμενος προορισμός', 420), ('size', 'Bytes', 90)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width)
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        nav = ttk.Frame(self, padding=8)
        nav.pack(fill='x')
        ttk.Button(nav, text='Προηγούμενη σελίδα', command=lambda: self.show_page(-1)).pack(side='left')
        ttk.Button(nav, text='Επόμενη σελίδα', command=lambda: self.show_page(1)).pack(side='left')
        self.page_label = ttk.Label(nav)
        self.page_label.pack(side='left', padx=12)
        ttk.Button(nav, text='Άνοιγμα ιστορικού', command=self.open_history).pack(side='right')
        self.bar = ttk.Progressbar(self, mode='indeterminate')
        self.bar.pack(fill='x', padx=12)
        ttk.Label(self, textvariable=self.status, wraplength=1000).pack(fill='x', padx=12, pady=6)
        self.history = tk.Text(self, height=7, state='disabled', wrap='word')
        self.history.pack(fill='x', padx=12, pady=(0, 12))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
        widget.pack(side='left', padx=4)
        self.controls.append(widget)
        return widget

    def invalidate(self):
        self.plan = None
        self.table.delete(*self.table.get_children())
        self.run_button.configure(state='disabled')

    def add_root(self):
        path = filedialog.askdirectory(title='Επιλέξτε φάκελο ή τη ρίζα δίσκου', mustexist=True)
        if path and path not in self.roots.get(0, 'end'):
            self.roots.insert('end', path)
            self.invalidate()

    def remove_root(self):
        for index in reversed(self.roots.curselection()):
            self.roots.delete(index)
        self.invalidate()

    def choose_destination(self):
        path = filedialog.askdirectory(title='Φάκελος προορισμού', mustexist=True)
        if path:
            self.destination.set(path)
            self.invalidate()

    def work(self, function):
        self.busy = True
        self.cancel.clear()
        for control in self.controls:
            control.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.bar.configure(mode='indeterminate')
        self.bar.start()
        def worker():
            try:
                self.events.put(('done', function()))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def begin_scan(self):
        roots = self.roots.get(0, 'end')
        destination = self.destination.get()
        if not roots or not destination:
            messagebox.showinfo('Επιλογή φακέλων', 'Επιλέξτε τουλάχιστον μία πηγή και προορισμό.')
            return
        self.invalidate()
        self.status.set('Σάρωση…')
        self.work(lambda: scan(roots, destination, self.cancel, lambda n: self.events.put(('scan', n))))

    def begin_execution(self):
        if not self.plan or self.plan.cancelled:
            return
        mode = self.mode.get()
        verb = 'μετακινηθούν' if mode == 'move' else 'αντιγραφούν'
        if not messagebox.askyesno('Επιβεβαίωση εκτέλεσης', f'Θα {verb} {len(self.plan.entries)} αρχεία.\n'
                                  'Ελέγξτε όλες τις σελίδες της προεπισκόπησης.\n'
                                  'Η μετακίνηση αφαιρεί την πηγή μετά την επιτυχή αντιγραφή.\n'
                                  'Η ακύρωση δεν αναιρεί ολοκληρωμένες ενέργειες.\nΣυνέχεια;'):
            return
        plan = self.plan
        log = self.logs / f'{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl'
        self.status.set(f'Εκτέλεση — ιστορικό: {log}')
        self.work(lambda: execute(plan, mode, log, self.cancel,
                                  lambda n, total, record: self.events.put(('progress', (n, total, record)))))

    def show_page(self, delta=0):
        if not self.plan:
            return
        pages = max(1, (len(self.plan.entries) + self.PAGE - 1) // self.PAGE)
        self.page = max(0, min(pages - 1, self.page + delta))
        self.table.delete(*self.table.get_children())
        for entry in self.plan.entries[self.page*self.PAGE:(self.page+1)*self.PAGE]:
            self.table.insert('', 'end', values=(str(entry.source), str(entry.target), entry.size))
        self.page_label.configure(text=f'Σελίδα {self.page + 1}/{pages} — {len(self.plan.entries)} αρχεία')

    def append_history(self, text):
        self.history.configure(state='normal')
        self.history.insert('end', text + '\n')
        if int(self.history.index('end-1c').split('.')[0]) > 500:
            self.history.delete('1.0', '101.0')
        self.history.see('end')
        self.history.configure(state='disabled')

    def poll(self):
        for _ in range(150):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'scan':
                self.status.set(f'Βρέθηκαν {data} αρχεία…')
            elif kind == 'progress':
                n, total, record = data
                self.bar.stop()
                self.bar.configure(mode='determinate', maximum=max(total, 1), value=n)
                self.status.set(f'Επεξεργασία {n}/{total}')
                self.append_history(f"{record['status']}: {record['source']} → {record['target']} {record.get('error', '')}")
            else:
                self.busy = False
                self.bar.stop()
                self.cancel_button.configure(state='disabled')
                for control in self.controls:
                    control.configure(state='normal')
                self.run_button.configure(state='disabled')
                if kind == 'error':
                    self.status.set('Η εργασία απέτυχε: ' + data)
                    messagebox.showerror('Σφάλμα', data)
                elif hasattr(data, 'entries'):
                    self.plan = data
                    self.page = 0
                    self.show_page()
                    self.status.set(f'{len(data.entries)} αρχεία, {len(data.warnings)} προειδοποιήσεις. ' +
                                    ('Ακυρώθηκε — απαιτείται νέα σάρωση.' if data.cancelled else 'Ελέγξτε την προεπισκόπηση.'))
                    for warning in data.warnings:
                        self.append_history(warning)
                    if data.entries and not data.cancelled:
                        self.run_button.configure(state='normal')
                else:
                    self.status.set(f"Ολοκληρώθηκαν: {data['completed']} · Απέτυχαν: {data['failed']} · Ακύρωση: {'ναι' if data['cancelled'] else 'όχι'}. Απαιτείται νέα σάρωση.")
                    self.plan = None
        self.after(100, self.poll)

    def open_history(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(self.logs)
        else:
            messagebox.showinfo('Ιστορικό', str(self.logs))

    def close(self):
        if self.busy:
            self.cancel.set()
            messagebox.showinfo('Ακύρωση', 'Περιμένετε να ολοκληρωθεί η ακύρωση πριν κλείσετε.')
        else:
            self.destroy()


def main():
    Application().mainloop()
