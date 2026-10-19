"""One download action: direct retrieval, then browser only for page-based sources."""
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
        self.browser_factory = browser_factory or (lambda: BrowserPDFDownloader(database,notice=notice))
        self.browser = None
        self.browser_unavailable = False

    def run(self, client, destination, cancel, progress=lambda done, total: None, dates=None):
        self.browser = None
        self.browser_unavailable = False
        with ExitStack() as stack:
            self.stack = stack
            return super().run(client,destination,cancel,progress,dates)

    def _download(self, client, row, root, cancel):
        try:
            return self.direct._download(client,row,root,cancel)
        except Pending as error:
            if error.state not in BROWSER_STATES:
                raise
        if cancel.is_set():
            return None
        self.notice(f"MARK {row['mark']}: η πηγή είναι ιστοσελίδα· συνεχίζω αυτόματα μέσω browser.")
        if self.browser_unavailable:
            raise Pending('browser_unavailable')
        if self.browser is None:
            try:
                browser = self.browser_factory()
                self.stack.enter_context(browser.session())
            except ApplicationError:
                self.browser_unavailable = True
                raise Pending('browser_unavailable') from None
            self.browser = browser
        return self.browser._download(client,row,root,cancel)
