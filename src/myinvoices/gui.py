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
from .browser_recovery import RecoveryPDFDownloader
from .ui_actions import WorkspaceActions
from .presentation import display_date, display_amount, PROVENANCE
from .settings import UISettings
from .batch import BatchProgress, save_batch, last_batch, batch_description


STATUS = {"url_available": "Υπάρχει link — PDF ανεξέλεγκτο", "no_url": "Χωρίς link PDF"}
ENVIRONMENTS = {"development": "Δοκιμαστικό ΑΑΔΕ", "production": "Παραγωγή ΑΑΔΕ"}
PAGE_SIZE = 200
CHANGE_LABELS = {"cancellation": "Ακύρωση", "income_classification": "Χαρακτηρισμός εσόδων",
                 "expense_classification": "Χαρακτηρισμός εξόδων", "payment": "Πληρωμή"}


class App(WorkspaceActions):
    def __init__(self, root, database, demo=False, credential_store=None, today=None):
        self.root, self.db, self.demo = root, database, demo
        families={family.casefold() for family in tkfont.families(root)}
        self.font_family = 'Segoe UI' if 'segoe ui' in families else 'DejaVu Sans' if 'dejavu sans' in families else 'Helvetica'
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
        self.settings = UISettings(database.path.parent / 'ui-settings.json')
        self.settings_loaded = False
        self.recovery_total = 0
        self.invoice_search = tk.StringVar()
        self.supplier_filter = tk.StringVar()
        self.reason_filter = tk.StringVar(value='Όλες οι αιτίες')
        self.provenance_filter = tk.StringVar(value='Κάθε προέλευση')
        self.status_filter = tk.StringVar(value='Όλες οι καταστάσεις')
        self.destination_text = tk.StringVar(value='Προορισμός: φόρτωση…')
        self.metrics_text = tk.StringVar()
        self.batch_summary = tk.StringVar(value='Δεν έχει εκτελεστεί παρτίδα σε αυτή τη συνεδρία.')
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
        root.geometry("1280x800")
        root.minsize(980, 640)
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
        self._document_selection()

    def _paging_buttons(self):
        active = self.document_book.select() in self.tab_buckets
        self.previous.configure(state="normal" if active and not self.busy and self.offset > 0 else "disabled")
        self.next.configure(state="normal" if active and not self.busy and self.offset + PAGE_SIZE < self.total else "disabled")

    def _page_status(self):
        self.status.set(f"Προβολή {self.offset + 1 if self.total else 0}–{min(self.offset + PAGE_SIZE, self.total)} από {self.total}.")

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
                       self.reset_downloads_button,self.delete_archive_button,self.recovery_button,self.export_button,
                       self.invoice_search_entry,self.supplier_entry,
                       self.from_entry,self.to_entry,self.search_entry,self.previous,self.next):
            widget.configure(state="disabled" if value else "normal")
        for combo in self.filter_combos:
            combo.configure(state='disabled' if value else 'readonly')
        for index in (0,1):
            self.main_menu.entryconfigure(index,state="disabled" if value else "normal")
        self.client_tree.configure(selectmode="none" if value else "browse")
        if value:
            self.stop_button.pack(side="right",padx=6)
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.stop_button.configure(state="disabled")
            self.stop_button.pack_forget()
            self._paging_buttons()
            self._update_recovery_scope()
            if not self.selected:
                for button in (self.sync_button,self.pdf_button,self.browser_pdf_button,self.open_pdf_button,self.open_folder_button,self.export_button):
                    button.configure(state='disabled')

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
                if kind == 'details':
                    generation,client_id,mark,result=payload
                    if generation==self.details_generation and client_id==self.selected:
                        if result:self.details.show(*result)
                        else:self.details.clear('Δεν φορτώθηκαν οι λεπτομέρειες. Επιλέξτε ξανά την εγγραφή.')
                elif kind == 'batch_progress':
                    self.status.set(payload.text())
                    if payload.total is not None:
                        self.progress.stop();self.progress.configure(mode='determinate',maximum=max(payload.total,1),value=payload.completed)
                elif kind == "browser_notice":
                    self._message(payload)
                elif kind == "pdf_progress":
                    done, total = payload
                    # Structured batch events provide counters/current item.
                    pass
                elif kind == "progress":
                    pages, records = payload
                    self.status.set(f"Άντληση ΑΑΔΕ · Σύνολο σελίδων άγνωστο · Αποθηκευμένες: {pages} · Εγγραφές: {records}")
                elif kind == "done":
                    self._set_busy(False)
                    completion, result = payload
                    completion(result)
                    # A completion may have changed pagination totals.
                    if not self.closed and not self.busy:
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
            for timer in (self.filter_timer,self.preferences_timer):
                if timer:self.root.after_cancel(timer)

    def _view(self, client_id):
        offsets = dict(self.offsets)
        pages, totals = {}, {}
        for bucket in offsets:
            total = self.db.count(client_id, self.dates, bucket=bucket, table=self.table_query)
            actual_offset = min(offsets[bucket], ((total - 1) // PAGE_SIZE) * PAGE_SIZE) if total else 0
            pages[bucket] = (self.db.invoices(client_id, actual_offset, PAGE_SIZE, self.dates, bucket=bucket, table=self.table_query), actual_offset)
            totals[bucket] = total
        return (pages, totals,
                self.db.checkpoint(client_id), self.db.events(client_id),
                self.db.change_count(client_id), self.db.changes(client_id),self.db.pdf_stats(client_id,self.dates),
                self.db.recovery_count(client_id,self.dates,self.table_query),
                str(client_archive_folder(Path(self.download_roots[client_id]) if client_id in self.download_roots else desktop_directory(),self.db.client(client_id))),last_batch(self.db.path.parent,client_id))

    def refresh(self):
        if self.busy:
            return
        selected = self.selected
        def work():
            config = self.settings.load() if not self.settings_loaded else None
            if config:
                self.download_roots.update(config.get('destinations',{}))
            clients = self.db.clients()
            client_id = selected if any(c.id == selected for c in clients) else (clients[0].id if clients else None)
            return clients, client_id, self._view(client_id) if client_id else None, config
        def done(result):
            clients, client_id, view, config = result
            if not self.settings_loaded:
                self._apply_preferences(config or {})
                self.settings_loaded = True
            self.clients = {c.id: c for c in clients}
            self.selected = client_id
            self._filter_clients()
            if client_id:
                self._display(view)
                self.status.set("Η τοπική λίστα ανανεώθηκε.")
            else:
                self.summary.set('Δεν υπάρχει πελάτης. Επιλέξτε Πελάτης → Νέος πελάτης για να ξεκινήσετε.')
                self.destination_text.set('Η αποθήκευση θα οργανωθεί ανά ΑΦΜ πελάτη και μήνα.')
                self.metrics_text.set('Παραστατικά 0 · Αποθηκευμένα 0')
                self.status.set('Προσθέστε τον πρώτο πελάτη από το μενού Πελάτης.')
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
        pages, self.bucket_totals, checkpoint, events, change_count, changes, stats, self.recovery_total, destination, previous_batch = view
        self.stat_total.set(str(stats['total']))
        self.stat_saved.set(str(stats['saved']))
        self.stat_pending.set(str(stats['pending']))
        self.metrics_text.set(f"Παραστατικά {stats['total']} · Αποθηκευμένα {stats['saved']} · Εκκρεμή browser {stats['pending']}")
        self.destination_text.set('Προορισμός: '+destination)
        self.last_batch_record=previous_batch
        self.last_batch_message=None
        self.batch_summary.set(batch_description(previous_batch))
        client = self.clients[self.selected]
        pending = " · Υπάρχει συνέχεια γενικού συγχρονισμού" if checkpoint.active else ""
        period_label = (f" · Περίοδος: {display_date(str(self.dates.start)) if self.dates.start else 'αρχή'} — {display_date(str(self.dates.end)) if self.dates.end else 'τέλος'}"
                        if self.dates.start or self.dates.end else " · Όλες οι ημερομηνίες")
        environment = ('[ΔΟΚΙΜΑΣΤΙΚΟ ΑΑΔΕ]' if client.environment=='development' else 'Παραγωγή ΑΑΔΕ')
        self.summary.set(f"{client.name} · ΑΦΜ {client.vat} · {environment}")
        methods = PROVENANCE
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
                    display_date(row["issue_date"]), row["mark"], row["issuer_vat"] or "Άγνωστο",
                    f'{row["series"] or ""} / {row["number"] or ""}',
                    display_amount(row["total"]), methods.get(state, "—"), status, reason),
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
        self._document_selection()

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
            save_batch(self.db.path.parent,client_id,'sync',dict(pages=result.pages,records=result.records,cancelled=result.cancelled))
            return result, self._view(client_id)
        def done(payload):
            result, view = payload
            self.last_result = result
            self._display(view)
            label = "Διακόπηκε με ασφάλεια" if result.cancelled else "Ο συγχρονισμός ολοκληρώθηκε"
            self.batch_summary.set(batch_description(self.last_batch_record))
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
            client=self.clients[self.selected]
            def validate():
                path=Path(folder)
                if not path.is_absolute() or not path.is_dir():
                    raise ApplicationError('Ο φάκελος δεν είναι διαθέσιμος. Επιλέξτε άλλον.')
                config=self.settings.load()
                config.setdefault('destinations',{})[client.id]=str(path)
                self.settings.save(config)
                return str(client_archive_folder(path,client))
            def done(destination):
                self.download_roots[client.id]=folder
                self.destination_text.set('Προορισμός: '+destination)
            self._submit(validate,done)

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

    def _download_pdfs(self,recovery=False):
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
        selected_marks=tuple(int(mark) for mark in self.failure_tree.selection())
        recovery_marks=tuple(mark for mark in selected_marks if self.visible_by_bucket['failure'].get(str(mark),{}).get('download_url')) or None
        recovery_count=len(recovery_marks or ()) if selected_marks else self.recovery_total
        if recovery:
            if not recovery_count:
                self._message('Δεν υπάρχουν επιλέξιμες εκκρεμότητες στο επιλεγμένο εύρος.')
                return
            if not selected_marks and recovery_count>1 and not messagebox.askyesno('Εύρος ανάκτησης',
                    f'Πελάτης: {client.name} · ΑΦΜ {client.vat}\n{recovery_count} φιλτραρισμένες εκκρεμότητες της περιόδου.\n'
                    'Θα ανοίξουν διαδοχικά στον browser ανάκτησης. Συνέχεια;',parent=self.root,default='no'):
                return
        self.cancel.clear()
        def work():
            self.operation_stage = "pdf"
            destination = Path(configured) if configured else desktop_directory()
            notice = lambda message: self.queue.put(("browser_notice", message))
            downloader = (RecoveryPDFDownloader(self.db,notice=notice,marks=recovery_marks,table=self.table_query) if recovery else
                          AutomaticPDFDownloader(self.db,direct=PDFDownloader(self.db),notice=notice))
            downloader.observer=lambda event:self.queue.put(('batch_progress',event))
            result = downloader.run(client, destination, self.cancel,
                lambda done, total: self.queue.put(("pdf_progress", (done, total))), dates=self.dates)
            save_batch(self.db.path.parent,client.id,'recovery' if recovery else 'download',result)
            return result, self._view(client.id), str(destination)
        def done(payload):
            result, view, destination = payload
            self.download_roots[client.id] = destination
            self._display(view)
            label = "Η λήψη διακόπηκε" if result['cancelled'] else "Η λήψη ολοκληρώθηκε"
            if recovery:
                label='Η ανάκτηση διακόπηκε' if result['cancelled'] else 'Η ανάκτηση ολοκληρώθηκε'
            reasons = " · ".join(f"{state_label(state)}: {count}" for state, count in sorted(result['reasons'].items()))
            message = (f"{label} · Νέα PDF: {result['saved']} · Ήδη αποθηκευμένα: {result['skipped']} · "
                       f"Εκκρεμή σε αυτή την εργασία: {result['pending']}. Φάκελος: {destination}.")
            if result.get('printed'):
                message += f" Από εκτύπωση: {result['printed']}."
            if reasons:
                message += f" Αιτίες: {reasons}."
            self.last_batch_message=message
            self.batch_summary.set(batch_description(self.last_batch_record))
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
        self._save_preferences(close_after=True)
