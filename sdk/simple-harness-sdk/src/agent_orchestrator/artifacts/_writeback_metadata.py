# SPDX-License-Identifier: Apache-2.0
"""核对替换会改变的文件元数据，返回逐文件拒写的原因。"""

import os
import sys

if sys.platform == "darwin":
    import ctypes
    import errno

    # Darwin 的 sys/stat.h 用低 16 位表示所有者可设置的文件标志。
    _UF_SETTABLE = 0x0000FFFF
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.acl_get_fd.argtypes = [ctypes.c_int]
    _libc.acl_get_fd.restype = ctypes.c_void_p
    _libc.acl_free.argtypes = [ctypes.c_void_p]
    _libc.acl_free.restype = ctypes.c_int

    def _has_acl(fd: int) -> bool:
        acl = _libc.acl_get_fd(fd)
        if acl:
            _libc.acl_free(acl)
            return True
        # 对已打开的文件，Darwin 以 ENOENT 表示没有附加 ACL。
        code = ctypes.get_errno()
        if code == errno.ENOENT:
            return False
        raise OSError(code, os.strerror(code))


def replacement_problem(
    source_fd: int, staged_fd: int, source: os.stat_result, staged: os.stat_result
) -> str | None:
    if source.st_uid != staged.st_uid:
        return "metadata_unpreserved: uid"
    if source.st_gid != staged.st_gid:
        return "metadata_unpreserved: gid"
    if sys.platform == "darwin":
        if (source.st_flags ^ staged.st_flags) & _UF_SETTABLE:
            return "metadata_unpreserved: flags"
        try:
            # 暂存文件也可能从父目录继承一份原件没有的 ACL。
            if _has_acl(source_fd) or _has_acl(staged_fd):
                return "metadata_unpreserved: acl"
        except OSError:
            return "metadata_unpreserved: acl"
    return None
