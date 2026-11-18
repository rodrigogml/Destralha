"""Manutenção do Windows somente por ferramentas nativas com ações fixas."""
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys

import engine
from .inventory import Installation,measure


def windows_directory():
    buffer=ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetWindowsDirectoryW(buffer,len(buffer)):
        raise engine.Refused('Não foi possível localizar o Windows.')
    return Path(buffer.value)


def installation():
    return Installation('windows','windows','Windows',windows_directory(),state='Sistema em execução',
        warnings=('Tamanhos lógicos dos arquivos acessíveis, com hardlinks deduplicados; não equivalem ao espaço recuperável.',))


def classify(path,root):
    name=path.name.casefold()
    if name=='hiberfil.sys': return 'Hibernação — funcionalidade'
    if name in ('pagefile.sys','swapfile.sys'): return 'Memória virtual — preservar'
    try: parts=tuple(part.casefold() for part in path.relative_to(root).parts)
    except ValueError:return 'Instaladores compartilhados — preservar'
    if parts[:1]==('winsxs',):return 'Componentes WinSxS — manutenção nativa'
    if parts[:1]==('installer',):return 'Windows Installer — preservar'
    if parts[:2]==('system32','driverstore'):return 'Drivers instalados — preservar'
    if parts[:2]==('softwaredistribution','download'):return 'Downloads de atualizações'
    if parts[:1]==('temp',):return 'Temporários do sistema — revisar'
    if parts[:1] in (('logs',),('minidump',),('livekernelreports',)) or name=='memory.dmp' or parts[:3]==('system32','winevt','logs'):
        return 'Logs e diagnósticos'
    return 'Demais arquivos do Windows — preservar'


def inventory(item,cancel=None,notify=lambda *_:None):
    actual=windows_directory()
    if item.kind!='windows' or item.root!=actual:
        raise engine.Refused('Escopo do Windows não corresponde ao sistema atual.')
    roots=[actual]
    for name in ('hiberfil.sys','pagefile.sys','swapfile.sys'):
        path=Path(actual.anchor)/name
        if path.exists():roots.append(path)
    package=Path(os.environ.get('ProgramData',str(Path(actual.anchor)/'ProgramData')))/'Package Cache'
    if package.exists():roots.append(package)
    return measure(item,roots,lambda path:classify(path,actual),cancel,notify)


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def command(action):
    root=windows_directory()/'System32'
    if action=='analyze_components':
        return [str(root/'dism.exe'),'/Online','/Cleanup-Image','/AnalyzeComponentStore','/English']
    if action=='clean_components':
        return [str(root/'dism.exe'),'/Online','/Cleanup-Image','/StartComponentCleanup','/English']
    if action=='disable_hibernation':
        return [str(root/'powercfg.exe'),'/hibernate','off']
    raise engine.Refused('Ação do Windows não permitida.')


def run(action,notify=lambda *_:None,cancel=None):
    """Executa argumentos constantes. Não força cancelamento nem reinicialização.

    Saída fica em memória. O Windows continua responsável por seus próprios
    registros nativos. Não há /ResetBase nem remoção manual de componentes.
    """
    args=command(action)
    if not is_admin(): raise engine.Refused('Esta operação requer executar Destralha como administrador.')
    engine.check_item(Path(args[0]))
    if cancel is not None and cancel.is_set():raise engine.Refused('Operação cancelada antes de iniciar o Windows.')
    output=[]; partial=[]; output_bytes=0
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        while True:
            char=process.stdout.read(1)
            if not char:break
            if char in '\r\n':
                line=''.join(partial).strip();partial=[]
                if line:
                    if output_bytes<1024*1024:output.append(line);output_bytes+=len(line)
                    match=re.search(r'(\d+(?:\.\d+)?)%',line)
                    notify(line,float(match.group(1)) if match else None)
            else:partial.append(char)
        if partial:output.append(''.join(partial).strip())
        code=process.wait()
    finally:
        process.stdout.close()
    if code not in (0,3010):
        raise engine.Refused(f'Windows retornou código {code}.\n'+'\n'.join(output[-12:]))
    return dict(action=action,exit_code=code,restart_required=code==3010,output='\n'.join(output))


def open_tool(tool):
    if tool=='storage':os.startfile('ms-settings:storagesense')
    elif tool=='cleanup':subprocess.Popen([str(windows_directory()/'System32/cleanmgr.exe')])
    else:raise engine.Refused('Ferramenta não permitida.')


def open_elevated():
    script=Path(__file__).resolve().parents[1]/'Abrir Destralha.pyw'
    executable=Path(sys.executable).with_name('pythonw.exe')
    if not executable.is_file():raise engine.Refused('Inicializador Python do Windows não encontrado.')
    function=ctypes.windll.shell32.ShellExecuteW
    function.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_int]
    function.restype=ctypes.c_void_p
    result=function(None,'runas',str(executable),f'"{script}" --programas',str(script.parent),1)
    if not result or result<=32:raise engine.Refused('A elevação foi cancelada ou recusada pelo Windows.')
