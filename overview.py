"""Visão geral da análise, com atalhos e estimativas de escopo explícito."""
import tkinter as tk
from tkinter import ttk

import engine
from maintenance import exports


class Overview:
    def __init__(self,app,parent):
        from destralha import BG,PANEL,MUTED,ACCENT
        self.app=app
        self.frame=tk.Frame(parent,bg=BG,padx=24,pady=22)
        app.label(self.frame,'Visão geral',size=24,bold=True).pack(anchor='w')
        app.label(self.frame,'Analise o computador e escolha onde recuperar espaço.',color=MUTED).pack(anchor='w',pady=(5,20))
        metrics=tk.Frame(self.frame,bg=BG);metrics.pack(fill='x',pady=(0,18))
        self.values=[]
        for caption in ('Caches disponíveis','Arquivos de Downloads a revisar','Instalações detectadas'):
            box=tk.Frame(metrics,bg=PANEL,padx=16,pady=14)
            box.pack(side='left',fill='x',expand=True,padx=(0,10))
            app.label(box,caption,size=9,color=MUTED).pack(anchor='w')
            value=app.label(box,'—',size=20,bold=True,color=ACCENT)
            value.pack(anchor='w',pady=(6,0));self.values.append(value)
        self.status=app.label(self.frame,'Clique em Analisar Computador para iniciar.',color=MUTED,wraplength=950,justify='left')
        self.status.pack(anchor='w',pady=(0,16))
        app.label(self.frame,'ESPAÇO ENCONTRADO POR ÁREA',size=10,bold=True).pack(anchor='w',pady=(0,8))
        table=tk.Frame(self.frame,bg=PANEL);table.pack(fill='both',expand=True)
        self.table=ttk.Treeview(table,columns=('name','found','potential','state'),show='tree headings',style='Destralha.Treeview')
        for key,title,width in (('#0','Abrir',58),('name','Área',180),('found','Inventariado',125),('potential','Possível limpeza / revisão',220),('state','Condição',240)):
            self.table.heading(key,text=title)
            self.table.column(key,width=width,minwidth=width if key=='#0' else 80,stretch=key in ('name','state'))
        scroll=ttk.Scrollbar(table,command=self.table.yview,style='Destralha.Vertical.TScrollbar')
        scroll.pack(side='right',fill='y');self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(fill='both',expand=True)
        self.table.bind('<ButtonRelease-1>',self.open_row)
        self.table.bind('<Return>',self.open_row)
        app.label(self.frame,'Clique em uma área para abrir. Valores lógicos; áreas podem se sobrepor. '
                  'Backups, downloads, binlogs e hibernação exigem decisões específicas.',size=9,color=MUTED,wraplength=950,justify='left').pack(anchor='w',pady=(12,0))

    def render(self):
        app=self.app;self.table.delete(*self.table.get_children())
        cache=sum(row.size for row in app.rows['Caches'] if not row.reason and row.selectable)
        download=sum(row.size for row in app.rows['Downloads'])
        self.values[0].configure(text=engine.human_size(cache) if app.analyzed else '—')
        self.values[1].configure(text=engine.human_size(download) if app.analyzed else '—')
        installations=app.management.installations if app.management else []
        self.values[2].configure(text=str(len(installations)) if installations else '—')
        self.status.configure(text=('Análise em andamento. Os resultados serão atualizados ao concluir.' if app.working else
            ('Análise cancelada: resultados incompletos.' if app.analysis_canceled else
             'Análise concluída com avisos. Consulte o relatório e as instalações.' if app.warnings else
             'Análise concluída. Nenhum arquivo é excluído sem revisão.') if app.analyzed else
             'Clique em Analisar Computador. A busca cobre as regras de arquivos e as instalações suportadas; não é uma busca irrestrita no disco.'))
        for name in ('Caches','Downloads','Outros'):
            rows=app.rows[name]
            total=sum(row.size for row in rows)
            ready=sum(row.size for row in rows if not row.reason and (not hasattr(row,'selectable') or row.selectable))
            potential=(engine.human_size(ready)+' disponíveis' if name=='Caches' else engine.human_size(ready)+' a revisar') if app.analyzed else 'A analisar'
            self.table.insert('','end',iid='files:'+name,image=app.icon('eye'),values=(name,engine.human_size(total) if app.analyzed else '—',potential,f'{len(rows)} alvos' if app.analyzed else 'Análise pendente'))
        for item in installations:
            inventory=app.management.inventories.get(item.key)
            potential='Depende da análise'
            if inventory:
                if item.kind=='mysql':
                    candidates=[record for record in inventory.files if record.category==exports.CATEGORY and not exports.reason(item,record)]
                    size=sum(record.size for record in candidates)
                    logs=any(record.category=='Logs binários' for record in inventory.files)
                    potential=(engine.human_size(size)+' a revisar'+(' + SQL' if logs else '')) if candidates else 'Binlogs: consultar SQL' if logs else 'Sem cópias revisáveis'
                else:potential='DISM / hibernação: avaliar'
            partial=inventory and (inventory.canceled or any('total parcial' in warning or 'não medidos' in warning for warning in inventory.warnings))
            condition='Inventário parcial' if partial else 'Medido com avisos' if inventory and inventory.warnings else 'Medido' if inventory else 'Análise pendente'
            self.table.insert('','end',iid='install:'+item.key,image=app.icon('eye'),values=(item.name,engine.human_size(inventory.total) if inventory else '—',potential,condition))

    def open_row(self,event=None):
        if event is not None and getattr(event,'y',None) is not None and getattr(event,'keysym','')!='Return':
            key=self.table.identify_row(event.y)
        else:
            selection=self.table.selection();key=selection[0] if selection else ''
        if key.startswith('files:'):self.app.change_category(key[6:])
        elif key.startswith('install:'):self.app.open_management(key[8:])
