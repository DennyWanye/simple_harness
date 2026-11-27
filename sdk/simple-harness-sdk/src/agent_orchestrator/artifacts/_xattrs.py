# SPDX-License-Identifier: Apache-2.0
"""按文件描述符读写扩展属性，暂存期间改名或出现软链都不会重定向属性操作。"""

import errno
import os
import sys

AVAILABLE = hasattr(os, "listxattr") or sys.platform == "darwin"

if hasattr(os, "listxattr"):
    from os import getxattr, listxattr, setxattr
elif sys.platform == "darwin":
    import ctypes as c

    # Darwin 的 fd 接口与路径接口共用值格式；描述符已经打开，无需解析路径。
    _libc = c.CDLL(None, use_errno=True)
    _libc.flistxattr.argtypes = [c.c_int, c.c_void_p, c.c_size_t, c.c_int]
    _libc.flistxattr.restype = c.c_ssize_t
    _libc.fgetxattr.argtypes = [c.c_int, c.c_char_p, c.c_void_p, c.c_size_t, c.c_uint32, c.c_int]
    _libc.fgetxattr.restype = c.c_ssize_t
    _libc.fsetxattr.argtypes = _libc.fgetxattr.argtypes
    _libc.fsetxattr.restype = c.c_int

    def _checked(result: int) -> int:
        if result < 0:
            code = c.get_errno()
            raise OSError(code, os.strerror(code))
        return result

    def _read(function, args, options) -> bytes:
        size = _checked(function(*args, None, 0, *options))
        # 空属性也要读第二次；至少给一个字节，以免被内核当作查询长度。
        buffer = c.create_string_buffer(max(size, 1))
        used = _checked(function(*args, buffer, len(buffer), *options))
        if used > len(buffer):
            raise OSError(errno.ERANGE, "读取期间扩展属性变长")
        return buffer[:used]

    def listxattr(fd: int) -> list[str]:
        names = _read(_libc.flistxattr, (fd,), (0,))
        return [os.fsdecode(name) for name in names.split(b"\0") if name]

    def getxattr(fd: int, name: str) -> bytes:
        return _read(_libc.fgetxattr, (fd, os.fsencode(name)), (0, 0))

    def setxattr(fd: int, name: str, value: bytes) -> None:
        _checked(_libc.fsetxattr(fd, os.fsencode(name), value, len(value), 0, 0))


def snapshot(fd: int) -> dict[str, bytes]:
    try:
        names = listxattr(fd)
    except OSError as error:
        # 仅列举时的 ENOTSUP 表示卷没有属性接口；已列出的属性若读不回必须拒写。
        if error.errno == errno.ENOTSUP:
            return {}
        raise
    return {name: getxattr(fd, name) for name in names}


def copy_to_stage(source: int, staged: int) -> dict[str, bytes]:
    original = snapshot(source)
    inherited = snapshot(staged)
    for name, value in original.items():
        # 系统可能为两个文件赋予相同的只读属性，此时无须重写。
        if inherited.get(name) != value:
            setxattr(staged, name, value)
    return original
