"""Directory references without requiring Windows Developer Mode or elevation."""
import os
from pathlib import Path


def link_directory(link, target):
    link, target = Path(link).absolute(), Path(target).resolve()
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    # NTFS mount-point reparse data: no shell command, so spaces and Chinese
    # paths work and path characters cannot become cmd.exe instructions.
    import ctypes
    from ctypes import wintypes
    import struct
    if str(target).startswith("\\\\"):
        raise ValueError("Windows 训练工作目录需位于本地磁盘")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.DeviceIoControl.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    printable = str(target).encode("utf-16-le")
    paths = substitute + b"\0\0" + printable + b"\0\0"
    payload = struct.pack("<IHHHHHH", 0xa0000003, len(paths) + 8, 0, 0, len(substitute), len(substitute) + 2, len(printable)) + paths
    link.mkdir()
    handle = api.CreateFileW(str(link), 0x40000000, 0, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        link.rmdir()
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(payload)
        if not api.DeviceIoControl(handle, 0x900a4, buffer, len(payload), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        api.CloseHandle(handle)
        link.rmdir()
        raise
    else:
        api.CloseHandle(handle)


def unlink_directory(link, expected_target):
    link = Path(link)
    is_reference = link.is_symlink() or (hasattr(link, "is_junction") and link.is_junction())
    if is_reference and link.resolve() == Path(expected_target).resolve():
        if os.name == "nt" and link.is_junction():
            link.rmdir()
        else:
            link.unlink()
