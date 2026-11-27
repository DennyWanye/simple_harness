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

    _libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    _libc.acl_get_flagset_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _libc.acl_get_flag_np.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    def _checked(result: int) -> int:
        if result < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return result

    def _inheritable(acl: int) -> bool:
        # Darwin 的 sys/acl.h 定义首条为 0、下一条为 -1，两种继承标志为第 5、6 位。
        entry = ctypes.c_void_p()
        flags = ctypes.c_void_p()
        which = 0
        while True:
            result = _libc.acl_get_entry(acl, which, ctypes.byref(entry))
            if which == -1 and result < 0 and ctypes.get_errno() == errno.EINVAL:
                return False  # Darwin 在最后一条之后返回 EINVAL。
            _checked(result)
            _checked(_libc.acl_get_flagset_np(entry, ctypes.byref(flags)))
            if any(_checked(_libc.acl_get_flag_np(flags, flag)) for flag in (1 << 5, 1 << 6)):
                return True
            which = -1

    def _has_acl(fd: int, *, inheritable_only: bool = False) -> bool:
        acl = _libc.acl_get_fd(fd)
        if acl:
            try:
                return _inheritable(acl) if inheritable_only else True
            finally:
                _libc.acl_free(acl)
        # 对已打开的文件，Darwin 以 ENOENT 表示没有附加 ACL。
        code = ctypes.get_errno()
        if code == errno.ENOENT:
            return False
        raise OSError(code, os.strerror(code))


def acl_problem(fd: int, *, inheritable_only: bool = False) -> str | None:
    if sys.platform == "darwin":
        try:
            if _has_acl(fd, inheritable_only=inheritable_only):
                return "metadata_unpreserved: acl"
        except OSError:
            return "metadata_unpreserved: acl"
    return None


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
        return acl_problem(source_fd) or acl_problem(staged_fd)
    return None


def new_stage_problem(fd: int, staged: os.stat_result) -> str | None:
    if sys.platform == "darwin" and staged.st_flags & _UF_SETTABLE:
        return "metadata_unpreserved: flags"
    return acl_problem(fd)
