import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from organizer.core import scan, execute
from organizer.ui import (COLORS, configure_style, enable_dpi_awareness,
                          format_count, format_size, help_icon)


class Application(tk.Tk):
    PAGE = 250

    def __init__(self):
        super().__init__()
        self.title('File Organizer · Οργάνωση αρχείων')
        self.style = configure_style(self)
        self.scale = float(self.tk.call('tk', 'scaling')) / (96 / 72)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f'{min(self.px(1360), screen_w - 60)}x{min(self.px(960), screen_h - 80)}')
        self.minsize(min(self.px(680), screen_w - 60), min(self.px(540), screen_h - 80))
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.plan = None
        self.busy = False
        self.operation = None
        self.page = 0
        self.controls = []
        self.steps = []
        self.destination = tk.StringVar()
        self.mode = tk.StringVar(value='copy')
        self.status = tk.StringVar()
        self.file_count = tk.StringVar(value='—')
        self.total_size = tk.StringVar(value='—')
        self.warning_count = tk.StringVar(value='—')
        self.logs = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'FileOrganizer' / 'logs'
        self._build_header()
        self._build_footer()
        self._build_body()
        self.set_status('Επιλέξτε πηγές και προορισμό για να ξεκινήσετε.', 'info', 'Έτοιμο')
        self.set_step(0)
        self.update_controls()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<FocusIn>', self._reveal_focus, add='+')
        self.bind_all('<MouseWheel>', self._wheel, add='+')
        self.bind_all('<Button-4>', self._wheel, add='+')
        self.bind_all('<Button-5>', self._wheel, add='+')
        self.poll_id = self.after(100, self.poll)

    def px(self, value):
        return round(value * self.scale)

    def _build_header(self):
        header = ttk.Frame(self, padding=(self.px(24), self.px(18), self.px(24), self.px(8)))
        header.pack(fill='x')
        ttk.Label(header, text='File Organizer', style='Title.TLabel').pack(anchor='w')
        self.subtitle = ttk.Label(header, text='Τα αρχεία σας, στη θέση τους. Οργάνωση ανά τύπο και έτος, με έλεγχο πριν από κάθε αλλαγή.', foreground=COLORS['muted'])
        self.subtitle.pack(anchor='w', pady=(3, 12))
        self.step_frame = ttk.Frame(header)
        self.step_frame.pack(fill='x')
        for text in ('1  Επιλογή φακέλων', '2  Σάρωση', '3  Προεπισκόπηση', '4  Οργάνωση'):
            self.steps.append(ttk.Label(self.step_frame, text=text, style='Step.TLabel'))

    def _build_footer(self):
        footer = ttk.Frame(self, padding=(self.px(24), self.px(12)))
        footer.pack(side='bottom', fill='x')
        footer.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(footer, mode='determinate', maximum=100)
        self.bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        status_row = ttk.Frame(footer)
        status_row.grid(row=1, column=0, sticky='ew')
        self.badge = ttk.Label(status_row, style='info.Badge.TLabel')
        self.badge.pack(anchor='w')
        self.status_label = ttk.Label(status_row, textvariable=self.status, justify='left')
        self.status_label.pack(anchor='w', pady=(5, 0))
        self.cancel_button = self.action(footer, 'Ακύρωση', self.request_cancel, 'cancel', style='Cancel.TButton', managed=False)
        self.cancel_button.master.grid(row=1, column=1, sticky='ne', padx=(12, 0))
        footer.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(200, e.width - self.px(210))))

    def _build_body(self):
        host = ttk.Frame(self)
        host.pack(fill='both', expand=True)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(host, background=COLORS['background'], highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(host, orient='vertical', command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scroll.set)
        self.content = ttk.Frame(self.canvas, padding=(self.px(24), 4, self.px(12), 4))
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._layout)
        self.settings = self.card(self.content)
        self.workspace = ttk.Frame(self.content)
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        self._build_settings()
        self._build_preview()
        self._build_history()
        self.compact = None

    def card(self, parent):
        outer = tk.Frame(parent, background=COLORS['surface'], highlightthickness=1,
                         highlightbackground=COLORS['line'], padx=self.px(18), pady=self.px(16))
        return outer

    def heading(self, parent, text, key):
        row = ttk.Frame(parent, style='Card.TFrame')
        row.pack(fill='x', pady=(0, 10))
        ttk.Label(row, text=text, style='Section.TLabel').pack(side='left')
        help_icon(row, key).pack(side='left', padx=8)
        return row

    def action(self, parent, text, command, key, style='TButton', managed=True):
        # The help badge remains keyboard-accessible even when the action is disabled.
        group = ttk.Frame(parent, style='Card.TFrame')
        group.columnconfigure(0, weight=1)
        button = ttk.Button(group, text=text, command=command, style=style)
        button.grid(row=0, column=0, sticky='ew')
        help_icon(group, key).grid(row=0, column=1, padx=(6, 0))
        if managed:
            self.controls.append(button)
        return button

    def _build_settings(self):
        self.heading(self.settings, 'Πηγές αρχείων', 'sources')
        self.sources_hint = ttk.Label(self.settings, text='Προσθέστε φακέλους ή ολόκληρους δίσκους.', style='Muted.TLabel', wraplength=self.px(280))
        self.sources_hint.pack(fill='x', pady=(0, 10))
        list_host = ttk.Frame(self.settings, style='Card.TFrame')
        list_host.pack(fill='x')
        list_host.columnconfigure(0, weight=1)
        self.roots = tk.Listbox(list_host, font='TkDefaultFont', height=3, width=1, exportselection=False, selectmode='extended',
                                background='#F5F8FC', foreground=COLORS['ink'], selectbackground=COLORS['blue'],
                                selectforeground='white', relief='flat', highlightthickness=1, highlightbackground=COLORS['line'],
                                highlightcolor=COLORS['blue'], activestyle='dotbox')
        self.roots.grid(row=0, column=0, sticky='ew')
        yscroll = ttk.Scrollbar(list_host, orient='vertical', command=self.roots.yview)
        yscroll.grid(row=0, column=1, sticky='ns')
        xscroll = ttk.Scrollbar(list_host, orient='horizontal', command=self.roots.xview)
        xscroll.grid(row=1, column=0, sticky='ew')
        self.roots.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.add_button = self.action(self.settings, '+  Προσθήκη πηγής', self.add_root, 'add')
        self.add_button.master.pack(fill='x', pady=(10, 6))
        self.remove_button = self.action(self.settings, 'Αφαίρεση επιλογής', self.remove_root, 'remove')
        self.remove_button.master.pack(fill='x')
        self.roots.bind('<<ListboxSelect>>', lambda e: self.update_controls())
        ttk.Separator(self.settings).pack(fill='x', pady=12)
        self.heading(self.settings, 'Φάκελος προορισμού', 'destination')
        self.destination_entry = ttk.Entry(self.settings, textvariable=self.destination, state='readonly', width=1)
        self.destination_entry.pack(fill='x', pady=(0, 8))
        button = self.action(self.settings, 'Επιλογή προορισμού…', self.choose_destination, 'destination')
        button.master.pack(fill='x')
        ttk.Label(self.settings, text='Δομή: Excel / 2026, PDF / 2025, …', style='Muted.TLabel').pack(anchor='w', pady=(8, 0))
        ttk.Separator(self.settings).pack(fill='x', pady=12)
        ttk.Label(self.settings, text='Τρόπος οργάνωσης', style='Section.TLabel').pack(anchor='w', pady=(0, 6))
        for text, value in [('Αντιγραφή · διατήρηση αρχικών', 'copy'), ('Μετακίνηση', 'move')]:
            row = ttk.Frame(self.settings, style='Card.TFrame')
            row.pack(fill='x')
            widget = ttk.Radiobutton(row, text=text, variable=self.mode, value=value, command=self._mode_changed)
            widget.pack(side='left')
            help_icon(row, value).pack(side='left', padx=4)
            self.controls.append(widget)
        self.mode_hint = ttk.Label(self.settings, style='Muted.TLabel', wraplength=self.px(280), text='Η αντιγραφή διατηρεί τα αρχικά αρχεία σας.')
        self.mode_hint.pack(fill='x', pady=(6, 14))
        self.scan_button = self.action(self.settings, 'Σάρωση αρχείων', self.begin_scan, 'scan', style='Primary.TButton')
        self.scan_button.master.pack(fill='x')

    def _build_preview(self):
        self.preview = self.card(self.workspace)
        self.preview.grid(row=0, column=0, sticky='nsew')
        self.preview.columnconfigure(0, weight=1)
        self.preview.rowconfigure(3, weight=1)
        heading = ttk.Frame(self.preview, style='Card.TFrame')
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='Προεπισκόπηση', style='Section.TLabel').pack(side='left')
        help_icon(heading, 'preview').pack(side='left', padx=8)
        self.metrics = ttk.Frame(self.preview, style='Card.TFrame')
        self.metrics.grid(row=1, column=0, sticky='ew', pady=(16, 12))
        for index, (title, value) in enumerate([('Αρχεία', self.file_count), ('Συνολικό μέγεθος', self.total_size), ('Προειδοποιήσεις', self.warning_count)]):
            self.metrics.columnconfigure(index, weight=1)
            metric = ttk.Frame(self.metrics, style='Card.TFrame')
            metric.grid(row=0, column=index, sticky='w', padx=(0, 12))
            ttk.Label(metric, text=title, style='Muted.TLabel').pack(anchor='w')
            ttk.Label(metric, textvariable=value, style='Metric.TLabel').pack(anchor='w', pady=(4, 0))
        self.empty_message = ttk.Label(self.preview, text='Η προεπισκόπηση θα εμφανιστεί μετά τη σάρωση.', style='Muted.TLabel', wraplength=self.px(460))
        self.empty_message.grid(row=2, column=0, sticky='w', pady=(0, 12))
        table_host = ttk.Frame(self.preview, style='Card.TFrame')
        table_host.grid(row=3, column=0, sticky='nsew')
        table_host.columnconfigure(0, weight=1)
        table_host.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_host, columns=('source', 'target', 'size'), show='headings', height=5, selectmode='browse')
        for key, title, width in [('source', 'Αρχείο προέλευσης', 270), ('target', 'Προορισμός', 290), ('size', 'Μέγεθος', 90)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=self.px(width), minwidth=self.px(80), anchor='e' if key == 'size' else 'w')
        self.table.tag_configure('even', background='#F6F8FC')
        vertical = ttk.Scrollbar(table_host, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_host, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.bind('<<TreeviewSelect>>', self._select_entry)
        nav = ttk.Frame(self.preview, style='Card.TFrame')
        nav.grid(row=4, column=0, sticky='ew', pady=(10, 10))
        self.previous_button = ttk.Button(nav, text='‹  Πίσω', command=lambda: self.show_page(-1))
        self.previous_button.pack(side='left')
        self.next_button = ttk.Button(nav, text='Επόμενη  ›', command=lambda: self.show_page(1))
        self.next_button.pack(side='left', padx=6)
        help_icon(nav, 'pagination').pack(side='left')
        self.page_label = ttk.Label(nav, text='Σελίδα — / —', style='Muted.TLabel')
        self.page_label.pack(side='right')
        details = ttk.Frame(self.preview, style='Card.TFrame')
        details.grid(row=5, column=0, sticky='ew', pady=(0, 12))
        details.columnconfigure(1, weight=1)
        self.selected_source = tk.StringVar()
        self.selected_target = tk.StringVar()
        for i, (title, variable) in enumerate([('Από', self.selected_source), ('Προς', self.selected_target)]):
            ttk.Label(details, text=title, style='Muted.TLabel').grid(row=i, column=0, sticky='w', padx=(0, 8), pady=2)
            ttk.Entry(details, textvariable=variable, state='readonly', width=1).grid(row=i, column=1, sticky='ew', pady=2)
        help_icon(details, 'details').grid(row=0, column=2, rowspan=2, padx=(8, 0))
        self.run_button = self.action(self.preview, 'Οργάνωση αρχείων…', self.begin_execution, 'execute', style='Primary.TButton')
        self.run_button.master.grid(row=6, column=0, sticky='e')

    def _build_history(self):
        self.history_card = self.card(self.workspace)
        self.history_card.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        row = self.heading(self.history_card, 'Πρόσφατες ενέργειες', 'history')
        button = self.action(row, 'Άνοιγμα ιστορικού', self.open_history, 'open_history', managed=False)
        button.master.pack(side='right')
        self.history = tk.Text(self.history_card, font='TkDefaultFont', height=3, state='disabled', wrap='word', relief='flat',
                               background='#F5F8FC', foreground=COLORS['muted'], padx=10, pady=8, width=1)
        self.history.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(self.history_card, orient='vertical', command=self.history.yview)
        scroll.pack(side='right', fill='y')
        self.history.configure(yscrollcommand=scroll.set)
        for tag in ('success', 'warning', 'error', 'info'):
            self.history.tag_configure(tag, foreground=COLORS.get(tag, COLORS['blue']))

    def _layout(self, event):
        width = event.width
        self.canvas.itemconfigure(self.content_window, width=width)
        compact = width < self.px(1080)
        if compact != self.compact:
            self.compact = compact
            self.settings.grid_forget()
            self.workspace.grid_forget()
            self.content.columnconfigure(0, weight=1 if compact else 0, minsize=0 if compact else self.px(350))
            self.content.columnconfigure(1, weight=0 if compact else 1)
            self.content.rowconfigure(0, weight=0 if compact else 1)
            self.content.rowconfigure(1, weight=1 if compact else 0)
            self.settings.grid(row=0, column=0, sticky='new', padx=(0, 0 if compact else self.px(16)))
            self.workspace.grid(row=1 if compact else 0, column=0 if compact else 1, sticky='nsew', pady=(16 if compact else 0, 0))
            for i, label in enumerate(self.steps):
                label.grid(row=i // 2 if compact else 0, column=i % 2 if compact else i, sticky='ew', padx=(0, 6), pady=2)
            for i in range(4):
                self.step_frame.columnconfigure(i, weight=1 if not compact or i < 2 else 0)
        self.content.update_idletasks()
        height = max(event.height, self.content.winfo_reqheight())
        self.canvas.itemconfigure(self.content_window, height=height)
        self.subtitle.configure(wraplength=max(200, width - self.px(50)))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _wheel(self, event):
        # List/table/history keep their own wheel handling.
        if isinstance(event.widget, (tk.Listbox, tk.Text, ttk.Treeview, ttk.Entry, ttk.Scrollbar)):
            return
        direction = -1 if getattr(event, 'num', None) == 4 else 1 if getattr(event, 'num', None) == 5 else -int(event.delta / 120)
        if direction:
            self.canvas.yview_scroll(direction * 3, 'units')

    def _reveal_focus(self, event):
        self._reveal_widget(event.widget)

    def _reveal_widget(self, widget):
        try:
            if str(widget).startswith(str(self.content) + '.'):
                self.update_idletasks()
                top = widget.winfo_rooty() - self.canvas.winfo_rooty()
                bottom = top + widget.winfo_height()
                current = self.canvas.canvasy(0)
                height = max(self.content.winfo_height(), 1)
                if top < 0:
                    self.canvas.yview_moveto((current + top - 8) / height)
                elif bottom > self.canvas.winfo_height():
                    self.canvas.yview_moveto((current + bottom - self.canvas.winfo_height() + 8) / height)
        except tk.TclError:
            pass

    def set_status(self, text, level='info', badge=None):
        self.status.set(text)
        self.badge.configure(text=badge or {'info': 'Πληροφορία', 'success': 'Επιτυχία', 'warning': 'Προσοχή', 'error': 'Σφάλμα'}[level], style=f'{level}.Badge.TLabel')

    def set_step(self, active):
        for i, label in enumerate(self.steps):
            label.configure(style='Active.Step.TLabel' if i == active else 'Step.TLabel')

    def update_controls(self):
        for control in self.controls:
            control.configure(state='disabled' if self.busy else 'normal')
        self.roots.configure(state='disabled' if self.busy else 'normal')
        self.remove_button.configure(state='normal' if not self.busy and self.roots.curselection() else 'disabled')
        ready = self.plan is not None and bool(self.plan.entries) and not self.plan.cancelled
        self.run_button.configure(state='normal' if ready and not self.busy else 'disabled')
        self.cancel_button.configure(state='normal' if self.busy and not self.cancel.is_set() else 'disabled')
        pages = max(1, (len(self.plan.entries) + self.PAGE - 1) // self.PAGE) if self.plan else 1
        self.previous_button.configure(state='normal' if self.plan and self.page > 0 and not self.busy else 'disabled')
        self.next_button.configure(state='normal' if self.plan and self.page < pages - 1 and not self.busy else 'disabled')

    def invalidate(self):
        self.plan = None
        self.page = 0
        self.table.delete(*self.table.get_children())
        self.selected_source.set('')
        self.selected_target.set('')
        self.file_count.set('—')
        self.total_size.set('—')
        self.warning_count.set('—')
        self.page_label.configure(text='Σελίδα — / —')
        self.empty_message.configure(text='Η προεπισκόπηση θα εμφανιστεί μετά τη σάρωση.')
        count = self.roots.size()
        self.sources_hint.configure(text=f'{format_count(count)} πηγές προς σάρωση' if count else 'Προσθέστε φακέλους ή ολόκληρους δίσκους.')
        self.set_step(0)
        self.update_controls()

    def add_root(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title='Επιλέξτε φάκελο ή τη ρίζα δίσκου', mustexist=True)
        if path and path not in self.roots.get(0, 'end'):
            self.roots.insert('end', path)
            self.invalidate()
            self.set_status('Η πηγή προστέθηκε. Επιλέξτε προορισμό ή ξεκινήστε σάρωση.')

    def remove_root(self):
        if self.busy:
            return
        for index in reversed(self.roots.curselection()):
            self.roots.delete(index)
        self.invalidate()
        self.set_status('Η λίστα πηγών ενημερώθηκε. Απαιτείται νέα σάρωση.')

    def choose_destination(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title='Φάκελος προορισμού', mustexist=True)
        if path:
            self.destination.set(path)
            self.destination_entry.xview_moveto(1)
            self.invalidate()
            self.set_status('Ο προορισμός επιλέχθηκε. Πατήστε «Σάρωση αρχείων».')

    def _mode_changed(self):
        move = self.mode.get() == 'move'
        self.mode_hint.configure(text='Τα αρχικά αφαιρούνται μετά την επιτυχή μεταφορά.' if move else 'Η αντιγραφή διατηρεί τα αρχικά αρχεία σας.',
                                 foreground=COLORS['warning'] if move else COLORS['muted'])

    def work(self, function, operation):
        self.busy = True
        self.operation = operation
        self.cancel.clear()
        self.update_controls()
        self.bar.configure(mode='indeterminate', value=0)
        self.bar.start()
        def worker():
            try:
                self.events.put(('done', function()))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def begin_scan(self):
        if self.busy:
            return
        roots = self.roots.get(0, 'end')
        destination = self.destination.get()
        if not roots or not destination:
            missing = 'Προσθέστε τουλάχιστον έναν φάκελο ή δίσκο.' if not roots else 'Επιλέξτε φάκελο προορισμού.'
            self.set_status(missing, 'warning')
            messagebox.showinfo('Επιλογή φακέλων', missing, parent=self)
            (self.add_button if not roots else self.destination_entry).focus_set()
            return
        self.invalidate()
        self.set_step(1)
        self.empty_message.configure(text='Αναζήτηση αρχείων… Η προεπισκόπηση θα είναι διαθέσιμη μόλις ολοκληρωθεί η σάρωση.')
        self.set_status('Σάρωση σε εξέλιξη. Τα αρχεία σας παραμένουν ανέπαφα.', badge='Σάρωση')
        self.work(lambda: scan(roots, destination, self.cancel, lambda n: self.events.put(('scan', n))), 'scan')

    def begin_execution(self):
        if self.busy or not self.plan or self.plan.cancelled or not self.plan.entries:
            return
        mode = self.mode.get()
        verb = 'μετακινηθούν' if mode == 'move' else 'αντιγραφούν'
        if not messagebox.askyesno('Επιβεβαίωση οργάνωσης', f'Θα {verb} {format_count(len(self.plan.entries))} αρχεία ({self.total_size.get()}).\n\n'
                                  f'Προορισμός: {self.plan.destination}\n\n'
                                  'Ελέγξτε όλες τις σελίδες της προεπισκόπησης.\n'
                                  'Η μετακίνηση αφαιρεί την πηγή μετά την επιτυχή αντιγραφή.\n'
                                  'Η ακύρωση δεν αναιρεί ολοκληρωμένες ενέργειες.\n\nΣυνέχεια;', parent=self):
            return
        plan = self.plan
        log = self.logs / f'{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl'
        self.append_history(f'Έναρξη οργάνωσης · Ιστορικό: {log}')
        self.set_step(3)
        self.set_status('Οργάνωση σε εξέλιξη. Μπορείτε να ζητήσετε ακύρωση.', badge='Οργάνωση')
        self.work(lambda: execute(plan, mode, log, self.cancel,
                                  lambda n, total, record: self.events.put(('progress', (n, total, record)))), 'execute')

    def request_cancel(self):
        if self.busy:
            self.cancel.set()
            self.cancel_button.configure(state='disabled')
            self.set_status('Ζητήθηκε ακύρωση. Περιμένετε να σταματήσει η τρέχουσα εργασία.', 'warning', 'Ακύρωση…')

    def show_page(self, delta=0):
        if not self.plan:
            return
        pages = max(1, (len(self.plan.entries) + self.PAGE - 1) // self.PAGE)
        self.page = max(0, min(pages - 1, self.page + delta))
        self.table.delete(*self.table.get_children())
        self.selected_source.set('')
        self.selected_target.set('')
        start = self.page * self.PAGE
        for index, entry in enumerate(self.plan.entries[start:start + self.PAGE], start):
            self.table.insert('', 'end', iid=str(index), values=(str(entry.source), str(entry.target), format_size(entry.size)), tags=('even' if index % 2 == 0 else 'odd',))
        self.page_label.configure(text=f'Σελίδα {self.page + 1} / {pages}')
        self.update_controls()

    def _select_entry(self, event=None):
        selected = self.table.selection()
        if self.plan and selected:
            entry = self.plan.entries[int(selected[0])]
            self.selected_source.set(str(entry.source))
            self.selected_target.set(str(entry.target))

    def append_history(self, text, level='info'):
        self.history.configure(state='normal')
        self.history.insert('end', text + '\n', level)
        if int(self.history.index('end-1c').split('.')[0]) > 500:
            self.history.delete('1.0', '101.0')
        self.history.see('end')
        self.history.configure(state='disabled')

    def _accept_plan(self, plan):
        self.plan = plan
        self.page = 0
        count = len(plan.entries)
        self.file_count.set(format_count(count))
        self.total_size.set(format_size(sum(entry.size for entry in plan.entries)))
        self.warning_count.set(format_count(len(plan.warnings)))
        self.show_page()
        self.set_step(2)
        if plan.cancelled:
            self.empty_message.configure(text='Μερική προεπισκόπηση · Η σάρωση ακυρώθηκε. Απαιτείται νέα σάρωση για εκτέλεση.')
            self.set_status('Η σάρωση ακυρώθηκε. Δεν τροποποιήθηκαν αρχεία.', 'warning', 'Ακυρώθηκε')
        elif not count:
            self.empty_message.configure(text='Δεν βρέθηκαν αρχεία. Ελέγξτε τις πηγές, τις εξαιρέσεις και τις προειδοποιήσεις στο ιστορικό.')
            self.set_status('Η σάρωση ολοκληρώθηκε χωρίς αποτελέσματα.', 'warning', 'Κανένα αρχείο')
        else:
            self.empty_message.configure(text='Ελέγξτε όλες τις σελίδες πριν από την οργάνωση. Επιλέξτε γραμμή για ολόκληρες τις διαδρομές.')
            self.set_status(f'Η σάρωση ολοκληρώθηκε · {format_count(count)} αρχεία · {self.total_size.get()}. ' +
                            ('Δείτε τις προειδοποιήσεις στο ιστορικό.' if plan.warnings else 'Το σχέδιο είναι διαθέσιμο για έλεγχο.'),
                            'warning' if plan.warnings else 'success', 'Προεπισκόπηση')
        if len(plan.warnings) > 500:
            self.append_history(f'Εμφανίζονται οι τελευταίες 500 από {format_count(len(plan.warnings))} προειδοποιήσεις.', 'warning')
        for warning in plan.warnings[-500:]:
            self.append_history('Προειδοποίηση: ' + warning, 'warning')
        self.update_controls()

    def _accept_result(self, result):
        failed, cancelled = result['failed'], result['cancelled']
        self.set_status(f"Ολοκληρώθηκαν: {format_count(result['completed'])} · Απέτυχαν: {format_count(failed)} · "
                        f"Ακύρωση: {'ναι' if cancelled else 'όχι'}. Απαιτείται νέα σάρωση.",
                        'error' if failed else 'warning' if cancelled else 'success',
                        'Με σφάλματα' if failed else 'Ακυρώθηκε' if cancelled else 'Ολοκληρώθηκε')
        self.append_history(self.status.get(), 'error' if failed else 'warning' if cancelled else 'success')
        self.plan = None
        self.selected_source.set('')
        self.selected_target.set('')
        self.table.delete(*self.table.get_children())
        self.page_label.configure(text='Σελίδα — / —')
        self.empty_message.configure(text='Το σχέδιο εκτελέστηκε ή διακόπηκε. Κάντε νέα σάρωση για ενημερωμένη προεπισκόπηση.')
        self.update_controls()

    def poll(self):
        for _ in range(150):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'scan':
                self.file_count.set(format_count(data))
                if not self.cancel.is_set():
                    self.set_status(f'Βρέθηκαν {format_count(data)} αρχεία. Η σάρωση συνεχίζεται…', badge='Σάρωση')
            elif kind == 'progress':
                n, total, record = data
                self.bar.stop()
                self.bar.configure(mode='determinate', maximum=max(total, 1), value=n)
                if not self.cancel.is_set():
                    self.set_status(f'Επεξεργασία {format_count(n)} / {format_count(total)} · {n / max(total, 1):.0%}', badge='Οργάνωση')
                state = record['status']
                label = {'ok': 'Επιτυχία', 'error': 'Σφάλμα', 'cancelled': 'Ακύρωση'}.get(state, 'Πληροφορία')
                level = {'ok': 'success', 'error': 'error', 'cancelled': 'warning'}.get(state, 'info')
                self.append_history(f"{label}: {record['source']} → {record['target']} {record.get('error', '')}", level)
            else:
                self.busy = False
                self.operation = None
                self.bar.stop()
                self.bar.configure(mode='determinate')
                if kind == 'error':
                    self.set_status('Η εργασία απέτυχε: ' + data, 'error')
                    self.append_history('Σφάλμα: ' + data, 'error')
                    self.plan = None  # An incomplete/failed operation always requires a fresh scan.
                    self.table.delete(*self.table.get_children())
                    self.selected_source.set('')
                    self.selected_target.set('')
                    self.page_label.configure(text='Σελίδα — / —')
                    self.empty_message.configure(text='Η εργασία απέτυχε. Δείτε το ιστορικό και ξεκινήστε νέα σάρωση.')
                    self.update_controls()
                    messagebox.showerror('Σφάλμα', data, parent=self)
                elif hasattr(data, 'entries'):
                    self.bar.configure(value=0 if data.cancelled else 100, maximum=100)
                    self._accept_plan(data)
                    # Reveal the next step on small screens; keyboard focus follows.
                    self.table.focus_set()
                    self._reveal_widget(self.table)
                else:
                    self._accept_result(data)
        self.poll_id = self.after(100, self.poll)

    def open_history(self):
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(self.logs)
            else:
                messagebox.showinfo('Ιστορικό', str(self.logs), parent=self)
        except OSError as exc:
            self.set_status(f'Δεν ήταν δυνατό το άνοιγμα του ιστορικού: {exc}', 'error')

    def close(self):
        if self.busy:
            self.request_cancel()
            messagebox.showinfo('Ακύρωση', 'Περιμένετε να ολοκληρωθεί η ακύρωση πριν κλείσετε.', parent=self)
        else:
            self.after_cancel(self.poll_id)
            self.destroy()


def main():
    enable_dpi_awareness()
    Application().mainloop()
