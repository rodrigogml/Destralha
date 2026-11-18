"""Revisão separada de Downloads: idade/tamanho não provam que algo é lixo."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import time
import uuid

from engine import (Refused, safe_path, check_item, installation_records,
                    process_snapshot, busy_reason)

LARGE = 1024 ** 3
PARTIAL = {'.crdownload', '.part', '.partial', '.download', '.tmp'}


def downloads_root() -> Path:
    """Resolve Downloads do usuário atual, inclusive quando redirecionado."""
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID('374de290-123f-4565-9164-39c4925e467b').bytes_le)
    result = ctypes.c_wchar_p()
    function = ctypes.windll.shell32.SHGetKnownFolderPath
    function.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                         ctypes.POINTER(ctypes.c_wchar_p)]
    function.restype = ctypes.c_long
    code = function(ctypes.byref(guid), 0, None, ctypes.byref(result))
    if code != 0:
        raise Refused('Não foi possível localizar Downloads do usuário atual.')
    try:
        return Path(result.value)
    finally:
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(result, ctypes.c_void_p))


def validate_days(days: int):
    if type(days) is not int or not 1 <= days <= 36500:
        raise ValueError('Escolha um prazo entre 1 e 36500 dias.')


@dataclass
class Download:
    root: Path
    path: Path
    size: int
    modified_ns: int
    identity: int
    days: int
    match: str
    reason: str = ''
    selected: bool = False


def match_reason(size, modified, days, now):
    reasons = []
    if modified < now - days * 86400:
        reasons.append(f'Mais de {days} dias')
    if size > LARGE:
        reasons.append('Maior que 1 GiB')
    return ' + '.join(reasons)


def source_roots(root: Path, records):
    """Preserva pacotes referenciados por software instalado, não só o MSI.

    Uma fonte interna protege o pacote que contém Setup.exe. Sem esse marcador,
    preserva o diretório de primeiro nível em Downloads por precaução.
    """
    protected = set()
    for record in records:
        for field, value in record.items():
            if field == 'DisplayName':
                continue
            values = [value.strip(' "')]
            values += re.findall(r'"([A-Za-z]:\\[^"\r\n]+)"', value)
            values += re.findall(r'([A-Za-z]:\\[^"\r\n]*?\.(?:exe|msi))\b', value, re.I)
            for text in values:
                path = Path(text)
                if not path.is_absolute():
                    continue
                try:
                    path = safe_path(root, path)
                except (ValueError, OSError, Refused):
                    continue
                relative = path.relative_to(root)
                boundary = root / relative.parts[0]
                for parent in (path, *path.parents):
                    if parent == root:
                        break
                    try:
                        marker = safe_path(root, parent / 'Setup.exe')
                        if marker.is_file():
                            boundary = parent
                            break
                    except (OSError, Refused):
                        continue
                protected.add(boundary)
    return protected


def blocked_reason(root, path, protected, processes):
    if path.suffix.casefold() in PARTIAL:
        return 'Download parcial ou arquivo temporário: preservado'
    if any(path == source or source in path.parents for source in protected):
        return 'Fonte/local de instalação ou reparo registrado: preservado'
    reason = busy_reason(dict(processes=[], prefixes=[], guard='Downloads',
                              install_tokens=[], command_pattern=''), root, processes)
    if reason:
        return reason
    target = str(path).casefold()
    for process in processes:
        if process.get('ProcessId') == os.getpid():
            continue
        if target in (process.get('ExecutablePath') or '').casefold() or target in (process.get('CommandLine') or '').casefold():
            return 'Processo usando este arquivo: preservado'
    return ''


def scan_downloads(root: Path, days=90, cancel=None):
    """Lista arquivos antigos OU >1 GiB; nunca seleciona/remove automaticamente.

    Examina metadados recursivamente. Links/placeholders e pastas inacessíveis
    são pulados com aviso, sem hidratação. Falha na consulta de instalação ou
    processos mantém todos os resultados bloqueados.
    """
    validate_days(days)
    check_item(root)
    warning = []
    try:
        protected = source_roots(root, list(installation_records()))
        processes = process_snapshot()
        unavailable = ''
    except Exception as exc:
        protected, processes = set(), []
        unavailable = 'Verificação de instalação/processos indisponível; preservado'
        warning.append(f'{unavailable}: {exc}')
    now, result, pending = time.time(), [], [root]
    while pending:
        if cancel is not None and cancel.is_set():
            break
        directory = pending.pop()
        try:
            if directory != root:
                safe_path(root, directory)
            children = list(directory.iterdir())
        except (OSError, Refused) as exc:
            warning.append(f'{directory}: {exc}')
            continue
        for path in children:
            if cancel is not None and cancel.is_set():
                break
            try:
                info = check_item(safe_path(root, path))
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                match = match_reason(info.st_size, info.st_mtime, days, now)
                if match:
                    result.append(Download(root, path, info.st_size, info.st_mtime_ns,
                                           info.st_ino, days, match, unavailable or
                                           blocked_reason(root, path, protected, processes)))
            except (OSError, Refused) as exc:
                warning.append(f'{path}: {exc}')
    if cancel is not None and cancel.is_set():
        warning.append('Busca cancelada: a lista está incompleta. Busque novamente para analisar tudo.')
    return sorted(result, key=lambda row: row.size, reverse=True), warning


def delete_download(row: Download):
    """Revalida a seleção e apaga só um arquivo, mantendo handle exclusivo.

    Recusa mudança de tamanho/data/identidade, links, execução, instalação,
    fonte registrada e mudança da pasta conhecida. Não remove diretórios.
    """
    validate_days(row.days)
    if row.root != downloads_root():
        raise Refused('A pasta Downloads mudou; analise novamente.')
    path = safe_path(row.root, row.path)
    info = check_item(path)
    if not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns, info.st_ino) != (row.size, row.modified_ns, row.identity):
        raise Refused('Arquivo mudou desde a análise; analise novamente.')
    if not match_reason(info.st_size, info.st_mtime, row.days, time.time()):
        raise Refused('Arquivo não corresponde mais ao filtro.')
    protected = source_roots(row.root, list(installation_records()))
    reason = blocked_reason(row.root, path, protected, process_snapshot())
    if reason:
        raise Refused(reason)
    from file_deletion import delete_snapshot
    return delete_snapshot(path,row.size,row.modified_ns,row.identity)
