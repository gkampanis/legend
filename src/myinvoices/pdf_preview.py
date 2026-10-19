"""Read-only PDF rendering and a local Tk viewer. No browser or network access."""
from dataclasses import dataclass
from contextlib import closing
import math
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

from .models import ApplicationError

_PDFIUM_LOCK = threading.Lock()  # PDFium must not be called concurrently.
MAX_PIXELS = 8_000_000
MAX_SIDE = 4096


@dataclass(frozen=True)
class RenderedPage:
    ppm: bytes
    width: int
    height: int
    count: int
    index: int
    zoom: float


def render_pdf_page(path, index=0, zoom=1.0, cancel=None):
    """Open/render/close one page in the worker; retain no native handles in Tk."""
    try:
        import pypdfium2 as pdfium
    except (ImportError, OSError):
        raise ApplicationError('Εκτελέστε INSTALL_BROWSER.bat ή INSTALL.bat για την προβολή PDF.') from None
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 40 * 1024 * 1024:
            raise ValueError()
        if not math.isfinite(zoom) or not 0.5 <= zoom <= 2.5:
            raise ValueError()
        with _PDFIUM_LOCK:
            if cancel and cancel.is_set():
                return None
            with pdfium.PdfDocument(str(path)) as document:
                count = len(document)
                if not 0 < count <= 500 or not 0 <= index < count:
                    raise ValueError()
                document.init_forms()
                with closing(document[index]) as page:
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                        raise ValueError()
                    scale = min(1.25 * zoom, MAX_SIDE / max(width, height),
                                math.sqrt(MAX_PIXELS / (width * height)))
                    with closing(page.render(scale=scale, rev_byteorder=True,
                                             force_bitmap_format=pdfium.raw.FPDFBitmap_BGR)) as bitmap:
                        if cancel and cancel.is_set():
                            return None
                        width, height = bitmap.width, bitmap.height
                        buffer = memoryview(bitmap.buffer).cast('B')
                        pixels = b''.join(bytes(buffer[y * bitmap.stride:y * bitmap.stride + width * 3])
                                          for y in range(height))
                        ppm = f'P6\n{width} {height}\n255\n'.encode('ascii') + pixels
                        return RenderedPage(ppm, width, height, count, index, zoom)
    except Exception:
        if cancel and cancel.is_set():
            return None
        raise ApplicationError('Δεν μπορεί να προβληθεί το PDF. Ελέγξτε ότι υπάρχει και ανοίγει κανονικά.') from None


class PDFPreview:
    def __init__(self, parent, path, title, on_error=lambda message: None):
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry('960x800')
        self.window.minsize(600, 450)
        self.cancel = threading.Event()
        self.queue = queue.Queue()
        self.path = Path(path)
        self.on_error = on_error
        self.index, self.count, self.zoom = 0, 0, 1.0
        self.photo = None
        self.closed = False
        self.busy = False
        self.poll_id = None
        bar = ttk.Frame(self.window, padding=8)
        bar.pack(fill='x')
        self.previous = ttk.Button(bar, text='◀ Προηγούμενη', command=lambda: self._load(self.index - 1, self.zoom))
        self.next = ttk.Button(bar, text='Επόμενη ▶', command=lambda: self._load(self.index + 1, self.zoom))
        self.smaller = ttk.Button(bar, text='−', width=4, command=lambda: self._load(self.index, round(self.zoom - 0.25, 2)))
        self.larger = ttk.Button(bar, text='+', width=4, command=lambda: self._load(self.index, round(self.zoom + 0.25, 2)))
        for button in (self.previous, self.next, self.smaller, self.larger):
            button.pack(side='left', padx=3)
        self.status = tk.StringVar(value='Φόρτωση PDF…')
        ttk.Label(bar, textvariable=self.status).pack(side='left', padx=12)
        body = ttk.Frame(self.window)
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, background='#525659', highlightthickness=0)
        vertical = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta / 120), 'units'))
        self.canvas.bind('<Button-4>', lambda _event: self.canvas.yview_scroll(-1, 'units'))
        self.canvas.bind('<Button-5>', lambda _event: self.canvas.yview_scroll(1, 'units'))
        self.window.bind('<Destroy>', self._destroy, add='+')
        self.poll_id = self.window.after(40, self._poll)
        self._load(0, 1.0)

    def _buttons(self):
        for button, enabled in ((self.previous, self.index > 0),
                                (self.next, self.index + 1 < self.count),
                                (self.smaller, self.zoom > 0.5), (self.larger, self.zoom < 2.5)):
            button.configure(state='normal' if enabled and not self.busy else 'disabled')

    def _load(self, index, zoom):
        if self.closed or self.busy or index < 0 or (self.count and index >= self.count) or not 0.5 <= zoom <= 2.5:
            return
        self.busy = True
        self.status.set('Φόρτωση PDF…')
        self._buttons()
        def worker():
            try:
                page = render_pdf_page(self.path, index, zoom, self.cancel)
            except ApplicationError as error:
                self.queue.put(('error', str(error)))
            else:
                self.queue.put(('page', page))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        self.poll_id = None
        if self.closed:
            return
        try:
            kind, result = self.queue.get_nowait()
            self.busy = False
            if kind == 'error':
                self.status.set(result)
                self.on_error(result)
            elif result is not None:
                self.index, self.count, self.zoom = result.index, result.count, result.zoom
                self.photo = tk.PhotoImage(master=self.window, data=result.ppm, format='PPM')
                self.canvas.delete('all')
                self.canvas.create_image(10, 10, image=self.photo, anchor='nw')
                self.canvas.configure(scrollregion=(0, 0, result.width + 20, result.height + 20))
                self.canvas.xview_moveto(0)
                self.canvas.yview_moveto(0)
                self.status.set(f'Σελίδα {self.index + 1} / {self.count} · {self.zoom:.0%}')
            self._buttons()
        except queue.Empty:
            pass
        if not self.closed:
            self.poll_id = self.window.after(40, self._poll)

    def _destroy(self, event):
        if event.widget is self.window:
            self.closed = True
            self.cancel.set()
            if self.poll_id is not None:
                self.window.after_cancel(self.poll_id)
                self.poll_id = None
            self.photo = None
