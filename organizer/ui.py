"""Presentation helpers; no file operations or optional dependencies."""
import os
import tkinter as tk
from tkinter import font, ttk

COLORS = {
    'background': '#F3F6FA', 'surface': '#FFFFFF', 'ink': '#172B46',
    'muted': '#53657C', 'line': '#DFE6EF', 'blue': '#205BCD',
    'blue_hover': '#1748A5', 'blue_light': '#EAF1FF', 'success': '#146448',
    'success_bg': '#E8F6EF', 'warning': '#835000', 'warning_bg': '#FFF3D9',
    'error': '#B02B36', 'error_bg': '#FCECEE',
}

HELP = {
    'sources': 'Επιλέξτε έναν ή περισσότερους φακέλους ή δίσκους. Η αναζήτηση περιλαμβάνει και τους υποφακέλους τους.',
    'add': 'Προσθέτει φάκελο στη λίστα. Για ολόκληρο δίσκο, επιλέξτε τη ρίζα του, π.χ. C:\\.',
    'remove': 'Αφαιρεί μόνο την επιλεγμένη πηγή από τη λίστα σάρωσης. Δεν διαγράφει αρχεία.',
    'destination': 'Εδώ θα δημιουργηθούν οι φάκελοι ανά τύπο αρχείου και έτος. Ο προορισμός εξαιρείται από τη σάρωση.',
    'copy': 'Δημιουργεί οργανωμένα αντίγραφα, διατηρώντας τα αρχικά αρχεία.',
    'move': 'Μεταφέρει τα αρχεία στους νέους φακέλους και αφαιρεί τα αρχικά μετά την επιτυχή μεταφορά.',
    'scan': 'Αναζητά αρχεία στους επιλεγμένους φακέλους χωρίς να τα τροποποιεί.',
    'preview': 'Εμφανίζει πού θα τοποθετηθεί κάθε αρχείο πριν εκτελεστεί η οργάνωση. Ελέγξτε όλες τις σελίδες.',
    'execute': 'Εκτελεί το σχέδιο της προεπισκόπησης, μετά από επιβεβαίωση. Τα υπάρχοντα αρχεία δεν αντικαθίστανται.',
    'cancel': 'Σταματά τη διαδικασία χωρίς να αναιρεί ενέργειες που έχουν ήδη ολοκληρωθεί.',
    'history': 'Εμφανίζει τις πρόσφατες ενέργειες και τυχόν σφάλματα. Το πλήρες ιστορικό εκτέλεσης αποθηκεύεται σε αρχείο.',
    'open_history': 'Ανοίγει τον φάκελο με τα πλήρη αρχεία ιστορικού εκτέλεσης.',
    'pagination': 'Μετακινηθείτε στις σελίδες για να ελέγξετε όλα τα αρχεία. Κάθε σελίδα εμφανίζει έως 250 εγγραφές.',
    'details': 'Επιλέξτε μία γραμμή για να δείτε και να αντιγράψετε ολόκληρες τις διαδρομές της.',
}


def format_size(value):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB', 'PB'):
        if value < 1024 or unit == 'PB':
            return f'{value:,.0f} B'.replace(',', '.') if unit == 'B' else f'{value:.1f} {unit}'.replace('.', ',')
        value /= 1024


def format_count(value):
    return f'{value:,}'.replace(',', '.')


def tooltip_position(anchor, size, screen, gap=8):
    """Prefer below the anchor, then above; clamp to the visible screen."""
    x, y, width, height = anchor
    tip_width, tip_height = size
    screen_width, screen_height = screen
    left = max(4, min(x, screen_width - tip_width - 4))
    top = y + height + gap
    if top + tip_height > screen_height - 4:
        top = y - tip_height - gap
    return left, max(4, min(top, screen_height - tip_height - 4))


def enable_dpi_awareness():
    """Called before creating Tk. Older Windows builds keep their default."""
    if os.name == 'nt':
        import ctypes
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def configure_style(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    family = 'Segoe UI' if os.name == 'nt' else 'DejaVu Sans'
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        font.nametofont(name).configure(family=family, size=10)
    root.configure(background=COLORS['background'])
    style.configure('.', background=COLORS['background'], foreground=COLORS['ink'], font='TkDefaultFont')
    style.configure('TFrame', background=COLORS['background'])
    style.configure('Card.TFrame', background=COLORS['surface'])
    style.configure('TLabel', background=COLORS['background'])
    style.configure('Card.TLabel', background=COLORS['surface'])
    style.configure('Muted.TLabel', background=COLORS['surface'], foreground=COLORS['muted'])
    style.configure('Title.TLabel', font=(family, 23, 'bold'))
    style.configure('Section.TLabel', background=COLORS['surface'], font=(family, 12, 'bold'))
    style.configure('Metric.TLabel', background=COLORS['surface'], font=(family, 18, 'bold'))
    style.configure('Step.TLabel', padding=(12, 8), foreground=COLORS['muted'])
    style.configure('Active.Step.TLabel', background=COLORS['blue_light'], foreground=COLORS['blue'], font=(family, 10, 'bold'))
    style.configure('TButton', background='#EBF0F7', foreground=COLORS['ink'], padding=(12, 9), borderwidth=0, focusthickness=2, focuscolor=COLORS['blue'])
    style.map('TButton', background=[('disabled', '#F0F2F5'), ('pressed', '#CDD8E7'), ('active', '#DCE5F1')], foreground=[('disabled', '#8591A3')])
    style.configure('Primary.TButton', background=COLORS['blue'], foreground='white', font=(family, 10, 'bold'))
    style.map('Primary.TButton', background=[('disabled', '#E3E9F2'), ('pressed', '#123A89'), ('active', COLORS['blue_hover'])], foreground=[('disabled', '#78869B'), ('!disabled', 'white')])
    style.configure('Cancel.TButton', foreground=COLORS['error'])
    style.configure('TRadiobutton', background=COLORS['surface'], padding=(4, 7), focuscolor=COLORS['blue'])
    style.map('TRadiobutton', background=[('active', COLORS['blue_light'])])
    style.configure('TEntry', fieldbackground='white', padding=7, bordercolor=COLORS['line'])
    style.map('TEntry', fieldbackground=[('readonly', '#F5F8FC')])
    style.configure('Treeview', background='white', fieldbackground='white', foreground=COLORS['ink'], borderwidth=0,
                    rowheight=font.nametofont('TkDefaultFont').metrics('linespace') + 16)
    style.configure('Treeview.Heading', background='#EFF3F9', foreground=COLORS['muted'], padding=(10, 10), font=(family, 10, 'bold'), relief='flat')
    style.map('Treeview', background=[('selected', COLORS['blue_light'])], foreground=[('selected', COLORS['ink'])])
    style.configure('Horizontal.TProgressbar', background=COLORS['blue'], troughcolor='#E1E8F2', borderwidth=0, thickness=10)
    for level, fg, bg in [('info', COLORS['blue'], COLORS['blue_light']), ('success', COLORS['success'], COLORS['success_bg']),
                          ('warning', COLORS['warning'], COLORS['warning_bg']), ('error', COLORS['error'], COLORS['error_bg'])]:
        style.configure(f'{level}.Badge.TLabel', background=bg, foreground=fg, padding=(10, 5), font=(family, 10, 'bold'))
    return style


class Tooltip:
    """Delayed hover and keyboard focus help; Escape and destruction clean up."""
    def __init__(self, widget, text, delay=400):
        self.widget, self.text, self.delay = widget, text, delay
        self.pending = None
        self.window = None
        self.hovered = False
        self.focused = False
        widget.bind('<Enter>', self._enter, add='+')
        widget.bind('<Leave>', self._leave, add='+')
        widget.bind('<FocusIn>', self._focus_in, add='+')
        widget.bind('<FocusOut>', self._focus_out, add='+')
        widget.bind('<Escape>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def _enter(self, event=None):
        self.hovered = True
        self.schedule()

    def _leave(self, event=None):
        self.hovered = False
        if not self.focused:
            self.hide()

    def _focus_in(self, event=None):
        self.focused = True
        self.widget.configure(highlightbackground=COLORS['blue'])
        self.schedule()

    def _focus_out(self, event=None):
        self.focused = False
        self.widget.configure(highlightbackground=COLORS['line'])
        if not self.hovered:
            self.hide()

    def schedule(self):
        self.hide()
        self.pending = self.widget.after(self.delay, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        screen = (self.widget.winfo_screenwidth(), self.widget.winfo_screenheight())
        tk.Label(self.window, text=self.text, background='#172B46', foreground='white',
                 font='TkDefaultFont', padx=12, pady=10, justify='left', wraplength=min(340, screen[0] - 40)).pack()
        self.window.update_idletasks()
        position = tooltip_position((self.widget.winfo_rootx(), self.widget.winfo_rooty(), self.widget.winfo_width(), self.widget.winfo_height()),
                                    (self.window.winfo_reqwidth(), self.window.winfo_reqheight()), screen)
        self.window.geometry(f'+{position[0]}+{position[1]}')
        self.window.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def help_icon(parent, key):
    widget = tk.Label(parent, text='?', background=COLORS['blue_light'], foreground=COLORS['blue'],
                      width=2, font='TkDefaultFont', cursor='question_arrow', takefocus=True, highlightthickness=1,
                      highlightbackground=COLORS['line'], highlightcolor=COLORS['blue'])
    widget.tooltip = Tooltip(widget, HELP[key])
    return widget
