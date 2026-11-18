"""Medição por metadados, com cancelamento e identificação de hardlinks."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import stat
import time

import engine


@dataclass(frozen=True)
class Installation:
    key: str
    kind: str
    name: str
    root: Path
    data: Path | None = None
    service: str = ''
    state: str = ''
    config: Path | None = None
    port: int = 3306
    warnings: tuple[str,...] = ()
    preserved_files: tuple[Path,...] = ()


@dataclass(frozen=True)
class FileRecord:
    path: Path
    size: int
    modified: float
    category: str
    modified_ns: int = 0
    identity: int = 0
    device: int = 0


@dataclass
class Inventory:
    installation: Installation
    files: list[FileRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_links: int = 0
    hardlinks: int = 0
    canceled: bool = False
    measured_at: float = field(default_factory=time.time)

    @property
    def total(self):
        return sum(item.size for item in self.files)

    def groups(self):
        groups = {}
        for item in self.files:
            group = groups.setdefault(item.category,dict(bytes=0,files=0))
            group['bytes'] += item.size
            group['files'] += 1
        return dict(sorted(groups.items(),key=lambda pair:pair[1]['bytes'],reverse=True))


def measure(installation, roots, classifier, cancel=None, notify=lambda *_:None):
    """Percorre sem ler conteúdo. Falhas produzem total parcial, nunca zero seguro.

    Diretórios são enumerados em fluxo; somente subdiretórios ficam pendentes.
    Não atravessa links, arquivos offline ou pontos de junção. IDs de arquivo
    evitam somar hardlinks novamente, inclusive entre escopos sobrepostos.
    """
    result = Inventory(installation,warnings=list(installation.warnings))
    pending = list(reversed(roots))
    seen_files,seen_dirs = set(),set()
    failures = 0
    while pending:
        if cancel is not None and cancel.is_set():
            result.canceled = True; break
        path = Path(pending.pop())
        try:
            info = engine.check_item(path)
            if stat.S_ISREG(info.st_mode):
                entries = [(path,info)]
            else:
                directory_key = (info.st_dev,info.st_ino)
                if directory_key in seen_dirs:
                    continue
                seen_dirs.add(directory_key)
                entries = None
            def accept(file,metadata):
                key = (metadata.st_dev,metadata.st_ino)
                if key in seen_files:
                    result.hardlinks += 1; return
                seen_files.add(key)
                result.files.append(FileRecord(file,metadata.st_size,metadata.st_mtime,classifier(file),
                                               metadata.st_mtime_ns,metadata.st_ino,metadata.st_dev))
                if len(result.files)%500==0:
                    notify(len(result.files),str(file.parent))
            if entries is not None:
                for file,metadata in entries: accept(file,metadata)
                continue
            notify(len(result.files),str(path))
            with os.scandir(path) as children:
                for child in children:
                    if cancel is not None and cancel.is_set():
                        result.canceled = True; break
                    file = Path(child.path)
                    try:
                        # DirEntry.stat no Windows omite IDs; lstat fornece a
                        # identidade necessária para não duplicar hardlinks.
                        metadata = engine.check_item(file)
                        if stat.S_ISDIR(metadata.st_mode): pending.append(file)
                        else: accept(file,metadata)
                    except engine.Refused:
                        result.skipped_links += 1
                    except OSError as exc:
                        failures += 1
                        if failures<=5: result.warnings.append(f'{file}: {exc.strerror or type(exc).__name__}')
        except engine.Refused:
            result.skipped_links += 1
        except OSError as exc:
            failures += 1
            if failures<=5: result.warnings.append(f'{path}: {exc.strerror or type(exc).__name__}')
    if failures:
        result.warnings.append(f'{failures} falha(s) de acesso: total parcial.')
    if result.skipped_links:
        result.warnings.append(f'{result.skipped_links} link(s) ou arquivos offline preservados; não medidos.')
    if result.hardlinks:
        result.warnings.append(f'{result.hardlinks} referência(s) compartilhada(s) não somada(s) novamente.')
    if result.canceled:
        result.warnings.append('Medição cancelada: resultados incompletos.')
    return result
