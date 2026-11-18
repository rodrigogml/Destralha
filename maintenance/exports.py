"""Revisão e exclusão individual de exportações de uma instalação MySQL."""
import stat

import engine
from file_deletion import delete_snapshot
from . import mysql

CATEGORY = 'Exportações / backups a revisar'
PACKAGED = {'share','scripts','support-files','mysql-test','include','lib','docs'}


def validate(installation, record):
    """Não confunde dumps revisáveis com arquivos de dados ou scripts distribuídos.

    A seleção confirma que a cópia não é necessária para restauração. Arquivos
    alterados, ligados, em uso ou sem identidade de inventário são preservados.
    """
    if installation.kind!='mysql' or record.category!=CATEGORY:
        raise engine.Refused('Este grupo não permite exclusão direta.')
    engine.safe_path(type(installation.root)(installation.root.anchor),installation.root)
    path=engine.safe_path(installation.root,record.path)
    if installation.data is None:
        raise engine.Refused('Diretório de dados desconhecido; exclusão direta bloqueada.')
    if path==installation.data or installation.data in path.parents:
        raise engine.Refused('Arquivo dentro do datadir: manutenção direta bloqueada.')
    if any(part.casefold() in PACKAGED for part in path.relative_to(installation.root).parts[:-1]):
        raise engine.Refused('Script ou conteúdo distribuído com o servidor; preservado.')
    if mysql.classify(path,installation)!=CATEGORY:
        raise engine.Refused('Tipo não corresponde a uma exportação revisável.')
    for target in installation.preserved_files:
        if not target.is_absolute():target=installation.root/target
        if mysql.normalized(str(target))==mysql.normalized(str(path)):
            raise engine.Refused('Arquivo referenciado pelo serviço; preservado.')
    info=engine.check_item(path)
    if not record.identity or not record.modified_ns:
        raise engine.Refused('Identidade não registrada; analise novamente.')
    if not stat.S_ISREG(info.st_mode) or (info.st_size,info.st_mtime_ns,info.st_ino,info.st_dev)!=(record.size,record.modified_ns,record.identity,record.device):
        raise engine.Refused('Arquivo mudou desde a análise; preservado.')
    # init-file pode executar um SQL no próximo início do servidor.
    if installation.config and installation.config.is_file():
        values=mysql.local_config(installation.config)
        for name in ('init-file','init_file'):
            if values.get(name):
                target=type(path)(values[name])
                if not target.is_absolute():target=installation.root/target
                if mysql.normalized(str(target))==mysql.normalized(str(path)):
                    raise engine.Refused('SQL configurado para inicialização do servidor; preservado.')
    return path


def reason(installation,record):
    try:validate(installation,record)
    except (OSError,ValueError,engine.Refused) as exc:return str(exc)
    return ''


def delete(installation,record):
    path=validate(installation,record)
    return delete_snapshot(path,record.size,record.modified_ns,record.identity,record.device)
