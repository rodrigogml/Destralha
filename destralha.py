"""Interface Destralha: revisão explícita, tarefas visíveis e relatórios em memória."""
from __future__ import annotations
import json
import queue
import shutil
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageDraw, ImageTk

import engine
import downloads

APP = Path(__file__).resolve().parent
VERSION = '1.1'
BG, PANEL, SOFT, LINE = '#10161e', '#18212c', '#202e3d', '#2b3949'
INK, MUTED, ACCENT, ACCENT_BG, WARN = '#edf4fc', '#a6b6c7', '#78d4d6', '#203c40', '#f0c381'
CATEGORIES = ('Caches', 'Downloads', 'Outros')
EFFECTS = {'cache':'Recria cache', 'offline':'Novo download / offline',
           'installer':'Cópia de instalador', 'diagnostic':'Remove histórico técnico',
           'residue':'Pacote antigo / sobra', 'protected':'Preservar'}
CONDITIONS = ('Todas as condições', 'Disponível agora', 'Aplicativo aberto',
              'Processo usando o perfil', 'Instalação / manutenção', 'Protegido',
              'Aguardando prazo mínimo', 'Verificação / outro impedimento')


def category_of(rule):
    return 'Caches' if rule['risk'] == 'cache' and not rule.get('protected') else 'Outros'


def effect_of(row):
    return 'Exclusão de arquivo' if isinstance(row, downloads.Download) else EFFECTS[row.rule['risk']]


def condition_of(row):
    reason = row.reason.casefold()
    if not isinstance(row, downloads.Download) and row.rule.get('protected'):
        return 'Protegido'
    if not reason:
        return 'Disponível agora'
    if 'registrad' in reason or 'preservado:' in reason:
        return 'Protegido'
    if reason.startswith(('aplicativo aberto', 'python/pip', 'node/npm/npx')):
        return 'Aplicativo aberto'
    if reason.startswith(('processo usando', 'processo do aplicativo', 'ferramenta relacionada')):
        return 'Processo usando o perfil'
    if reason.startswith(('instala', 'instalador')):
        return 'Instalação / manutenção'
    if reason.startswith('arquivo recente'):
        return 'Aguardando prazo mínimo'
    return 'Verificação / outro impedimento'


def selectable(row):
    return not row.reason if isinstance(row, downloads.Download) else row.selectable


def row_name(row):
    return row.path.name if isinstance(row, downloads.Download) else row.rule['name']


def row_key(row):
    return str(row.path).casefold()


def filter_rows(rows, query='', effect='Todos os efeitos', condition='Todas as condições', mark='Todas as marcações'):
    """Filtra alvos individuais; não altera a marcação nem conta agrupadores."""
    query = query.strip().casefold()
    result = []
    for row in rows:
        text = ' '.join((row_name(row), str(row.path), row.reason, effect_of(row)))
        if not isinstance(row, downloads.Download):
            text += ' ' + row.rule['purpose'] + ' ' + row.rule['effect']
        if query and query not in text.casefold():
            continue
        if effect not in ('Todos os efeitos', effect_of(row)):
            continue
        if condition not in ('Todas as condições', condition_of(row)):
            continue
        if mark == 'Marcados' and not row.selected or mark == 'Desmarcados' and row.selected:
            continue
        result.append(row)
    return result


def facet_counts(rows, query, filters, name, options):
    result = {}
    for option in options:
        chosen = dict(filters, **{name: option})
        result[option] = len(filter_rows(rows, query, **chosen))
    return result


def group_rows(rows):
    grouped = {}
    for row in rows:
        key = row_key(row) if isinstance(row, downloads.Download) else row.rule['id']
        grouped.setdefault(key, []).append(row)
    return sorted(grouped.values(), key=lambda group: sum(row.size for row in group), reverse=True)


class DestralhaApp:
    """Nenhum botão de análise, marcação ou simulação remove arquivos.

    Exclusão real passa pela confirmação concreta e pelos mesmos guardas do
    motor. Workers não acessam widgets nem gravam relatórios automaticamente.
    """
    def __init__(self, root):
        self.root = root
        self.roots = engine.known_roots()
        self.downloads_root = downloads.downloads_root()
        self.rules = engine.load_catalog()
        self.rows = {category: [] for category in CATEGORIES}
        self.category = 'Caches'
        self.mode = 'catalog'
        self.management = None
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.working = False
        self.job = None
        self.report = None
        self.analyzed = False
        self.warnings = []
        self.images = {}
        self.preferences = self.read_preferences()
        self.states = {category: dict(query='', effect='Todos os efeitos', condition='Todas as condições',
                                      mark='Todas as marcações', open=set()) for category in CATEGORIES}
        self.updating = False
        self.items = {}
        self.progress_window = None
        self.progress_state = dict(title='', phase='', current='', index=0, total=0)
        self.root.title(f'Destralha — {VERSION}')
        icon = APP/'assets/brand/destralha.ico'
        if icon.exists():
            self.root.iconbitmap(str(icon))
        self.root.geometry('1320x880')
        self.root.minsize(1100, 720)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.render()
        self.timer = root.after(80, self.pump)

    def read_preferences(self):
        result = dict(remember=False, protected=False, selected=[])
        for filename in ('preferences.json', 'cache-preferences.json'):
            try:
                data = json.loads((APP/filename).read_text(encoding='utf-8'))
                if filename == 'preferences.json':
                    result['protected'] = data.get('protected') is True
                else:
                    result['remember'] = data.get('remember') is True
                    if isinstance(data.get('selected'), list):
                        result['selected'] = [value for value in data['selected'] if isinstance(value, str)]
            except (OSError, ValueError, AttributeError):
                pass
        return result

    def icon(self, name, color=INK):
        """Ícones pequenos desenhados em memória; nenhum arquivo auxiliar é criado."""
        key = name, color
        if key in self.images:
            return self.images[key]
        image = Image.new('RGBA', (64,64))
        draw = ImageDraw.Draw(image)
        def line(points): draw.line([(x*4,y*4) for x,y in points], fill=color, width=5, joint='curve')
        def box(rect): draw.rectangle(tuple(value*4 for value in rect), outline=color, width=5)
        if name == 'search':
            draw.ellipse((9,8,43,42), outline=color, width=5); line([(10,10),(14,14)])
        elif name == 'brush':
            line([(5,10),(12,3),(14,5),(7,12)]); line([(5,9),(2,12),(4,14),(8,13)])
        elif name == 'download':
            line([(8,2),(8,11)]); line([(4,7),(8,11),(12,7)]); line([(2,11),(2,14),(14,14),(14,11)])
        elif name == 'folder':
            line([(2,13),(2,4),(7,4),(9,6),(14,6),(14,13),(2,13)])
        elif name == 'trash':
            line([(2,4),(14,4)]); line([(5,3),(5,2),(11,2),(11,3)]); line([(4,5),(5,14),(11,14),(12,5)]); line([(7,6),(7,12)]); line([(9,6),(9,12)])
        elif name == 'save':
            box((2,2,14,14)); box((5,2,10,6)); box((5,9,11,14))
        elif name == 'report':
            box((4,3,13,14)); box((6,1,11,4)); line([(6,7),(11,7)]); line([(6,10),(11,10)])
        elif name == 'check':
            line([(2,8),(6,12),(14,3)])
        elif name == 'clear':
            line([(3,3),(13,13)]); line([(13,3),(3,13)])
        elif name == 'eye':
            line([(1,8),(5,4),(11,4),(15,8),(11,12),(5,12),(1,8)]); draw.ellipse((25,23,39,40), outline=color, width=4)
        elif name == 'gear':
            draw.ellipse((14,14,50,50), outline=color, width=5); draw.ellipse((26,26,38,38), outline=color, width=4)
            for x,y,x2,y2 in ((8,1,8,4),(8,12,8,15),(1,8,4,8),(12,8,15,8)):
                line([(x,y),(x2,y2)])
        elif name == 'disk':
            box((2,4,14,13)); line([(2,9),(14,9)]); draw.ellipse((43,42,48,47), fill=color)
        else:
            line([(5,3),(11,8),(5,13)])
        self.images[key] = ImageTk.PhotoImage(image.resize((18,18), Image.Resampling.LANCZOS), master=self.root)
        return self.images[key]

    def label(self, parent, text='', *, size=10, color=INK, bold=False, bg=None, **kwargs):
        return tk.Label(parent, text=text, font=('Segoe UI',size,'bold' if bold else 'normal'), fg=color,
                        bg=bg or parent.cget('bg'), anchor='w', **kwargs)

    def button(self, parent, text, command, icon=None, primary=False, quiet=False):
        background = ACCENT if primary else (parent.cget('bg') if quiet else SOFT)
        foreground = '#10292c' if primary else INK
        return tk.Button(parent, text=text, command=command, image=self.icon(icon, foreground) if icon else '',
                         compound='left', padx=14, pady=9, bg=background, fg=foreground,
                         activebackground=ACCENT_BG if not primary else '#9be1e2', activeforeground=foreground,
                         disabledforeground=MUTED, relief='flat', bd=0, highlightthickness=0,
                         font=('Segoe UI',10,'bold'), cursor='hand2')

    def build(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Destralha.Treeview', background=PANEL, fieldbackground=PANEL, foreground=INK,
                        borderwidth=0, rowheight=40, font=('Segoe UI',10))
        style.layout('Destralha.Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
        style.configure('Destralha.Treeview.Heading', background=BG, foreground=MUTED,
                        borderwidth=0, padding=(12,12), font=('Segoe UI',9))
        style.map('Destralha.Treeview', background=[('selected',ACCENT_BG)], foreground=[('selected',INK)])
        style.map('Destralha.Treeview.Heading', background=[('active',SOFT)])
        style.configure('Destralha.TCombobox', fieldbackground=PANEL, background=SOFT, foreground=INK,
                        arrowcolor=MUTED, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=7)
        style.map('Destralha.TCombobox', fieldbackground=[('readonly',PANEL)], foreground=[('readonly',INK)],
                  selectbackground=[('readonly',PANEL)], selectforeground=[('readonly',INK)])
        style.configure('Destralha.Horizontal.TProgressbar', background=ACCENT, troughcolor=SOFT,
                        bordercolor=SOFT,lightcolor=SOFT,darkcolor=SOFT,borderwidth=0, thickness=8)
        style.configure('Destralha.Vertical.TScrollbar', background=SOFT, troughcolor=PANEL, borderwidth=0,
                        bordercolor=PANEL,lightcolor=PANEL,darkcolor=PANEL,arrowcolor=MUTED)
        style.map('Destralha.Vertical.TScrollbar',background=[('active',LINE),('!active',SOFT)])
        self.root.option_add('*TCombobox*Listbox.background',PANEL)
        self.root.option_add('*TCombobox*Listbox.foreground',INK)
        self.root.option_add('*TCombobox*Listbox.selectBackground',ACCENT_BG)
        self.root.columnconfigure(1,weight=1)
        self.root.rowconfigure(0,weight=1)
        sidebar = tk.Frame(self.root,bg=PANEL,width=205,padx=17,pady=23)
        sidebar.grid(row=0,column=0,sticky='nsew'); sidebar.grid_propagate(False)
        logo = Image.open(APP/'assets/brand/destralha-logo.png')
        logo.thumbnail((171,70), Image.Resampling.LANCZOS)
        self.images['logo'] = ImageTk.PhotoImage(logo, master=self.root)
        tk.Label(sidebar,image=self.images['logo'],bg=PANEL).pack(anchor='w',pady=(0,9))
        self.label(sidebar,'Espaço sob controle.',size=9,color=MUTED).pack(anchor='w',pady=(0,30))
        self.nav = {}
        for category, icon in zip(CATEGORIES,('brush','download','folder')):
            button = self.button(sidebar,category,lambda value=category:self.change_category(value),icon,quiet=True)
            button.configure(anchor='w',padx=13,pady=13)
            button.pack(fill='x',pady=4)
            self.nav[category] = button
        self.management_button = self.button(sidebar,'Programas e sistema',self.open_management,'gear',quiet=True)
        self.management_button.configure(anchor='w',padx=13,pady=13)
        self.management_button.pack(fill='x',pady=(18,4))
        self.button(sidebar,'Opções',self.settings,'gear',quiet=True).pack(side='bottom',fill='x',pady=(20,0))
        disk = tk.Frame(sidebar,bg=PANEL)
        disk.pack(side='bottom',fill='x',pady=(25,0))
        self.label(disk,'DISCO LOCAL '+self.roots['local'].anchor,size=9,color=MUTED).pack(anchor='w')
        self.disk_bar = ttk.Progressbar(disk,style='Destralha.Horizontal.TProgressbar',length=165)
        self.disk_bar.pack(fill='x',pady=10)
        self.disk_label = self.label(disk,size=12,bold=True); self.disk_label.pack(anchor='w')
        self.disk_capacity = self.label(disk,size=9,color=MUTED); self.disk_capacity.pack(anchor='w')
        self.update_disk()
        main = tk.Frame(self.root,bg=BG); main.grid(row=0,column=1,sticky='nsew')
        self.main_area = main
        header = tk.Frame(main,bg=PANEL,padx=24,pady=17); header.pack(fill='x')
        self.label(header,'PERFIL ATUAL  ·  ANÁLISE LOCAL',size=9,color=MUTED).pack(side='left')
        self.analyze_button = self.button(header,'Analisar diretórios',self.analyze,'search',primary=True)
        self.analyze_button.pack(side='right')
        self.report_button = self.button(header,'Relatório',self.show_report,'report',quiet=True)
        self.report_button.pack(side='right',padx=12)
        self.live = tk.Frame(main,bg=ACCENT_BG,padx=24,pady=8)
        self.live_text = self.label(self.live,'',size=9,color=ACCENT,bg=ACCENT_BG)
        self.live_text.pack(side='left',fill='x',expand=True)
        self.button(self.live,'Ver progresso',self.show_progress,quiet=True).pack(side='right')
        self.live.pack(fill='x'); self.live.pack_forget()
        footer = tk.Frame(main,bg=PANEL,padx=24,pady=14); footer.pack(side='bottom',fill='x')
        self.catalog_footer = footer
        self.footer_main = self.label(footer,'Nenhum item marcado',size=11,bold=True)
        self.footer_main.grid(row=0,column=0,sticky='w')
        self.footer_sub = self.label(footer,'Marque somente o que deseja limpar.',size=9,color=MUTED)
        self.footer_sub.grid(row=1,column=0,sticky='w',pady=(3,0))
        footer.columnconfigure(0,weight=1)
        self.simulate_button = self.button(footer,'Simular',lambda:self.review(True),'eye')
        self.simulate_button.grid(row=0,column=1,rowspan=2,padx=8)
        self.clean_button = self.button(footer,'Revisar limpeza',lambda:self.review(False),'trash',primary=True)
        self.clean_button.grid(row=0,column=2,rowspan=2)
        self.label(footer,'Relatórios só são gravados ao clicar em Salvar.',size=9,color=MUTED).grid(row=2,column=0,columnspan=3,pady=(12,0))
        content = tk.Frame(main,bg=BG,padx=24,pady=22); content.pack(fill='both',expand=True)
        self.catalog_page = content
        self.title = self.label(content,'Caches',size=24,bold=True); self.title.pack(anchor='w')
        self.subtitle = self.label(content,'',size=10,color=MUTED); self.subtitle.pack(anchor='w',pady=(5,18))
        metrics = tk.Frame(content,bg=BG); metrics.pack(fill='x',pady=(0,18))
        self.metric_values = []
        for caption in ('Encontrado nesta categoria','Disponível agora','Preservado / em uso'):
            box = tk.Frame(metrics,bg=BG); box.pack(side='left',padx=(0,36))
            self.label(box,caption,size=9,color=MUTED).pack(anchor='w')
            value = self.label(box,'—',size=21,bold=True); value.pack(anchor='w',pady=(4,0))
            self.metric_values.append(value)
        self.download_options = tk.Frame(content,bg=SOFT,padx=12,pady=10)
        self.label(self.download_options,'Última modificação há mais de',size=9,bg=SOFT).pack(side='left')
        self.days = tk.StringVar(value='90')
        self.days_input = tk.Spinbox(self.download_options,from_=1,to=36500,textvariable=self.days,width=5,
                                     bg=PANEL,fg=INK,buttonbackground=SOFT,insertbackground=INK,relief='flat',font=('Segoe UI',11))
        self.days_input.pack(side='left',padx=9)
        self.label(self.download_options,'dias, OU arquivos acima de 1 GiB. Atualize pela análise global.',size=9,color=MUTED,bg=SOFT).pack(side='left')
        toolbar = tk.Frame(content,bg=BG); toolbar.pack(fill='x',pady=(0,12))
        self.mark_button = self.button(toolbar,'Marcar Tudo',self.mark_all,'check'); self.mark_button.pack(side='left')
        self.clear_button = self.button(toolbar,'Desmarcar Tudo',self.clear_all,'clear',quiet=True)
        self.clear_button.pack(side='left',padx=7)
        self.search = tk.StringVar()
        search_frame = tk.Frame(toolbar,bg=PANEL,highlightbackground=LINE,highlightthickness=1)
        search_frame.pack(side='right',fill='x',expand=True,padx=(16,0))
        tk.Label(search_frame,image=self.icon('search',MUTED),bg=PANEL).pack(side='left',padx=9)
        self.label(search_frame,'Busca',size=9,color=MUTED).pack(side='left',padx=(0,9))
        self.search_entry = tk.Entry(search_frame,textvariable=self.search,bg=PANEL,fg=INK,insertbackground=INK,relief='flat',
                                    font=('Segoe UI',10),width=18)
        self.search_entry.pack(side='left',fill='x',expand=True,pady=9,padx=(0,10))
        self.root.bind('<Control-f>',lambda event:self.search_entry.focus_set())
        self.search.trace_add('write',self.on_search)
        filterbar = tk.Frame(content,bg=BG); filterbar.pack(fill='x',pady=(0,12))
        self.filters = {}
        self.filter_options = {'effect':('Todos os efeitos',*EFFECTS.values(),'Exclusão de arquivo'),
                               'condition':CONDITIONS,'mark':('Todas as marcações','Marcados','Desmarcados')}
        for index,(name,caption) in enumerate((('effect','Efeito da exclusão'),('condition','Condição atual'),('mark','Marcação'))):
            box = tk.Frame(filterbar,bg=BG); box.grid(row=0,column=index,sticky='ew',padx=(0,10 if index<2 else 0))
            filterbar.columnconfigure(index,weight=1)
            self.label(box,caption,size=9,color=MUTED).pack(anchor='w',pady=(0,5))
            variable = tk.StringVar()
            combo = ttk.Combobox(box,textvariable=variable,state='readonly',style='Destralha.TCombobox',width=20,font=('Segoe UI',9))
            combo.pack(fill='x'); combo.bind('<<ComboboxSelected>>',lambda event,key=name:self.on_filter(key))
            self.filters[name] = variable,combo
        self.visible_label = self.label(content,'Clique em Analisar diretórios para começar.',size=9,color=MUTED)
        self.visible_label.pack(side='bottom',anchor='w',pady=(10,0))
        table = tk.Frame(content,bg=PANEL,highlightbackground=LINE,highlightthickness=1)
        table.pack(fill='both',expand=True)
        self.tree = ttk.Treeview(table,columns=('name','size','status','detail'),style='Destralha.Treeview',selectmode='browse')
        for key,title,width,stretch in (('#0','Limpar',75,False),('name','Aplicativo / diretório',360,True),
                                         ('size','Tamanho ↓',115,False),('status','Condição',180,False),('detail','Detalhes',82,False)):
            self.tree.heading(key,text=title)
            self.tree.column(key,width=width,minwidth=width if not stretch else 160,stretch=stretch,
                             anchor='e' if key=='size' else 'center' if key in ('#0','detail') else 'w')
        self.tree.tag_configure('blocked',foreground=WARN)
        self.tree.tag_configure('protected',foreground=MUTED)
        self.tree.tag_configure('child',background='#151d27')
        scrollbar = ttk.Scrollbar(table,orient='vertical',command=self.tree.yview,style='Destralha.Vertical.TScrollbar')
        scrollbar.pack(side='right',fill='y'); self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill='both',expand=True)
        self.tree.bind('<Button-1>',self.tree_click)
        self.tree.bind('<space>',self.tree_space)
        self.tree.bind('<Return>',lambda event:self.details_selected())
        self.tree.bind('<<TreeviewOpen>>',lambda event:self.remember_open(True))
        self.tree.bind('<<TreeviewClose>>',lambda event:self.remember_open(False))

    def current_filters(self):
        return {key:self.states[self.category][key] for key in self.filters}

    def visible_rows(self):
        return filter_rows(self.rows[self.category], self.states[self.category]['query'], **self.current_filters())

    def render(self):
        if self.mode == 'management':
            for button in self.nav.values():button.configure(bg=PANEL,fg=MUTED)
            self.management_button.configure(bg=ACCENT_BG,fg=ACCENT)
            if self.management is not None:self.management.refresh_controls()
            return
        self.management_button.configure(bg=PANEL,fg=MUTED)
        self.updating = True
        try:
            state = self.states[self.category]
            if self.search.get()!=state['query']:
                self.search.set(state['query'])
            for key,(variable,combo) in self.filters.items():
                options = self.filter_options[key]
                counts = facet_counts(self.rows[self.category],state['query'],self.current_filters(),key,options)
                values = [f'{option} ({counts[option]})' for option in options]
                combo.configure(values=values)
                variable.set(values[options.index(state[key])])
            for category,button in self.nav.items():
                button.configure(bg=ACCENT_BG if category==self.category else PANEL,
                                 fg=ACCENT if category==self.category else MUTED,
                                 text=f'{category}   {len(self.rows[category])}')
            self.title.configure(text=self.category)
            self.subtitle.configure(text={
                'Caches':'Arquivos recriáveis. Seus dados continuam com você.',
                'Downloads':'Revise arquivos antigos ou grandes antes de descartá-los.',
                'Outros':'Modelos, instaladores e diagnósticos: revise o efeito de cada escolha.'}[self.category])
            if self.category=='Downloads':
                if not self.download_options.winfo_manager():
                    self.download_options.pack(fill='x',pady=(0,14),before=self.mark_button.master)
            else:
                self.download_options.pack_forget()
            rows = self.rows[self.category]
            sizes = (sum(row.size for row in rows),sum(row.size for row in rows if not row.reason),
                     sum(row.size for row in rows if row.reason))
            for label,size in zip(self.metric_values,sizes):
                label.configure(text='—' if not rows and not self.analyzed else engine.human_size(size))
            self.tree.delete(*self.tree.get_children())
            self.items.clear()
            visible = self.visible_rows()
            for index,group in enumerate(group_rows(visible)):
                parent = str(index)
                if isinstance(group[0],downloads.Download):
                    self.insert_row('',parent,group,row_name(group[0]))
                else:
                    self.insert_row('',parent,group,row_name(group[0]), opened=group[0].rule['id'] in state['open'])
                    for child,row in enumerate(sorted(group,key=lambda value:value.size,reverse=True)):
                        self.insert_row(parent,f'{parent}:{child}',[row],str(row.path.relative_to(row.root)),child=True)
            self.visible_label.configure(text=f'{len(visible)} de {len(rows)} alvos · {engine.human_size(sum(row.size for row in visible))} · '
                                         + ('Clique em Analisar diretórios para começar.' if not rows and not self.analyzed else
                                            'Nenhum alvo encontrado nesta categoria.' if not rows else
                                            'Condições da última análise; revalidadas antes de excluir.'))
            selected = [row for row in rows if row.selected]
            visible_keys = {row_key(row) for row in visible}
            hidden = sum(row_key(row) not in visible_keys for row in selected)
            waiting = sum(bool(row.reason) for row in selected)
            self.footer_main.configure(text=f'{len(selected)} marcados · {engine.human_size(sum(row.size for row in selected))}' if selected else 'Nenhum item marcado')
            self.footer_sub.configure(text=f'{waiting} aguardando liberação · {hidden} fora do filtro' if selected else 'Marque somente o que deseja limpar.')
            for button in (self.simulate_button,self.clean_button):
                button.configure(state='normal' if selected and not self.working else 'disabled')
            self.clean_button.configure(bg=ACCENT if selected and not self.working else SOFT)
        finally:
            self.updating = False

    def insert_row(self,parent,key,rows,name,child=False,opened=False):
        allowed = [row for row in rows if selectable(row)]
        marked = sum(row.selected for row in allowed)
        mark = '—' if not allowed else '☑' if marked==len(allowed) else '◩' if marked else '☐'
        reasons = sorted({condition_of(row) for row in rows})
        status = reasons[0] if len(reasons)==1 else 'Disponível em parte' if any(not row.reason for row in rows) else 'Ver detalhes'
        tag = 'protected' if not allowed else 'blocked' if any(row.reason for row in rows) else ''
        self.tree.insert(parent,'end',iid=key,text=mark,values=(name,engine.human_size(sum(row.size for row in rows)),status,'›'),
                         tags=tuple(value for value in (tag,'child' if child else '') if value),open=opened)
        self.items[key] = rows

    def on_search(self,*_):
        if not self.updating:
            self.states[self.category]['query'] = self.search.get()
            self.render()

    def on_filter(self,name):
        if not self.updating:
            combo = self.filters[name][1]
            index = combo.current()
            if index>=0:
                self.states[self.category][name] = self.filter_options[name][index]
                self.render()

    def change_category(self,category):
        if category in CATEGORIES:
            if self.mode == 'management':
                self.management.frame.pack_forget()
                self.catalog_footer.pack(side='bottom',fill='x')
                self.catalog_page.pack(fill='both',expand=True)
            self.mode = 'catalog'
            self.category = category
            self.render()

    def open_management(self):
        if self.management is None:
            from maintenance.view import MaintenanceView
            self.management = MaintenanceView(self,self.main_area)
        self.mode = 'management'
        self.catalog_page.pack_forget()
        self.catalog_footer.pack_forget()
        self.management.activate()
        self.render()

    def remember_open(self,opened):
        key = self.tree.focus()
        if key in self.items and not isinstance(self.items[key][0],downloads.Download):
            rule_id = self.items[key][0].rule['id']
            state = self.states[self.category]['open']
            state.add(rule_id) if opened else state.discard(rule_id)

    def toggle(self,key):
        if self.working or key not in self.items:
            return
        rows = [row for row in self.items[key] if selectable(row)]
        if rows:
            value = not all(row.selected for row in rows)
            for row in rows:
                row.selected = value
            self.render()
        else:
            self.show_details(self.items[key])

    def tree_click(self,event):
        key = self.tree.identify_row(event.y)
        if not key:
            return
        column = self.tree.identify_column(event.x)
        if column=='#0' and 'indicator' not in self.tree.identify_element(event.x,event.y):
            self.toggle(key); return 'break'
        if column=='#4':
            self.show_details(self.items[key]); return 'break'

    def tree_space(self,_):
        self.toggle(self.tree.focus()); return 'break'

    def mark_all(self):
        if not self.working:
            for row in self.visible_rows():
                if selectable(row):
                    row.selected = True
            self.render()

    def clear_all(self):
        if not self.working:
            for row in self.rows[self.category]:
                row.selected = False
            self.render()

    def details_selected(self):
        key = self.tree.focus()
        if key in self.items:
            self.show_details(self.items[key])

    def dialog(self,title,size='720x520'):
        window = tk.Toplevel(self.root,bg=PANEL)
        icon = APP/'assets/brand/destralha.ico'
        if icon.exists():
            window.iconbitmap(str(icon))
        window.title('Destralha · '+title)
        window.geometry(size)
        window.minsize(580,380)
        window.transient(self.root)
        frame = tk.Frame(window,bg=PANEL,padx=26,pady=22); frame.pack(fill='both',expand=True)
        self.label(frame,title,size=20,bold=True).pack(anchor='w',pady=(0,10))
        return window,frame

    def text_view(self,parent,text):
        frame = tk.Frame(parent,bg=BG); frame.pack(fill='both',expand=True,pady=12)
        view = tk.Text(frame,bg=BG,fg=INK,insertbackground=INK,relief='flat',wrap='word',
                       font=('Segoe UI',10),padx=14,pady=12)
        scroll = ttk.Scrollbar(frame,orient='vertical',command=view.yview,style='Destralha.Vertical.TScrollbar')
        scroll.pack(side='right',fill='y'); view.configure(yscrollcommand=scroll.set)
        view.pack(fill='both',expand=True); view.insert('1.0',text); view.configure(state='disabled')
        return view

    def show_details(self,rows):
        window,frame = self.dialog(row_name(rows[0]))
        info = []
        for row in rows:
            if isinstance(row,downloads.Download):
                effect = 'Exclusão permanente de arquivo pessoal. Idade e tamanho não comprovam que seja descartável.'
                purpose = row.match + ' · Modificado em '+time.strftime('%d/%m/%Y',time.localtime(row.modified_ns/1e9))
            else:
                effect,purpose = row.rule['effect'],row.rule['purpose']
            info.append(f'{purpose}\n\nLocal: {row.path}\nTamanho: {engine.human_size(row.size)}\n\n'
                        f'Efeito: {effect}\n\nCondição: {row.reason or "Disponível; será revalidado antes de excluir."}')
        self.label(frame,'Revise o local, a consequência e a condição da exclusão.',color=MUTED).pack(anchor='w')
        footer = tk.Frame(frame,bg=PANEL); footer.pack(side='bottom',fill='x')
        self.button(footer,'Fechar',window.destroy).pack(side='right')
        self.text_view(frame,'\n\n'+'\n\n──────────\n\n'.join(info))

    def settings(self):
        if self.working:
            return
        window,frame = self.dialog('Opções','660x400')
        include = tk.BooleanVar(value=self.preferences['protected'])
        remember = tk.BooleanVar(value=self.preferences['remember'])
        for variable,text in ((include,'Incluir diretórios preservados na próxima análise'),
                              (remember,'Lembrar a seleção de caches entre execuções')):
            tk.Checkbutton(frame,text=text,variable=variable,bg=PANEL,fg=INK,selectcolor=SOFT,
                           activebackground=PANEL,activeforeground=INK,font=('Segoe UI',11),anchor='w').pack(fill='x',pady=12)
        self.label(frame,'Modelos, mídia offline, instaladores e Downloads sempre começam desmarcados.\n'
                   'Relatórios não são gravados automaticamente.',color=MUTED,justify='left',wraplength=590).pack(anchor='w',pady=15)
        def save():
            self.preferences.update(protected=include.get(),remember=remember.get())
            try:
                self.save_preferences()
                window.destroy()
            except OSError as exc:
                messagebox.showerror('Opções',str(exc),parent=window)
        self.button(frame,'Salvar opções',save,'save',primary=True).pack(anchor='e',pady=12)

    def save_preferences(self):
        if not self.preferences['remember']:
            self.preferences['selected'] = []
        else:
            self.preferences['selected'] = sorted({row.rule['id'] for row in self.rows['Caches'] if row.selected})
        (APP/'cache-preferences.json').write_text(json.dumps(self.preferences,ensure_ascii=False,indent=2),encoding='utf-8')
        (APP/'preferences.json').write_text(json.dumps(dict(remember=False,protected=self.preferences['protected'],selected=[]),ensure_ascii=False,indent=2),encoding='utf-8')

    def update_disk(self):
        try:
            usage = shutil.disk_usage(self.roots['local'].anchor)
            self.disk_bar['value'] = 100*(usage.total-usage.free)/usage.total
            self.disk_label.configure(text=engine.human_size(usage.free)+' livres')
            self.disk_capacity.configure(text='de '+engine.human_size(usage.total))
        except OSError:
            self.disk_label.configure(text='Espaço indisponível')

    def set_busy(self,value):
        self.working = value
        for button in (self.analyze_button,self.mark_button,self.clear_button):
            button.configure(state='disabled' if value else 'normal')
        self.days_input.configure(state='disabled' if value else 'normal')
        if value:
            if not self.live.winfo_manager():
                page = self.management.frame if self.mode=='management' else self.catalog_page
                self.live.pack(fill='x',before=page)
        else:
            self.live.pack_forget()
        self.render()

    def begin(self,job,title):
        self.cancel.clear()
        self.job = job
        self.progress_state = dict(title=title,phase='Preparando',current='Consultando arquivos e condições…',index=0,total=0)
        self.set_busy(True)
        self.show_progress()

    def show_progress(self):
        if not self.working:
            return
        if self.progress_window is not None and self.progress_window.winfo_exists():
            self.progress_window.deiconify(); self.progress_window.lift(); return
        window,frame = self.dialog(self.progress_state['title'],'690x420')
        self.progress_window = window
        window.protocol('WM_DELETE_WINDOW',window.withdraw)
        self.progress_phase = self.label(frame,'',size=12,bold=True)
        self.progress_phase.pack(anchor='w',pady=(15,8))
        self.progress_count = self.label(frame,'',size=11,color=ACCENT)
        self.progress_count.pack(anchor='w',pady=(0,9))
        self.progress_bar = ttk.Progressbar(frame,style='Destralha.Horizontal.TProgressbar',mode='indeterminate')
        self.progress_bar.pack(fill='x',pady=10)
        self.progress_current = self.label(frame,'',color=MUTED,wraplength=610,justify='left')
        self.progress_current.pack(anchor='w',pady=10)
        self.label(frame,'Nenhum aplicativo é encerrado. O cancelamento aguarda o alvo atual.',size=9,color=MUTED,wraplength=610).pack(anchor='w',pady=10)
        footer = tk.Frame(frame,bg=PANEL); footer.pack(side='bottom',fill='x',pady=(18,0))
        self.button(footer,'Cancelar',self.request_cancel,'clear').pack(side='right')
        self.button(footer,'Continuar em segundo plano',window.withdraw,quiet=True).pack(side='right',padx=8)
        self.update_progress()

    def request_cancel(self):
        self.cancel.set()
        self.live_text.configure(text='Cancelamento solicitado; aguardando o alvo atual…')
        if self.progress_window is not None and self.progress_window.winfo_exists():
            self.progress_current.configure(text='Cancelamento solicitado. Aguarde a conclusão do alvo atual.')

    def update_progress(self):
        state = self.progress_state
        index,total = state['index'],state['total']
        self.live_text.configure(text=f"{state['phase']} · {state['current'][:85]}")
        if self.progress_window is not None and self.progress_window.winfo_exists():
            self.progress_phase.configure(text=state['phase'])
            self.progress_current.configure(text=state['current'])
            if total:
                self.progress_bar.stop(); self.progress_bar.configure(mode='determinate',maximum=total,value=index)
                self.progress_count.configure(text=f'{index} de {total} alvos concluídos · {int(100*index/total)}% da etapa')
            else:
                self.progress_bar.configure(mode='indeterminate'); self.progress_bar.start(18)
                self.progress_count.configure(text='Analisando · total ainda não conhecido')

    def finish_progress(self):
        if self.progress_window is not None and self.progress_window.winfo_exists():
            self.progress_bar.stop()
            self.progress_window.destroy()
        self.progress_window = None
        self.job = None
        self.set_busy(False)
        self.update_disk()

    def analyze(self):
        if self.working:
            return
        if self.mode == 'management':
            self.management.analyze()
            return
        try:
            days = int(self.days.get()); downloads.validate_days(days)
        except ValueError:
            self.change_category('Downloads')
            messagebox.showerror('Prazo de Downloads','Informe um prazo entre 1 e 36500 dias.',parent=self.root)
            return
        rules = [rule for rule in self.rules if self.preferences['protected'] or not rule.get('protected')]
        self.begin('analysis','Analisando seu espaço')
        def worker():
            try:
                rows = engine.scan(rules,self.roots,
                    lambda i,n,name:self.events.put(('progress',dict(phase='1 / 2 · Diretórios conhecidos',index=i,total=n,current=name))),self.cancel)
                self.events.put(('progress',dict(phase='2 / 2 · Downloads',index=0,total=0,current='Identificando arquivos por idade e tamanho…')))
                if self.cancel.is_set():
                    download_rows,warnings = [],['Análise cancelada; resultados incompletos.']
                else:
                    try:
                        download_rows,warnings = downloads.scan_downloads(self.downloads_root,days,self.cancel)
                    except Exception as exc:
                        download_rows,warnings = [],[f'Downloads não pôde ser analisado: {exc}']
                self.events.put(('analysis',(rows,download_rows,warnings)))
            except Exception as exc:
                self.events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def review(self,simulate):
        if self.working:
            return
        selected = [row for row in self.rows[self.category] if row.selected and selectable(row)]
        if not selected:
            return
        if simulate:
            self.run_cleanup(tuple(selected),True,self.category)
            return
        visible = {row_key(row) for row in self.visible_rows()}
        hidden = sum(row_key(row) not in visible for row in selected)
        waiting = sum(bool(row.reason) for row in selected)
        window,frame = self.dialog('Revisar limpeza','760x600')
        self.label(frame,f'{len(selected)} alvos · {engine.human_size(sum(row.size for row in selected))}',size=15,bold=True).pack(anchor='w')
        self.label(frame,f'{waiting} aguardando liberação · {hidden} marcados fora do filtro atual',color=WARN).pack(anchor='w',pady=(5,12))
        self.label(frame,'A exclusão é permanente, sem Lixeira. Arquivos em uso e itens protegidos serão preservados.',
                   color=WARN,wraplength=700,justify='left').pack(anchor='w')
        if self.category=='Downloads':
            self.label(frame,'Downloads pode conter documentos, fotos, projetos e programas portáteis. Revise os arquivos marcados.',
                       color=WARN,wraplength=700,justify='left').pack(anchor='w',pady=(8,0))
        footer = tk.Frame(frame,bg=PANEL); footer.pack(side='bottom',fill='x',pady=(12,0))
        category = self.category
        def confirm():
            if self.working:
                return
            window.destroy()
            self.run_cleanup(tuple(selected),False,category)
        self.button(footer,'Confirmar exclusão',confirm,'trash',primary=True).pack(side='right')
        self.button(footer,'Voltar à seleção',window.destroy,quiet=True).pack(side='right',padx=8)
        text = []
        for row in selected:
            effect = 'Exclusão permanente de arquivo pessoal.' if isinstance(row,downloads.Download) else row.rule['effect']
            text.append(f'{row_name(row)} · {engine.human_size(row.size)}\n{row.path}\n{effect}')
        self.text_view(frame,'\n\n'.join(text))
        window.grab_set()

    def run_cleanup(self,selected,simulate,category):
        """Recebe snapshot da confirmação; nunca usa uma seleção alterada depois."""
        if self.working:
            return
        self.begin('simulation' if simulate else 'cleanup','Simulando a seleção' if simulate else 'Limpando a seleção')
        def worker():
            results,removed = [],[]
            freed = 0
            for index,row in enumerate(selected):
                if self.cancel.is_set():
                    break
                self.events.put(('progress',dict(phase='Simulação' if simulate else 'Limpeza da seleção',
                                                index=index,total=len(selected),current=row_name(row))))
                record = dict(path=str(row.path),name=row_name(row),size=row.size)
                try:
                    if isinstance(row,downloads.Download):
                        if simulate:
                            # A simulação repete a descoberta e todos os guardas sem abrir para exclusão.
                            reason = downloads.blocked_reason(row.root,row.path,
                                downloads.source_roots(row.root,list(engine.installation_records())),engine.process_snapshot())
                            info = engine.check_item(engine.safe_path(row.root,row.path))
                            if row.root!=downloads.downloads_root() or (info.st_size,info.st_mtime_ns,info.st_ino)!=(row.size,row.modified_ns,row.identity):
                                raise engine.Refused('Arquivo mudou; analise novamente.')
                            if reason: raise engine.Refused(reason)
                            size = row.size
                        else:
                            size = downloads.delete_download(row)
                    else:
                        if simulate:
                            reason = engine.busy_reason(row.rule,row.root,engine.process_snapshot()) or engine.special_reason(row.rule,row.root,row.path,self.roots)
                            if reason: raise engine.Refused(reason)
                            size,_ = engine.measure(row.root,row.path,self.cancel)
                        else:
                            size,_ = engine.delete_candidate(row,self.roots)
                    record.update(result='Simulado' if simulate else 'Excluído',bytes=size)
                    if not simulate:
                        removed.append(row); freed += size
                except Exception as exc:
                    record.update(result='Preservado / falha',reason=str(exc))
                results.append(record)
            report = dict(category=category,simulation=simulate,canceled=self.cancel.is_set(),
                          selected=len(selected),unprocessed=len(selected)-len(results),
                          complete_targets_bytes=freed,results=results)
            self.events.put(('cleaned',(report,removed)))
        threading.Thread(target=worker,daemon=True).start()

    def pump(self):
        try:
            while True:
                kind,payload = self.events.get_nowait()
                if kind.startswith('managed_') and self.management is not None:
                    self.management.on_event(kind,payload)
                elif kind=='progress':
                    self.progress_state.update(payload); self.update_progress()
                elif kind=='analysis':
                    catalog,download_rows,self.warnings = payload
                    self.analyzed = True
                    self.rows = {category:[] for category in CATEGORIES}
                    for row in catalog:
                        category = category_of(row.rule)
                        row.selected = bool(category=='Caches' and self.preferences['remember'] and row.rule['id'] in self.preferences['selected'] and selectable(row))
                        self.rows[category].append(row)
                    self.rows['Downloads'] = download_rows
                    self.finish_progress()
                    note = 'Análise cancelada; resultados incompletos.' if self.cancel.is_set() else 'Análise concluída; nenhum arquivo excluído.'
                    if self.warnings:
                        note += f' {len(self.warnings)} aviso(s); veja Relatório.'
                    self.visible_label.configure(text=note)
                    self.report = dict(category='Todas',analysis=True,canceled=self.cancel.is_set(),
                                       counts={key:len(value) for key,value in self.rows.items()},warnings=self.warnings,results=[])
                elif kind=='cleaned':
                    self.report,removed = payload
                    removed_keys = {row_key(row) for row in removed}
                    category = self.report['category']
                    self.rows[category] = [row for row in self.rows[category] if row_key(row) not in removed_keys]
                    reasons = {record['path']:record.get('reason') for record in self.report['results'] if record.get('reason')}
                    for row in self.rows[category]:
                        if str(row.path) in reasons:
                            row.reason = reasons[str(row.path)]
                    self.finish_progress()
                    self.show_report()
                elif kind=='error':
                    self.finish_progress()
                    messagebox.showerror('Destralha',payload,parent=self.root)
        except queue.Empty:
            pass
        self.timer = self.root.after(80,self.pump)

    def report_text(self):
        if self.report is None:
            return 'Nenhuma operação nesta sessão. Relatórios não são gravados automaticamente.'
        data = self.report
        if data.get('management'):
            return data['title']+'\n\n'+data['text']
        if data.get('analysis'):
            lines = ['ANÁLISE '+('CANCELADA' if data['canceled'] else 'CONCLUÍDA'),
                     ' · '.join(f'{key}: {value} alvos' for key,value in data['counts'].items()),
                     'Nenhum arquivo excluído.']
            lines += data.get('warnings',[])
        else:
            lines = ['SIMULAÇÃO' if data['simulation'] else 'LIMPEZA',f"Categoria: {data['category']}",
                     f"{data['selected']} alvos selecionados · {data['unprocessed']} não processados",
                     'Cancelado: '+('Sim' if data['canceled'] else 'Não')]
            if not data['simulation']:
                lines.append('Alvos completos removidos: '+engine.human_size(data['complete_targets_bytes']))
            for record in data['results']:
                lines += ['',f"{record['result']} · {record['name']}",record['path']]
                if record.get('reason'):
                    lines.append(record['reason']+' Se a remoção já havia começado, pode haver exclusão parcial do alvo.')
        return '\n'.join(lines)

    def show_report(self):
        window,frame = self.dialog('Resultado da sessão','800x600')
        self.label(frame,'Somente na memória. Salvar é a única ação que grava este relatório.',color=MUTED,wraplength=730).pack(anchor='w')
        footer = tk.Frame(frame,bg=PANEL); footer.pack(side='bottom',fill='x')
        snapshot = self.report
        text = self.report_text()
        self.button(footer,'Concluir',window.destroy,primary=True).pack(side='right')
        if snapshot is not None:
            self.button(footer,'Salvar relatório',lambda:self.export_report(snapshot,text),'save').pack(side='right',padx=8)
        self.text_view(frame,text)

    def export_report(self,report=None,text=None):
        report = self.report if report is None else report
        if report is None:
            return
        target = filedialog.asksaveasfilename(parent=self.root,title='Salvar relatório Destralha',
                    initialfile='Destralha-relatorio.json',defaultextension='.json',filetypes=[('JSON','*.json'),('Texto','*.txt')])
        if target:
            try:
                content = json.dumps(report,ensure_ascii=False,indent=2) if Path(target).suffix.casefold()=='.json' else (text or self.report_text())
                Path(target).write_text(content,encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('Salvar relatório',str(exc),parent=self.root)

    def close(self):
        if self.working:
            self.request_cancel()
            messagebox.showinfo('Tarefa em andamento','Cancelamento solicitado. Aguarde a tarefa terminar antes de fechar.',parent=self.root)
            return
        if self.preferences['remember']:
            try:
                self.save_preferences()
            except OSError as exc:
                messagebox.showwarning('Opções',str(exc),parent=self.root)
        self.dispose()

    def dispose(self):
        self.root.after_cancel(self.timer)
        if self.management is not None:self.management.close()
        if self.progress_window is not None and self.progress_window.winfo_exists():
            self.progress_bar.stop()
        self.root.update_idletasks()
        self.root.destroy()


def main():
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Rodrigo.Destralha')
    root = tk.Tk()
    try:
        app = DestralhaApp(root)
        import sys
        if '--programas' in sys.argv:root.after_idle(app.open_management)
    except Exception as exc:
        messagebox.showerror('Não foi possível abrir Destralha',str(exc),parent=root)
        root.destroy(); return
    root.mainloop()


if __name__=='__main__':
    main()
