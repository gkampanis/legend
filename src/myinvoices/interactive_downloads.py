"""Second pass only: the real default browser, human challenge, new-file confirmation."""
from pathlib import Path
import time

from .models import ApplicationError
from .pdf_downloads import (PDFDownloader, Pending, MAX_BYTES, open_provider,
                            valid_pdf, archive_pdf_bytes)


class InteractivePDFDownloader(PDFDownloader):
    def __init__(self, database, download_folder, *, confirm, notice=lambda message: None,
                 browser=None, timeout=300, stable_seconds=1, poll_seconds=0.25):
        super().__init__(database)
        self.folder = Path(download_folder)
        self.confirm, self.notice, self.browser = confirm, notice, browser
        self.timeout, self.stable_seconds, self.poll_seconds = timeout, stable_seconds, poll_seconds
        if not self.folder.is_dir():
            raise ApplicationError('Επιλέξτε τον φάκελο όπου ο browser αποθηκεύει τις λήψεις του.')

    def _candidates(self, client_id, dates=None):
        return self.db.pending_candidates(client_id,dates)

    def _snapshot(self):
        result={}
        for path in self.folder.iterdir():
            try:
                if path.suffix.lower()=='.pdf' and path.is_file() and not path.is_symlink():
                    stat=path.stat()
                    result[path]=(stat.st_size,stat.st_mtime_ns)
            except OSError:
                continue
        return result

    def _download(self, client, row, root, cancel):
        baseline=self._snapshot()
        try:
            open_provider(self.db,client,row['mark'],browser=self.browser)
        except ApplicationError:
            raise Pending('browser_closed') from None
        self.notice(f"MARK {row['mark']}: ολοκληρώστε τον έλεγχο στον κανονικό browser και κατεβάστε το PDF "
                    f"στον επιλεγμένο φάκελο λήψεων. Η εφαρμογή περιμένει το νέο αρχείο.")
        deadline=time.monotonic()+self.timeout
        last_signature=None
        stable_since=time.monotonic()
        while time.monotonic()<deadline:
            if cancel.wait(self.poll_seconds):
                return None
            snapshot=self._snapshot()
            changed={p:s for p,s in snapshot.items() if baseline.get(p)!=s}
            if not changed:
                continue
            if len(changed)>1:
                raise Pending('ambiguous_document')
            path,signature=next(iter(changed.items()))
            if signature!=last_signature:
                last_signature=signature
                stable_since=time.monotonic()
                continue
            if time.monotonic()-stable_since<self.stable_seconds:
                continue
            if signature[0]>MAX_BYTES:
                raise Pending('too_large')
            try:
                if not valid_pdf(path):
                    continue
                with path.open('rb') as stream:
                    data=stream.read(MAX_BYTES+1)
                if path.stat().st_size!=signature[0] or path.stat().st_mtime_ns!=signature[1]:
                    last_signature=None
                    continue
            except OSError:
                continue
            # A normal browser can have other simultaneous downloads; confirmation is mandatory.
            if not self.confirm(row,path):
                if cancel.is_set():
                    return None
                raise Pending('browser_confirmation')
            return archive_pdf_bytes(data,client,row,root,cancel)
        raise Pending('browser_timeout')
