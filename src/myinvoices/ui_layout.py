"""Data-first desktop layout; actions remain in App for startup compatibility."""
import tkinter as tk
from tkinter import ttk
from .ui_components import configure_style, Tooltip, DetailsPanel, SPACING
from .table_export import HEADERS
from .settings import COLUMNS
from .presentation import REASONS, PROVENANCE


def build(app):
    root=app.root
    configure_style(root,app.font_family)
    outer=ttk.Frame(root,padding=SPACING);outer.pack(fill='both',expand=True)
    header=ttk.Frame(outer);header.pack(fill='x')
    ttk.Label(header,text='myInvoices',font=(app.font_family,15,'bold')).pack(side='left')
    ttk.Label(header,text='OFFLINE DEMO — συνθετικά στοιχεία' if app.demo else 'Παραστατικά ΑΑΔΕ και τοπική αρχειοθέτηση',style='Muted.TLabel').pack(side='left',padx=12)
    menu=tk.Menu(root,tearoff=False);root.configure(menu=menu)
    clients=tk.Menu(menu,tearoff=False)
    for label,command in [('Νέος πελάτης…',app._client_form),('Στοιχεία πελάτη…',app._edit_client),('Ανανέωση τοπικής λίστας',app.refresh)]:clients.add_command(label=label,command=command)
    menu.add_cascade(label='Πελάτης',menu=clients)
    tools=tk.Menu(menu,tearoff=False)
    for label,command in [('Έλεγχος σύνδεσης API',app._check_api),('Άνοιγμα συνδέσμου στον προσωπικό browser',app._open_provider),('Χειροκίνητη εισαγωγή PDF…',app._import_pdf),('Επιλογή φακέλου PDF…',app._choose_destination),('Προηγούμενος μήνας',app._previous_month),('Όλες οι ημερομηνίες',app._clear_dates),('Αποθήκευση διάταξης',app._save_preferences),('Διαχείριση πελάτη…',app._manage_client),('Backup και έλεγχος επαναφοράς…',app._backup_window),('Εξαγωγή τεχνικής αναφοράς…',app._technical_report)]:tools.add_command(label=label,command=command)
    from .entersoft.ui import open_extension
    tools.add_command(label='Entersoft EBS — προετοιμασία (πρόσθετη λειτουργία)…',command=lambda:open_extension(app))
    menu.add_cascade(label='Περισσότερα',menu=tools);app.main_menu=menu
    app.messages_menu=tk.Menu(menu,tearoff=False)
    app.messages_menu.add_command(label='Αντιγραφή τελευταίου σφάλματος',command=app._copy_error,state='disabled')
    app.messages_menu.add_command(label='Αντιγραφή όλων',command=app._copy_messages)
    app.messages_menu.add_command(label='Καθαρισμός μηνυμάτων',command=app._clear_messages)
    menu.add_cascade(label='Μηνύματα',menu=app.messages_menu)
    ttk.Label(outer,textvariable=app.summary,style='Context.TLabel',wraplength=1100).pack(fill='x',pady=(5,2))
    ttk.Label(outer,textvariable=app.destination_text,style='Muted.TLabel',wraplength=1100).pack(fill='x')
    toolbar=ttk.Frame(outer);toolbar.pack(fill='x',pady=6)
    app.sync_button=ttk.Button(toolbar,text='1. Εύρεση παραστατικών',style='Primary.TButton',command=app._sync)
    app.pdf_button=ttk.Button(toolbar,text='2. Λήψη PDF',command=app._download_pdfs)
    app.sync_button.pack(side='left');app.pdf_button.pack(side='left',padx=6)
    ttk.Label(toolbar,textvariable=app.metrics_text,wraplength=500).pack(side='right')
    period=ttk.Frame(outer);period.pack(fill='x',pady=(0,6))
    ttk.Label(period,text='Περίοδος έκδοσης · Από').pack(side='left')
    app.from_entry=ttk.Entry(period,textvariable=app.date_from,width=12);app.from_entry.pack(side='left',padx=5)
    ttk.Label(period,text='Έως').pack(side='left')
    app.to_entry=ttk.Entry(period,textvariable=app.date_to,width=12);app.to_entry.pack(side='left',padx=5)
    for entry in (app.from_entry,app.to_entry):entry.bind('<Return>',lambda _e:app._apply_dates())
    ttk.Button(period,text='Εφαρμογή',command=app._apply_dates).pack(side='left')
    app.filters_button=ttk.Button(period,text='Φίλτρα',command=app._toggle_filters)
    app.filters_button.pack(side='left',padx=6)
    ttk.Label(period,text='ΗΗ/ΜΜ/ΕΕΕΕ',style='Muted.TLabel').pack(side='left',padx=8)
    app.selection_text=tk.StringVar(value='0 επιλεγμένα')
    ttk.Label(period,textvariable=app.selection_text).pack(side='right')
    body=ttk.Panedwindow(outer,orient='horizontal');body.pack(fill='both',expand=True)
    left,right=ttk.Frame(body,width=215),ttk.Frame(body)
    body.add(left,weight=0);body.add(right,weight=1)
    ttk.Label(left,textvariable=app.client_count_label).pack(anchor='w')
    ttk.Label(left,text='Αναζήτηση πελάτη / ΑΦΜ').pack(anchor='w',pady=(4,2))
    app.search_entry=ttk.Entry(left,textvariable=app.client_filter);app.search_entry.pack(fill='x',padx=(0,8),pady=(0,5))
    treebox=ttk.Frame(left);treebox.pack(fill='both',expand=True)
    app.client_tree=ttk.Treeview(treebox,columns=('vat',),show='tree headings',selectmode='browse',height=8)
    app.client_tree.heading('#0',text='Πελάτης');app.client_tree.heading('vat',text='ΑΦΜ')
    app.client_tree.column('#0',width=120,minwidth=85);app.client_tree.column('vat',width=90,minwidth=80)
    scroll=ttk.Scrollbar(treebox,command=app.client_tree.yview);app.client_tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y');app.client_tree.pack(fill='both',expand=True)
    app.client_tree.bind('<<TreeviewSelect>>',app._select);app.client_tree.bind('<Double-1>',lambda _e:app._edit_client())
    app.client_filter.trace_add('write',lambda *_:app._filter_clients())
    filters=app.filters_frame=ttk.Frame(right);filters.pack(fill='x')
    ttk.Label(filters,text='Παραστατικό').grid(row=0,column=0,sticky='w')
    app.invoice_search_entry=ttk.Entry(filters,textvariable=app.invoice_search,width=26)
    app.invoice_search_entry.grid(row=0,column=1,sticky='ew',padx=4)
    Tooltip(app.invoice_search_entry,'Αναζήτηση MARK, σειράς/αριθμού ή ΑΦΜ προμηθευτή. Η επωνυμία προμηθευτή δεν υπάρχει στα αποθηκευμένα πεδία.')
    ttk.Label(filters,text='Προμηθευτής ΑΦΜ').grid(row=0,column=2,sticky='w')
    app.supplier_entry=ttk.Entry(filters,textvariable=app.supplier_filter,width=13);app.supplier_entry.grid(row=0,column=3,padx=4)
    ttk.Button(filters,text='Καθαρισμός',command=app._reset_filters).grid(row=0,column=4,padx=4)
    filters.columnconfigure(1,weight=1)
    selects=app.filter_selects=ttk.Frame(right);selects.pack(fill='x',pady=4)
    choices=[(app.status_filter,['Όλες οι καταστάσεις','Αποθηκευμένα','Δοκιμασμένα / εκκρεμή','Αδοκίμαστα','Ακυρωμένα']),
             (app.reason_filter,['Όλες οι αιτίες']+[v[0] for v in REASONS.values()]),
             (app.provenance_filter,['Κάθε προέλευση']+list(PROVENANCE.values()))]
    app.filter_combos=[]
    for i,(variable,options) in enumerate(choices):
        combo=ttk.Combobox(selects,textvariable=variable,values=options,state='readonly',width=22)
        combo.grid(row=0,column=i,sticky='ew',padx=(0,4));selects.columnconfigure(i,weight=1);app.filter_combos.append(combo)
    vertical=ttk.Panedwindow(right,orient='vertical');vertical.pack(fill='both',expand=True)
    table_frame=ttk.Frame(vertical);vertical.add(table_frame,weight=4)
    book=app.document_book=ttk.Notebook(table_frame);book.pack(fill='both',expand=True)
    app.document_trees={};app.tab_buckets={}
    widths=[100,145,110,135,105,135,170,230]
    for bucket,title in [('all','Παραστατικά'),('success','Αποθηκευμένα'),('failure','Εκκρεμή / αποτυχημένα')]:
        frame=ttk.Frame(book);book.add(frame,text=title);app.tab_buckets[str(frame)]=bucket
        row=0
        if bucket=='failure':
            app.recovery_button=ttk.Button(frame,text='Ανάκτηση όλων των 0 εκκρεμών',command=lambda:app._download_pdfs(recovery=True))
            app.recovery_button.grid(row=0,column=0,sticky='w',pady=4);row=1
            Tooltip(app.recovery_button,'Ξεχωριστός ορατός browser της εφαρμογής. Ολοκληρώνετε εσείς τυχόν έλεγχο πρόσβασης. Κλείσιμο ή Διακοπή σταματά την παρτίδα.')
        tree=ttk.Treeview(frame,columns=COLUMNS,show='headings',selectmode='extended',height=8)
        tree.tag_configure('cancelled',foreground='#a82f2f')
        for col,label,width in zip(COLUMNS,HEADERS,widths):
            tree.heading(col,text=label,command=lambda c=col:app._sort_table(c))
            tree.column(col,width=width,minwidth=65,anchor='e' if col in ('mark','total') else 'w')
        # Long reason lives in the always available details panel, not far right.
        tree.configure(displaycolumns=('date','mark','supplier','identity','total','method','status'))
        vs=ttk.Scrollbar(frame,command=tree.yview);hs=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        tree.grid(row=row,column=0,sticky='nsew');vs.grid(row=row,column=1,sticky='ns');hs.grid(row=row+1,column=0,sticky='ew')
        frame.rowconfigure(row,weight=1);frame.columnconfigure(0,weight=1)
        app.document_trees[bucket]=tree
        tree.bind('<<TreeviewSelect>>',app._document_selection)
        tree.bind('<Double-1>',lambda _e:app._open_saved_pdf())
        tree.bind('<Return>',lambda _e:app._open_saved_pdf())
        tree.bind('<Control-c>',app._copy_rows)
        tree.bind('<Button-3>',app._table_menu)
        tree.bind('<Shift-F10>',lambda _e,t=tree:app._keyboard_table_menu(t))
        tree.bind('<Button-1>',app._remember_cell,add='+')
        tree.bind('<ButtonRelease-1>',app._schedule_preferences,add='+')
    app.document_tree=app.document_trees['all'];app.success_tree=app.document_trees['success'];app.failure_tree=app.document_trees['failure']
    history=ttk.Frame(book);changes=ttk.Frame(book)
    book.add(history,text='Ιστορικό');book.add(changes,text='Μεταβολές')
    book.bind('<<NotebookTabChanged>>',app._document_tab_changed)
    app.history_tree=ttk.Treeview(history,columns=('at','kind','count'),show='headings')
    for c,t in [('at','Χρόνος UTC'),('kind','Ενέργεια'),('count','Εγγραφές')]:app.history_tree.heading(c,text=t)
    app.history_tree.pack(fill='both',expand=True)
    app.changes_tree=ttk.Treeview(changes,columns=('kind','event','invoice','date'),show='headings')
    for c,t in [('kind','Είδος'),('event','MARK μεταβολής'),('invoice','MARK παραστατικού'),('date','Ημερομηνία')]:app.changes_tree.heading(c,text=t)
    app.changes_tree.pack(fill='both',expand=True)
    details_frame=ttk.LabelFrame(vertical,text='Λεπτομέρειες και επόμενη ενέργεια',padding=4);vertical.add(details_frame,weight=1)
    app.details=DetailsPanel(details_frame);app.details.pack(fill='both',expand=True)
    actions=ttk.Frame(right);actions.pack(fill='x',pady=(4,0))
    app.open_pdf_button=ttk.Button(actions,text='Άνοιγμα PDF',command=app._open_saved_pdf)
    app.browser_pdf_button=ttk.Button(actions,text='Άνοιγμα συνδέσμου',command=app._open_provider)
    app.open_folder_button=ttk.Button(actions,text='Φάκελος τιμολογίων',command=app._open_archive_folder)
    app.export_button=ttk.Button(actions,text='Εξαγωγή CSV…',command=app._export_csv)
    for i,button in enumerate((app.open_pdf_button,app.browser_pdf_button,app.open_folder_button,app.export_button)):
        button.grid(row=0,column=i,sticky='w',padx=(0,4))
    Tooltip(app.browser_pdf_button,'Ανοίγει μόνο τον σύνδεσμο της επιλεγμένης εγγραφής στον προσωπικό σας browser. Δεν εκτελεί ανάκτηση.')
    # Management commands are separate, never adjacent to everyday open actions.
    app.management_frame=ttk.Frame(root)
    app.reset_downloads_button=ttk.Button(app.management_frame,text='Μηδενισμός λήψεων',command=app._reset_downloads)
    app.delete_archive_button=ttk.Button(app.management_frame,text='Διαγραφή φακέλου πελάτη',command=app._delete_archive)
    paging=ttk.Frame(right);paging.pack(fill='x',pady=4)
    app.previous=ttk.Button(paging,text='◀ Προηγούμενα',command=lambda:app._page(-1));app.previous.pack(side='left')
    app.next=ttk.Button(paging,text='Επόμενα ▶',command=lambda:app._page(1));app.next.pack(side='left',padx=4)
    ttk.Label(paging,text='Έως 200 ανά σελίδα',style='Muted.TLabel').pack(side='right')
    statusbar=ttk.Frame(outer);statusbar.pack(fill='x',pady=(5,0))
    app.stop_button=ttk.Button(statusbar,text='Διακοπή εργασίας',command=app._stop,state='disabled')
    app.progress=ttk.Progressbar(statusbar,mode='indeterminate',length=110);app.progress.pack(side='left',padx=(0,8))
    ttk.Label(statusbar,textvariable=app.status,wraplength=900).pack(side='left',fill='x',expand=True)
    app.statusbar=statusbar
    batch_label=ttk.Label(outer,textvariable=app.batch_summary,wraplength=1100,style='Muted.TLabel')
    batch_label.pack(fill='x',pady=3)
    messages=ttk.Frame(outer);messages.pack(fill='x')
    ttk.Button(messages,text='Μηνύματα / Ctrl+C',command=app._toggle_messages).pack(side='left')
    ttk.Button(messages,text='Αποτέλεσμα τελευταίας παρτίδας',command=app._show_batch_result).pack(side='left',padx=6)
    app.messages_frame=ttk.Frame(outer)
    app.message_text=tk.Text(app.messages_frame,height=4,wrap='word',state='disabled',font=(app.font_family,10))
    scroll=ttk.Scrollbar(app.messages_frame,command=app.message_text.yview);app.message_text.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y');app.message_text.pack(fill='both',expand=True)
    app.message_text.tag_configure('error',foreground='#a82f2f');app.message_text.bind('<Control-a>',app._select_messages)
    root.bind('<Control-f>',lambda _e:app._focus_invoice_search())
    root.bind('<Control-Shift-C>',app._copy_cell)
    root.bind('<Escape>',lambda _e:app._stop() if app.busy else app._clear_table_selection())

    # Reserve footer rows before letting the document area consume free space.
    # This avoids pack-order clipping at 1366x768 and larger display scaling.
    for child in outer.winfo_children():
        if child.winfo_manager()=='pack':child.pack_forget()
    for index,child in enumerate(outer.winfo_children()):
        child.grid(row=index,column=0,sticky='nsew' if child is body else 'ew',pady=2)
        if child is body:outer.rowconfigure(index,weight=1)
    outer.columnconfigure(0,weight=1)
    app.messages_frame.grid_remove()

    for child in right.winfo_children():
        if child.winfo_manager()=='pack':child.pack_forget()
    for index,child in enumerate(right.winfo_children()):
        child.grid(row=index,column=0,sticky='nsew' if child is vertical else 'ew',pady=2)
        if child is vertical:right.rowconfigure(index,weight=1)
    right.columnconfigure(0,weight=1)
    def size_split(event):
        if event.height>80:
            vertical.sashpos(0,int(event.height*(.68 if event.height>380 else .55)))
    vertical.bind('<Configure>',size_split,add='+')

    if float(root.tk.call('tk','scaling'))>1.8:
        header.grid_remove()  # Decorative title gives room to actual document data.
        app.filters_frame.grid_remove();app.filter_selects.grid_remove()
        batch_label.grid_remove()  # Full persistent result remains available from its button.
