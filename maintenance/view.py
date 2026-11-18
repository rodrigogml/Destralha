"""Área de gerenciamento por instalação, integrada à interface Destralha."""
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk,messagebox

import engine
from . import mysql,windows,exports


class MaintenanceView:
    def __init__(self,app,parent):
        from destralha import BG,PANEL,MUTED,INK,WARN
        self.app=app
        self.palette=(BG,PANEL,MUTED,INK,WARN)
        self.installations=[]
        self.inventories={}
        self.current=None
        self.credentials=None
        self.snapshot=None
        self.connected_key=None
        self.controls=[]
        self.last_progress=0.0
        self.frame=tk.Frame(parent,bg=BG,padx=24,pady=20)
        app.label(self.frame,'Programas e sistema',size=23,bold=True).pack(anchor='w')
        app.label(self.frame,'Entenda o espaço de cada instalação. Medido não significa descartável.',color=MUTED).pack(anchor='w',pady=(5,13))
        toolbar=tk.Frame(self.frame,bg=BG);toolbar.pack(fill='x',pady=(0,12))
        self.discover_button=app.button(toolbar,'Detectar instalações',self.discover,'search')
        self.discover_button.pack(side='left')
        self.analyze_button=app.button(toolbar,'Analisar instalação',self.analyze,'disk')
        self.analyze_button.pack(side='left',padx=9)
        body=tk.Frame(self.frame,bg=BG);body.pack(fill='both',expand=True)
        right=tk.Frame(body,bg=BG);right.pack(side='left',fill='both',expand=True)
        self.name=app.label(right,'Selecione uma instalação',size=17,bold=True)
        self.name.pack(anchor='w')
        self.location=app.label(right,'Detectamos serviços e instalações locais de MySQL, além do Windows.',color=MUTED,wraplength=600,justify='left')
        self.location.pack(anchor='w',pady=(4,10))
        self.total=app.label(right,'Ainda não medido',size=18,bold=True)
        self.total.pack(anchor='w',pady=(0,5))
        self.status=app.label(right,'Use Detectar instalações para começar.',size=9,color=MUTED,wraplength=600,justify='left')
        self.status.pack(anchor='w',pady=(0,10))
        self.actions=tk.Frame(right,bg=BG)
        self.actions.pack(side='bottom',fill='x',pady=(10,0))
        suggestions_label=app.label(right,'Sugestões e condições',size=11,bold=True)
        notes=tk.Frame(right,bg=PANEL)
        notes.pack(side='bottom',fill='x')
        self.notes=tk.Text(notes,height=4,bg=PANEL,fg=INK,relief='flat',wrap='word',font=('Segoe UI',10),padx=12,pady=10)
        scrollbar=ttk.Scrollbar(notes,orient='vertical',command=self.notes.yview,style='Destralha.Vertical.TScrollbar')
        scrollbar.pack(side='right',fill='y');self.notes.configure(yscrollcommand=scrollbar.set)
        self.notes.pack(fill='both',expand=True)
        suggestions_label.pack(side='bottom',anchor='w',pady=(10,4))
        table=tk.Frame(right,bg=PANEL);table.pack(fill='both',expand=True)
        ttk.Style().configure('Management.Destralha.Treeview',rowheight=32)
        self.groups=ttk.Treeview(table,columns=('name','size','files'),style='Management.Destralha.Treeview',height=6,selectmode='browse')
        self.groups.heading('#0',text='Detalhes')
        self.groups.column('#0',width=70,minwidth=70,stretch=False,anchor='center')
        self.groups.heading('name',text='Tipo de conteúdo')
        self.groups.heading('size',text='Espaço lógico')
        self.groups.heading('files',text='Arquivos')
        self.groups.column('name',width=450,minwidth=170)
        self.groups.column('size',width=115,stretch=False,anchor='e')
        self.groups.column('files',width=85,stretch=False,anchor='e')
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.groups.yview,style='Destralha.Vertical.TScrollbar')
        scroll.pack(side='right',fill='y');self.groups.configure(yscrollcommand=scroll.set)
        self.groups.pack(fill='both',expand=True)
        self.groups.bind('<Double-1>',lambda event:self.details() if self.groups.identify_column(event.x)!='#0' else 'break')
        self.groups.bind('<ButtonRelease-1>',self.details_click)
        self.groups.bind('<Return>',lambda event:self.details())
        self.group_names={}

    def activate(self,key=None):
        if key is not None:
            self.requested_key=key
            self.select_installation(key)
        self.frame.pack(fill='both',expand=True)
        self.refresh_controls()
        if not self.installations and not self.app.working:self.discover()

    def refresh_controls(self):
        state='disabled' if self.app.working else 'normal'
        self.discover_button.configure(state=state)
        self.analyze_button.configure(state=state if self.current else 'disabled')
        for button,allowed in self.controls:
            button.configure(state=state if allowed else 'disabled')

    def task(self,title,worker):
        if self.app.working:return
        self.app.begin('management',title)
        def run():
            try:worker()
            except Exception as exc:
                message=str(exc)
                if self.credentials and self.credentials.password:
                    message=message.replace(self.credentials.password,'[oculto]')
                self.app.events.put(('managed_error',message))
        threading.Thread(target=run,daemon=True).start()

    def progress(self,count,path):
        now=time.monotonic()
        if now-self.last_progress<.15:return
        self.last_progress=now
        self.app.events.put(('progress',dict(phase='Inventário por metadados',index=0,total=0,
                                             current=f'{count:,} arquivos · {path}')))

    def discover(self):
        def worker():
            items,warnings=mysql.discover()
            self.app.events.put(('managed_discovery',([windows.installation(),*items],warnings)))
        self.task('Detectando instalações',worker)

    def select_installation(self,key):
        if self.app.working:return
        item=next((item for item in self.installations if item.key==key),None)
        if item:
            if self.current is None or self.current.key!=item.key:
                self.disconnect()
            self.current=item;self.render()

    def render(self):
        if self.current is None:return
        item=self.current
        self.name.configure(text=item.name)
        state={'Running':'Em execução','Stopped':'Parado'}.get(item.state,item.state)
        location=f'{item.root}\n{state}'
        if item.data is not None:location+=f' · Dados: {item.data}'
        self.location.configure(text=location)
        self.groups.delete(*self.groups.get_children());self.group_names.clear()
        inventory=self.inventories.get(item.key)
        if inventory:
            self.total.configure(text=engine.human_size(inventory.total)+' inventariados')
            self.status.configure(text=f'{len(inventory.files):,} arquivos únicos · '+
                                  ('Medição cancelada / parcial' if inventory.canceled else 'Medição com avisos' if inventory.warnings else 'Medição concluída')+
                                  ' · Clique no ícone de detalhes para consultar arquivos e ações.')
            for index,(name,values) in enumerate(inventory.groups().items()):
                key=str(index);self.group_names[key]=name
                self.groups.insert('','end',iid=key,image=self.app.icon('eye'),values=(name,engine.human_size(values['bytes']),f"{values['files']:,}"))
        else:
            self.total.configure(text='Ainda não medido')
            self.status.configure(text='Clique em Analisar instalação para medir este escopo.')
        self.set_notes(self.suggestions(inventory))
        self.build_actions()
        self.refresh_controls()

    def set_notes(self,text):
        self.notes.configure(state='normal');self.notes.delete('1.0','end');self.notes.insert('1.0',text);self.notes.configure(state='disabled')

    def suggestions(self,inventory):
        if self.current.kind=='mysql':
            lines=['• Binlogs: expurgo somente pelo MySQL, após confirmar backups e réplicas inclusive desconectadas.',
                   '• Tabelas, índices e recuperação: preservados. O tamanho de um .ibd não é espaço livre.',
                   '• A conexão consulta metadados e confirma o datadir antes de permitir manutenção.']
            if self.current.state=='Stopped':
                lines.append('• Serviço parado: não será iniciado automaticamente. A porta configurada pode responder por outra instância; o datadir será conferido.')
            if inventory:
                logs=[item for item in inventory.files if item.category=='Logs binários']
                if logs:
                    recent=sum(item.size for item in logs if item.modified>time.time()-86400)
                    lines.append(f'• Binlogs com data nas últimas 24 h: {engine.human_size(recent)}. Retenção por dias pode não resolver crescimento recente.')
                candidates=sorted((item for item in inventory.files if item.category=='Tabelas e índices' and any(token in item.path.stem.casefold() for token in ('log','history','audit','failed'))),key=lambda item:item.size,reverse=True)
                if candidates:lines.append(f'• Nome sugere histórico: {candidates[0].path.name} ({engine.human_size(candidates[0].size)}). Confirme a função da tabela; não exclua o arquivo.')
            if self.snapshot:
                values=self.snapshot.variables
                lines.append(f"• Retenção informada pelo servidor: {values.get('binlog_expire_logs_seconds','não disponível')} segundos; expire_logs_days={values.get('expire_logs_days','não disponível')}.")
                lines.extend('• '+warning for warning in self.snapshot.warnings)
        else:
            lines=['• WinSxS: use a análise oficial do DISM antes de decidir pela limpeza de componentes substituídos.',
                   '• Windows Installer, Package Cache, DriverStore e memória virtual são preservados.',
                   '• Hibernação ocupa espaço, mas desativá-la também desativa Inicialização Rápida e suspensão híbrida.',
                   '• Nenhuma ação usa /ResetBase, apaga componentes manualmente ou encerra aplicativos.']
            if not windows.is_admin():lines.append('• DISM e hibernação requerem administrador. O botão de elevação abre outra janela; nenhuma limpeza começa sozinha.')
        if inventory:
            lines.extend('• '+warning for warning in inventory.warnings)
        return '\n\n'.join(lines)

    def build_actions(self):
        for child in self.actions.winfo_children():child.destroy()
        self.controls=[]
        def add(text,callback,allowed=True):
            index=len(self.controls)
            button=self.app.button(self.actions,text,callback,quiet=True)
            button.grid(row=index//2,column=index%2,sticky='w',padx=(0,12),pady=3)
            self.controls.append((button,allowed))
        if self.current.kind=='mysql':
            add('Conectar / consultar MySQL',self.connect_dialog)
            eligible=bool(self.snapshot and self.credentials and self.connected_key==self.current.key and
                          self.snapshot.replication_checked and not self.snapshot.replicas and not self.snapshot.replica_channels and len(self.snapshot.binary_logs)>1)
            add('Expurgar binlogs…',self.purge_dialog,eligible)
            add('Ver consulta do servidor',self.server_details,self.snapshot is not None)
        else:
            admin=windows.is_admin()
            add('Analisar componentes (DISM)',lambda:self.native_action('analyze_components'),admin)
            add('Limpar componentes…',lambda:self.native_action('clean_components'),admin)
            add('Abrir armazenamento Windows',lambda:self.open_tool('storage'))
            add('Abrir Limpeza de Disco',lambda:self.open_tool('cleanup'))
            add('Desativar hibernação…',lambda:self.native_action('disable_hibernation'),admin)
            if not admin:add('Abrir como administrador',self.elevate)

    def analyze(self):
        if self.current is None:
            self.discover();return
        item=self.current
        def worker():
            inventory=(mysql.inventory if item.kind=='mysql' else windows.inventory)(item,self.app.cancel,self.progress)
            self.app.events.put(('managed_inventory',inventory))
        self.task('Analisando '+item.name,worker)

    def details_click(self,event):
        key=self.groups.identify_row(event.y)
        if key and self.groups.identify_column(event.x)=='#0':
            self.groups.selection_set(key);self.details()
            return 'break'

    def details(self):
        if not self.current:return
        inventory=self.inventories.get(self.current.key)
        selection=self.groups.selection()
        if not inventory or not selection:return
        category=self.group_names[selection[0]]
        files=sorted((item for item in inventory.files if item.category==category),key=lambda item:item.size,reverse=True)
        window,frame=self.app.dialog(category,'1040x650')
        item=self.current
        removable=item.kind=='mysql' and category==exports.CATEGORY
        reasons={str(index):exports.reason(item,record) if removable else 'Preservado: use a manutenção específica desta instalação.' for index,record in enumerate(files)}
        selected=set()
        page=[0];page_size=200
        self.app.label(frame,('Revise as cópias que não precisa guardar. A exclusão é permanente; pode remover sua única cópia de recuperação.' if removable else
                       'Arquivos preservados. Binlogs exigem expurgo pelo MySQL; componentes do Windows usam manutenção nativa.'),wraplength=960,justify='left',color=self.palette[4]).pack(anchor='w')
        footer=tk.Frame(frame,bg=self.palette[1]);footer.pack(side='bottom',fill='x')
        self.app.button(footer,'Fechar',window.destroy).pack(side='right')
        status=self.app.label(frame,'',size=10);status.pack(anchor='w',pady=8)
        toolbar=tk.Frame(frame,bg=self.palette[1]);toolbar.pack(fill='x',pady=(0,8))
        table_frame=tk.Frame(frame,bg=self.palette[0]);table_frame.pack(fill='both',expand=True)
        table=ttk.Treeview(table_frame,columns=('path','size','date','condition'),style='Management.Destralha.Treeview',selectmode='browse')
        for column,title,width in (('#0','Marcar',60),('path','Arquivo / caminho',390),('size','Tamanho',100),('date','Modificado',95),('condition','Condição',220)):
            table.heading(column,text=title);table.column(column,width=width,minwidth=50,stretch=column in ('path','condition'))
        scroll=ttk.Scrollbar(table_frame,command=table.yview,style='Destralha.Vertical.TScrollbar');scroll.pack(side='right',fill='y')
        horizontal=ttk.Scrollbar(table_frame,orient='horizontal',command=table.xview,style='Destralha.Horizontal.TScrollbar');horizontal.pack(side='bottom',fill='x')
        table.configure(yscrollcommand=scroll.set,xscrollcommand=horizontal.set);table.pack(fill='both',expand=True)
        def render():
            table.delete(*table.get_children())
            start=page[0]*page_size
            for index in range(start,min(start+page_size,len(files))):
                record=files[index]
                key=str(index)
                values=(str(record.path),engine.human_size(record.size),time.strftime('%d/%m/%Y',time.localtime(record.modified)),reasons[key] or 'Disponível para revisão')
                text='☑' if key in selected else '☐' if removable and not reasons[key] else '—'
                if table.exists(key):table.item(key,text=text,values=values)
                else:table.insert('','end',iid=key,text=text,values=values)
            status.configure(text=f'{len(files)} arquivos · Página {page[0]+1}/{max(1,(len(files)+page_size-1)//page_size)} · '
                                  f'{len(selected)} marcados no grupo · {engine.human_size(sum(files[int(key)].size for key in selected))}')
            if removable:delete_button.configure(state='normal' if selected and not self.app.working else 'disabled')
            previous.configure(state='normal' if page[0]>0 else 'disabled')
            following.configure(state='normal' if start+page_size<len(files) else 'disabled')
        def navigate(offset):
            page[0]+=offset;render()
        def mark(all_items):
            if self.app.working:return
            selected.clear()
            if all_items:selected.update(key for key,reason in reasons.items() if not reason)
            render()
        def toggle(event):
            if self.app.working:return
            key=table.identify_row(event.y) if event.keysym!='space' else table.focus()
            if key and not reasons[key] and (event.keysym=='space' or table.identify_column(event.x)=='#0'):
                selected.remove(key) if key in selected else selected.add(key);render();return 'break'
        def remove():
            if self.app.working or not selected:return
            records=tuple(files[int(key)] for key in sorted(selected,key=int))
            text=f'{len(records)} arquivos · {engine.human_size(sum(record.size for record in records))}\n\n'
            text+='\n'.join(str(record.path) for record in records[:12])
            if len(records)>12:text+=f'\n… e mais {len(records)-12} arquivos listados na tabela.'
            text+='\n\nConfirma que não precisa destas cópias para recuperação ou importação e deseja excluí-las permanentemente?'
            if not messagebox.askyesno('Excluir exportações / backups',text,default='no',parent=window):return
            window.destroy();self.delete_exports(item,records)
        if removable:
            self.app.button(toolbar,'Marcar Tudo',lambda:mark(True),'check').pack(side='left')
            self.app.button(toolbar,'Desmarcar Tudo',lambda:mark(False),'clear',quiet=True).pack(side='left',padx=8)
            delete_button=self.app.button(footer,'Excluir marcados',remove,'trash',primary=True)
            delete_button.pack(side='right',padx=8)
            table.bind('<Button-1>',toggle);table.bind('<space>',toggle)
        elif item.kind=='mysql' and category=='Logs binários':
            self.app.button(footer,'Consultar / expurgar pelo MySQL',lambda:(window.destroy(),self.purge_dialog() if self.snapshot and self.credentials else self.connect_dialog()),'gear').pack(side='left')
        following=self.app.button(toolbar,'Próxima página',lambda:navigate(1),quiet=True);following.pack(side='right')
        previous=self.app.button(toolbar,'Página anterior',lambda:navigate(-1),quiet=True);previous.pack(side='right')
        render()

    def delete_exports(self,item,records):
        def worker():
            results=[];removed=[]
            for index,record in enumerate(records):
                if self.app.cancel.is_set():break
                self.app.events.put(('progress',dict(phase='Exclusão de exportações',index=index,total=len(records),current=str(record.path))))
                try:
                    size=exports.delete(item,record);removed.append(record)
                    results.append(dict(path=str(record.path),result='Excluído',bytes=size))
                except Exception as exc:results.append(dict(path=str(record.path),result='Preservado / falha',reason=str(exc)))
            self.app.events.put(('managed_exports',(item.key,removed,results,len(records))))
        self.task('Excluindo exportações selecionadas',worker)

    def disconnect(self):
        self.credentials=None;self.snapshot=None;self.connected_key=None

    def connect_dialog(self):
        if self.app.working or not self.current or self.current.kind!='mysql':return
        item=self.current
        window,frame=self.app.dialog('Consultar MySQL','690x470')
        self.app.label(frame,f'{item.name}\n{item.data}\nConexão local: 127.0.0.1',wraplength=610,justify='left').pack(anchor='w')
        variables={}
        for name,title,value,hidden in (('user','Usuário','',False),('password','Senha','',True),('port','Porta',str(item.port),False)):
            self.app.label(frame,title,size=10).pack(anchor='w',pady=(10,3))
            variable=tk.StringVar(value=value)
            tk.Entry(frame,textvariable=variable,show='*' if hidden else '',bg=self.palette[0],fg=self.palette[3],
                     insertbackground=self.palette[3],relief='flat',font=('Segoe UI',11)).pack(fill='x',ipady=6)
            variables[name]=variable
        self.app.label(frame,'Senha somente em memória; não será gravada em preferências ou relatórios.',size=9,color=self.palette[2]).pack(anchor='w',pady=12)
        def submit():
            try:
                credentials=mysql.Credentials(variables['user'].get(),variables['password'].get(),int(variables['port'].get()))
                if not credentials.user.strip() or not 1<=credentials.port<=65535:raise ValueError()
            except ValueError:
                messagebox.showerror('MySQL','Informe usuário e porta válidos.',parent=window);return
            variables['password'].set('');self.disconnect();window.destroy()
            def worker():
                connection=None
                try:
                    connection=mysql.connect(credentials)
                    snapshot=mysql.inspect(connection,item)
                    if self.app.cancel.is_set():raise engine.Refused('Consulta cancelada; credenciais não foram mantidas.')
                    self.app.events.put(('managed_connected',(item.key,credentials,snapshot)))
                except Exception as exc:
                    message=str(exc)
                    if credentials.password:message=message.replace(credentials.password,'[oculto]')
                    self.app.events.put(('managed_error',message))
                finally:
                    if connection is not None:connection.close()
            self.task('Consultando metadados MySQL',worker)
        self.app.button(frame,'Conectar e consultar',submit,'search',primary=True).pack(anchor='e',pady=10)
        window.grab_set()

    def server_text(self):
        snapshot=self.snapshot
        if snapshot is None:return ''
        lines=[f'Versão: {snapshot.version}',f'Datadir confirmado: {snapshot.data_dir}',
               f'Arquivo ativo: {snapshot.current_log or "não confirmado"}',
               f'Binlogs: {len(snapshot.binary_logs)} · {engine.human_size(sum(size for _,size in snapshot.binary_logs))}',
               f'Réplicas registradas nesta consulta: {len(snapshot.replicas)}',
               f'Canais de réplica desta instância: {len(snapshot.replica_channels)}',
               'Lista vazia não prova a inexistência de réplicas desconectadas.','', 'Retenção e opções:']
        lines += [f'{key}: {value}' for key,value in snapshot.variables.items()]
        lines += ['', 'Maiores tabelas visíveis a esta conta (estimativas do servidor):']
        for table in snapshot.tables:
            size=int(table.get('DATA_LENGTH') or 0)+int(table.get('INDEX_LENGTH') or 0)
            lines.append(f"{table['TABLE_SCHEMA']}.{table['TABLE_NAME']} · {engine.human_size(size)} · espaço interno livre: {engine.human_size(int(table.get('DATA_FREE') or 0))}")
        lines.extend(snapshot.warnings)
        return '\n'.join(lines)

    def server_details(self):
        if self.snapshot is None:return
        window,frame=self.app.dialog('Consulta do servidor MySQL','820x600')
        footer=tk.Frame(frame,bg=self.palette[1]);footer.pack(side='bottom',fill='x')
        self.app.button(footer,'Fechar',window.destroy).pack(side='right')
        self.app.text_view(frame,self.server_text())

    def purge_dialog(self):
        if self.app.working or self.snapshot is None or self.credentials is None or self.current.key!=self.connected_key:return
        item,snapshot,credentials=self.current,self.snapshot,self.credentials
        window,frame=self.app.dialog('Expurgo de binlogs','800x600')
        self.app.label(frame,f'{item.name}\n{snapshot.data_dir}',wraplength=720,justify='left').pack(anchor='w')
        self.app.label(frame,'Escolha o primeiro arquivo que será MANTIDO. Somente os anteriores serão candidatos.',wraplength=720,justify='left',color=self.palette[4]).pack(anchor='w',pady=12)
        boundary=tk.StringVar()
        combo=ttk.Combobox(frame,textvariable=boundary,state='readonly',values=[name for name,_ in snapshot.binary_logs[1:]],style='Destralha.TCombobox')
        combo.pack(fill='x')
        plan_label=self.app.label(frame,'Nenhum expurgo selecionado.',wraplength=720,justify='left')
        plan_label.pack(anchor='w',pady=12)
        backup=tk.BooleanVar(value=False);replication=tk.BooleanVar(value=False)
        for variable,text in ((backup,'Revisei meus backups e não preciso deste intervalo para recuperação.'),
                              (replication,'Confirmei que nenhuma réplica, inclusive desconectada, precisa deste intervalo.')):
            tk.Checkbutton(frame,text=text,variable=variable,bg=self.palette[1],fg=self.palette[3],selectcolor=self.palette[0],
                           activebackground=self.palette[1],activeforeground=self.palette[3],wraplength=710,justify='left').pack(anchor='w',pady=8)
        self.app.label(frame,'O expurgo é permanente. Réplicas desconectadas não são comprovadas pela consulta atual.\n'
                       'Nenhuma tabela será limpa; nenhum arquivo do banco será apagado pelo Explorer.',wraplength=720,justify='left',color=self.palette[4]).pack(anchor='w',pady=12)
        def make_plan():
            try:
                plan=mysql.plan_purge(snapshot,boundary.get())
                plan_label.configure(text=f'{len(plan.files)} arquivos anteriores · {engine.human_size(plan.size)} candidatos.\nManter: {plan.boundary} e posteriores.')
            except engine.Refused as exc:plan_label.configure(text=str(exc))
        combo.bind('<<ComboboxSelected>>',lambda event:make_plan())
        def confirm():
            try:
                plan=mysql.plan_purge(snapshot,boundary.get())
                if not backup.get() or not replication.get():raise engine.Refused('Confirme a revisão de backups e de réplicas antes de continuar.')
            except engine.Refused as exc:
                messagebox.showerror('Expurgo bloqueado',str(exc),parent=window);return
            if not messagebox.askyesno('Confirmar expurgo permanente',f'{item.name}\n{len(plan.files)} binlogs anteriores a {plan.boundary}\n'
                                     f'{engine.human_size(plan.size)} candidatos\n\nConfirma o expurgo pelo MySQL?',default='no',parent=window):return
            window.destroy()
            def worker():
                connection=mysql.connect(credentials)
                try:
                    result=mysql.purge(connection,item,plan,backups_reviewed=True,replication_reviewed=True,cancel=self.app.cancel)
                finally:connection.close()
                self.app.events.put(('managed_action',dict(title='Expurgo MySQL',text=f"Expurgados pelo servidor: {result['files']} arquivos · {engine.human_size(result['bytes'])}.\n"
                                                         f"Preservados pelo servidor: {result['preserved']}.\nAnalise ou consulte novamente para atualizar os tamanhos.",result=result)))
            self.task('Revalidando e expurgando binlogs',worker)
        self.app.button(frame,'Revisar e confirmar expurgo',confirm,'trash',primary=True).pack(anchor='e',pady=12)
        window.grab_set()

    def native_action(self,action):
        if self.app.working or not self.current or self.current.kind!='windows':return
        titles={'analyze_components':'Análise de componentes do Windows','clean_components':'Limpeza de componentes substituídos',
                'disable_hibernation':'Desativar hibernação'}
        if action not in titles:return
        if not windows.is_admin():
            messagebox.showinfo('Administrador','Use Abrir como administrador para esta operação.',parent=self.app.root);return
        if action!='analyze_components':
            effect=('O Windows removerá componentes substituídos pelo DISM. Não é usado /ResetBase. Aguarde a conclusão; não encerraremos o processo.'
                    if action=='clean_components' else 'Desativa hibernação, Inicialização Rápida e suspensão híbrida. O Windows remove seu arquivo de hibernação. Para restaurar: powercfg /hibernate on.')
            if not messagebox.askyesno(titles[action],effect+'\n\nConfirma executar esta operação nativa?',default='no',parent=self.app.root):return
        def worker():
            def progress(line,percent):
                self.app.events.put(('progress',dict(phase=titles[action],current=line,index=percent or 0,total=100 if percent is not None else 0)))
            result=windows.run(action,progress,self.app.cancel)
            text=result['output'] or 'Operação concluída pelo Windows.'
            if result['restart_required']:text+='\n\nO Windows informou que é necessário reiniciar. Nenhuma reinicialização foi iniciada.'
            text+='\n\nAnalise novamente para atualizar os tamanhos.'
            self.app.events.put(('managed_action',dict(title=titles[action],text=text,result=result)))
        self.task(titles[action],worker)

    def elevate(self):
        if self.app.working:return
        try:windows.open_elevated()
        except engine.Refused as exc:messagebox.showerror('Administrador',str(exc),parent=self.app.root)

    def open_tool(self,tool):
        if self.app.working:return
        try:windows.open_tool(tool)
        except (OSError,engine.Refused) as exc:messagebox.showerror('Windows',str(exc),parent=self.app.root)

    def on_event(self,kind,payload):
        if kind=='managed_discovery':
            self.installations,warnings=payload
            self.disconnect()
            self.current=next((item for item in self.installations if item.key==getattr(self,'requested_key',None)),
                              next((item for item in self.installations if item.kind=='mysql'),self.installations[0]))
            self.app.refresh_installations()
            self.app.report=dict(management=True,title='Instalações detectadas',text='\n\n'.join(
                f'{item.name}\n{item.root}\n{item.state}' for item in self.installations)+'\n\n'+'\n'.join(warnings))
            self.app.finish_progress();self.render()
            if warnings:self.set_notes('\n'.join(warnings)+'\n\n'+self.suggestions(None))
        elif kind=='managed_inventory':
            old=self.inventories.get(payload.installation.key)
            self.inventories[payload.installation.key]=payload
            text=f'{payload.installation.name}\n{payload.installation.root}\nTotal lógico inventariado: {engine.human_size(payload.total)}\n\n'
            text+='\n'.join(f"{name}: {engine.human_size(value['bytes'])} · {value['files']} arquivos" for name,value in payload.groups().items())
            text+='\n\n'+'\n'.join(payload.warnings)
            if old and not payload.canceled and not old.canceled:
                text+=f'\n\nVariação desde a medição anterior nesta sessão: {(payload.total-old.total)/1024**3:+.2f} GiB. Não é estimativa de espaço recuperável.'
            self.app.report=dict(management=True,title='Inventário por instalação',text=text)
            self.app.finish_progress();self.render()
        elif kind=='managed_connected':
            key,credentials,snapshot=payload
            self.disconnect();self.credentials=credentials;self.connected_key=key;self.snapshot=snapshot
            self.app.report=dict(management=True,title='Consulta MySQL',text=self.server_text())
            self.app.finish_progress();self.render();self.server_details()
        elif kind=='managed_action':
            self.app.report=dict(management=True,**payload)
            self.app.finish_progress();self.render();self.app.show_report()
        elif kind=='managed_exports':
            key,removed,results,total=payload
            inventory=self.inventories.get(key)
            if inventory:inventory.files=[record for record in inventory.files if record not in removed]
            text='\n\n'.join(f"{result['result']} · {result['path']}\n"+(result.get('reason') or engine.human_size(result['bytes'])) for result in results)
            text+=f'\n\n{total-len(results)} não processados.'
            self.app.report=dict(management=True,title='Exclusão de exportações',text=text,results=results,canceled=self.app.cancel.is_set())
            self.app.finish_progress();self.render();self.app.show_report()
        elif kind=='managed_error':
            self.app.report=dict(management=True,title='Operação não concluída',text=payload)
            self.app.finish_progress()
            messagebox.showerror('Gerenciamento',payload,parent=self.app.root)

    def close(self):
        self.disconnect()
