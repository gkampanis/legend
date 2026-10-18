import threading

from .models import SyncError, SyncResult
from .xml_parser import parse_response


class Synchronizer:
    def __init__(self, database, api, max_pages=1000):
        self.database = database
        self.api = api
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.max_pages = max_pages

    def run(self, client_id, cancel=None, progress=None, stage=None):
        cancel = cancel or threading.Event()
        client = self.database.client(client_id)
        if self.api.environment.name.lower() != client.environment:
            raise SyncError("Το περιβάλλον API δεν συμφωνεί με τον πελάτη.")
        checkpoint = self.database.checkpoint(client_id)
        pages = records = 0
        while pages < self.max_pages:
            if cancel.is_set():
                return SyncResult(pages, records, True)
            if stage:
                stage("request")
            raw = self.api.fetch(checkpoint.request_mark, checkpoint.continuation)
            if stage:
                stage("xml")
            page = parse_response(raw)
            if cancel.is_set():
                return SyncResult(pages, records, True)
            if stage:
                stage("database")
            checkpoint = self.database.commit_page(checkpoint, page)
            pages += 1
            records += len(page.invoices) + len(page.changes)
            if progress:
                progress(pages, records)
            if page.continuation is None:
                return SyncResult(pages, records)
        raise SyncError("Συμπληρώθηκε το όριο σελίδων. Η πρόοδος αποθηκεύτηκε· συνεχίστε με νέο συγχρονισμό.")
