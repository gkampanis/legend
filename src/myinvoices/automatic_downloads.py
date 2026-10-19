"""Background first pass: direct/dynamic retrieval; human challenges remain pending."""
from contextlib import ExitStack

from .models import ApplicationError
from .pdf_downloads import PDFDownloader, Pending
from .browser_downloads import BrowserPDFDownloader

BROWSER_STATES = {'html_page', 'challenge', 'http_403', 'descriptor_limit', 'invalid_descriptor', 'browser_required'}


class AutomaticPDFDownloader(PDFDownloader):
    def __init__(self, database, *, notice=lambda message: None, direct=None, browser_factory=None):
        super().__init__(database)
        self.direct = direct or PDFDownloader(database)
        self.notice = notice
        self.browser_factory = browser_factory or (lambda: BrowserPDFDownloader(database,notice=notice,headless=True,timeout=15,allow_print=True))
        self.browser = None
        self.browser_unavailable = False

    def run(self, client, destination, cancel, progress=lambda done, total: None, dates=None):
        self.browser = None
        self.browser_unavailable = False
        with ExitStack() as stack:
            self.stack = stack
            return super().run(client,destination,cancel,progress,dates)

    def _download(self, client, row, root, cancel):
        import hashlib
        if row.get('pdf_state') in {'challenge','http_403'} and row.get('pdf_source_hash') == hashlib.sha256(row['download_url'].encode()).hexdigest():
            raise Pending('challenge')
        try:
            return self.direct._download(client,row,root,cancel)
        except Pending as error:
            if error.state in {'challenge','http_403'}:
                raise Pending('challenge') from None
            if error.state not in BROWSER_STATES:
                raise
        if cancel.is_set():
            return None
        self.notice(f"MARK {row['mark']}: ανάκτηση δυναμικής σελίδας στο παρασκήνιο.")
        if self.browser_unavailable:
            raise Pending('browser_unavailable')
        if self.browser is None:
            try:
                browser = self.browser_factory()
                self.stack.enter_context(browser.session())
            except ApplicationError:
                self.browser_unavailable = True
                self.notice('Δεν ξεκίνησε ο browser για τις δυναμικές σελίδες. Δεν έγινε εκτύπωση σε αυτές. '
                            'Τρέξτε INSTALL_BROWSER.bat για εγκατάσταση Chromium και δοκιμάστε ξανά.')
                raise Pending('browser_unavailable') from None
            self.browser = browser
        return self.browser._download(client,row,root,cancel)
