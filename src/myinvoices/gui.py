"""Thin Tk view: all database/network work runs off the GUI thread."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .api import Environment, RequestDocsAPI
from .demo import DemoAPI
from .models import ApplicationError, Credentials
from .sync import Synchronizer


STATUS = {"url_available": "Υπάρχει link — PDF ανεξέλεγκτο", "no_url": "Χωρίς link PDF"}
ENVIRONMENTS = {"development": "Δοκιμαστικό ΑΑΔΕ", "production": "Παραγωγή ΑΑΔΕ"}
PAGE_SIZE = 200


class App:
    def __init__(self, root, database, demo=True):
        self.root, self.db, self.demo = root, database, demo
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.clients = {}
        self.selected = None
        self.offset = 0
        self.total = 0
        self.last_result = None
        self.status = tk.StringVar(value="Έτοιμο")
        self.summary = tk.StringVar(value="Επιλέξτε πελάτη")
        self.user = tk.StringVar()
        self.key = tk.StringVar()
        root.title("myInvoices Starter 0.1 — " + ("OFFLINE DEMO" if demo else "Σύνδεση ΑΑΔΕ"))
        root.geometry("1180x760")
        root.minsize(950, 600)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(50, self._poll)
        self.refresh()

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        title = "myInvoices — Δοκιμαστική έκδοση"
        ttk.Label(outer, text=title, font=("Segoe UI", 17, "bold")).pack(anchor="w")
        notice = ("OFFLINE DEMO: συνθετικά στοιχεία, χωρίς σύνδεση ή λήψη PDF."
                  if self.demo else
                  "Πειραματική σύνδεση ΑΑΔΕ: εκκρεμεί επαλήθευση τρέχοντος schema και live δοκιμή.")
        ttk.Label(outer, text=notice, wraplength=1050).pack(anchor="w", pady=(4, 10))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.add_button = ttk.Button(bar, text="Νέος πελάτης", command=self._client_form)
        self.add_button.pack(side="left")
        self.refresh_button = ttk.Button(bar, text="Ανανέωση", command=self.refresh)
        self.refresh_button.pack(side="left", padx=6)
        self.sync_button = ttk.Button(bar, text="Συγχρονισμός επιλεγμένου", command=self._sync)
        self.sync_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="Διακοπή", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        self.clear_button = ttk.Button(bar, text="Καθαρισμός credentials", command=self._clear_credentials)
        self.clear_button.pack(side="right")
        credentials = ttk.LabelFrame(outer, text="API credentials — μόνο στη μνήμη, όχι κωδικοί TAXISnet", padding=8)
        credentials.pack(fill="x", pady=10)
        ttk.Label(credentials, text="AADE User ID:").grid(row=0, column=0, sticky="w")
        self.user_entry = ttk.Entry(credentials, textvariable=self.user, width=28)
        self.user_entry.grid(row=0, column=1, padx=6, sticky="ew")
        ttk.Label(credentials, text="Subscription key:").grid(row=0, column=2)
        self.key_entry = ttk.Entry(credentials, textvariable=self.key, show="•", width=36)
        self.key_entry.grid(row=0, column=3, padx=6, sticky="ew")
        credentials.columnconfigure(1, weight=1)
        credentials.columnconfigure(3, weight=1)
        if self.demo:
            self.user_entry.configure(state="disabled")
            self.key_entry.configure(state="disabled")
        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        left, right = ttk.Frame(body), ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=4)
        ttk.Label(left, text="Πελάτες / ΑΦΜ").pack(anchor="w")
        self.client_tree = ttk.Treeview(left, columns=("vat",), show="tree headings", selectmode="browse")
        self.client_tree.heading("#0", text="Πελάτης")
        self.client_tree.heading("vat", text="ΑΦΜ")
        self.client_tree.column("#0", width=170)
        self.client_tree.column("vat", width=100)
        self.client_tree.pack(fill="both", expand=True)
        self.client_tree.bind("<<TreeviewSelect>>", self._select)
        ttk.Label(right, textvariable=self.summary, wraplength=750).pack(anchor="w", pady=(0, 8))
        book = ttk.Notebook(right)
        book.pack(fill="both", expand=True)
        documents, history = ttk.Frame(book), ttk.Frame(book)
        book.add(documents, text="Παραστατικά")
        book.add(history, text="Ιστορικό συγχρονισμού")
        columns = ("date", "mark", "supplier", "identity", "total", "status")
        self.document_tree = ttk.Treeview(documents, columns=columns, show="headings")
        labels = ("Ημερομηνία", "MARK", "ΑΦΜ προμηθευτή", "Σειρά / Αριθμός", "Σύνολο", "Κατάσταση PDF")
        for column, label in zip(columns, labels):
            self.document_tree.heading(column, text=label)
            self.document_tree.column(column, width=130 if column != "status" else 260, minwidth=90)
        vertical = ttk.Scrollbar(documents, orient="vertical", command=self.document_tree.yview)
        horizontal = ttk.Scrollbar(documents, orient="horizontal", command=self.document_tree.xview)
        self.document_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.document_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        documents.rowconfigure(0, weight=1)
        documents.columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(history, columns=("at", "kind", "count"), show="headings")
        for column, label in (("at", "Χρόνος UTC"), ("kind", "Ενέργεια"), ("count", "Εγγραφές σελίδας")):
            self.history_tree.heading(column, text=label)
        self.history_tree.pack(fill="both", expand=True)
        paging = ttk.Frame(right)
        paging.pack(fill="x", pady=6)
        self.previous = ttk.Button(paging, text="Προηγούμενα", command=lambda: self._page(-1))
        self.previous.pack(side="left")
        self.next = ttk.Button(paging, text="Επόμενα", command=lambda: self._page(1))
        self.next.pack(side="left", padx=6)
        ttk.Label(paging, text="Προβολή έως 200 εγγραφών ανά σελίδα").pack(side="right")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 4))
        ttk.Label(outer, textvariable=self.status, wraplength=1050).pack(anchor="w")

    def _clear_credentials(self):
        self.user.set("")
        self.key.set("")

    def _set_busy(self, value):
        self.busy = value
        for widget in (self.add_button, self.refresh_button, self.sync_button, self.previous, self.next):
            widget.configure(state="disabled" if value else "normal")
        self.client_tree.configure(selectmode="none" if value else "browse")
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.stop_button.configure(state="disabled")
            self.previous.configure(state="normal" if self.offset > 0 else "disabled")
            self.next.configure(state="normal" if self.offset + PAGE_SIZE < self.total else "disabled")

    def _submit(self, operation, completion):
        if self.busy:
            return
        self._set_busy(True)
        self.status.set("Εργασία σε εξέλιξη…")
        def worker():
            try:
                result = operation()
            except ApplicationError as error:
                self.queue.put(("error", str(error)))
            except Exception:
                # Never put credentials, URLs or raw exception details into GUI/logs.
                self.queue.put(("error", "Η εργασία απέτυχε. Οι προηγούμενες αποθηκευμένες σελίδες διατηρούνται."))
            else:
                self.queue.put(("done", (completion, result)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
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
                    self.status.set(payload)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _view(self, client_id, offset):
        return (self.db.invoices(client_id, offset, PAGE_SIZE), self.db.count(client_id),
                self.db.checkpoint(client_id), self.db.events(client_id))

    def refresh(self):
        if self.busy:
            return
        selected, offset = self.selected, self.offset
        def work():
            clients = self.db.clients()
            client_id = selected if any(c.id == selected for c in clients) else (clients[0].id if clients else None)
            return clients, client_id, self._view(client_id, offset) if client_id else None
        def done(result):
            clients, client_id, view = result
            self.clients = {c.id: c for c in clients}
            self.selected = client_id
            self.client_tree.delete(*self.client_tree.get_children())
            for client in clients:
                self.client_tree.insert("", "end", iid=client.id, text=client.name, values=(client.vat,))
            if client_id:
                self.client_tree.selection_set(client_id)
                self._display(view)
            self.status.set("Η τοπική λίστα ανανεώθηκε.")
        self._submit(work, done)

    def _select(self, _event=None):
        if self.busy:
            return
        selection = self.client_tree.selection()
        if not selection or selection[0] == self.selected:
            return
        self.selected, self.offset = selection[0], 0
        self._clear_credentials()
        client_id = self.selected
        self._submit(lambda: self._view(client_id, 0), self._display)

    def _display(self, view):
        rows, self.total, checkpoint, events = view
        client = self.clients[self.selected]
        pending = " · Υπάρχει συνέχεια προς άντληση" if checkpoint.active else ""
        self.summary.set(f"{client.name} · ΑΦΜ {client.vat} · {ENVIRONMENTS[client.environment]}\n"
                         f"Παραστατικά: {self.total} · Ολοκληρωμένο MARK: {checkpoint.cursor}{pending}")
        self.document_tree.delete(*self.document_tree.get_children())
        for row in rows:
            self.document_tree.insert("", "end", values=(
                row["issue_date"], row["mark"], row["issuer_vat"] or "Άγνωστο",
                f'{row["series"] or ""} / {row["number"] or ""}',
                row["total"] if row["total"] is not None else "—", STATUS[row["status"]]))
        self.history_tree.delete(*self.history_tree.get_children())
        for row in events:
            self.history_tree.insert("", "end", values=(row["at"], "Αποθήκευση σελίδας", row["record_count"]))
        self.status.set(f"Προβολή {self.offset + 1 if self.total else 0}–{min(self.offset + PAGE_SIZE, self.total)} από {self.total}.")

    def _page(self, direction):
        if self.busy or not self.selected:
            return
        self.offset = max(0, self.offset + direction * PAGE_SIZE)
        client_id, offset = self.selected, self.offset
        self._submit(lambda: self._view(client_id, offset), self._display)

    def _sync(self):
        if self.busy or not self.selected:
            return
        client = self.clients[self.selected]
        if self.demo:
            api = DemoAPI()
        else:
            try:
                credentials = Credentials(self.user.get().strip(), self.key.get().strip())
            except ApplicationError as error:
                self.status.set(str(error))
                return
            environment = Environment[client.environment.upper()]
            if not messagebox.askyesno("Επιβεβαίωση σύνδεσης ΑΑΔΕ",
                f"Θα γίνουν πραγματικά, μόνο αναγνωστικά αιτήματα στο {ENVIRONMENTS[client.environment]}.\n"
                f"Πελάτης: {client.name} · ΑΦΜ: {client.vat}\n\n"
                "Τα credentials πρέπει να ανήκουν σε αυτόν τον πελάτη. "
                "Η διαλειτουργικότητα με το τρέχον schema δεν έχει ακόμη επαληθευτεί. Συνέχεια;",
                parent=self.root):
                return
            api = RequestDocsAPI(credentials, environment)
            self._clear_credentials()
        self.cancel.clear()
        client_id = client.id
        self.offset = 0
        def work():
            result = Synchronizer(self.db, api).run(client_id, self.cancel,
                lambda pages, records: self.queue.put(("progress", (pages, records))))
            return result, self._view(client_id, 0)
        def done(payload):
            result, view = payload
            self.last_result = result
            self._display(view)
            label = "Διακόπηκε με ασφάλεια" if result.cancelled else "Ο συγχρονισμός ολοκληρώθηκε"
            self.status.set(f"{label} · Σελίδες: {result.pages} · Εγγραφές που επεξεργάστηκαν: {result.records}. "
                            "Δεν έγινε λήψη ή επαλήθευση PDF.")
        self._submit(work, done)
        self.stop_button.configure(state="normal")

    def _stop(self):
        self.cancel.set()
        self.status.set("Ζητήθηκε διακοπή. Αναμονή ολοκλήρωσης τρέχοντος αιτήματος/συναλλαγής…")
        self.stop_button.configure(state="disabled")

    def _client_form(self):
        if self.busy:
            return
        window = tk.Toplevel(self.root)
        window.title("Νέος πελάτης")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        name, vat = tk.StringVar(), tk.StringVar()
        environment = tk.StringVar(value="development")
        for index, (label, variable) in enumerate((("Όνομα", name), ("ΑΦΜ (9 ψηφία)", vat))):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=variable, width=35).grid(row=index, column=1, padx=8)
        ttk.Radiobutton(frame, text="Δοκιμαστικό ΑΑΔΕ", variable=environment, value="development").grid(row=2, column=0)
        if not self.demo:
            ttk.Radiobutton(frame, text="Παραγωγή ΑΑΔΕ", variable=environment, value="production").grid(row=2, column=1)
        if self.demo:
            ttk.Label(frame, text="Το demo διαθέτει δεδομένα μόνο για ΑΦΜ 000000000.").grid(row=3, column=0, columnspan=2)
        def save():
            values = name.get(), vat.get(), environment.get()
            def done(_client):
                window.destroy()
                self.refresh()
            self._submit(lambda: self.db.add_client(*values), done)
        ttk.Button(frame, text="Αποθήκευση", command=save).grid(row=4, column=1, pady=12)

    def _close(self):
        if self.busy:
            self._stop()
            messagebox.showinfo("Εργασία σε εξέλιξη", "Ζητήθηκε διακοπή. Περιμένετε να τελειώσει η εργασία και κλείστε ξανά.", parent=self.root)
            return
        self._clear_credentials()
        self.root.destroy()
