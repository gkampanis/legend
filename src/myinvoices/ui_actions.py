"""Table, selection, settings and contextual UI behavior, separate from services."""
from dataclasses import replace
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .models import ApplicationError
from .presentation import TableQuery, REASONS, PROVENANCE
from .settings import COLUMNS
from .table_export import export_csv


class WorkspaceActions:
    def _build(self):
        from .ui_layout import build
        build(self)
        self.filter_timer = None
        self.preferences_timer = None
        self.details_generation = 0
        self.clicked_cell = None
        self.table_query = TableQuery()
        for variable in (self.invoice_search,self.supplier_filter,self.reason_filter,self.provenance_filter,self.status_filter):
            variable.trace_add('write',self._schedule_filter)

    def _schedule_filter(self,*_args):
        if self.filter_timer:self.root.after_cancel(self.filter_timer)
        self.filter_timer=self.root.after(300,self._apply_table_filters)

    def _apply_table_filters(self):
        self.filter_timer=None
        if self.busy or not self.selected:return
        reasons={title:group.value for group,(title,_) in REASONS.items()}
        provenance={v:k for k,v in PROVENANCE.items()}
        states={'Αποθηκευμένα':'saved','Δοκιμασμένα / εκκρεμή':'failed','Αδοκίμαστα':'new','Ακυρωμένα':'cancelled'}
        self.table_query=replace(self.table_query,search=self.invoice_search.get()[:200],supplier=self.supplier_filter.get()[:30],
            reason=reasons.get(self.reason_filter.get(),''),provenance=provenance.get(self.provenance_filter.get(),''),status=states.get(self.status_filter.get(),''))
        self._reset_pages();client_id=self.selected
        self._submit(lambda:self._view(client_id),self._display)

    def _reset_filters(self):
        if self.busy:return
        for variable,value in ((self.invoice_search,''),(self.supplier_filter,''),(self.reason_filter,'Όλες οι αιτίες'),
                               (self.provenance_filter,'Κάθε προέλευση'),(self.status_filter,'Όλες οι καταστάσεις')):variable.set(value)

    def _sort_table(self,column):
        if self.busy or not self.selected:return
        self.table_query=replace(self.table_query,sort=column,descending=not self.table_query.descending if self.table_query.sort==column else False)
        self._reset_pages();client=self.selected
        self._submit(lambda:self._view(client),self._display)

    def _active_tree(self):
        bucket=self.tab_buckets.get(self.document_book.select())
        return self.document_trees.get(bucket)

    def _clear_table_selection(self):
        tree=self._active_tree()
        if tree:tree.selection_remove(*tree.selection())

    def _document_selection(self,_event=None):
        tree=self._active_tree();selection=tree.selection() if tree else ()
        self.selection_text.set(f'{len(selection)} επιλεγμένα')
        self._update_recovery_scope()
        self.details_generation+=1;generation=self.details_generation
        if len(selection)!=1 or not self.selected:
            self.details.clear('Επιλεγμένα '+str(len(selection))+' παραστατικά.' if selection else 'Επιλέξτε ένα παραστατικό για λεπτομέρειες.')
            return
        client_id,mark=self.selected,int(selection[0])
        def work():
            try:result=self.db.invoice_details(client_id,mark)
            except Exception:result=None
            self.queue.put(('details',(generation,client_id,mark,result)))
        threading.Thread(target=work,daemon=True).start()

    def _update_recovery_scope(self):
        selected=self.failure_tree.selection()
        eligible=getattr(self,'recovery_total',0)
        marks=[int(x) for x in selected if self.visible_by_bucket['failure'].get(x,{}).get('download_url')]
        text=f'Ανάκτηση {len(marks)} επιλεγμένων' if selected else f'Ανάκτηση όλων των {eligible} εκκρεμών'
        self.recovery_button.configure(text=text,state='disabled' if self.busy or not (marks if selected else eligible) else 'normal')

    def _remember_cell(self,event):
        tree=event.widget;column=tree.identify_column(event.x);row=tree.identify_row(event.y)
        if row and column:self.clicked_cell=(tree,row,column)

    def _copy_cell(self,_event=None):
        tree=self._active_tree()
        if tree:
            selected=tree.selection()
            row=tree.focus() if tree.focus() in selected else selected[0] if selected else None
            column='mark'
            if self.clicked_cell and self.clicked_cell[0] is tree and self.clicked_cell[1] in selected:
                _,row,display_column=self.clicked_cell
                column=tree.column(display_column,'id')
            if row and tree.exists(row):
                self.root.clipboard_clear();self.root.clipboard_append(tree.set(row,column))
        return 'break'

    def _keyboard_table_menu(self,tree):
        from types import SimpleNamespace
        selected=tree.selection()
        if not selected:return 'break'
        box=tree.bbox(selected[0])
        if box:
            x,y=box[0]+4,box[1]+4
            self._table_menu(SimpleNamespace(widget=tree,x=x,y=y,x_root=tree.winfo_rootx()+x,y_root=tree.winfo_rooty()+y))
        return 'break'

    def _copy_rows(self,_event=None):
        tree=self._active_tree()
        if tree:
            cols=tree.cget('displaycolumns')
            if cols==('#all',):cols=COLUMNS
            content='\n'.join('\t'.join(tree.set(row,col) for col in cols) for row in tree.selection())
            if content:self.root.clipboard_clear();self.root.clipboard_append(content)
        return 'break'

    def _table_menu(self,event):
        tree=event.widget;row=tree.identify_row(event.y)
        if row:
            if row not in tree.selection():tree.selection_set(row)
            self._remember_cell(event)
        menu=tk.Menu(self.root,tearoff=False)
        for label,command in [('Άνοιγμα PDF',self._open_saved_pdf),('Άνοιγμα συνδέσμου στον προσωπικό browser',self._open_provider),
                              ('Αντιγραφή κελιού',self._copy_cell),('Αντιγραφή επιλεγμένων γραμμών',self._copy_rows),('Εξαγωγή CSV…',self._export_csv)]:
            menu.add_command(label=label,command=command)
        menu.add_separator()
        menu.add_command(label='Μετακίνηση στήλης αριστερά',command=lambda:self._move_column(-1))
        menu.add_command(label='Μετακίνηση στήλης δεξιά',command=lambda:self._move_column(1))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def _move_column(self,direction):
        if not self.clicked_cell:return
        tree,_,column=self.clicked_cell
        cols=list(tree.cget('displaycolumns'));name=tree.column(column,'id');index=cols.index(name)
        target=index+direction
        if not 0<=target<len(cols):return
        cols[index],cols[target]=cols[target],cols[index]
        for candidate in self.document_trees.values():candidate.configure(displaycolumns=cols)
        self._schedule_preferences()

    def _schedule_preferences(self,_event=None):
        if self.preferences_timer:self.root.after_cancel(self.preferences_timer)
        self.preferences_timer=self.root.after(700,self._save_preferences)

    def _save_preferences(self,close_after=False):
        self.preferences_timer=None
        if self.busy:return
        tree=self._active_tree() or self.document_tree
        widths={c:tree.column(c,'width') for c in COLUMNS}
        order=list(tree.cget('displaycolumns'))
        if order==['#all']:order=list(COLUMNS)
        order += [c for c in COLUMNS if c not in order]
        config=dict(widths=widths,order=order,destinations=dict(self.download_roots))
        def done(_):
            if close_after:
                self.credentials.clear();self.root.destroy()
        self._submit(lambda:self.settings.save(config),done)

    def _apply_preferences(self,config):
        self.download_roots.update(config.get('destinations',{}))
        for tree in self.document_trees.values():
            for col,width in config.get('widths',{}).items():tree.column(col,width=width)
            order=config.get('order')
            if order:tree.configure(displaycolumns=[c for c in order if c!='reason'])

    def _export_csv(self):
        if self.busy or not self.selected:return
        tree=self._active_tree()
        if not tree:return
        marks=tuple(int(x) for x in tree.selection())
        selected=False
        if marks:
            answer=messagebox.askyesnocancel('Εύρος εξαγωγής',f'Ναι: {len(marks)} επιλεγμένα.\nΌχι: όλα τα φιλτραρισμένα στοιχεία της καρτέλας.\nΔεν εξάγονται σύνδεσμοι, XML, credentials ή διαδρομές.',parent=self.root)
            if answer is None:return
            selected=answer
        path=filedialog.asksaveasfilename(parent=self.root,title='Νέο CSV — χωρίς αντικατάσταση',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        client_id,dates,bucket,query=self.selected,self.dates,self.document_bucket,self.table_query
        self.cancel.clear()
        def work():
            self.operation_stage='export'
            rows=self.db.export_rows(client_id,dates,bucket,query)
            if selected:rows=(row for row in rows if row['mark'] in marks)
            return export_csv(path,rows,self.cancel)
        self._submit(work,lambda count:self._message(f'Εξήχθησαν {count} εγγραφές σε CSV.'))
        self.stop_button.configure(state='normal')

    def _toggle_messages(self):
        if self.messages_frame.winfo_manager():self.messages_frame.grid_remove()
        else:self.messages_frame.grid()

    def _manage_client(self):
        if self.busy or not self.selected:return
        window=tk.Toplevel(self.root);window.title('Διαχείριση πελάτη');window.transient(self.root)
        client=self.clients[self.selected]
        ttk.Label(window,text=f'{client.name} · ΑΦΜ {client.vat}',padding=12).pack()
        ttk.Label(window,text='Αφορά όλες τις ημερομηνίες. Κάθε ενέργεια ζητά ξεχωριστή επιβεβαίωση.',padding=12,wraplength=480).pack()
        ttk.Button(window,text='Μηδενισμός λήψεων',command=lambda:(window.destroy(),self._reset_downloads())).pack(padx=12,pady=8,fill='x')
        ttk.Button(window,text='Μεταφορά φακέλου πελάτη στον Κάδο',command=lambda:(window.destroy(),self._delete_archive())).pack(padx=12,pady=8,fill='x')
        window.bind('<Escape>',lambda _e:window.destroy())

    def _backup_window(self):
        from .backup_ui import show_backup
        if not self.busy:show_backup(self)

    def _technical_report(self):
        if self.busy:return
        path=filedialog.asksaveasfilename(parent=self.root,title='Τεχνική αναφορά χωρίς προσωπικά δεδομένα',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        from .diagnostics import export_report
        self._submit(lambda:export_report(self.db.path.parent,path),
                     lambda count:self._message(f'Εξήχθη τεχνική αναφορά με {count} ασφαλείς εγγραφές διάγνωσης.'))

    def _show_batch_result(self):
        record=getattr(self,'last_batch_record',None)
        from .batch import batch_description
        window=tk.Toplevel(self.root);window.title('Αποτέλεσμα τελευταίας παρτίδας');window.transient(self.root)
        text=tk.Text(window,height=10,width=80,wrap='word',font='TkTextFont')
        text.pack(fill='both',expand=True,padx=12,pady=12)
        timestamp=record.get('at','') if record else ''
        content=batch_description(record)+'\nΧρόνος UTC: '+timestamp
        if getattr(self,'last_batch_message',None):content+='\n'+self.last_batch_message
        text.insert('1.0',content);text.configure(state='disabled')
        window.bind('<Escape>',lambda _e:window.destroy())

    def _toggle_filters(self):
        visible=bool(self.filters_frame.winfo_manager())
        for frame in (self.filters_frame,self.filter_selects):
            if visible:frame.grid_remove()
            else:frame.grid()

    def _focus_invoice_search(self):
        if not self.filters_frame.winfo_manager():self._toggle_filters()
        self.invoice_search_entry.focus_set()
