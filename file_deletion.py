"""Exclusão de arquivo único, vinculada a identidade e handle exclusivo."""
import ctypes
from ctypes import wintypes
import msvcrt
import os
import stat

from engine import Refused


def delete_snapshot(path, size, modified_ns, identity, device=None):
    """Recusa uso concorrente e substituição; não remove pastas nem altera ACLs."""
    # DELETE + leitura de atributos, share=0: nenhuma gravação/uso concorrente.
    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x10000 | 0x80, 0, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise Refused('Arquivo em uso ou sem permissão; preservado.')
    close = ctypes.windll.kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    descriptor = None
    try:
        # Revalida identidade após adquirir o handle. DELETE fica vinculado ao
        # objeto aberto, evitando excluir outro arquivo inserido no mesmo nome.
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or getattr(current,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise Refused('Tipo de arquivo mudou; preservado.')
        if (current.st_size, current.st_mtime_ns, current.st_ino) != (size, modified_ns, identity):
            raise Refused('Arquivo mudou; preservado.')
        if device is not None and current.st_dev != device:
            raise Refused('Volume do arquivo mudou; preservado.')
        disposition = ctypes.c_ubyte(1)
        function = ctypes.windll.kernel32.SetFileInformationByHandle
        function.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        function.restype = wintypes.BOOL
        if not function(handle, 4, ctypes.byref(disposition), 1):
            raise Refused('Windows recusou excluir o arquivo; preservado.')
    finally:
        if descriptor is None:
            close(handle)
        else:
            os.close(descriptor)
    return size
