"""Inventário e limpeza restritos ao catálogo aprovado para a conta atual.

Não encerra aplicativos, não segue links e não procura pastas arbitrárias pelo
nome. Toda remoção revalida catálogo, processos, condição especial e arquivos.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import threading
import time
from urllib.parse import urlparse
import winreg

APP = Path(__file__).resolve().parent
UNSUPPORTED = 0x441400  # ReparsePoint, Offline, RecallOnOpen/DataAccess.
CATALOG_HASH = "c4798163dd90f25db116b777d44a0640697d070d37e52e893af3f55a5e0f26d4"


class Refused(Exception):
    """O contrato de limpeza não pôde ser comprovado; preservar o alvo."""


def known_roots() -> dict[str, Path]:
    """Resolve pastas da conta executante pela API Windows, sem usuário fixo."""
    result = {}
    for name, csidl in (("local", 28), ("roaming", 26), ("profile", 40)):
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer):
            raise Refused(f"Não foi possível localizar a pasta {name}.")
        result[name] = Path(buffer.value)
    return result


def load_catalog() -> list[dict]:
    """Recusa catálogo alterado, incompleto ou com padrões fora do contrato."""
    raw = (APP / "catalog.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_HASH:
        raise Refused("O catálogo foi alterado. Restaure a versão distribuída do aplicativo.")
    rules = json.loads(raw)
    for rule in rules:
        if rule["root"] not in {"local", "roaming", "profile"}:
            raise Refused("Raiz de catálogo desconhecida.")
        for pattern in rule["patterns"]:
            parts = PureWindowsPath(pattern)
            if parts.is_absolute() or parts.drive or ".." in parts.parts or "**" in pattern:
                raise Refused("Padrão de catálogo inválido.")
    return rules


def check_item(path: Path):
    """Lê apenas metadados e recusa links, placeholders e objetos não regulares."""
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & UNSUPPORTED or stat.S_ISLNK(info.st_mode):
        raise Refused(f"Link ou arquivo offline preservado: {path}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise Refused(f"Tipo de arquivo não reconhecido: {path}")
    return info


def safe_path(root: Path, path: Path) -> Path:
    """Valida limite absoluto e cada ancestral, incluindo a própria raiz.

    Não aceita excluir a raiz, nem sair dela por '..', links ou junctions.
    Ausência de um arquivo gera FileNotFoundError e nunca amplia a seleção.
    """
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise Refused("Alvo fora da raiz permitida.") from exc
    if not relative.parts:
        raise Refused("A raiz inteira não pode ser excluída.")
    current = root
    check_item(current)
    for part in relative.parts:
        current /= part
        check_item(current)
    return path


def expand(root: Path, pattern: str) -> list[Path]:
    """Expande um segmento por vez, validando antes de enumerar diretórios."""
    parts = PureWindowsPath(pattern)
    if parts.is_absolute() or parts.drive or ".." in parts.parts or "**" in pattern:
        raise Refused("Padrão de caminho recusado.")
    check_item(root)
    current = [root]
    for part in parts.parts:
        following = []
        for parent in current:
            if not parent.is_dir():
                continue
            if any(c in part for c in "*?["):
                for child in parent.iterdir():
                    if fnmatch.fnmatchcase(child.name.casefold(), part.casefold()):
                        if part == 'Profile [0-9]*' and not re.fullmatch(r'Profile [0-9]+', child.name):
                            continue
                        following.append(safe_path(root, child))
            else:
                try:
                    following.append(safe_path(root, parent / part))
                except FileNotFoundError:
                    pass
        current = following
    return current


def tree(root: Path, path: Path, cancel: threading.Event | None = None):
    """Enumera árvore validada sem seguir links; erros preservam o alvo inteiro."""
    pending = [safe_path(root, path)]
    while pending:
        if cancel is not None and cancel.is_set():
            raise Refused("Operação cancelada.")
        current = pending.pop()
        info = check_item(current)
        yield current, info
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as entries:
                pending.extend(Path(entry.path) for entry in entries)


def measure(root: Path, path: Path, cancel=None) -> tuple[int, int]:
    size = count = 0
    for _, info in tree(root, path, cancel):
        if stat.S_ISREG(info.st_mode):
            size += info.st_size
            count += 1
    return size, count


def native_process_names() -> dict[int, str]:
    """Obtém nomes inclusive de processos protegidos, sem ler sua memória."""
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]
    api = ctypes.windll.kernel32
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise Refused("Consulta nativa de processos falhou.")
    result = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        ok = api.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            result[entry.pid] = entry.name
            ok = api.Process32NextW(handle, ctypes.byref(entry))
    finally:
        api.CloseHandle(handle)
    if not result:
        raise Refused("Consulta nativa de processos vazia.")
    return result


def process_snapshot() -> list[dict]:
    """Consulta somente leitura via Windows; comandos ficam apenas em memória.

    Usa o PowerShell incorporado ao Windows como ponte CIM, sem ler ou executar
    nenhum PS1. Falha/timeout impede a limpeza. Não requer pacote Python externo.
    """
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        result = []
        native_names = None
        for process in psutil.process_iter(["name", "exe", "cmdline", "pid"], ad_value=None):
            info = process.info
            if not info["name"]:
                if native_names is None:
                    native_names = native_process_names()
                info["name"] = native_names.get(info["pid"])
                if not info["name"]:
                    if not process.is_running():
                        continue
                    raise Refused("Nome de processo indisponível.")
            result.append({"Name": info["name"], "ExecutablePath": info["exe"],
                           "CommandLine": " ".join(info["cmdline"]) if info["cmdline"] else None,
                           "ProcessId": info["pid"]})
        if not result:
            raise Refused("Consulta de processos vazia.")
        return result
    command = "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Select-Object Name,ExecutablePath,CommandLine,ProcessId) | ConvertTo-Json -Compress"
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise Refused("Não foi possível consultar aplicativos em execução.")
    # PowerShell redirecionado usa a página de código do console para JSON.
    text = result.stdout.decode("utf-8-sig", errors="strict")
    rows = json.loads(text)
    if not isinstance(rows, list) or not rows:
        raise Refused("Consulta de processos incompleta.")
    return rows


def busy_reason(rule: dict, root: Path, processes: list[dict]) -> str:
    """Protege instaladores e proprietários ativos, incluindo runtimes genéricos."""
    names = {name.casefold() for name in rule["processes"]}
    prefixes = [str(root / p.split('*', 1)[0].rstrip('\\')).casefold() for p in rule["prefixes"]]
    for process in processes:
        if process.get("ProcessId") == os.getpid():
            continue  # Este aplicativo não instala pacotes nem usa os caches-alvo.
        name = str(process.get("Name") or "").casefold().removesuffix(".exe")
        identity = f" (PID {process['ProcessId']})" if process.get('ProcessId') else ''
        command = str(process.get("CommandLine") or "")
        executable = str(process.get("ExecutablePath") or "")
        if re.fullmatch(r"msiexec|setup|vs_installer|vs_installershell|installshield|windowsupdatebox|appinstaller", name):
            return "Instalador em execução" + identity
        if name in names:
            return f"Aplicativo aberto: {name}" + identity
        if rule["guard"] == "Pip":
            python = re.fullmatch(r"python[\d.]*w?|py", name)
            pip = re.fullmatch(r"pip[\d.]*", name)
            invokes_pip = re.search(r'(?:^|\s)-m\s+[\"]?pip(?:\s|[\"]|$)|(?:^|[\\/\s\"])pip(?:\.py|[\\/]__main__\.py)(?:\s|[\"]|$)', command, re.I)
            if pip or (python and invokes_pip):
                return "Python/pip em execução" + identity
            if any(prefix in command.casefold() for prefix in prefixes):
                return "Processo usando o perfil" + identity
            # Um runtime Python ou WebView aberto não demonstra uso do pip.
            # Uso programático não identificável fica sujeito à trava de arquivo.
            continue
        if rule["guard"] == "Npm" and name in {"node", "npm", "npx"}:
            return "Node/npm/npx em execução" + identity
        if name == "msedgewebview2" and not command:
            return "WebView2 sem informação suficiente" + identity
        if any(prefix in command.casefold() for prefix in prefixes):
            return "Processo usando o perfil" + identity
        if any(token.casefold() in executable.casefold() for token in rule.get("install_tokens", [])):
            return "Processo do aplicativo em execução" + identity
        if rule.get("command_pattern") and re.search(rule["command_pattern"], command, re.I):
            return "Ferramenta relacionada em execução"
    return ""


def public_wheel(path: Path) -> bool:
    """Aceita somente origem pública PyPI e SHA256 reconhecido, sem VCS/local."""
    try:
        check_item(path / "origin.json")
        data = json.loads((path / "origin.json").read_text(encoding="utf-8"))
        archive = data.get("archive_info")
        if not isinstance(archive, dict) or "dir_info" in data or "vcs_info" in data:
            return False
        hashes = archive.get("hashes", {})
        sha = hashes.get("sha256", "") if isinstance(hashes, dict) else ""
        if not re.fullmatch(r"[a-fA-F0-9]{64}", str(sha)) and not re.fullmatch(r"sha256=[a-fA-F0-9]{64}", str(archive.get("hash", ""))):
            return False
        url = urlparse(data["url"])
        if url.scheme != "https" or url.hostname != "files.pythonhosted.org" or url.username or url.password or not url.path.startswith("/packages/"):
            return False
        files = list(path.iterdir())
        return any(p.suffix == ".whl" for p in files) and all(p.is_file() and (p.name == "origin.json" or p.suffix == ".whl") for p in files)
    except (OSError, ValueError, TypeError, KeyError, Refused):
        return False


def spotify_layout(root: Path, path: Path) -> bool:
    """Somente buckets hexadecimais de mídia; bancos e desconhecidos recusados."""
    try:
        safe_path(root, path)
        for bucket in path.iterdir():
            check_item(bucket)
            if not bucket.is_dir() or not re.fullmatch(r"[a-fA-F0-9]{2}", bucket.name):
                return False
            for item in bucket.iterdir():
                check_item(item)
                if not item.is_file() or not re.fullmatch(r"[a-fA-F0-9]{40}\.file", item.name) or not item.name.casefold().startswith(bucket.name.casefold()):
                    return False
        return True
    except (OSError, Refused):
        return False


def version_identity(path: Path) -> dict[str, str]:
    """Consulta VERSIONINFO sem executar o instalador."""
    api = ctypes.windll.version
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return {}
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return {}
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not api.VerQueryValueW(buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        return {}
    words = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))
    translation = f"{words[0]:04x}{words[1]:04x}"
    result = {}
    for name in ("CompanyName", "OriginalFilename", "ProductName"):
        if api.VerQueryValueW(buffer, f"\\StringFileInfo\\{translation}\\{name}", ctypes.byref(pointer), ctypes.byref(length)):
            result[name] = ctypes.wstring_at(pointer.value)
    return result


def installer_reason(root: Path, path: Path, rule: dict) -> str:
    """Recusa cópia recente, identidade trocada, reboot pendente ou fonte registrada."""
    if time.time() - path.stat().st_mtime < rule.get("minimum_age_days", 30) * 86400:
        return "Instalador recente"
    if version_identity(path) != {"CompanyName": "Trimble, Inc.", "OriginalFilename": "SketchUpStudioFull.exe", "ProductName": "SketchUp 2026"}:
        return "Identidade do instalador não comprovada"
    for subkey in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\InProgress",
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey):
                return "Instalação ou reinício pendente"
        except FileNotFoundError:
            pass
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as registry:
                    for n in range(winreg.QueryInfoKey(registry)[0]):
                        with winreg.OpenKey(registry, winreg.EnumKey(registry, n)) as product:
                            for m in range(winreg.QueryInfoKey(product)[1]):
                                value = str(winreg.EnumValue(product, m)[1]).casefold()
                                if str(path.parent).casefold() in value:
                                    return "Instalador usado para manutenção"
            except FileNotFoundError:
                pass
    return ""


def installation_records():
    """Lê somente metadados de manutenção, incluindo fontes MSI por usuário.

    Ausência de uma chave é normal. Acesso negado não equivale a ausência:
    propaga o erro para que o alvo permaneça protegido.
    """
    fields = ('DisplayName', 'InstallSource', 'InstallLocation', 'LocalPackage',
              'ModifyPath', 'UninstallString', 'QuietUninstallString',
              'BundleCachePath', 'BundleOriginalSource')
    def read(key):
        result = {}
        for name in fields:
            try:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str):
                    result[name] = os.path.expandvars(value)
            except FileNotFoundError:
                pass
        return result

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as product:
                            yield read(product)
            except FileNotFoundError:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData', 0, winreg.KEY_READ | view) as users:
                for index in range(winreg.QueryInfoKey(users)[0]):
                    user = winreg.EnumKey(users, index)
                    try:
                        with winreg.OpenKey(users, user + r'\Products') as products:
                            for number in range(winreg.QueryInfoKey(products)[0]):
                                name = winreg.EnumKey(products, number)
                                try:
                                    with winreg.OpenKey(products, name + r'\InstallProperties') as properties:
                                        yield read(properties)
                                except FileNotFoundError:
                                    pass
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass


def unused_download_reason(path: Path, rule: dict, records=None) -> str:
    """Recusa pacote antigo se instalado, referenciado ou alterado desde a análise."""
    records = list(installation_records()) if records is None else records
    if any(re.search(rule['installed_name_regex'], record.get('DisplayName', ''), re.I) for record in records):
        return 'Produto ainda instalado; pacote preservado'
    target = str(path).casefold()
    if any(target in value.casefold() for record in records for value in record.values()):
        return 'Fonte de instalação/reparo ainda registrada'
    allowed = {'3rdParty', '4cd', 'Content', 'manifest', 'ODIS', 'SetupRes', 'x64', 'x86',
               'setup_ext.xml', 'Setup.exe', 'Setup.exe.config', 'setup.xml'}
    if any(item.name not in allowed for item in path.iterdir()):
        return 'Conteúdo do pacote desconhecido; preservado'
    setup = path / 'Setup.exe'
    safe_path(path.parent, setup)
    digest = hashlib.sha256()
    with setup.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != rule['setup_sha256']:
        return 'Identidade do pacote mudou; preservado'
    return ''


def special_reason(rule: dict, root: Path, path: Path, roots: dict[str, Path]) -> str:
    """Reavalia contratos especiais também imediatamente antes da exclusão."""
    guard = rule["guard"]
    if rule.get('minimum_age_days', 0) and guard != 'InstallerArtifact':
        cutoff = time.time() - rule['minimum_age_days'] * 86400
        if path.is_dir():
            if any(info.st_mtime > cutoff for _, info in tree(root, path)):
                return 'Arquivo recente; aguarde o prazo mínimo'
        elif path.stat().st_mtime > cutoff:
            return 'Arquivo recente; aguarde o prazo mínimo'
    if guard == 'DiagnosticDump':
        if not path.is_file() or path.suffix.casefold() not in ('.dmp', '.mdmp'):
            return 'Formato de diagnóstico desconhecido; preservado'
    if guard == 'UnusedAutodeskDownload':
        return unused_download_reason(path, rule)
    if rule["category"] == "Gradle" and not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", path.name):
        return "Nome de cache Gradle não reconhecido"
    if rule["name"] == "Visual Studio component cache":
        if not re.fullmatch(r"[0-9]+\.[0-9]+(?:_[A-Za-z0-9]+)?", path.parent.name):
            return "Instância Visual Studio não reconhecida"
    if "wheels" in path.parts and rule["category"] == "Pip" and not public_wheel(path):
        return "Wheel local, privado ou de origem não comprovada"
    if guard == "SpotifyMedia" and not spotify_layout(root, path):
        return "Mídia Spotify de formato desconhecido"
    if rule["category"] == "Modelos" and (os.environ.get("HF_HUB_OFFLINE") or os.environ.get("TRANSFORMERS_OFFLINE")):
        return "Modo offline configurado"
    if rule["category"] == "Modelos":
        allowed = {"blobs", "refs", "snapshots", "trees", ".no_exist"}
        for item in path.iterdir():
            check_item(item)
            if (item.is_dir() and item.name not in allowed) or (item.is_file() and item.name != "CACHEDIR.TAG"):
                return "Conteúdo de modelo desconhecido; preservado"
    if guard == "StudioPlugins":
        parts = path.relative_to(root).parts
        base = root.joinpath(*parts[:2])
        if (base / "plugins/action.script").exists():
            return "Instalação de plugin pendente"
        if os.environ.get("STUDIO_PROPERTIES") or os.environ.get("STUDIO_VM_OPTIONS"):
            return "Caminhos personalizados da IDE"
        for properties in (base / "idea.properties", roots["roaming"] / "Google" / parts[1] / "idea.properties"):
            if properties.exists():
                check_item(properties)
                if re.search(r"^\s*idea\.(plugins|system|config)\.path(?:\s*[:=]|\s+)", properties.read_text(encoding="utf-8", errors="strict"), re.M):
                    return "Caminhos personalizados da IDE"
    if guard == "InstallerArtifact":
        return installer_reason(root, path, rule)
    return ""


@dataclass
class Candidate:
    rule: dict
    root: Path
    path: Path
    size: int = 0
    files: int = 0
    reason: str = ""
    selected: bool = False
    @property
    def selectable(self):
        """Permite planejar alvos em uso, sem liberar sua exclusão antecipada."""
        temporary = ('Aplicativo aberto', 'Processo usando', 'Processo do aplicativo',
                     'Python/pip', 'Node/npm/npx', 'WebView2 sem informação',
                     'Instalador em execução', 'Ferramenta relacionada',
                     'Consulta de processos indisponível', 'Instalação de plugin pendente',
                     'Instalação ou reinício pendente')
        return not self.rule.get('protected') and (not self.reason or self.reason.startswith(temporary))
    @property
    def key(self):
        return self.rule["id"] + ":" + str(self.path.relative_to(self.root)).casefold()


def scan(rules, roots, notify=lambda *_: None, cancel=None) -> list[Candidate]:
    """Mede somente alvos catalogados; captura falhas por alvo e nunca remove."""
    result, seen = [], set()
    try:
        processes = process_snapshot()
        process_error = ""
    except Exception:
        processes, process_error = [], "Consulta de processos indisponível"
    for index, rule in enumerate(rules):
        if cancel is not None and cancel.is_set():
            break
        notify(index, len(rules), rule["name"])
        root = roots[rule["root"]]
        reason = "Preservado: " + rule["effect"] if rule.get("protected") else process_error or busy_reason(rule, root, processes)
        for pattern in rule["patterns"]:
            try:
                paths = expand(root, pattern)
            except (OSError, Refused) as exc:
                notify(index, len(rules), f"Preservado: {exc}")
                result.append(Candidate(rule, root, root / pattern, reason=f"Preservado: {exc}"))
                continue
            for path in paths:
                key = str(path).casefold()
                if key in seen:
                    continue
                seen.add(key)
                candidate = Candidate(rule, root, path, reason=reason)
                try:
                    candidate.size, candidate.files = measure(root, path, cancel)
                    special = special_reason(rule, root, path, roots)
                    candidate.reason = candidate.reason or special
                except (OSError, ValueError, Refused) as exc:
                    candidate.reason = f"Preservado: {exc}"
                result.append(candidate)
    return result


def exclusive_check(path: Path):
    """Abre sem compartilhamento e sem ler conteúdo; recusa arquivo em uso."""
    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise Refused(f"Arquivo em uso ou sem acesso: {path.name}")
    close = ctypes.windll.kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close(handle)


def unlink_approved_file(root: Path, path: Path):
    """Retira somente leitura de arquivo aprovado, sem alterar ACL/proprietário.

    O chamador deve ter validado catálogo, processos e guardas. Em falha de
    remoção, tenta restaurar o atributo somente se o mesmo arquivo ainda existir.
    """
    current = safe_path(root, path)
    info = check_item(current)
    exclusive_check(current)
    readonly = bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY)
    if readonly:
        try:
            os.chmod(current, info.st_mode | stat.S_IWRITE)
        except PermissionError as exc:
            raise Refused('Não foi possível retirar o atributo Somente leitura. '
                          'Execute o programa como administrador; permissões e proprietário não foram alterados.') from exc
    try:
        safe_path(root, current)
        latest = check_item(current)
        if (latest.st_ino, latest.st_size, latest.st_mtime_ns) != (info.st_ino, info.st_size, info.st_mtime_ns):
            raise Refused('Arquivo mudou antes da exclusão; preservado.')
        exclusive_check(current)
        current.unlink()
    except Exception:
        if readonly:
            try:
                latest = check_item(safe_path(root, current))
                if latest.st_ino == info.st_ino:
                    os.chmod(current, info.st_mode)
            except (OSError, Refused):
                pass
        raise


def delete_candidate(candidate: Candidate, roots: dict[str, Path], processes=None) -> tuple[int, int]:
    """Exclui apenas alvo ainda pertencente ao catálogo íntegro.

    Revalida processo, condições e toda a árvore antes de remover o primeiro
    arquivo. Revalida caminhos a cada remoção, nunca segue links nem força apps.
    Erros de disco após o início podem resultar em exclusão parcial de cache.
    """
    rules = load_catalog()
    approved = next((rule for rule in rules if rule["id"] == candidate.rule["id"]), None)
    if approved and approved.get("protected"):
        raise Refused("Diretório protegido; não é alvo de limpeza.")
    if approved != candidate.rule or roots[approved["root"]] != candidate.root:
        raise Refused("Regra ou raiz mudou desde a análise.")
    relative = str(candidate.path.relative_to(candidate.root))
    if not any(path_pattern_matches(relative, pattern) for pattern in approved["patterns"]):
        raise Refused("Caminho fora da seleção catalogada.")
    reason = busy_reason(approved, candidate.root, process_snapshot() if processes is None else processes)
    reason = reason or special_reason(approved, candidate.root, candidate.path, roots)
    if reason:
        raise Refused(reason)
    entries = list(tree(candidate.root, candidate.path))
    expected_file = approved["target_type"] == "File"
    if expected_file == stat.S_ISDIR(entries[0][1].st_mode):
        raise Refused("O tipo do alvo mudou.")
    for path, info in entries:
        if stat.S_ISREG(info.st_mode):
            exclusive_check(path)
    reason = busy_reason(approved, candidate.root, process_snapshot() if processes is None else processes)
    reason = reason or special_reason(approved, candidate.root, candidate.path, roots)
    if reason:
        raise Refused(reason)
    # Acesso exclusivo não permanece aberto durante a remoção; uma corrida
    # externa ainda pode gerar falha parcial, informada ao usuário pelo executor.
    size = count = 0
    last_check = time.monotonic()
    for path, _ in reversed(entries):
        if processes is None and time.monotonic() - last_check >= 1:
            reason = busy_reason(approved, candidate.root, process_snapshot())
            if reason:
                raise Refused(reason)
            last_check = time.monotonic()
        current = safe_path(candidate.root, path)
        info = check_item(current)
        if stat.S_ISDIR(info.st_mode):
            current.rmdir()
        else:
            unlink_approved_file(candidate.root, current)
            size += info.st_size
            count += 1
    return size, count


def path_pattern_matches(relative: str, pattern: str) -> bool:
    """Curinga nunca atravessa separadores ou sobe de diretório."""
    actual, expected = PureWindowsPath(relative).parts, PureWindowsPath(pattern).parts
    return len(actual) == len(expected) and all(fnmatch.fnmatchcase(a.casefold(), b.casefold()) for a, b in zip(actual, expected))


def human_size(size: int) -> str:
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or suffix == "TiB":
            return f"{size:.2f} {suffix}"
        size /= 1024
