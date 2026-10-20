"""Small native Tk components: tokens, contextual help and read-only details."""
import tkinter as tk
from tkinter import ttk
from .presentation import display_date, display_amount, guidance, PROVENANCE

SPACING = 8
INK = '#243746'
MUTED = '#4b5d6b'
ACCENT = '#165d96'


def configure_style(root, family):
    style=ttk.Style(root)
    native='vista' in style.theme_names()
    if native:style.theme_use('vista')
    elif 'clam' in style.theme_names():style.theme_use('clam')
    style.configure('.',font=(family,10))
    style.configure('Treeview',rowheight=28)
    style.configure('Treeview.Heading',font=(family,10,'bold'))
    style.configure('TButton',padding=(8,4))
    style.configure('Primary.TButton',font=(family,10,'bold'))
    style.map('Treeview',background=[('selected','#244d70')],foreground=[('selected','white')])
    if not native:
        style.configure('.',background='#f4f7fa',foreground=INK)
        style.configure('Treeview',background='white',fieldbackground='white')
        style.configure('Treeview.Heading',background='#e7edf3')
        style.configure('Primary.TButton',background=ACCENT,foreground='white')
        style.map('Primary.TButton',background=[('disabled','#dde4eb'),('active','#124e7e')],
                  foreground=[('disabled','#607080'),('active','white')])
    style.configure('Context.TLabel',foreground=INK,font=(family,11,'bold'))
    style.configure('Muted.TLabel',foreground=MUTED)
    # Native themes retain their keyboard focus/hover/disabled rendering.
    return style


class Tooltip:
    def __init__(self, widget, text):
        self.widget,self.text,self.window,self.timer=widget,text,None,None
        widget.bind('<Enter>',self.schedule,add='+')
        widget.bind('<FocusIn>',self.schedule,add='+')
        for event in ('<Leave>','<FocusOut>','<ButtonPress>','<Destroy>'):
            widget.bind(event,self.hide,add='+')
    def schedule(self,_event=None):
        self.hide();self.timer=self.widget.after(650,self.show)
    def show(self):
        self.timer=None
        if not self.widget.winfo_exists():return
        self.window=tk.Toplevel(self.widget);self.window.wm_overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()+8}+{self.widget.winfo_rooty()+self.widget.winfo_height()+4}')
        ttk.Label(self.window,text=self.text,padding=8,wraplength=380,relief='solid').pack()
    def hide(self,_event=None):
        if self.timer:
            self.widget.after_cancel(self.timer);self.timer=None
        if self.window:
            self.window.destroy();self.window=None


class DetailsPanel(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.text=tk.Text(self,height=6,wrap='word',state='disabled',takefocus=True,borderwidth=0,font='TkTextFont')
        scroll=ttk.Scrollbar(self,command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.text.bind('<Control-a>',lambda _e:self.select_all())
        self.clear()
    def select_all(self):
        self.text.tag_add('sel','1.0','end-1c');return 'break'
    def clear(self,message='Επιλέξτε ένα παραστατικό για αιτία, προέλευση και επόμενη ενέργεια.'):
        self.set_text(message)
    def set_text(self,text):
        self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('end',text);self.text.configure(state='disabled')
    def show(self,row,count,attempts):
        from .pdf_downloads import state_label
        title,action=guidance(row)
        provenance=PROVENANCE.get(row.get('pdf_state'),'Δεν έχει αποθηκευτεί PDF')
        lines=[f"MARK {row['mark']} · {display_date(row['issue_date'])} · Προμηθευτής {row.get('issuer_vat') or '—'} · {display_amount(row.get('total'))}",
               f"Σειρά / αριθμός: {row.get('series') or '—'} / {row.get('number') or '—'} · Προέλευση: {provenance}",
               f"Κατάσταση: {title}. {state_label(row['pdf_state']) if row.get('pdf_attempted') and not row.get('pdf_saved') else ''}",
               f"Επόμενη ενέργεια: {action}",
               f"Τοπικό αρχείο: {row.get('pdf_path') or 'Δεν υπάρχει καταγεγραμμένο αρχείο'}",
               f"Σύνδεσμος παρόχου: {row.get('download_url') or 'Δεν επιστράφηκε'}",
               f"Ιστορικό: {count} συμβάντα · εμφανίζονται έως 20 νεότερα (UTC)."]
        lines.extend(f"{a['at']} · {state_label(a['state']) if a['state']!='downloads_reset' else 'Μηδενισμός λήψεων'}" for a in attempts)
        self.set_text('\n'.join(lines))
