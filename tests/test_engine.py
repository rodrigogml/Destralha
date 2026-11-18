"""Testes do contrato de segurança; exclusões atingem somente fixtures."""
import json
import hashlib
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as e


def rule(**changes):
    data = dict(id="fixture", name="Fixture", category="Chrome", root="local",
                patterns=["app/Cache"], processes=["chrome"], prefixes=["app"],
                guard="App", target_type="Directory", minimum_age_days=0,
                command_pattern="", install_tokens=[], risk="cache", protected=False,
                purpose="Fixture", effect="Fixture")
    data.update(changes)
    return data


def process(name="notepad.exe", pid=999999, command=""):
    return dict(Name=name, ProcessId=pid, ExecutablePath="", CommandLine=command)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.roots = {name: self.root for name in ("local", "roaming", "profile")}
        self.cache = self.root / "app/Cache"
        self.cache.mkdir(parents=True)
        (self.cache / "data").write_bytes(b"cache")
        self.sentinel = self.root / "app/Network/Cookies"
        self.sentinel.parent.mkdir()
        self.sentinel.write_text("preserve")

    def tearDown(self):
        self.temp.cleanup()

    def test_root_and_escape_refused(self):
        for path in (self.root, self.root.parent / "outside", self.root / "../outside"):
            with self.subTest(path=path), self.assertRaises(e.Refused):
                e.safe_path(self.root, path)

    def test_wildcard_cannot_cross_separator(self):
        self.assertTrue(e.path_pattern_matches(r"app\Cache", r"app\*"))
        self.assertFalse(e.path_pattern_matches(r"app\x\Cache", r"app\*"))
        with self.assertRaises(e.Refused):
            e.expand(self.root, r"..\*")

    def test_metadata_measure(self):
        self.assertEqual(e.measure(self.root, self.cache), (5, 1))
        self.assertEqual(e.measure(self.root, self.sentinel), (8, 1))

    def test_missing_and_empty(self):
        self.assertEqual(e.expand(self.root, "missing/Cache"), [])
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(e.measure(self.root, empty), (0, 0))

    def test_junction_in_tree_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.cache / "junction"
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises(e.Refused):
                e.measure(self.root, self.cache)
            with self.assertRaises(e.Refused):
                e.safe_path(self.root, link)
        finally:
            link.rmdir()

    def test_file_in_use_preserved(self):
        r = rule()
        c = e.Candidate(r, self.root, self.cache)
        with (self.cache / "data").open("rb"):
            with patch.object(e, "load_catalog", return_value=[r]), self.assertRaises(e.Refused):
                e.delete_candidate(c, self.roots, [process()])
        self.assertTrue((self.cache / "data").exists())

    def test_readonly_approved_cache_removed_without_changing_sibling(self):
        path = self.cache / 'data'
        path.chmod(path.stat().st_mode & ~stat.S_IWRITE)
        self.assertTrue(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
        r = rule()
        with patch.object(e, 'load_catalog', return_value=[r]):
            self.assertEqual(e.delete_candidate(e.Candidate(r, self.root, self.cache), self.roots, [process()]), (5, 1))
        self.assertEqual(self.sentinel.read_text(), 'preserve')

    def test_readonly_attribute_restored_if_removal_fails(self):
        path = self.cache / 'data'
        path.chmod(path.stat().st_mode & ~stat.S_IWRITE)
        try:
            with patch.object(Path, 'unlink', side_effect=PermissionError('fixture')), self.assertRaises(PermissionError):
                e.unlink_approved_file(self.root, path)
            self.assertTrue(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
            self.assertEqual(path.read_bytes(), b'cache')
        finally:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)

    def test_fixture_removal_preserves_cookie(self):
        r = rule()
        with patch.object(e, "load_catalog", return_value=[r]):
            self.assertEqual(e.delete_candidate(e.Candidate(r, self.root, self.cache), self.roots, [process()]), (5, 1))
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.sentinel.read_text(), "preserve")

    def test_file_target_preserves_parent(self):
        r = rule(patterns=[r"app\Cache\data"], target_type="File")
        with patch.object(e, "load_catalog", return_value=[r]):
            e.delete_candidate(e.Candidate(r, self.root, self.cache / "data"), self.roots, [process()])
        self.assertTrue(self.cache.exists())

    def test_changed_rule_or_unlisted_path_refused(self):
        approved = rule()
        for candidate in (e.Candidate(rule(processes=[]), self.root, self.cache),
                          e.Candidate(approved, self.root, self.sentinel)):
            with patch.object(e, "load_catalog", return_value=[approved]), self.assertRaises(e.Refused):
                e.delete_candidate(candidate, self.roots, [process()])

    def test_protected_rule_refused(self):
        r = rule(protected=True)
        with patch.object(e, "load_catalog", return_value=[r]), self.assertRaises(e.Refused):
            e.delete_candidate(e.Candidate(r, self.root, self.cache), self.roots, [process()])

    def test_waiting_item_selectable_but_execution_still_refused(self):
        r = rule()
        item = e.Candidate(r, self.root, self.cache, reason='Aplicativo aberto: chrome', selected=True)
        self.assertTrue(item.selectable)
        with patch.object(e, 'load_catalog', return_value=[r]), self.assertRaises(e.Refused):
            e.delete_candidate(item, self.roots, [process('chrome.exe')])
        self.assertTrue((self.cache / 'data').exists())
        item.reason = 'Preservado: formato desconhecido'
        self.assertFalse(item.selectable)

    def test_process_reopens_during_preflight(self):
        r = rule()
        with patch.object(e, "load_catalog", return_value=[r]), \
             patch.object(e, "process_snapshot", side_effect=[[process()], [process("CHROME.EXE")]]), \
             self.assertRaises(e.Refused):
            e.delete_candidate(e.Candidate(r, self.root, self.cache), self.roots)
        self.assertTrue((self.cache / "data").exists())

    def test_process_guards_and_own_gui(self):
        self.assertTrue(e.busy_reason(rule(), self.root, [process("CHROME.EXE")]))
        self.assertTrue(e.busy_reason(rule(), self.root, [process("msiexec.exe")]))
        self.assertFalse(e.busy_reason(rule(guard="Pip"), self.root, [process("pythonw.exe")]))
        self.assertFalse(e.busy_reason(rule(guard="Pip"), self.root, [process("pythonw.exe", os.getpid())]))
        self.assertTrue(e.busy_reason(rule(guard="Npm"), self.root, [process("node.exe")]))

    def test_pip_ignores_unrelated_runtimes_and_detects_real_invocations(self):
        r = rule(guard='Pip', processes=[], prefixes=['pip/cache'])
        for name, command in [('python.exe', 'python app.py'), ('pythonw.exe', None),
                              ('py.exe', 'py -3 server.py'), ('msedgewebview2.exe', None)]:
            with self.subTest(name=name):
                self.assertFalse(e.busy_reason(r, self.root, [process(name, command=command)]))
        for name, command in [('python.exe', 'python -m pip install project'),
                              ('py.exe', 'py -3 -m pip download project'),
                              ('pip3.12.exe', 'pip3.12 install project'),
                              ('python.exe', 'python "C:\\tools\\pip\\__main__.py" install project')]:
            with self.subTest(command=command):
                self.assertTrue(e.busy_reason(r, self.root, [process(name, command=command)]))
        self.assertTrue(e.busy_reason(r, self.root, [process('custom.exe', command=str(self.root/'pip/cache'))]))

    def test_pip_cleanup_with_unrelated_python_preserves_installed_data_and_locks(self):
        r = rule(guard='Pip', category='Pip', processes=[])
        c = e.Candidate(r, self.root, self.cache)
        unrelated = [process('python.exe', command='python server.py')]
        with patch.object(e, 'load_catalog', return_value=[r]):
            with (self.cache/'data').open('rb'), self.assertRaises(e.Refused):
                e.delete_candidate(c, self.roots, unrelated)
            self.assertTrue((self.cache/'data').exists())
            self.assertEqual(e.delete_candidate(c, self.roots, unrelated), (5, 1))
            self.assertTrue(self.sentinel.exists())

    def test_webview_profile_ownership(self):
        r = rule(processes=[])
        self.assertTrue(e.busy_reason(r, self.root, [process("msedgewebview2.exe", command=None)]))
        self.assertTrue(e.busy_reason(r, self.root, [process("msedgewebview2.exe", command=str(self.root / "app"))]))
        self.assertFalse(e.busy_reason(r, self.root, [process("msedgewebview2.exe", command="other-profile")]))

    def test_scan_is_read_only_and_cancellable(self):
        with patch.object(e, "process_snapshot", return_value=[process()]):
            rows = e.scan([rule()], self.roots)
            self.assertEqual(rows[0].size, 5)
            self.assertFalse(rows[0].selected)
            cancel = threading.Event()
            cancel.set()
            self.assertEqual(e.scan([rule()], self.roots, cancel=cancel), [])
        self.assertTrue(self.sentinel.exists())

    def test_unknown_process_state_blocks_scan(self):
        with patch.object(e, "process_snapshot", side_effect=OSError("fixture")):
            rows = e.scan([rule()], self.roots)
        self.assertTrue(rows[0].reason)
        self.assertTrue(self.cache.exists())

    def test_discovery_failure_is_visible_and_blocked(self):
        with patch.object(e, 'process_snapshot', return_value=[process()]), \
             patch.object(e, 'expand', side_effect=e.Refused('fixture')):
            rows = e.scan([rule()], self.roots)
        self.assertEqual(len(rows), 1)
        self.assertIn('fixture', rows[0].reason)
        self.assertFalse(rows[0].selected)

    def test_spotify_unknown_entries_preserved(self):
        media = self.root / "Data/aa"
        media.mkdir(parents=True)
        (media / ("a" * 40 + ".file")).write_bytes(b"media")
        self.assertTrue(e.spotify_layout(self.root, media.parent))
        (media / "catalog.db").write_bytes(b"preserve")
        self.assertFalse(e.spotify_layout(self.root, media.parent))

    def test_wheel_origins(self):
        (self.cache / "file.whl").write_bytes(b"wheel")
        metadata = self.cache / "origin.json"
        for archive, url, expected in (
            (None, "https://files.pythonhosted.org/packages/test.zip", False),
            ({}, "https://files.pythonhosted.org/packages/test.zip", False),
            ({"hashes": {"sha256": "a" * 64}}, "file:///local.zip", False),
            ({"hash": "sha256=" + "a" * 64}, "https://files.pythonhosted.org/packages/test.zip", True)):
            metadata.write_text(json.dumps({"url": url, "archive_info": archive}), encoding="utf-8")
            # Pasta de wheel válida não contém arquivos desconhecidos.
            (self.cache / "data").unlink(missing_ok=True)
            self.assertEqual(e.public_wheel(self.cache), expected)

    def test_models_unknown_layout_and_offline(self):
        r = rule(category="Modelos")
        self.assertTrue(e.special_reason(r, self.root, self.cache, self.roots))
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            self.assertIn("offline", e.special_reason(r, self.root, self.cache, self.roots))

    def test_studio_pending_plugins_and_overrides(self):
        target = self.root / 'Google/AndroidStudio2024.1/plugins/meta'
        target.mkdir(parents=True)
        r = rule(guard='StudioPlugins', category='AndroidStudio')
        pending = target.parent / 'action.script'
        pending.write_text('pending')
        self.assertIn('pendente', e.special_reason(r, self.root, target, self.roots))
        pending.unlink()
        properties = target.parents[1] / 'idea.properties'
        for separator in ('=', ':', ' '):
            properties.write_text('idea.plugins.path' + separator + 'C:/custom')
            self.assertTrue(e.special_reason(r, self.root, target, self.roots))

    def test_gradle_unknown_version_preserved(self):
        target = self.root / '.gradle/caches/8-my-files'
        target.mkdir(parents=True)
        self.assertTrue(e.special_reason(rule(category='Gradle'), self.root, target, self.roots))

    def test_installer_recent_identity_and_registry(self):
        target = self.cache / 'installer.exe'
        target.write_bytes(b'fixture')
        r = rule(guard='InstallerArtifact', minimum_age_days=30)
        self.assertIn('recente', e.installer_reason(self.root, target, r))
        os.utime(target, (0, 0))
        with patch.object(e, 'version_identity', return_value={}):
            self.assertIn('Identidade', e.installer_reason(self.root, target, r))
        identity = {'CompanyName': 'Trimble, Inc.', 'OriginalFilename': 'SketchUpStudioFull.exe', 'ProductName': 'SketchUp 2026'}
        with patch.object(e, 'version_identity', return_value=identity), patch.object(e.winreg, 'OpenKey'):
            self.assertIn('pendente', e.installer_reason(self.root, target, r))
        with patch.object(e, 'version_identity', return_value=identity), patch.object(e.winreg, 'OpenKey', side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):
                e.installer_reason(self.root, target, r)

    def test_old_download_preserves_registered_sources_and_changed_content(self):
        package = self.root / 'old-installer'
        package.mkdir()
        (package/'Setup.exe').write_bytes(b'fixture installer')
        r = rule(guard='UnusedAutodeskDownload', installed_name_regex='AutoCAD.*2024',
                 setup_sha256=hashlib.sha256(b'fixture installer').hexdigest())
        self.assertEqual(e.unused_download_reason(package, r, records=[]), '')
        self.assertIn('instalado', e.unused_download_reason(package, r, records=[{'DisplayName':'AutoCAD 2024'}]))
        records = [{'DisplayName':'Componente compartilhado', 'InstallSource':str(package/'x64/CER')}]
        self.assertIn('registrada', e.unused_download_reason(package, r, records=records))
        (package/'my-documents').mkdir()
        self.assertIn('desconhecido', e.unused_download_reason(package, r, records=[]))
        (package/'my-documents').rmdir()
        (package/'Setup.exe').write_bytes(b'changed')
        self.assertIn('mudou', e.unused_download_reason(package, r, records=[]))

    def test_diagnostic_age_and_format_are_revalidated_before_delete(self):
        dump = self.cache/'old.dmp'
        dump.write_bytes(b'fixture dump')
        r = rule(guard='DiagnosticDump', category='Diagnosticos', minimum_age_days=14,
                 patterns=['app/Cache/*.dmp'], target_type='File', risk='diagnostic')
        self.assertIn('recente', e.special_reason(r,self.root,dump,self.roots))
        os.utime(dump,(0,0))
        self.assertEqual(e.special_reason(r,self.root,dump,self.roots),'')
        item = e.Candidate(r,self.root,dump,selected=True)
        with patch.object(e,'load_catalog',return_value=[r]):
            size,count=e.delete_candidate(item,self.roots,[process()])
        self.assertEqual((size,count),(12,1))
        self.assertFalse(dump.exists())
        self.assertTrue(self.sentinel.exists())
        unknown = self.cache/'database.log'
        unknown.write_bytes(b'keep')
        os.utime(unknown,(0,0))
        self.assertIn('desconhecido', e.special_reason(r,self.root,unknown,self.roots))

    def test_catalog_integrity(self):
        self.assertGreater(len(e.load_catalog()), 40)
        (self.root / "catalog.json").write_text("[]")
        with patch.object(e, "APP", self.root), self.assertRaises(e.Refused):
            e.load_catalog()


if __name__ == "__main__":
    unittest.main()
