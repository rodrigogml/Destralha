"""Downloads: só fixtures artificiais são removidas por estes testes."""
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import downloads as d


class DownloadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.records = patch.object(d, 'installation_records', return_value=[])
        self.processes = patch.object(d, 'process_snapshot', return_value=[])
        self.known = patch.object(d, 'downloads_root', return_value=self.root)
        for mock in (self.records, self.processes, self.known):
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.temp.cleanup)

    def file(self, name, age=100, size=5):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as stream:
            stream.truncate(size)
        timestamp = time.time() - age * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def test_union_recursive_default_unselected_and_exact_boundary(self):
        old = self.file('nested/personal.jpg')
        large = self.file('large.iso', 1, d.LARGE + 1)
        self.file('recent.txt', 1)
        self.file('exact.bin', 1, d.LARGE)
        rows, warnings = d.scan_downloads(self.root)
        self.assertFalse(warnings)
        self.assertEqual({r.path for r in rows}, {old, large})
        self.assertTrue(all(not r.selected for r in rows))
        self.assertEqual(rows[0].match, 'Maior que 1 GiB')
        self.assertEqual(rows[1].match, 'Mais de 90 dias')

    def test_days_validation_and_configurable_threshold(self):
        self.file('file', 50)
        self.assertEqual(len(d.scan_downloads(self.root, 30)[0]), 1)
        self.assertEqual(d.scan_downloads(self.root, 60)[0], [])
        for invalid in (0, -1, 36501, True, 1.5, '90'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                d.scan_downloads(self.root, invalid)

    def test_inventory_allows_relaxing_filters_and_combines_age_and_size(self):
        old_large=self.file('old-large',100,2049)
        self.file('old-small',100,10)
        self.file('recent-large',1,2049)
        self.file('exact-size',100,2048)
        rows,_=d.scan_downloads(self.root,all_files=True)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row.reviewed_all and not row.selected for row in rows))
        self.assertEqual([row.path for row in d.filter_downloads(rows,90,2048)],[old_large])
        self.assertEqual(len(d.filter_downloads(rows,0,2048)),2)
        self.assertEqual(len(d.filter_downloads(rows,90,0)),3)
        self.assertEqual(len(d.filter_downloads(rows,0,0)),4)
        cutoff=rows[0].modified_ns/1e9+90*86400
        self.assertNotIn(rows[0],d.filter_downloads(rows,90,0,cutoff))

    def test_size_units_decimal_and_invalid_values(self):
        self.assertEqual(d.size_threshold('1,5','MB'),1572864)
        self.assertEqual(d.size_threshold('0','GB'),0)
        self.assertEqual(d.size_threshold('1','TB'),1024**4)
        for value,unit in (('-1','KB'),('NaN','B'),('Infinity','GB'),('text','MB'),('1','invalid')):
            with self.subTest(value=value,unit=unit),self.assertRaises(ValueError):d.size_threshold(value,unit)
        for days,size in ((-1,0),(36501,0),(False,0),(0,-1)):
            with self.assertRaises(ValueError):d.filter_downloads([],days,size)

    def test_explicitly_reviewed_recent_small_file_can_be_removed_after_filter_clear(self):
        target=self.file('recent',1,5)
        self.assertEqual(d.scan_downloads(self.root)[0],[])
        row=d.scan_downloads(self.root,all_files=True)[0][0]
        row.selected=True
        self.assertEqual(d.delete_download(row),5)
        self.assertFalse(target.exists())

    def test_partial_and_failed_checks_preserved(self):
        self.file('incomplete.crdownload')
        self.assertTrue(d.scan_downloads(self.root)[0][0].reason)
        with patch.object(d, 'installation_records', side_effect=PermissionError('fixture')):
            rows, warnings = d.scan_downloads(self.root)
            self.assertTrue(rows[0].reason)
            self.assertTrue(warnings)

    def test_installed_source_protects_whole_package_not_sibling(self):
        package = self.file('Autodesk/2027/Setup.exe')
        source = package.parent / 'x64/CER'
        source.mkdir(parents=True)
        target = self.file('Autodesk/2027/other.dat')
        sibling = self.file('Autodesk/2024/old.dat')
        with patch.object(d, 'installation_records', return_value=[dict(InstallSource=str(source))]):
            rows, _ = d.scan_downloads(self.root)
            by_path = {r.path: r for r in rows}
            self.assertTrue(by_path[target].reason)
            self.assertFalse(by_path[sibling].reason)
            with self.assertRaises(d.Refused):
                d.delete_download(by_path[target])

    def test_modified_replaced_and_wrong_root_refused(self):
        path = self.file('file')
        row = d.scan_downloads(self.root)[0][0]
        path.write_bytes(b'replacement')
        with self.assertRaises(d.Refused):
            d.delete_download(row)
        with patch.object(d, 'downloads_root', return_value=self.root/'other'), self.assertRaises(d.Refused):
            d.delete_download(row)
        self.assertTrue(path.exists())

    def test_only_selected_file_deleted_keeps_siblings_and_directories(self):
        target = self.file('nested/delete.txt')
        keep = self.file('nested/keep.txt')
        row = next(r for r in d.scan_downloads(self.root)[0] if r.path == target)
        self.assertEqual(d.delete_download(row), 5)
        self.assertFalse(target.exists())
        self.assertTrue(keep.exists())
        self.assertTrue(target.parent.exists())

    def test_open_file_and_active_installer_preserved(self):
        target = self.file('locked.txt')
        row = d.scan_downloads(self.root)[0][0]
        with target.open('rb'), self.assertRaises(d.Refused):
            d.delete_download(row)
        with patch.object(d, 'process_snapshot', return_value=[dict(Name='msiexec.exe', ProcessId=999999)]):
            with self.assertRaises(d.Refused):
                d.delete_download(row)
        self.assertTrue(target.exists())

    def test_junction_is_not_followed_and_canceled_scan_is_explicit(self):
        outside = self.root.parent / (self.root.name + '-outside')
        outside.mkdir()
        sentinel = outside / 'preserve.txt'
        sentinel.write_bytes(b'personal')
        link = self.root/'junction'
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(outside)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertEqual(result.returncode, 0)
            rows, warnings = d.scan_downloads(self.root)
            self.assertEqual(rows, [])
            self.assertTrue(warnings)
            self.assertEqual(sentinel.read_bytes(), b'personal')
            cancel = threading.Event()
            cancel.set()
            rows, warnings = d.scan_downloads(self.root, cancel=cancel)
            self.assertEqual(rows, [])
            self.assertTrue(any('cancelada' in warning for warning in warnings))
        finally:
            if link.exists():
                link.rmdir()
            sentinel.unlink()
            outside.rmdir()

