"""Thin Tk view: all database/network work runs off the GUI thread."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .api import Environment, RequestDocsAPI
from .demo import DemoAPI
from .credential_store import default_store
from .diagnostics import record_failure
from .models import ApplicationError, Credentials
from .sync import Synchronizer


STATUS = {"url_available": "Υπάρχει link — PDF ανεξέλεγκτο", "no_url": "Χωρίς link PDF"}
ENVIRONMENTS = {"development": "Δοκιμαστικό ΑΑΔΕ", "production": "Παραγωγή ΑΑΔΕ"}
PAGE_SIZE = 200


class App:
    def __init__(self, root, database, demo=False, credential_store=None):
        self.root, self.db, self.demo = root, database, demo
        self.credentials = credential_store or default_store(database.path.parent / "credentials")
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.clients = {}
        self.selected = None
        self.offset = 0
        self.total = 0
        self.last_result = None
        self.operation_stage = "local"
        self.status = tk.StringVar(value="Έτοιμο")
        self.summary = tk.StringVar(value="Επιλέξτε πελάτη")
        root.title("myInvoices Starter 0.2.2 — " + ("OFFLINE DEMO" if demo else "Στοιχεία πελατών / ΑΑΔΕ"))
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
                  "Σύνδεση ΑΑΔΕ βάσει ERP API 2.0.2 — εκκρεμεί live δοκιμή και πλήρης υποστήριξη μεταβολών.")
        ttk.Label(outer, text=notice, wraplength=1050).pack(anchor="w", pady=(4, 10))
        if self.demo:
            ttk.Label(outer, text="Για ενεργά πεδία API: κλείστε το demo και ανοίξτε START_MYINVOICES.bat.",
                      wraplength=1050).pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.add_button = ttk.Button(bar, text="Νέος πελάτης", command=self._client_form)
        self.add_button.pack(side="left")
        self.edit_button = ttk.Button(bar, text="Στοιχεία πελάτη", command=self._edit_client)
        self.edit_button.pack(side="left", padx=6)
        self.refresh_button = ttk.Button(bar, text="Ανανέωση", command=self.refresh)
        self.refresh_button.pack(side="left", padx=6)
        self.sync_button = ttk.Button(bar, text="Συγχρονισμός επιλεγμένου", command=self._sync)
        self.sync_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="Διακοπή", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Label(outer, text="Τα API credentials και το περιβάλλον ορίζονται στα στοιχεία κάθε πελάτη.").pack(anchor="w", pady=10)
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

    def _set_busy(self, value):
        self.busy = value
        for widget in (self.add_button, self.edit_button, self.refresh_button, self.sync_button, self.previous, self.next):
            widget.configure(state="disabled" if value else "normal")
        self.client_tree.configure(selectmode="none" if value else "browse")
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.stop_button.configure(state="disabled")
            self.previous.configure(state="normal" if self.offset > 0 else "disabled")
            self.next.configure(state="normal" if self.offset + PAGE_SIZE < self.total else "disabled")

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
                    message, handler = payload
                    self.status.set(message)
                    if handler:
                        handler(message)
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
            if client.vat != "000000000":
                self.status.set("Το OFFLINE DEMO έχει δεδομένα μόνο για τον συνθετικό πελάτη 000000000. Για ΑΑΔΕ ανοίξτε το run_live.bat.")
                return
            api = DemoAPI()
        else:
            environment = Environment[client.environment.upper()]
            if not messagebox.askyesno("Επιβεβαίωση σύνδεσης ΑΑΔΕ",
                f"Θα γίνουν πραγματικά, μόνο αναγνωστικά αιτήματα στο {ENVIRONMENTS[client.environment]}.\n"
                f"Πελάτης: {client.name} · ΑΦΜ: {client.vat}\n\n"
                "Θα χρησιμοποιηθούν τα αποθηκευμένα credentials του πελάτη. "
                "Απαιτείται δικαίωμα πρόσβασης/εκπροσώπησης στην ΑΑΔΕ. Συνέχεια;",
                parent=self.root):
                return
            api = None
        self.cancel.clear()
        client_id = client.id
        self.offset = 0
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
                lambda stage: setattr(self, "operation_stage", stage))
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
        self._show_client_form(None, None)

    def _edit_client(self):
        if self.busy or not self.selected:
            return
        client = self.clients[self.selected]
        def load():
            editable = self.db.checkpoint(client.id).revision == 0 and self.db.count(client.id) == 0
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
        ttk.Checkbutton(frame, text="Εξουσιοδοτημένος λογιστής / εκπρόσωπος (entityVatNumber)",
            variable=representative, state="disabled" if self.demo else "normal").grid(row=5, column=0, columnspan=2, pady=6)
        persistence = ("Windows: τα credentials αποθηκεύονται κρυπτογραφημένα για τον τρέχοντα χρήστη."
                       if self.credentials.persistent else "Σε αυτό το σύστημα: credentials μόνο στη μνήμη, μέχρι να κλείσει η εφαρμογή.")
        ttk.Label(frame, text=persistence, wraplength=580).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Κλειδιά από το κανονικό myDATA: Παραγωγή ΑΑΔΕ. Κλειδιά από το dev portal: Δοκιμαστικό ΑΑΔΕ.\nΤο ΑΦΜ μένει σταθερό. Αλλαγή περιβάλλοντος επιτρέπεται πριν τον πρώτο αποθηκευμένο συγχρονισμό.",
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
