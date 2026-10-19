"""Thin Tk view: all database/network work runs off the GUI thread."""

import queue
import threading
import tkinter as tk
import unicodedata
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk, filedialog, font as tkfont

from . import __version__
from .api import Environment, RequestDocsAPI
from .demo import DemoAPI
from .credential_store import default_store
from .diagnostics import record_failure
from .models import ApplicationError, Credentials
from .local_check import check_client
from .sync import Synchronizer
from .pdf_downloads import PDFDownloader, state_label, open_provider, import_browser_pdf, saved_pdf_path
from .date_range import DateRange, previous_month
from .automatic_downloads import AutomaticPDFDownloader
from .local_files import desktop_directory, client_archive_folder, open_directory
from .pdf_preview import PDFPreview
from .archive_actions import removable_client_folder, trash_client_archive


STATUS = {"url_available": "Υπάρχει link — PDF ανεξέλεγκτο", "no_url": "Χωρίς link PDF"}
ENVIRONMENTS = {"development": "Δοκιμαστικό ΑΑΔΕ", "production": "Παραγωγή ΑΑΔΕ"}
PAGE_SIZE = 200
CHANGE_LABELS = {"cancellation": "Ακύρωση", "income_classification": "Χαρακτηρισμός εσόδων",
                 "expense_classification": "Χαρακτηρισμός εξόδων", "payment": "Πληρωμή"}


class App:
    def __init__(self, root, database, demo=False, credential_store=None, today=None):
        self.root, self.db, self.demo = root, database, demo
        self.font_family = 'Segoe UI' if 'Segoe UI' in tkfont.families(root) else 'Helvetica'
        for name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont'):
            tkfont.nametofont(name,root=root).configure(family=self.font_family,size=10)
        self.credentials = credential_store or default_store(database.path.parent / "credentials")
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.clients = {}
        self.selected = None
        self.document_bucket = "all"
        self.offsets = {bucket: 0 for bucket in ("all", "success", "failure")}
        self.bucket_totals = dict(self.offsets)
        self.visible_by_bucket = {bucket: {} for bucket in self.offsets}
        self.dates = DateRange() if demo else previous_month(today)
        self.date_from = tk.StringVar(value=self.dates.start.strftime('%d/%m/%Y') if self.dates.start else '')
        self.date_to = tk.StringVar(value=self.dates.end.strftime('%d/%m/%Y') if self.dates.end else '')
        self.download_roots = {}
        self.pdf_preview = None
        self.stat_total = tk.StringVar(value='0')
        self.stat_saved = tk.StringVar(value='0')
        self.stat_pending = tk.StringVar(value='0')
        self.visible_documents = {}
        self.total = 0
        self.last_result = None
        self.closed = False
        self.last_error = ""
        self.client_filter = tk.StringVar()
        self.client_count_label = tk.StringVar(value="Πελάτες")
        self.operation_stage = "local"
        self.status = tk.StringVar(value="Έτοιμο")
        self.summary = tk.StringVar(value="Επιλέξτε πελάτη")
        root.title(f"myInvoices Starter {__version__} — " + ("OFFLINE DEMO" if demo else "Στοιχεία πελατών / ΑΑΔΕ"))
        root.geometry("1280x860")
        root.minsize(1020, 720)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.poll_id = root.after(50, self._poll)
        root.bind("<Destroy>", self._destroy, add="+")
        self.refresh()

    @property
    def offset(self):
        return self.offsets[self.document_bucket]

    @offset.setter
    def offset(self, value):
        self.offsets[self.document_bucket] = value

    def _reset_pages(self):
        self.offsets = {bucket: 0 for bucket in self.offsets}

    def _document_tab_changed(self, _event=None):
        bucket = self.tab_buckets.get(self.document_book.select())
        if bucket:
            self.document_bucket = bucket
            self.visible_documents = self.visible_by_bucket[bucket]
            self.total = self.bucket_totals[bucket]
            self._page_status()
        self._paging_buttons()

    def _paging_buttons(self):
        active = self.document_book.select() in self.tab_buckets
        self.previous.configure(state="normal" if active and not self.busy and self.offset > 0 else "disabled")
        self.next.configure(state="normal" if active and not self.busy and self.offset + PAGE_SIZE < self.total else "disabled")

    def _page_status(self):
        self.status.set(f"Προβολή {self.offset + 1 if self.total else 0}–{min(self.offset + PAGE_SIZE, self.total)} από {self.total}.")

    def _build(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=(self.font_family, 10))
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=(self.font_family, 10, "bold"))
        style.configure("TButton", padding=(8, 5))
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        title = "myInvoices — Δοκιμαστική έκδοση"
        ttk.Label(outer, text=title, font=(self.font_family, 17, "bold")).pack(anchor="w")
        notice = ("OFFLINE DEMO: συνθετικά στοιχεία, χωρίς σύνδεση ή λήψη PDF."
                  if self.demo else
                  "Επιλέξτε πελάτη και περίοδο → εύρεση παραστατικών → λήψη PDF. Επιλέξτε μία εγγραφή για άνοιγμα PDF ή συνδέσμου.")
        ttk.Label(outer, text=notice, wraplength=1050).pack(anchor="w", pady=(4, 10))
        if self.demo:
            ttk.Label(outer, text="Για ενεργά πεδία API: κλείστε το demo και ανοίξτε START_MYINVOICES.bat.",
                      wraplength=1050).pack(anchor="w", pady=(0, 8))
        menu = tk.Menu(self.root,tearoff=False)
        self.root.configure(menu=menu)
        clients_menu = tk.Menu(menu,tearoff=False)
        clients_menu.add_command(label="Νέος πελάτης…",command=self._client_form)
        clients_menu.add_command(label="Στοιχεία πελάτη…",command=self._edit_client)
        clients_menu.add_command(label="Ανανέωση τοπικής λίστας",command=self.refresh)
        menu.add_cascade(label="Πελάτης",menu=clients_menu)
        tools = tk.Menu(menu,tearoff=False)
        for text,command in (("Έλεγχος σύνδεσης API",self._check_api),("Άνοιγμα επιλεγμένου στον browser",self._open_provider),
                             ("Άνοιγμα αποθηκευμένου PDF",self._open_saved_pdf),("Φάκελος τιμολογίων",self._open_archive_folder),
                             ("Χειροκίνητη εισαγωγή PDF…",self._import_pdf),("Επιλογή φακέλου PDF…",self._choose_destination),
                             ("Προηγούμενος μήνας",self._previous_month),("Όλες οι ημερομηνίες",self._clear_dates),
                             ("Εφαρμογή περιόδου στη λίστα",self._apply_dates)):
            tools.add_command(label=text,command=command)
        menu.add_cascade(label="Περισσότερα",menu=tools)
        self.main_menu = menu
        self.messages_menu = tk.Menu(menu,tearoff=False)
        self.messages_menu.add_command(label="Αντιγραφή τελευταίου σφάλματος",command=self._copy_error,state="disabled")
        self.messages_menu.add_command(label="Αντιγραφή όλων",command=self._copy_messages)
        self.messages_menu.add_command(label="Καθαρισμός μηνυμάτων",command=self._clear_messages)
        menu.add_cascade(label="Μηνύματα",menu=self.messages_menu)
        dashboard = ttk.Frame(outer)
        dashboard.pack(fill="x",pady=(0,12))
        period = ttk.LabelFrame(dashboard,text="Περίοδος έκδοσης",padding=10)
        period.pack(side="left",fill="y",padx=(0,10))
        ttk.Label(period,text="Από").grid(row=0,column=0,sticky="w")
        self.from_entry = ttk.Entry(period,textvariable=self.date_from,width=13)
        self.from_entry.grid(row=0,column=1,padx=5,pady=3)
        ttk.Label(period,text="Έως").grid(row=1,column=0,sticky="w")
        self.to_entry = ttk.Entry(period,textvariable=self.date_to,width=13)
        self.to_entry.grid(row=1,column=1,padx=5,pady=3)
        for entry in (self.from_entry,self.to_entry):
            entry.bind('<Return>',lambda _event:self._apply_dates())
        ttk.Label(period,text="ΗΗ/ΜΜ/ΕΕΕΕ",foreground="#666666").grid(row=2,column=0,columnspan=2)
        cards = ttk.Frame(dashboard)
        cards.pack(side="left",fill="both",expand=True)
        for title,value,color in (("Παραστατικά",self.stat_total,"#375566"),("PDF αποθηκευμένα",self.stat_saved,"#28745b"),
                                  ("Εκκρεμή browser",self.stat_pending,"#b67a12")):
            card = tk.Frame(cards,bg=color,padx=14,pady=10)
            card.pack(side="left",fill="both",expand=True,padx=3)
            tk.Label(card,text=title,bg=color,fg="white",font=(self.font_family,10,"bold")).pack(anchor="w")
            tk.Label(card,textvariable=value,bg=color,fg="white",font=(self.font_family,26,"bold")).pack(anchor="e")
        actions = ttk.Frame(dashboard)
        actions.pack(side="right",padx=(10,0),fill="y")
        self.sync_button = ttk.Button(actions,text="1. Εύρεση παραστατικών",command=self._sync)
        self.pdf_button = ttk.Button(actions,text="2. Λήψη PDF",command=self._download_pdfs)
        self.browser_pdf_button = ttk.Button(actions,text="3. Άνοιγμα στον browser",command=self._open_provider)
        for button in (self.sync_button,self.pdf_button,self.browser_pdf_button):
            button.pack(fill="x",pady=2)
        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        left, right = ttk.Frame(body), ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=4)
        ttk.Label(left, textvariable=self.client_count_label).pack(anchor="w")
        ttk.Label(left, text="Αναζήτηση ονόματος ή ΑΦΜ").pack(anchor="w", pady=(6, 2))
        self.search_entry = ttk.Entry(left, textvariable=self.client_filter)
        self.search_entry.pack(fill="x", pady=(0, 8))
        treebox = ttk.Frame(left)
        treebox.pack(fill="both", expand=True)
        self.client_tree = ttk.Treeview(treebox, columns=("vat",), show="tree headings", selectmode="browse")
        self.client_tree.heading("#0", text="Πελάτης")
        self.client_tree.heading("vat", text="ΑΦΜ")
        self.client_tree.column("#0", width=170)
        self.client_tree.column("vat", width=100)
        client_scroll = ttk.Scrollbar(treebox, orient="vertical", command=self.client_tree.yview)
        self.client_tree.configure(yscrollcommand=client_scroll.set)
        client_scroll.pack(side="right", fill="y")
        self.client_tree.pack(side="left", fill="both", expand=True)
        self.client_tree.bind("<<TreeviewSelect>>", self._select)
        self.client_tree.bind("<Double-1>", lambda _event: self._edit_client())
        self.client_filter.trace_add("write", lambda *_args: self._filter_clients())
        ttk.Label(right, textvariable=self.summary, wraplength=750).pack(anchor="w", pady=(0, 8))
        book = self.document_book = ttk.Notebook(right)
        book.pack(fill="both", expand=True)
        self.document_trees, self.tab_buckets = {}, {}
        columns = ("date", "mark", "supplier", "identity", "total", "method", "status", "reason")
        labels = ("Ημερομηνία", "MARK", "ΑΦΜ προμηθευτή", "Σειρά / Αριθμός", "Σύνολο",
                  "Προέλευση PDF", "Κατάσταση PDF", "Αιτία / επόμενη ενέργεια")
        for bucket, title in (("all", "Παραστατικά"), ("success", "Επιτυχημένα"), ("failure", "Αποτυχημένα")):
            documents = ttk.Frame(book)
            book.add(documents, text=title)
            self.tab_buckets[str(documents)] = bucket
            tree = ttk.Treeview(documents, columns=columns, show="headings")
            tree.tag_configure("cancelled", foreground="#a82f2f")
            for column, label in zip(columns, labels):
                tree.heading(column, text=label)
                tree.column(column, width=260 if column == "reason" else 150 if column in ("method", "status") else 130, minwidth=90)
            vertical = ttk.Scrollbar(documents, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(documents, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            documents.rowconfigure(0, weight=1)
            documents.columnconfigure(0, weight=1)
            self.document_trees[bucket] = tree
            tree.bind('<Double-1>', lambda _event: self._open_saved_pdf())
        # Keep the original name for the complete invoice list.
        self.document_tree = self.document_trees["all"]
        self.success_tree, self.failure_tree = self.document_trees["success"], self.document_trees["failure"]
        history, changes = ttk.Frame(book), ttk.Frame(book)
        book.add(history, text="Ιστορικό συγχρονισμού")
        book.add(changes, text="Ακυρώσεις / Μεταβολές")
        book.bind("<<NotebookTabChanged>>", self._document_tab_changed)
        self.history_tree = ttk.Treeview(history, columns=("at", "kind", "count"), show="headings")
        for column, label in (("at", "Χρόνος UTC"), ("kind", "Ενέργεια"), ("count", "Εγγραφές σελίδας")):
            self.history_tree.heading(column, text=label)
        self.history_tree.pack(fill="both", expand=True)
        ttk.Label(changes, text="Εμφανίζονται οι 200 νεότερες μεταβολές. Το XML διατηρείται στη βάση.").pack(anchor="w", pady=6)
        changes_body = ttk.Frame(changes)
        changes_body.pack(fill="both", expand=True)
        self.changes_tree = ttk.Treeview(changes_body, columns=("kind", "event", "invoice", "date"), show="headings")
        for column, label in (("kind", "Είδος"), ("event", "MARK μεταβολής"), ("invoice", "MARK παραστατικού"), ("date", "Ημ/νία ακύρωσης")):
            self.changes_tree.heading(column, text=label)
            self.changes_tree.column(column, width=170)
        changes_scroll = ttk.Scrollbar(changes_body, orient="vertical", command=self.changes_tree.yview)
        self.changes_tree.configure(yscrollcommand=changes_scroll.set)
        changes_scroll.pack(side="right", fill="y")
        self.changes_tree.pack(side="left", fill="both", expand=True)
        file_actions = ttk.Frame(right)
        file_actions.pack(fill='x', pady=(6, 2))
        self.open_pdf_button = ttk.Button(file_actions, text='Άνοιγμα PDF', command=self._open_saved_pdf)
        self.open_pdf_button.pack(side='left', padx=(0, 8))
        self.open_folder_button = ttk.Button(file_actions, text='Φάκελος τιμολογίων', command=self._open_archive_folder)
        self.open_folder_button.pack(side='left')
        self.reset_downloads_button = ttk.Button(file_actions, text='Μηδενισμός λήψεων', command=self._reset_downloads)
        self.reset_downloads_button.pack(side='left', padx=8)
        self.delete_archive_button = ttk.Button(file_actions, text='Διαγραφή φακέλου πελάτη', command=self._delete_archive)
        self.delete_archive_button.pack(side='left')
        paging = ttk.Frame(right)
        paging.pack(fill="x", pady=6)
        self.previous = ttk.Button(paging, text="Προηγούμενα", command=lambda: self._page(-1))
        self.previous.pack(side="left")
        self.next = ttk.Button(paging, text="Επόμενα", command=lambda: self._page(1))
        self.next.pack(side="left", padx=6)
        ttk.Label(paging, text="Προβολή έως 200 εγγραφών ανά σελίδα").pack(side="right")
        self.stop_button = ttk.Button(outer,text="Διακοπή εργασίας",command=self._stop,state="disabled")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 4))
        ttk.Label(outer, textvariable=self.status, wraplength=1050).pack(anchor="w")
        messages = ttk.LabelFrame(outer, text="Μηνύματα και σφάλματα — επιλογή κειμένου / Ctrl+C", padding=6)
        messages.pack(fill="x", pady=(8, 0))
        self.message_text = tk.Text(messages, height=5, wrap="word", font=(self.font_family, 10), state="disabled")
        message_scroll = ttk.Scrollbar(messages, orient="vertical", command=self.message_text.yview)
        self.message_text.configure(yscrollcommand=message_scroll.set)
        message_scroll.pack(side="right", fill="y")
        self.message_text.pack(fill="both", expand=True)
        self.message_text.tag_configure("error", foreground="#a82f2f")
        self.message_text.bind("<Control-a>", self._select_messages)

    def _select_messages(self, _event=None):
        self.message_text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy_error(self):
        if self.last_error:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_error)

    def _copy_messages(self):
        content = self.message_text.get("1.0", "end-1c")
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)

    def _clear_messages(self):
        self.message_text.configure(state="normal")
        self.message_text.delete("1.0", "end")
        self.message_text.configure(state="disabled")
        self.last_error = ""
        self.messages_menu.entryconfigure(0,state="disabled")

    def _message(self, message, error=False):
        self.status.set(message)
        self.message_text.configure(state="normal")
        self.message_text.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n", ("error",) if error else ())
        # Bound widget size without discarding the last error used by its copy button.
        if int(self.message_text.index("end-1c").split(".")[0]) > 500:
            self.message_text.delete("1.0", "101.0")
        self.message_text.configure(state="disabled")
        self.message_text.see("end")
        if error:
            self.last_error = message
            self.messages_menu.entryconfigure(0,state="normal")

    def _filter_clients(self):
        def normalize(value):
            return "".join(c for c in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(c))
        query = normalize(self.client_filter.get().strip())
        matches = [c for c in self.clients.values() if query in normalize(c.name) or query in c.vat]
        self.client_tree.delete(*self.client_tree.get_children())
        for client in matches:
            self.client_tree.insert("", "end", iid=client.id, text=client.name, values=(client.vat,))
        if self.selected and self.client_tree.exists(self.selected):
            self.client_tree.selection_set(self.selected)
        self.client_count_label.set(f"Πελάτες: {len(matches)} / {len(self.clients)}")

    def _set_busy(self, value):
        self.busy = value
        for widget in (self.sync_button,self.pdf_button,self.browser_pdf_button,self.open_pdf_button,self.open_folder_button,
                       self.reset_downloads_button,self.delete_archive_button,
                       self.from_entry,self.to_entry,self.search_entry,self.previous,self.next):
            widget.configure(state="disabled" if value else "normal")
        for index in (0,1):
            self.main_menu.entryconfigure(index,state="disabled" if value else "normal")
        self.client_tree.configure(selectmode="none" if value else "browse")
        if value:
            self.stop_button.pack(anchor="e",before=self.progress)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.stop_button.configure(state="disabled")
            self.stop_button.pack_forget()
            self._paging_buttons()

    def _submit(self, operation, completion, on_error=None):
        if self.busy:
            return
        self._set_busy(True)
        self.operation_stage = "local"
        self.status.set("Εργασία σε εξέλιξη…")
        def worker():
            try:
                result = operation()
            except ApplicationError as error:
                record_failure(self.db.path.parent / "diagnostics.jsonl", error, self.operation_stage)
                self.queue.put(("error", (f"[{self.operation_stage}] {error}", on_error)))
            except Exception as error:
                # Never put credentials, URLs or raw exception details into GUI/logs.
                record_failure(self.db.path.parent / "diagnostics.jsonl", error, self.operation_stage)
                self.queue.put(("error", (f"[{self.operation_stage}] Εσωτερικό σφάλμα εφαρμογής. Η προηγούμενη πρόοδος διατηρείται. Καταγράφηκε διάγνωση στο diagnostics.jsonl.", on_error)))
            else:
                self.queue.put(("done", (completion, result)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "browser_notice":
                    self._message(payload)
                elif kind == "pdf_progress":
                    done, total = payload
                    self.status.set(f"Έλεγχος συνδέσμων PDF: {done} / {total}")
                elif kind == "progress":
                    pages, records = payload
                    self.status.set(f"Αποθηκευμένες σελίδες: {pages} · Εγγραφές που επεξεργάστηκαν: {records}")
                elif kind == "done":
                    self._set_busy(False)
                    completion, result = payload
                    completion(result)
                    # A completion may have changed pagination totals.
                    if not self.busy:
                        self._set_busy(False)
                else:
                    self._set_busy(False)
                    message, handler = payload
                    self._message(message, error=True)
                    if handler:
                        handler(message)
        except queue.Empty:
            pass
        if not self.closed:
            self.poll_id = self.root.after(50, self._poll)

    def _destroy(self, event):
        if event.widget is self.root and not self.closed:
            self.closed = True
            self.cancel.set()
            self.credentials.clear()
            self.root.after_cancel(self.poll_id)

    def _view(self, client_id):
        offsets = dict(self.offsets)
        pages, totals = {}, {}
        for bucket in offsets:
            total = self.db.count(client_id, self.dates, bucket=bucket)
            actual_offset = min(offsets[bucket], ((total - 1) // PAGE_SIZE) * PAGE_SIZE) if total else 0
            pages[bucket] = (self.db.invoices(client_id, actual_offset, PAGE_SIZE, self.dates, bucket=bucket), actual_offset)
            totals[bucket] = total
        return (pages, totals,
                self.db.checkpoint(client_id), self.db.events(client_id),
                self.db.change_count(client_id), self.db.changes(client_id),self.db.pdf_stats(client_id,self.dates))

    def refresh(self):
        if self.busy:
            return
        selected = self.selected
        def work():
            clients = self.db.clients()
            client_id = selected if any(c.id == selected for c in clients) else (clients[0].id if clients else None)
            return clients, client_id, self._view(client_id) if client_id else None
        def done(result):
            clients, client_id, view = result
            self.clients = {c.id: c for c in clients}
            self.selected = client_id
            self._filter_clients()
            if client_id:
                self._display(view)
            self.status.set("Η τοπική λίστα ανανεώθηκε.")
        self._submit(work, done)

    def _select(self, _event=None):
        if self.busy:
            return
        selection = self.client_tree.selection()
        if not selection or selection[0] == self.selected:
            return
        self.selected = selection[0]
        self._reset_pages()
        client_id = self.selected
        self._submit(lambda: self._view(client_id), self._display)

    def _display(self, view):
        pages, self.bucket_totals, checkpoint, events, change_count, changes, stats = view
        self.stat_total.set(str(stats['total']))
        self.stat_saved.set(str(stats['saved']))
        self.stat_pending.set(str(stats['pending']))
        client = self.clients[self.selected]
        pending = " · Υπάρχει συνέχεια γενικού συγχρονισμού" if checkpoint.active else ""
        period_label = (f" · Περίοδος: {self.dates.start or 'αρχή'} — {self.dates.end or 'τέλος'}"
                        if self.dates.start or self.dates.end else " · Όλες οι ημερομηνίες")
        self.summary.set(f"{client.name} · ΑΦΜ {client.vat} · {ENVIRONMENTS[client.environment]}\n"
                         f"Παραστατικά: {stats['total']} · Μεταβολές: {change_count} · MARK γενικού συγχρονισμού: {checkpoint.cursor}{pending}{period_label}")
        methods = {"saved_unverified": "Αρχικό PDF", "imported_unverified": "Εισαγωγή PDF",
                   "printed_unverified": "Εκτύπωση σε PDF"}
        for bucket, (rows, offset) in pages.items():
            tree = self.document_trees[bucket]
            selection = tree.selection()
            tree.delete(*tree.get_children())
            self.offsets[bucket] = offset
            self.visible_by_bucket[bucket] = {str(row['mark']): row for row in rows}
            for row in rows:
                state = row["pdf_state"]
                status = ("Ακυρωμένο (myDATA)" if row["cancelled"] else state_label(state) if row["pdf_saved"]
                          else "Αποτυχημένη λήψη / εκκρεμεί" if row["pdf_attempted"] else STATUS[row["status"]])
                reason = state_label(state) if row["pdf_attempted"] and not row["pdf_saved"] else ""
                tree.insert("", "end", iid=str(row['mark']), values=(
                    row["issue_date"], row["mark"], row["issuer_vat"] or "Άγνωστο",
                    f'{row["series"] or ""} / {row["number"] or ""}',
                    row["total"] if row["total"] is not None else "—", methods.get(state, "—"), status, reason),
                    tags=("cancelled",) if row["cancelled"] else ())
            tree.selection_set([item for item in selection if tree.exists(item)])
        self.total = self.bucket_totals[self.document_bucket]
        self.visible_documents = self.visible_by_bucket[self.document_bucket]
        self.history_tree.delete(*self.history_tree.get_children())
        for row in events:
            kind={'period_page_committed':'Άντληση περιόδου','pdf_downloads_reset':'Μηδενισμός λήψεων'}.get(row['kind'],'Αποθήκευση σελίδας')
            self.history_tree.insert("", "end", values=(row["at"], kind, row["record_count"]))
        self.changes_tree.delete(*self.changes_tree.get_children())
        for row in changes:
            self.changes_tree.insert("", "end", values=(CHANGE_LABELS[row["kind"]],
                row["event_mark"] if row["event_mark"] is not None else "Δεν δόθηκε",
                row["invoice_mark"], row["cancellation_date"] or "—"))
        self._page_status()
        self._paging_buttons()

    def _page(self, direction):
        if self.busy or not self.selected or self.document_book.select() not in self.tab_buckets:
            return
        self.offset = max(0, self.offset + direction * PAGE_SIZE)
        client_id = self.selected
        self._submit(lambda: self._view(client_id), self._display)

    def _sync(self):
        if self.busy or not self.selected:
            return
        try:
            dates = DateRange.parse(self.date_from.get(), self.date_to.get())
            dates.period
        except ApplicationError as error:
            self._message(str(error), error=True)
            return
        if self.demo and dates.period:
            self._message("Το demo δεν υποστηρίζει άντληση περιόδου. Επιλέξτε Όλες οι ημερομηνίες.", error=True)
            return
        self.dates = dates
        client = self.clients[self.selected]
        if self.demo:
            if client.vat != "000000000":
                self._message("Το OFFLINE DEMO έχει δεδομένα μόνο για τον συνθετικό πελάτη 000000000. Για ΑΑΔΕ ανοίξτε το run_live.bat.", error=True)
                return
            api = DemoAPI()
        else:
            environment = Environment[client.environment.upper()]
            api = None
        self.cancel.clear()
        client_id = client.id
        self._reset_pages()
        def work():
            selected_api = api
            if not self.demo:
                self.operation_stage = "credentials"
                credentials = self.credentials.get(client)
                if credentials is None:
                    raise ApplicationError("Ορίστε πρώτα AADE User ID και Subscription key στα Στοιχεία πελάτη.")
                selected_api = RequestDocsAPI(credentials, environment)
            result = Synchronizer(self.db, selected_api).run(client_id, self.cancel,
                lambda pages, records: self.queue.put(("progress", (pages, records))),
                lambda stage: setattr(self, "operation_stage", stage), dates=dates)
            return result, self._view(client_id)
        def done(payload):
            result, view = payload
            self.last_result = result
            self._display(view)
            label = "Διακόπηκε με ασφάλεια" if result.cancelled else "Ο συγχρονισμός ολοκληρώθηκε"
            self._message(f"{label} · Σελίδες: {result.pages} · Εγγραφές που επεξεργάστηκαν: {result.records}. "
                            "Η λίστα είναι έτοιμη. Συνεχίστε με Λήψη PDF.")
        self._submit(work, done)
        self.stop_button.configure(state="normal")

    def _previous_month(self):
        dates=previous_month()
        self.date_from.set(dates.start.strftime('%d/%m/%Y'))
        self.date_to.set(dates.end.strftime('%d/%m/%Y'))
        self._apply_dates()

    def _choose_destination(self):
        if self.busy or not self.selected:
            return
        folder=filedialog.askdirectory(title="Φάκελος αποθήκευσης PDF",parent=self.root)
        if folder:
            self.download_roots[self.selected]=folder

    def _apply_dates(self):
        if self.busy:
            return
        try:
            dates = DateRange.parse(self.date_from.get(), self.date_to.get())
        except ApplicationError as error:
            self._message(str(error), error=True)
            return
        self.dates = dates
        self._reset_pages()
        if self.selected:
            self._submit(lambda: self._view(self.selected), self._display)

    def _clear_dates(self):
        if not self.busy:
            self.date_from.set("")
            self.date_to.set("")
            self._apply_dates()

    def _selected_document(self):
        bucket = self.tab_buckets.get(self.document_book.select())
        selection = self.document_trees[bucket].selection() if bucket else ()
        if len(selection) != 1:
            self._message("Επιλέξτε ακριβώς ένα παραστατικό στη λίστα.", error=True)
            return None
        return self.visible_by_bucket[bucket].get(selection[0])

    def _open_provider(self):
        if self.busy or not self.selected:
            return
        row = self._selected_document()
        if row is None:
            return
        if self.demo:
            self._message("Το demo δεν ανοίγει παρόχους.")
            return
        client = self.clients[self.selected]
        mark = row['mark']
        self._submit(lambda: open_provider(self.db, client, mark),
            lambda _result: self._message(f"Άνοιξε ο σύνδεσμος του παραστατικού MARK {mark} στον browser."))

    def _open_saved_pdf(self):
        if self.busy or not self.selected:
            return
        row = self._selected_document()
        if row is None:
            return
        client = self.clients[self.selected]
        def show(path):
            self.pdf_preview = PDFPreview(self.root, path, f"myInvoices · ΑΦΜ {client.vat} · MARK {row['mark']}",
                                          on_error=lambda message: self._message(message, error=True))
        self._submit(lambda: saved_pdf_path(self.db, client, row['mark']), show)

    def _open_archive_folder(self):
        if self.busy or not self.selected:
            return
        client = self.clients[self.selected]
        configured = self.download_roots.get(client.id)
        def work():
            root = Path(configured) if configured else desktop_directory()
            folder = client_archive_folder(root, client)
            open_directory(folder)
            return str(root), folder
        def done(result):
            root, folder = result
            self.download_roots[client.id] = root
            self._message(f"Φάκελος τιμολογίων: {folder}")
        self._submit(work, done)

    def _import_pdf(self):
        if self.busy or not self.selected:
            return
        row = self._selected_document()
        if row is None:
            return
        if self.demo:
            self._message("Το demo δεν εισάγει πραγματικά PDF.")
            return
        client = self.clients[self.selected]
        source = filedialog.askopenfilename(title=f"PDF για MARK {row['mark']}",
            filetypes=[("PDF", "*.pdf")], parent=self.root)
        if not source:
            return
        if not messagebox.askyesno("Αντιστοίχιση PDF", f"Πελάτης: {client.name} · ΑΦΜ {client.vat}\n"
            f"MARK: {row['mark']} · Προμηθευτής: {row['issuer_vat'] or 'Άγνωστος'}\n"
            f"Ημερομηνία: {row['issue_date']} · Σειρά/αριθμός: {row['series'] or ''}/{row['number'] or ''}\n\n"
            "Το επιλεγμένο PDF αφορά αυτό το παραστατικό; Το αρχικό αρχείο θα διατηρηθεί.", parent=self.root):
            return
        configured = self.download_roots.get(client.id)
        self.cancel.clear()
        def work():
            self.operation_stage = "pdf_import"
            destination = Path(configured) if configured else desktop_directory()
            path = import_browser_pdf(self.db, client, row['mark'], source, destination, self.cancel)
            return path, self._view(client.id), str(destination)
        def done(payload):
            path, view, destination = payload
            self.download_roots[client.id] = destination
            self._display(view)
            self._message(f"PDF για MARK {row['mark']} αποθηκεύτηκε: {path}" if path else "Η εισαγωγή διακόπηκε.")
        self._submit(work, done)
        self.stop_button.configure(state="normal")

    def _reset_downloads(self):
        if self.busy or not self.selected:
            return
        client=self.clients[self.selected]
        if not messagebox.askyesno('Μηδενισμός λήψεων',
                f'Πελάτης: {client.name} · ΑΦΜ {client.vat}\n\n'
                'Θα μηδενιστούν οι καταστάσεις λήψης PDF για όλες τις ημερομηνίες αυτού του πελάτη. '
                'Τα υπάρχοντα PDF, τα παραστατικά ΑΑΔΕ και τα credentials διατηρούνται.\n\n'
                'Η νέα λήψη θα ξαναδοκιμάσει και τα ήδη αποθηκευμένα. Αν υπάρχει ίδιο όνομα αρχείου, '
                'θα δημιουργηθεί νέο αρχείο χωρίς αντικατάσταση.\n\nΣυνέχεια;',
                parent=self.root,default='no',icon='warning'):
            return
        self._reset_pages()
        def work():
            count=self.db.reset_downloads(client.id)
            return count,self._view(client.id)
        def done(payload):
            count,view=payload
            self._display(view)
            self._message(f'Μηδενίστηκαν οι λήψεις για {count} παραστατικά του ΑΦΜ {client.vat}. '
                          'Πατήστε Λήψη PDF για νέα λήψη στην επιλεγμένη περίοδο.')
        self._submit(work,done)

    def _delete_archive(self):
        if self.busy or not self.selected:
            return
        client=self.clients[self.selected]
        configured=self.download_roots.get(client.id)
        def prepare():
            root=Path(configured) if configured else desktop_directory()
            folder=removable_client_folder(root,client)
            return root,folder,folder.exists()
        def review(payload):
            root,folder,exists=payload
            operation=('Ολόκληρος ο παρακάτω φάκελος θα μεταφερθεί στον Κάδο ανακύκλωσης.' if exists else
                       'Ο παρακάτω φάκελος δεν υπάρχει. Θα μηδενιστούν οι λήψεις αυτού του πελάτη.')
            if not messagebox.askyesno('Διαγραφή φακέλου πελάτη',
                    f'Πελάτης: {client.name} · ΑΦΜ {client.vat}\n\n{folder}\n\n{operation}\n\n'
                    'Μηδενίζονται επίσης όλες οι καταστάσεις λήψης του πελάτη, για νέα λήψη από την αρχή. '
                    'Η εγγραφή πελάτη, τα credentials και τα παραστατικά ΑΑΔΕ διατηρούνται.\n\nΣυνέχεια;',
                    parent=self.root,default='no',icon='warning'):
                return
            self._reset_pages()
            def work():
                result=trash_client_archive(self.db,client,root)
                return result,self._view(client.id)
            def done(result):
                (path,moved,count),view=result
                self._display(view)
                self._message((f'Ο φάκελος μεταφέρθηκε στον Κάδο: {path}.' if moved else 'Ο φάκελος δεν υπήρχε.')+
                              f' Μηδενίστηκαν οι λήψεις για {count} παραστατικά του πελάτη.')
            self._submit(work,done)
        self._submit(prepare,review)

    def _download_pdfs(self):
        if self.busy or not self.selected:
            return
        if self.demo:
            self._message("Το demo δεν πραγματοποιεί λήψεις από παρόχους.")
            return
        try:
            self.dates = DateRange.parse(self.date_from.get(), self.date_to.get())
        except ApplicationError as error:
            self._message(str(error), error=True)
            return
        self._reset_pages()
        client = self.clients[self.selected]
        configured = self.download_roots.get(client.id)
        self.cancel.clear()
        def work():
            self.operation_stage = "pdf"
            destination = Path(configured) if configured else desktop_directory()
            notice = lambda message: self.queue.put(("browser_notice", message))
            downloader = AutomaticPDFDownloader(self.db,direct=PDFDownloader(self.db),notice=notice)
            result = downloader.run(client, destination, self.cancel,
                lambda done, total: self.queue.put(("pdf_progress", (done, total))), dates=self.dates)
            return result, self._view(client.id), str(destination)
        def done(payload):
            result, view, destination = payload
            self.download_roots[client.id] = destination
            self._display(view)
            label = "Η λήψη διακόπηκε" if result['cancelled'] else "Η λήψη ολοκληρώθηκε"
            reasons = " · ".join(f"{state_label(state)}: {count}" for state, count in sorted(result['reasons'].items()))
            message = (f"{label} · Νέα PDF: {result['saved']} · Ήδη αποθηκευμένα: {result['skipped']} · "
                       f"Εκκρεμή σε αυτή την εργασία: {result['pending']}. Φάκελος: {destination}.")
            if result.get('printed'):
                message += f" Από εκτύπωση: {result['printed']}."
            if reasons:
                message += f" Αιτίες: {reasons}."
            self._message(message)
        self._submit(work, done)
        self.stop_button.configure(state="normal")

    def _check_api(self):
        if self.busy:
            return
        if self.demo:
            self._message("Ο πραγματικός έλεγχος API δεν εκτελείται στο OFFLINE DEMO. Ανοίξτε START_MYINVOICES.bat.", error=True)
            return
        if not self.selected:
            self._message("Επιλέξτε πρώτα πελάτη για έλεγχο σύνδεσης API.", error=True)
            return
        client_id = self.selected
        self._submit(lambda: check_client(self.db, self.credentials, client_id,
            stage=lambda stage: setattr(self, "operation_stage", stage)), self._message)

    def _stop(self):
        self.cancel.set()
        self.status.set("Ζητήθηκε διακοπή. Αναμονή ολοκλήρωσης τρέχοντος αιτήματος/συναλλαγής…")
        self.stop_button.configure(state="disabled")

    def _client_form(self):
        if self.busy:
            return
        self._show_client_form(None, None)

    def _edit_client(self):
        if self.busy or not self.selected:
            return
        client = self.clients[self.selected]
        def load():
            editable = self.db.environment_editable(client.id)
            return self.credentials.get(client), editable
        self._submit(load,
                     lambda payload: self._show_client_form(client, payload[0], environment_editable=payload[1]),
                     lambda message: self._show_client_form(client, None, message))

    def _show_client_form(self, client, saved_credentials, initial_error="", environment_editable=False):
        window = tk.Toplevel(self.root)
        window.title("Στοιχεία πελάτη" if client else "Νέος πελάτης")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        name = tk.StringVar(value=client.name if client else "")
        vat = tk.StringVar(value=client.vat if client else "")
        environment = tk.StringVar(value=client.environment if client else ("development" if self.demo else "production"))
        user = tk.StringVar(value=saved_credentials.user_id if saved_credentials else "")
        key = tk.StringVar(value=saved_credentials.subscription_key if saved_credentials else "")
        representative = tk.BooleanVar(value=bool(saved_credentials and saved_credentials.entity_vat))
        error_text = tk.StringVar(value=initial_error)
        for index, (label, variable) in enumerate((("Όνομα", name), ("ΑΦΜ (9 ψηφία)", vat),
                                                   ("AADE User ID", user), ("Subscription key", key))):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=5)
            entry = ttk.Entry(frame, textvariable=variable, width=42, show="•" if index == 3 else "")
            entry.grid(row=index, column=1, padx=8)
            if index == 1:
                vat_field = entry
            if index == 1 and client:
                entry.configure(state="readonly")
            if self.demo and index >= 2:
                entry.configure(state="disabled")
        radio_state = "disabled" if (client and not environment_editable) or self.demo else "normal"
        ttk.Radiobutton(frame, text="Δοκιμαστικό ΑΑΔΕ", variable=environment, value="development", state=radio_state).grid(row=4, column=0)
        if not self.demo:
            ttk.Radiobutton(frame, text="Παραγωγή ΑΑΔΕ", variable=environment, value="production", state=radio_state).grid(row=4, column=1)
        ttk.Checkbutton(frame, text="Χρησιμοποιώ κλειδιά λογιστή / εκπροσώπου για άλλον ΑΦΜ",
            variable=representative, state="disabled" if self.demo else "normal").grid(row=5, column=0, columnspan=2, pady=6)
        persistence = ("Windows: τα credentials αποθηκεύονται κρυπτογραφημένα για τον τρέχοντα χρήστη."
                       if self.credentials.persistent else "Σε αυτό το σύστημα: credentials μόνο στη μνήμη, μέχρι να κλείσει η εφαρμογή.")
        ttk.Label(frame, text=persistence, wraplength=580).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Με κλειδιά του ίδιου του πελάτη αφήστε την επιλογή εκπροσώπησης κλειστή.\nΚανονικό myDATA: Παραγωγή ΑΑΔΕ. Dev portal: Δοκιμαστικό ΑΑΔΕ.\nΤο ΑΦΜ μένει σταθερό. Αλλαγή περιβάλλοντος επιτρέπεται πριν τον πρώτο αποθηκευμένο συγχρονισμό.",
                  wraplength=580).grid(row=7, column=0, columnspan=2, sticky="w", pady=5)
        if self.demo:
            ttk.Label(frame, text="OFFLINE DEMO: τα API credentials δεν χρησιμοποιούνται.").grid(row=8, column=0, columnspan=2)
        ttk.Label(frame, textvariable=error_text, wraplength=580).grid(row=9, column=0, columnspan=2)
        def close():
            if self.busy:
                return
            user.set("")
            key.set("")
            window.destroy()
            self.refresh()
        window.protocol("WM_DELETE_WINDOW", close)
        def save():
            values = name.get(), vat.get(), environment.get()
            try:
                credentials = None if self.demo else Credentials(user.get().strip(), key.get().strip(),
                                                                vat.get().strip() if representative.get() else None)
            except ApplicationError as error:
                error_text.set(str(error))
                self._message(str(error), error=True)
                return
            def work():
                updated = (self.db.configure_client(client.id, values[0], values[2]) if client
                           else self.db.add_client(*values))
                if credentials:
                    try:
                        self.credentials.set(updated, credentials)
                    except ApplicationError as error:
                        return updated, str(error)
                return updated, None
            def done(payload):
                nonlocal client
                client, error = payload
                vat.set(client.vat)
                environment.set(client.environment)
                vat_field.configure(state="readonly")
                for control in frame.winfo_children():
                    if isinstance(control, ttk.Radiobutton):
                        control.configure(state="disabled")
                if error:
                    error_text.set("Ο πελάτης αποθηκεύτηκε, αλλά όχι τα credentials. " + error)
                    return
                close()
            self._submit(work, done, error_text.set)
        ttk.Button(frame, text="Αποθήκευση πελάτη και credentials", command=save).grid(row=10, column=1, pady=12)

    def _close(self):
        if self.busy:
            self._stop()
            messagebox.showinfo("Εργασία σε εξέλιξη", "Ζητήθηκε διακοπή. Περιμένετε να τελειώσει η εργασία και κλείστε ξανά.", parent=self.root)
            return
        self.credentials.clear()
        self.root.destroy()
