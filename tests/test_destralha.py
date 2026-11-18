"""Contrato da interface premium; apenas arquivos artificiais são manipulados."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import destralha as d
import engine as e
import downloads
from maintenance import mysql,windows
from maintenance.inventory import Inventory,Installation,FileRecord


class DestralhaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = d.DestralhaApp(self.root)
        self.app.change_category('Caches')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.app.dispose)

    def candidate(self, name='Fixture', **kwargs):
        path = self.base/name
        path.mkdir(exist_ok=True)
        (path/'payload').write_bytes(b'cache')
        rule = dict(id=name,name=name,category='Pip',root='local',patterns=[name],processes=[],prefixes=[],
                    guard='Pip',target_type='Directory',minimum_age_days=0,command_pattern='',install_tokens=[],
                    risk='cache',protected=False,purpose='Fixture',effect='Recria cache')
        changes = kwargs.pop('rule',{})
        rule.update(changes)
        return e.Candidate(rule,self.base,path,size=5,files=1,**kwargs)

    def wait(self):
        deadline = time.monotonic()+8
        while self.app.working and time.monotonic()<deadline:
            self.root.update(); time.sleep(.01)
        self.assertFalse(self.app.working)

    def test_category_filter_counts_and_hidden_selection_are_independent(self):
        a = self.candidate('Ready',selected=True)
        b = self.candidate('Busy',reason='Aplicativo aberto: chrome')
        unknown = self.candidate('Unknown',reason='Conteúdo desconhecido; preservado')
        self.app.rows['Caches'] = [a,b,unknown]
        other = self.candidate('Model',rule=dict(risk='offline'))
        self.app.rows['Outros'] = [other]
        self.app.render()
        self.assertEqual([label.cget('text') for label in self.app.metric_values],['15.00 B','5.00 B','10.00 B'])
        state = self.app.states['Caches']
        state['condition'] = 'Aplicativo aberto'
        self.app.render()
        self.app.mark_all()
        self.assertTrue(a.selected and b.selected)
        self.assertFalse(unknown.selected)
        self.assertIn('1 fora do filtro',self.app.footer_sub.cget('text'))
        self.assertIn('Disponível agora (1)',self.app.filters['condition'][1]['values'])
        self.assertIn('Marcados (1)',self.app.filters['mark'][1]['values'])
        self.app.change_category('Outros')
        self.assertFalse(other.selected)
        self.app.mark_all()
        self.assertTrue(other.selected)
        self.app.change_category('Caches')
        self.assertEqual(state['condition'],'Aplicativo aberto')
        self.app.clear_all()
        self.assertFalse(a.selected or b.selected)
        self.assertTrue(other.selected)

    def test_global_analysis_distributes_catalog_and_downloads_without_writing(self):
        cache = self.candidate('Cache')
        other = self.candidate('Other',rule=dict(risk='diagnostic'))
        self.app.days.set('120')
        with patch.object(e,'scan',return_value=[cache,other]) as scan, \
             patch.object(mysql,'discover',return_value=([],[])), \
             patch.object(windows,'inventory',side_effect=lambda item,*args:Inventory(item)), \
             patch.object(downloads,'scan_downloads',return_value=([],[])) as scan_downloads, \
             patch.object(Path,'write_text',side_effect=AssertionError('Escrita automática proibida')):
            self.app.analyze(); self.wait()
        self.assertEqual(self.app.rows['Caches'],[cache])
        self.assertEqual(self.app.rows['Outros'],[other])
        self.assertEqual(scan_downloads.call_args.args[1],120)
        self.assertEqual(len(scan.call_args.args[0]),len([r for r in self.app.rules if not r.get('protected')]))
        self.assertTrue(self.app.report['analysis'])
        self.assertIsNone(self.app.progress_window)
        self.assertFalse((self.base/'logs').exists())

    def test_actual_cache_worker_uses_engine_and_keeps_report_in_memory(self):
        row = self.candidate(selected=True)
        self.app.rows['Caches'] = [row]
        roots = dict(self.app.roots,local=self.base)
        process = dict(Name='python.exe',ExecutablePath='',CommandLine='python server.py',ProcessId=999999)
        with patch.object(self.app,'show_report'), patch.object(e,'load_catalog',return_value=[row.rule]), \
             patch.object(e,'process_snapshot',return_value=[process]), \
             patch.object(Path,'write_text',side_effect=AssertionError('Escrita automática proibida')):
            self.app.roots = roots
            self.app.run_cleanup((row,),False,'Caches'); self.wait()
        self.assertFalse(row.path.exists())
        self.assertEqual(self.app.rows['Caches'],[])
        self.assertEqual(self.app.report['complete_targets_bytes'],5)
        self.assertIn('Excluído',self.app.report_text())
        target = self.base/'manual.json'
        with patch.object(d.filedialog,'asksaveasfilename',return_value=str(target)):
            self.app.export_report()
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')),self.app.report)

    def test_simulation_never_calls_delete_and_report_save_cancel_is_inert(self):
        row = self.candidate(selected=True)
        self.app.rows['Caches'] = [row]
        with patch.object(self.app,'show_report'), patch.object(e,'process_snapshot',return_value=[]), \
             patch.object(e,'delete_candidate',side_effect=AssertionError('Simulação não exclui')):
            self.app.review(True); self.wait()
        self.assertTrue(row.path.exists())
        self.assertEqual(self.app.report['results'][0]['result'],'Simulado')
        with patch.object(d.filedialog,'asksaveasfilename',return_value=''), \
             patch.object(Path,'write_text',side_effect=AssertionError('Salvar cancelado não grava')):
            self.app.export_report()

    def test_confirmation_waits_for_explicit_click_and_covers_hidden_items(self):
        row = self.candidate(selected=True)
        self.app.rows['Caches'] = [row]
        self.app.states['Caches']['query'] = 'not-found'
        self.app.render()
        with patch.object(self.app,'run_cleanup') as clean:
            self.app.review(False)
            self.assertFalse(self.app.working)
            clean.assert_not_called()
            dialogs = [widget for widget in self.root.winfo_children() if isinstance(widget,tk.Toplevel)]
            self.assertEqual(len(dialogs),1)
            def texts(widget):
                return [widget.cget('text')] if isinstance(widget,(tk.Label,tk.Button)) else sum((texts(child) for child in widget.winfo_children()),[])
            content = texts(dialogs[0])
            self.assertTrue(any('1 marcados fora do filtro' in value for value in content))
            dialogs[0].destroy()

    def test_progress_indeterminate_background_cancel_and_failure_recovery(self):
        self.app.begin('analysis','Fixture')
        self.assertEqual(str(self.app.progress_bar.cget('mode')),'indeterminate')
        self.app.progress_state.update(phase='Medindo',index=3,total=10,current='Cache')
        self.app.update_progress()
        self.assertEqual(str(self.app.progress_bar.cget('mode')),'determinate')
        self.assertIn('30%',self.app.progress_count.cget('text'))
        self.app.progress_window.withdraw()
        self.app.show_progress()
        self.app.request_cancel()
        self.assertTrue(self.app.cancel.is_set())
        with patch.object(d.messagebox,'showerror'):
            self.app.events.put(('error','fixture'))
            self.wait()
        self.assertFalse(self.app.working)

    def test_download_worker_keeps_registered_source_and_category_scoped(self):
        path = self.base/'file.iso'
        path.write_bytes(b'fixture')
        info = path.stat()
        row = downloads.Download(self.base,path,info.st_size,info.st_mtime_ns,info.st_ino,90,'Fixture',selected=True)
        self.app.rows['Downloads'] = [row]
        cache = self.candidate('OtherCache',selected=True)
        self.app.rows['Caches'] = [cache]
        self.app.change_category('Downloads')
        with patch.object(self.app,'show_report'), patch.object(downloads,'delete_download',side_effect=e.Refused('Fonte registrada; preservada')) as delete:
            self.app.run_cleanup((row,),False,'Downloads'); self.wait()
        delete.assert_called_once_with(row)
        self.assertTrue(path.exists())
        self.assertTrue(cache.path.exists())
        self.assertTrue(cache.selected)
        self.assertIn('Fonte registrada',self.app.report_text())

    def test_dashboard_routes_and_global_analysis_includes_installations(self):
        item=Installation('mysql:fixture','mysql','MySQL fixture',self.base,self.base/'data')
        inventory=Inventory(item,files=[FileRecord(self.base/'data/binlog.000001',40,time.time(),'Logs binários')])
        with patch.object(e,'scan',return_value=[]),patch.object(downloads,'scan_downloads',return_value=([],[])), \
             patch.object(mysql,'discover',return_value=([item],[])),patch.object(mysql,'inventory',return_value=inventory) as measure, \
             patch.object(windows,'inventory',side_effect=lambda item,*args:Inventory(item)):
            self.app.analyze();self.wait()
        measure.assert_called_once()
        self.assertEqual(self.app.mode,'overview')
        self.assertTrue(self.app.overview.table.exists('install:'+item.key))
        self.assertIn(item.key,self.app.installation_buttons)
        self.app.overview.table.selection_set('install:'+item.key)
        self.app.overview.open_row()
        self.assertEqual(self.app.mode,'management')
        self.assertEqual(self.app.management.current,item)
        self.assertIn('40.00 B',self.app.management.total.cget('text'))
        self.app.show_overview()
        self.app.overview.table.selection_set('files:Downloads');self.app.overview.open_row()
        self.assertEqual(self.app.category,'Downloads')

    def test_dialogs_center_on_parent_and_nested_parent(self):
        self.root.deiconify();self.root.geometry('1320x880+40+35');self.root.update()
        window,_=self.app.dialog('Fixture','720x520',parent=self.root)
        self.root.update()
        def center(widget):return widget.winfo_rootx()+widget.winfo_width()/2,widget.winfo_rooty()+widget.winfo_height()/2
        for a,b in zip(center(window),center(self.root)):self.assertLessEqual(abs(a-b),2)
        child,_=self.app.dialog('Nested','600x400',parent=window);self.root.update()
        for a,b in zip(center(child),center(window)):self.assertLessEqual(abs(a-b),2)
        child.destroy();window.destroy()

    def test_partial_global_analysis_preserves_cancellation_and_other_area_results(self):
        cache=self.candidate('Cache')
        item=Installation('mysql:fixture','mysql','MySQL fixture',self.base,self.base/'data')
        def interrupted_windows(item,*args):
            self.app.cancel.set()
            return Inventory(item,canceled=True,warnings=['Medição parcial'])
        with patch.object(e,'scan',return_value=[cache]), \
             patch.object(downloads,'scan_downloads',side_effect=PermissionError('fixture')), \
             patch.object(mysql,'discover',return_value=([item],[])), \
             patch.object(windows,'inventory',side_effect=interrupted_windows),patch.object(mysql,'inventory') as mysql_measure:
            self.app.analyze();self.wait()
        mysql_measure.assert_not_called()
        self.assertEqual(self.app.rows['Caches'],[cache])
        self.assertTrue(self.app.report['canceled'])
        self.assertEqual(self.app.mode,'overview')
        self.assertNotIn(item.key,self.app.management.inventories)
        self.assertTrue(any('Downloads' in warning for warning in self.app.warnings))


if __name__=='__main__':
    unittest.main()
