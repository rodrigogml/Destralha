"""Exclusão real apenas de dumps artificiais; preservação de dados do servidor."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine
import file_deletion
from maintenance import mysql,exports
from maintenance.inventory import Installation


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.item=Installation('fixture','mysql','MySQL fixture',self.root,self.root/'data')
        self.item.data.mkdir()

    def record(self,name='bin/backup.sql'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'dump')
        return next(record for record in mysql.inventory(self.item).files if record.path==path)

    def test_only_reviewed_export_removed_preserves_server_and_siblings(self):
        record=self.record();keep=self.record('bin/keep.sql');table=self.record('data/table.ibd')
        self.assertEqual(exports.delete(self.item,record),4)
        self.assertFalse(record.path.exists())
        self.assertTrue(keep.path.exists() and table.path.exists())
        self.assertTrue(record.path.parent.exists())

    def test_locked_changed_and_missing_identity_preserved(self):
        record=self.record()
        with record.path.open('rb'),self.assertRaises(engine.Refused):exports.delete(self.item,record)
        with self.assertRaises(engine.Refused):exports.delete(self.item,replace(record,identity=0))
        record.path.write_bytes(b'changed')
        with self.assertRaises(engine.Refused):exports.delete(self.item,record)
        self.assertTrue(record.path.exists())

    def test_data_packaged_scripts_and_unknown_groups_cannot_be_deleted(self):
        for name in ('data/dump.sql','share/install.sql','scripts/start.sql','data/table.ibd','bin/server.exe'):
            record=self.record(name)
            with self.subTest(name=name),self.assertRaises(engine.Refused):exports.delete(self.item,record)
            self.assertTrue(record.path.exists())
        record=self.record()
        with self.assertRaises(engine.Refused):exports.delete(replace(self.item,data=None),record)

    def test_sql_referenced_by_configuration_or_service_is_preserved(self):
        record=self.record()
        config=self.root/'my.ini'
        config.write_text(f'[mysqld]\ninit-file={record.path}\n',encoding='utf-8')
        for item in (replace(self.item,config=config),replace(self.item,preserved_files=(record.path,))):
            with self.assertRaises(engine.Refused):exports.delete(item,record)
        self.assertTrue(record.path.exists())

    def test_wrong_scope_and_changed_volume_preserved(self):
        record=self.record()
        with self.assertRaises(engine.Refused):exports.delete(self.item,replace(record,path=self.root.parent/'outside.sql'))
        with self.assertRaises(engine.Refused):exports.delete(self.item,replace(record,device=record.device+1))
        self.assertTrue(record.path.exists())

    def test_junction_parent_is_preserved(self):
        record=self.record()
        with patch.object(exports.engine,'safe_path',side_effect=engine.Refused('Link preservado')):
            with self.assertRaises(engine.Refused):exports.delete(self.item,record)
        self.assertTrue(record.path.exists())

    def test_identity_is_rechecked_after_exclusive_handle_and_failures_preserve_file(self):
        from types import SimpleNamespace
        record=self.record()
        metadata=dict(st_mode=record.path.stat().st_mode,st_size=record.size,st_mtime_ns=record.modified_ns,
                      st_ino=record.identity,st_dev=record.device,st_file_attributes=0)
        for change in (dict(st_ino=record.identity+1),dict(st_dev=record.device+1),dict(st_file_attributes=0x400)):
            with patch.object(file_deletion.os,'fstat',return_value=SimpleNamespace(**dict(metadata,**change))):
                with self.assertRaises(engine.Refused):exports.delete(self.item,record)
            self.assertTrue(record.path.exists())
        with patch.object(file_deletion.ctypes.windll.kernel32,'SetFileInformationByHandle',return_value=0):
            with self.assertRaises(engine.Refused):exports.delete(self.item,record)
        self.assertTrue(record.path.exists())
