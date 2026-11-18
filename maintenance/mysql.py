"""Descoberta local do MySQL e expurgo exclusivamente pelo servidor."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import subprocess

import engine
from .inventory import Installation, measure

CONFIG_KEYS = {'basedir','datadir','port','log-bin','log_bin','log_error','general_log_file','slow_query_log_file'}
BINLOG = re.compile(r'.*\.(?:[0-9]{6,})$',re.I)


def service_inventory():
    import json
    command = "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Service | Where-Object {$_.Name -match 'mysql|maria' -or $_.PathName -match 'mysqld|mariadbd'} | Select-Object Name,State,PathName) | ConvertTo-Json -Compress"
    shell = Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    completed = subprocess.run([str(shell),'-NoProfile','-NonInteractive','-Command',command],
                                capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode: raise engine.Refused('Consulta de serviços indisponível.')
    data=json.loads(completed.stdout.decode('utf-8-sig')) or []
    return [data] if isinstance(data,dict) else data


def executable_from_service(command):
    match = re.match(r'^\s*(?:"([^"]*(?:mysqld|mariadbd)(?:\.exe)?)"|(.+?(?:mysqld|mariadbd)(?:\.exe)?)(?=\s|$))',command,re.I)
    if not match: return None
    path = Path(match.group(1) or match.group(2))
    if not path.suffix: path = path.with_suffix('.exe')
    return path if path.is_absolute() and path.name.casefold() in ('mysqld.exe','mariadbd.exe') else None


def local_config(path):
    """Lê só parâmetros de localização; nunca retorna senhas ou contas."""
    result = {}; section = ''
    engine.check_item(path)
    with path.open(encoding='utf-8-sig',errors='replace') as stream:
        for line in stream:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1].casefold(); continue
            if section not in ('mysqld','server','mariadb') or not line or line.startswith(('#',';')):
                continue
            if '=' in line:
                key,value = line.split('=',1)
                if key.strip().casefold() in CONFIG_KEYS:
                    result[key.strip().casefold()] = value.strip().strip('"\'')
    return result


def installation_from_service(service):
    command = service.get('PathName','')
    executable = executable_from_service(command)
    if executable is None: return None
    root = executable.parent.parent
    engine.check_item(root); engine.safe_path(root,executable)
    explicit = re.search(r'--defaults-file(?:=|\s+)(?:"([^"]+)"|(\S+))',command,re.I)
    config = Path(explicit.group(1) or explicit.group(2)) if explicit else root/'my.ini'
    values = local_config(config) if config.is_file() else {}
    data = Path(values.get('datadir',str(root/'data')))
    if not data.is_absolute(): data = root/data
    # Backslashes duplicadas são comuns em arquivos de opções do MySQL.
    data = Path(os.path.normpath(str(data)))
    if not data.drive or str(data).startswith('\\\\'):
        raise engine.Refused('Diretório de dados não é um caminho local absoluto.')
    port = int(values.get('port','3306'))
    if not 1<=port<=65535: raise engine.Refused('Porta inválida na configuração local.')
    warnings = ['Localização inferida da configuração local; a conexão SQL confirma a instância e o datadir efetivos.']
    if not explicit:
        warnings.append('Serviço sem --defaults-file: outras opções do servidor podem alterar os caminhos.')
    return Installation('mysql:'+service.get('Name',str(root)),'mysql','MySQL · '+service.get('Name',root.name),
                        root,data,service.get('Name',''),service.get('State','Desconhecido'),
                        config if config.is_file() else None,port,tuple(warnings))


def discover(services=None, locations=None):
    result, warnings = [],[]
    if services is None:
        try: services = service_inventory()
        except Exception as exc: services=[]; warnings.append(str(exc))
    seen = set()
    for service in services:
        try:
            item = installation_from_service(service)
            if item:
                result.append(item); seen.add(str(item.root).casefold())
        except (OSError,ValueError,engine.Refused) as exc:
            warnings.append(f"{service.get('Name','MySQL')}: {exc}")
    if locations is None:
        drive = Path(os.environ.get('SystemDrive','C:')+'\\')
        locations = list(drive.glob('mysql*'))
        program_files = Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'MySQL'
        if program_files.is_dir(): locations += list(program_files.iterdir())
    for root in locations:
        if str(root).casefold() in seen: continue
        try:
            executable = root/'bin/mysqld.exe'
            if not executable.is_file(): continue
            item = installation_from_service(dict(Name=root.name,State='Sem serviço identificado',PathName=f'"{executable}"'))
            if item: result.append(item); seen.add(str(root).casefold())
        except (OSError,ValueError,engine.Refused) as exc: warnings.append(f'{root}: {exc}')
    return result,warnings


def classify(path,installation):
    name,suffix = path.name.casefold(),path.suffix.casefold()
    parts = {part.casefold() for part in path.parts}
    if BINLOG.fullmatch(name) and ('relay' in name): return 'Logs de réplica'
    if BINLOG.fullmatch(name) and ('binlog' in name or '-bin.' in name): return 'Logs binários'
    if suffix=='.ibd' or suffix in ('.myd','.myi'): return 'Tabelas e índices'
    if name.startswith(('ibdata','ib_logfile','ibtmp','undo_','#ib_')) or '#innodb_redo' in parts or '#innodb_temp' in parts:
        return 'InnoDB e recuperação'
    if suffix in ('.err','.log') or re.match(r'.*\.log\.\d+$',name): return 'Logs de diagnóstico'
    if (suffix in ('.sql','.bak','.zst','.gz') and 'share' not in parts): return 'Exportações / backups a revisar'
    if suffix in ('.exe','.dll','.lib','.so','.pdb'): return 'Executáveis e bibliotecas'
    return 'Outros arquivos / configuração'


def inventory(installation,cancel=None,notify=lambda *_:None):
    roots = [installation.root]
    if installation.data is not None and installation.data!=installation.root and installation.root not in installation.data.parents:
        roots.append(installation.data)
    return measure(installation,roots,lambda path:classify(path,installation),cancel,notify)


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str = field(repr=False)
    port: int = 3306


@dataclass(frozen=True)
class ServerSnapshot:
    version: str
    data_dir: str
    server_id: str
    binary_logs: tuple[tuple[str,int],...]
    current_log: str
    variables: dict
    replicas: tuple[dict,...]
    replica_channels: tuple[dict,...]
    tables: tuple[dict,...]
    warnings: tuple[str,...]
    replication_checked: bool


@dataclass(frozen=True)
class PurgePlan:
    boundary: str
    files: tuple[tuple[str,int],...]
    server_id: str
    data_dir: str

    @property
    def size(self): return sum(size for _,size in self.files)


def connect(credentials):
    if not credentials.user.strip() or not 1<=credentials.port<=65535:
        raise engine.Refused('Informe usuário e porta válidos.')
    try: import mysql.connector
    except ImportError as exc: raise engine.Refused('Instale a dependência mysql-connector-python do requirements.txt para conectar.') from exc
    return mysql.connector.connect(host='127.0.0.1',port=credentials.port,user=credentials.user,
        password=credentials.password,connection_timeout=5,autocommit=True,allow_local_infile=False)


def normalized(path):
    return os.path.normcase(os.path.normpath(path.replace('/','\\'))).rstrip('\\')


def inspect(connection,installation):
    """Consulta metadados; não acessa registros das tabelas nem altera o servidor."""
    cursor = connection.cursor(dictionary=True)
    warnings = []
    try:
        def query(sql,params=None):
            cursor.execute(sql,params); return cursor.fetchall()
        identity = query('SELECT @@version AS version, @@datadir AS datadir, @@server_uuid AS server_id')[0]
        if not identity.get('server_id'):
            raise engine.Refused('Identidade do servidor não confirmada; manutenção bloqueada.')
        if installation.data is None or normalized(identity['datadir'])!=normalized(str(installation.data)):
            raise engine.Refused('A porta respondeu por outro datadir. Nenhuma manutenção foi autorizada; reveja a instância e a porta.')
        try:rows = query('SHOW BINARY LOGS')
        except Exception:
            rows=[];warnings.append('Binlogs desativados ou indisponíveis para esta conta; expurgo bloqueado.')
        binary_logs = tuple((str(row['Log_name']),int(row['File_size'])) for row in rows)
        variables = {str(row['Variable_name']):str(row['Value']) for row in query(
            "SHOW VARIABLES WHERE Variable_name IN ('log_bin','binlog_expire_logs_seconds','expire_logs_days','binlog_format','max_binlog_size')")}
        parts = [int(value) for value in re.findall(r'\d+',identity['version'])[:2]]+[0,0]
        modern = parts[0]>=9 or parts[0]==8 and parts[1]>=4
        current_log=''; replicas=[]; channels=[]; checked=True
        for label,sql in [('current','SHOW BINARY LOG STATUS' if modern else 'SHOW MASTER STATUS'),
                          ('replicas','SHOW REPLICAS' if modern else 'SHOW SLAVE HOSTS'),
                          ('channels','SHOW REPLICA STATUS' if modern else 'SHOW SLAVE STATUS')]:
            try:
                answer=query(sql)
                if label=='current': current_log=str(answer[0]['File']) if answer else ''
                elif label=='replicas': replicas=answer
                else: channels=answer
            except Exception:
                checked=False; warnings.append('Status de '+label+' indisponível; expurgo bloqueado.')
        try:
            tables=query("SELECT /*+ MAX_EXECUTION_TIME(5000) */ TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, DATA_FREE FROM information_schema.tables WHERE TABLE_SCHEMA NOT IN ('mysql','information_schema','performance_schema','sys') ORDER BY DATA_LENGTH+INDEX_LENGTH DESC LIMIT 50")
        except Exception:
            tables=[]; warnings.append('Tamanhos internos das tabelas indisponíveis para esta conta.')
        return ServerSnapshot(str(identity['version']),str(identity['datadir']),str(identity['server_id']),binary_logs,
                              current_log,variables,tuple(replicas),tuple(channels),tuple(tables),tuple(warnings),checked)
    finally: cursor.close()


def plan_purge(snapshot,boundary):
    if not snapshot.replication_checked or not snapshot.current_log:
        raise engine.Refused('Verificação de replicação ou arquivo ativo incompleta.')
    if snapshot.replicas or snapshot.replica_channels:
        raise engine.Refused('Há replicação identificada. Esta versão exige manutenção pelo administrador da replicação.')
    if not re.fullmatch(r'[\w.-]+\.\d+',boundary,re.ASCII):
        raise engine.Refused('Nome de binlog não permitido.')
    names=[name for name,_ in snapshot.binary_logs]
    if boundary not in names or snapshot.current_log not in names:
        raise engine.Refused('Arquivo de corte ou arquivo ativo não está na lista atual.')
    index=names.index(boundary)
    if index==0: raise engine.Refused('Não há arquivos anteriores a esse corte.')
    files=snapshot.binary_logs[:index]
    if snapshot.current_log in {name for name,_ in files}:
        raise engine.Refused('A seleção inclui o arquivo ativo; expurgo bloqueado.')
    return PurgePlan(boundary,files,snapshot.server_id,snapshot.data_dir)


def purge(connection,installation,plan,*,backups_reviewed=False,replication_reviewed=False,cancel=None):
    """Única operação SQL mutável: PURGE até um limite preservado e confirmado.

    Nunca remove arquivos, tabelas ou dados por sistema de arquivos. Revalida
    instância, logs e replicação; réplicas desconectadas dependem de confirmação
    explícita do responsável, porque SHOW não comprova sua inexistência.
    """
    if not backups_reviewed or not replication_reviewed:
        raise engine.Refused('Confirme a revisão de backups e de réplicas, inclusive desconectadas.')
    if cancel is not None and cancel.is_set():raise engine.Refused('Expurgo cancelado antes da execução.')
    fresh = inspect(connection,installation)
    current = plan_purge(fresh,plan.boundary)
    if (current.server_id,current.data_dir,current.files)!=(plan.server_id,plan.data_dir,plan.files):
        raise engine.Refused('A instância ou os binlogs mudaram. Consulte e revise novamente.')
    cursor=connection.cursor(dictionary=True)
    try:
        if cancel is not None and cancel.is_set():raise engine.Refused('Expurgo cancelado antes da execução.')
        cursor.execute('PURGE BINARY LOGS TO %s',(plan.boundary,))
        cursor.execute('SHOW BINARY LOGS')
        remaining={row['Log_name'] for row in cursor.fetchall()}
        removed=[(name,size) for name,size in plan.files if name not in remaining]
        return dict(files=len(removed),bytes=sum(size for _,size in removed),preserved=len(plan.files)-len(removed))
    finally:cursor.close()
