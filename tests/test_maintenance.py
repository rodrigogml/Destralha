"""Manutenção: arquivos artificiais, SQL simulado e comandos nativos simulados."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine
import destralha
from maintenance import mysql,windows
from maintenance.inventory import Installation,Inventory,FileRecord,measure


class FakeConnection:
    def __init__(self,data,**changes):
        self.data=str(data);self.calls=[];self.purged=False
        self.logs=[('binlog.000001',10),('binlog.000002',20),('binlog.000003',30)]
        self.uuid='server-1';self.replicas=[];self.channels=[];self.fail_replication=False
        self.__dict__.update(changes)
    def cursor(self,dictionary=True):return FakeCursor(self)
    def close(self):pass


class FakeCursor:
    def __init__(self,connection):self.connection=connection;self.rows=[]
    def execute(self,sql,params=None):
        c=self.connection;c.calls.append((sql,params))
        if sql.startswith('SELECT @@version'):
            self.rows=[dict(version='8.0.11',datadir=c.data,server_id=c.uuid)]
        elif sql=='SHOW BINARY LOGS':
            self.rows=[dict(Log_name=name,File_size=size) for name,size in (c.logs[-1:] if c.purged else c.logs)]
        elif sql.startswith('SHOW VARIABLES'):
            self.rows=[dict(Variable_name='binlog_expire_logs_seconds',Value='2592000')]
        elif sql=='SHOW MASTER STATUS':self.rows=[dict(File='binlog.000003')]
        elif sql=='SHOW SLAVE HOSTS':
            if c.fail_replication:raise PermissionError('Fixture')
            self.rows=c.replicas
        elif sql=='SHOW SLAVE STATUS':self.rows=c.channels
        elif 'information_schema.tables' in sql:self.rows=[]
        elif sql=='PURGE BINARY LOGS TO %s':c.purged=True;self.rows=[]
        else:raise AssertionError('SQL não previsto: '+sql)
    def fetchall(self):return self.rows
    def close(self):pass


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.item=Installation('mysql:fixture','mysql','MySQL fixture',self.base,self.base/'data',state='Running')
        self.item.data.mkdir()

    def test_stream_inventory_groups_and_deduplicates_hardlinks(self):
        root=self.item.root
        (root/'bin').mkdir();(root/'bin/server.exe').write_bytes(b'app')
        (self.item.data/'binlog.000001').write_bytes(b'log')
        (self.item.data/'table.ibd').write_bytes(b'data')
        os.link(self.item.data/'table.ibd',self.item.data/'alias.ibd')
        result=mysql.inventory(self.item)
        self.assertEqual(result.total,10)
        self.assertEqual(result.hardlinks,1)
        self.assertEqual(result.groups()['Logs binários']['bytes'],3)
        self.assertEqual(result.groups()['Tabelas e índices']['bytes'],4)
        self.assertTrue(any('compartilhada' in warning for warning in result.warnings))

    def test_canceled_or_unreadable_inventory_is_partial(self):
        cancel=threading.Event();cancel.set()
        self.assertTrue(mysql.inventory(self.item,cancel).canceled)
        with patch('maintenance.inventory.engine.check_item',side_effect=PermissionError('fixture')):
            result=mysql.inventory(self.item)
        self.assertEqual(result.total,0)
        self.assertTrue(any('parcial' in warning for warning in result.warnings))

    def test_overlapping_scopes_are_not_double_counted(self):
        (self.item.data/'file').write_bytes(b'123')
        result=measure(self.item,[self.base,self.item.data],lambda p:'Test')
        self.assertEqual(result.total,3)

    def test_discovery_parses_service_and_ignores_password_fields(self):
        binary=self.base/'bin/mysqld.exe';binary.parent.mkdir();binary.write_bytes(b'fixture')
        config=self.base/'my.ini'
        config.write_text('[client]\nuser=secret-user\npassword=secret-pass\n[mysqld]\ndatadir=data\nport=3307\npassword=do-not-return\n',encoding='utf-8')
        service=dict(Name='Fixture',State='Running',PathName=f'"{binary}" --defaults-file="{config}" Fixture')
        items,warnings=mysql.discover([service],[])
        self.assertFalse(warnings)
        self.assertEqual(items[0].data,self.item.data)
        self.assertEqual(items[0].port,3307)
        self.assertNotIn('secret',repr(items))
        self.assertEqual(mysql.local_config(config),dict(datadir='data',port='3307'))
        self.assertIsNone(mysql.executable_from_service('cmd.exe /c echo mysqld.exe'))

    def test_service_inventory_accepts_single_json_object(self):
        data=dict(Name='mysql',State='Running',PathName='C:\\mysql\\bin\\mysqld.exe')
        completed=Mock(returncode=0,stdout=json.dumps(data).encode())
        with patch.object(mysql.subprocess,'run',return_value=completed):
            self.assertEqual(mysql.service_inventory(),[data])

    def test_classification_preserves_database_recovery_and_installed_windows(self):
        cases={'data/a.ibd':'Tabelas e índices','data/ib_logfile0':'InnoDB e recuperação',
               'data/relay-bin.000001':'Logs de réplica','data/binlog.000001':'Logs binários',
               'data/server.err':'Logs de diagnóstico','bin/backup.sql':'Exportações / backups a revisar',
               'share/install.sql':'Outros arquivos / configuração'}
        for path,expected in cases.items():
            self.assertEqual(mysql.classify(self.base/path,self.item),expected)
        self.assertEqual(windows.classify(self.base/'Installer/repair.msi',self.base),'Windows Installer — preservar')
        self.assertEqual(windows.classify(self.base/'System32/DriverStore/driver.sys',self.base),'Drivers instalados — preservar')

    def test_sql_inspection_is_metadata_only_and_checks_correct_instance(self):
        connection=FakeConnection(self.item.data)
        snapshot=mysql.inspect(connection,self.item)
        self.assertEqual(len(snapshot.binary_logs),3)
        self.assertTrue(snapshot.replication_checked)
        self.assertTrue(all(sql.startswith(('SELECT','SHOW')) for sql,_ in connection.calls))
        wrong=FakeConnection(self.base/'wrong')
        with self.assertRaises(engine.Refused):mysql.inspect(wrong,self.item)
        self.assertEqual(len(wrong.calls),1)

    def test_purge_requires_explicit_confirmations(self):
        c=FakeConnection(self.item.data)
        snapshot=mysql.inspect(c,self.item)
        plan=mysql.plan_purge(snapshot,'binlog.000003')
        self.assertEqual(plan.size,30)
        for backup,replica in ((False,False),(True,False),(False,True)):
            with self.assertRaises(engine.Refused):mysql.purge(c,self.item,plan,backups_reviewed=backup,replication_reviewed=replica)
        self.assertFalse(c.purged)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(engine.Refused):mysql.purge(c,self.item,plan,backups_reviewed=True,replication_reviewed=True,cancel=cancel)
        self.assertFalse(c.purged)

    def test_purge_rejects_replication_unknown_status_and_sql_injection(self):
        c=FakeConnection(self.item.data)
        snapshot=mysql.inspect(c,self.item)
        for changed in (replace(snapshot,replicas=({'Host':'fixture'},)),replace(snapshot,replica_channels=({'Master_Host':'fixture'},)),
                        replace(snapshot,replication_checked=False),replace(snapshot,current_log='')):
            with self.assertRaises(engine.Refused):mysql.plan_purge(changed,'binlog.000003')
        for boundary in ('binlog.000001',"binlog.000003'; DROP TABLE test;",'missing.999999'):
            with self.assertRaises(engine.Refused):mysql.plan_purge(snapshot,boundary)
        unknown=FakeConnection(self.item.data,fail_replication=True)
        self.assertFalse(mysql.inspect(unknown,self.item).replication_checked)

    def test_logs_unavailable_do_not_prevent_readonly_table_diagnostics(self):
        connection=FakeConnection(self.item.data)
        original=FakeCursor.execute
        def execute(cursor,sql,params=None):
            if sql=='SHOW BINARY LOGS':raise PermissionError('Fixture')
            return original(cursor,sql,params)
        with patch.object(FakeCursor,'execute',execute):
            snapshot=mysql.inspect(connection,self.item)
        self.assertFalse(snapshot.binary_logs)
        self.assertTrue(snapshot.warnings)
        self.assertTrue(any('information_schema.tables' in sql for sql,_ in connection.calls))
        with self.assertRaises(engine.Refused):mysql.plan_purge(snapshot,'binlog.000003')

    def test_modern_mysql_uses_supported_status_statements(self):
        connection=FakeConnection(self.item.data)
        original=FakeCursor.execute
        def execute(cursor,sql,params=None):
            if sql.startswith('SELECT @@version'):
                original(cursor,sql,params);cursor.rows[0]['version']='9.7.2';return
            aliases={'SHOW BINARY LOG STATUS':'SHOW MASTER STATUS','SHOW REPLICAS':'SHOW SLAVE HOSTS',
                     'SHOW REPLICA STATUS':'SHOW SLAVE STATUS'}
            if sql in aliases:
                cursor.connection.calls.append((sql,params))
                return original(cursor,aliases[sql],params)
            return original(cursor,sql,params)
        with patch.object(FakeCursor,'execute',execute):snapshot=mysql.inspect(connection,self.item)
        self.assertTrue(snapshot.replication_checked)
        self.assertEqual(snapshot.version,'9.7.2')
        self.assertTrue(any(sql=='SHOW BINARY LOG STATUS' for sql,_ in connection.calls))

    def test_purge_revalidates_identity_and_closed_log_sizes(self):
        original=FakeConnection(self.item.data)
        plan=mysql.plan_purge(mysql.inspect(original,self.item),'binlog.000003')
        for changed in (FakeConnection(self.item.data,uuid='another-server'),
                        FakeConnection(self.item.data,logs=[('binlog.000001',11),('binlog.000002',20),('binlog.000003',30)])):
            with self.assertRaises(engine.Refused):mysql.purge(changed,self.item,plan,backups_reviewed=True,replication_reviewed=True)
            self.assertFalse(changed.purged)

    def test_cancel_during_revalidation_prevents_purge(self):
        c=FakeConnection(self.item.data)
        snapshot=mysql.inspect(c,self.item)
        plan=mysql.plan_purge(snapshot,'binlog.000003')
        cancel=threading.Event()
        def inspect(*args):
            cancel.set();return snapshot
        with patch.object(mysql,'inspect',side_effect=inspect):
            with self.assertRaises(engine.Refused):
                mysql.purge(c,self.item,plan,backups_reviewed=True,replication_reviewed=True,cancel=cancel)
        self.assertFalse(c.purged)

    def test_canceled_native_action_does_not_launch_process(self):
        cancel=threading.Event();cancel.set()
        with patch.object(windows,'is_admin',return_value=True),patch.object(windows.engine,'check_item'), \
             patch.object(windows.subprocess,'Popen') as popen:
            with self.assertRaises(engine.Refused):windows.run('clean_components',cancel=cancel)
        popen.assert_not_called()

    def test_purge_uses_one_parameterized_native_statement(self):
        c=FakeConnection(self.item.data)
        plan=mysql.plan_purge(mysql.inspect(c,self.item),'binlog.000003')
        result=mysql.purge(c,self.item,plan,backups_reviewed=True,replication_reviewed=True)
        self.assertEqual(result,dict(files=2,bytes=30,preserved=0))
        self.assertEqual([(sql,params) for sql,params in c.calls if sql.startswith('PURGE')],
                         [('PURGE BINARY LOGS TO %s',('binlog.000003',))])
        self.assertFalse(any(sql.startswith(('DELETE','TRUNCATE','RESET','DROP')) for sql,_ in c.calls))
        self.assertNotIn('hidden-password',repr(mysql.Credentials('user','hidden-password')))

    def test_native_windows_commands_are_fixed_and_require_elevation(self):
        with patch.object(windows,'windows_directory',return_value=self.base):
            command=windows.command('clean_components')
            self.assertIn('/StartComponentCleanup',command)
            self.assertNotIn('/ResetBase',command)
            with self.assertRaises(engine.Refused):windows.command('arbitrary shell')
            with patch.object(windows,'is_admin',return_value=False),patch.object(windows.subprocess,'Popen') as popen:
                with self.assertRaises(engine.Refused):windows.run('clean_components')
                popen.assert_not_called()

    def test_native_output_is_in_memory_and_reports_real_progress(self):
        process=Mock(stdout=io.StringIO('Starting\r42.0%\nCompleted\n'))
        process.wait.return_value=3010
        events=[]
        with patch.object(windows,'is_admin',return_value=True),patch.object(windows.engine,'check_item'), \
             patch.object(windows.subprocess,'Popen',return_value=process), \
             patch.object(Path,'write_text',side_effect=AssertionError('No automatic log')):
            result=windows.run('analyze_components',lambda line,pct:events.append((line,pct)))
        self.assertTrue(result['restart_required'])
        self.assertIn(('42.0%',42.0),events)
        process.kill.assert_not_called()
        process.terminate.assert_not_called()


class MaintenanceInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=tk.Tk();self.root.withdraw()
        self.app=destralha.DestralhaApp(self.root)
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.app.dispose)
        self.item=Installation('mysql:test','mysql','MySQL fixture',self.base,self.base/'data',state='Running')

    def wait(self):
        deadline=time.monotonic()+6
        while self.app.working and time.monotonic()<deadline:
            self.root.update();time.sleep(.01)
        self.assertFalse(self.app.working)

    def open(self):
        with patch.object(mysql,'discover',return_value=([self.item],[])):
            self.app.open_management();self.wait()
        return self.app.management

    def test_navigation_discovery_inventory_and_no_automatic_writes(self):
        view=self.open()
        self.assertEqual(self.app.mode,'management')
        self.assertEqual(len(view.installations),2)
        result=Inventory(self.item,files=[FileRecord(self.base/'data/binlog.000001',100,time.time(),'Logs binários')])
        with patch.object(mysql,'inventory',return_value=result), \
             patch.object(Path,'write_text',side_effect=AssertionError('No automatic report')):
            view.analyze();self.wait()
        self.assertEqual(view.inventories[self.item.key].total,100)
        self.assertIn('100.00 B',self.app.report_text())
        self.assertFalse(self.app.rows['Caches'])
        self.app.change_category('Caches')
        self.assertEqual(self.app.mode,'catalog')
        self.app.open_management()
        self.assertEqual(view.current.key,self.item.key)

    def test_native_cleanup_requires_confirmation_and_uses_no_fake_file_removal(self):
        view=self.open()
        view.current=windows.installation()
        with patch.object(windows,'is_admin',return_value=True),patch.object(destralha.messagebox,'askyesno',return_value=False), \
             patch.object(windows,'run') as run:
            view.native_action('clean_components')
            run.assert_not_called()
            self.assertFalse(self.app.working)

    def test_purge_dialog_defaults_to_unchecked_without_connecting(self):
        view=self.open()
        view.credentials=mysql.Credentials('fixture','secret')
        view.connected_key=self.item.key
        view.snapshot=mysql.inspect(FakeConnection(self.item.data),self.item)
        with patch.object(mysql,'connect',side_effect=AssertionError('Não conectar sem confirmação')):
            view.purge_dialog()
            dialogs=[widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel) and 'Expurgo' in widget.title()]
            self.assertEqual(len(dialogs),1)
            def widgets(parent):
                for child in parent.winfo_children():
                    yield child;yield from widgets(child)
            checks=[widget for widget in widgets(dialogs[0]) if isinstance(widget,tk.Checkbutton)]
            self.assertEqual(len(checks),2)
            self.assertTrue(all(not bool(self.root.getvar(widget.cget('variable'))) for widget in checks))
            self.assertNotIn('secret',self.app.report_text())
            dialogs[0].destroy()

    def test_export_table_selects_and_removes_only_confirmed_copies(self):
        view=self.open()
        (self.base/'bin').mkdir();(self.base/'data').mkdir()
        first=self.base/'bin/backup.sql';first.write_bytes(b'dump')
        second=self.base/'bin/other.sql';second.write_bytes(b'dump2')
        protected=self.base/'data/server.sql';protected.write_bytes(b'valid data')
        view.inventories[self.item.key]=mysql.inventory(self.item);view.render()
        key=next(key for key,name in view.group_names.items() if name=='Exportações / backups a revisar')
        self.assertTrue(view.groups.item(key,'image'))
        view.groups.selection_set(key);view.details()
        window=next(widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel) and 'backups' in widget.title())
        def widgets(parent):
            for child in parent.winfo_children():
                yield child;yield from widgets(child)
        children=list(widgets(window))
        table=next(widget for widget in children if isinstance(widget,ttk.Treeview))
        buttons={widget.cget('text'):widget for widget in children if isinstance(widget,tk.Button)}
        self.assertEqual(len(table.get_children()),3)
        buttons['Marcar Tudo'].invoke()
        self.assertEqual(sum(table.item(key,'text')=='☑' for key in table.get_children()),2)
        with patch('maintenance.view.messagebox.askyesno',return_value=False):buttons['Excluir marcados'].invoke()
        self.assertTrue(first.exists() and second.exists())
        with patch('maintenance.view.messagebox.askyesno',return_value=True),patch.object(self.app,'show_report'), \
             patch.object(Path,'write_text',side_effect=AssertionError('No automatic report')):
            buttons['Excluir marcados'].invoke();self.wait()
        self.assertFalse(first.exists() or second.exists())
        self.assertTrue(protected.exists())
        self.assertEqual(len(self.app.report['results']),2)

    def test_detail_icon_opens_details_with_single_click(self):
        from types import SimpleNamespace
        view=self.open()
        result=Inventory(self.item,files=[FileRecord(self.base/'table.ibd',10,time.time(),'Tabelas e índices')])
        view.inventories[self.item.key]=result;view.render()
        with patch.object(view.groups,'identify_row',return_value='0'),patch.object(view.groups,'identify_column',return_value='#0'), \
             patch.object(view,'details') as details:
            view.details_click(SimpleNamespace(x=5,y=8))
        details.assert_called_once()

    def test_file_table_pages_keep_group_selection(self):
        view=self.open()
        records=[FileRecord(self.base/f'backup-{index}.sql',1,time.time(),'Exportações / backups a revisar') for index in range(205)]
        view.inventories[self.item.key]=Inventory(self.item,files=records);view.render()
        view.groups.selection_set('0')
        with patch('maintenance.view.exports.reason',return_value=''):view.details()
        window=next(widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel) and 'backups' in widget.title())
        def widgets(parent):
            for child in parent.winfo_children():
                yield child;yield from widgets(child)
        children=list(widgets(window))
        table=next(widget for widget in children if isinstance(widget,ttk.Treeview))
        buttons={widget.cget('text'):widget for widget in children if isinstance(widget,tk.Button)}
        self.assertEqual(len(table.get_children()),200)
        buttons['Marcar Tudo'].invoke();buttons['Próxima página'].invoke()
        self.assertEqual(len(table.get_children()),5)
        self.assertTrue(all(table.item(key,'text')=='☑' for key in table.get_children()))
        self.assertTrue(any('205 marcados no grupo' in widget.cget('text') for widget in children if isinstance(widget,tk.Label)))
        buttons['Desmarcar Tudo'].invoke();buttons['Página anterior'].invoke()
        self.assertTrue(all(table.item(key,'text')=='☐' for key in table.get_children()))
        window.destroy()
