"""Windows-protected credential files; broker secrets never belong in PostgreSQL."""
import ctypes
import os
import tempfile
from pathlib import Path
from ctypes import wintypes

SECRET_DIRECTORY = Path(__file__).resolve().parent.parent / '.local-secrets'


def credential_path(user_id: int) -> Path:
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError('유효하지 않은 사용자입니다.')
    return SECRET_DIRECTORY / f'broker-{user_id}.bin'


def read_encrypted(user_id: int) -> bytes | None:
    try:
        return credential_path(user_id).read_bytes()
    except FileNotFoundError:
        return None


def write_encrypted(user_id: int, payload: bytes | None) -> None:
    path = credential_path(user_id)
    if payload is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(payload: bytes, decrypt: bool) -> bytes:
    if os.name != 'nt':
        raise ValueError('이 설치의 API 연결 저장은 Windows에서 지원합니다.')
    buffer = ctypes.create_string_buffer(payload)
    incoming = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('API 연결 정보를 보호하거나 읽을 수 없습니다. 연결 정보를 다시 등록하세요.')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def protect(payload: bytes) -> bytes:
    return _crypt(payload, False)


def unprotect(payload: bytes) -> bytes:
    return _crypt(payload, True)
