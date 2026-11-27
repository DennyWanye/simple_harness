# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""任务的「底」：宿主交来的文件树，建任务时拍成 SDK 工作仓库里的一个 git 提交。

设计见 ``plans/2026-11-26-workspace-base/00-设计-任务工作区以底为基础.md``。

* :class:`WorkspaceBase` 是宿主实现的端口：按范围列出底里的文件（字节按需读）。SDK 自带
  :class:`GitRepoBase`（用户的 git 仓库：受版本管理 + 未忽略的新文件，含没提交的改动）、
  :class:`DirectoryBase`（普通目录）、:class:`InMemoryBase`（测试替身）。
* :class:`BaseRepo` 是 SDK 自管的裸仓库（``<数据根>/bases/work.git``）。只用底层命令
  （``fast-import`` / ``ls-tree`` / ``cat-file``），并隔离用户与系统的 git 配置：换行转换、
  属性过滤一概不发生，铺回目录的字节与拍快照时逐字节相同。
* :func:`capture_base` 守 SDK 这一道：范围、上限、扣留（疑似密钥、软链、大小写冲突、保留路径）、
  二进制 / 超大文件标成不可读。同样的内容拍出同一个提交号（作者、时间、说明都固定）。

提交号是底的身份：任务行记它，尝试工作区、验收副本、判定树都从它铺底；提交与对象由引用
``refs/harness/base/<提交号>`` 保住，工作区目录清掉以后照样能重建。任务交付时在底提交上叠一个
交付提交（``refs/harness/delivery/<提交号>``），改动与 diff 都在这两个提交之间算。

能写回的底另外实现 :class:`WritableBase`：按 git blob 号报现状，逐文件「现状仍是预期才写」。
SDK 自带的三个适配器都实现了；写回只改工作目录里的文件，不碰 ``.git``。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess  # noqa: S404 - 固定的 git 底层命令，参数不来自模型
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable

from ..observability.secrets import find_secrets
from . import _writeback_metadata, _xattrs
from .paths import ambiguous_paths, spelling_key, under_prefix
from .store import ArtifactStoreError, open_nofollow, read_nofollow

GIT_MIN_VERSION = (2, 38)
#: 工作区读写单文件的上限（与 :data:`workspace.MAX_FILE_BYTES` 同值）；更大的底文件只读不可改。
READABLE_MAX_BYTES = 512 * 1024
#: 遍历时跳过的目录名（与工作区相同）。
IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git"})
#: 按名字直接扣留的文件：凭据、私钥、证书一类，即使被 git 跟踪了也不交给模型。
SECRET_NAME_PATTERNS = (
    re.compile(r"^\.env(rc|\..*)?$"),
    re.compile(r".*\.(pem|key|p12|pfx|keystore|jks|mobileprovision)$"),
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"),
    re.compile(r"^\.(netrc|npmrc|pypirc)$"),
)
_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
#: SDK 自己在工作区里用的根：资料（``.harness/sources/``）、执行者草稿（``.harness/scratch/``）、
#: 判定树里落选的产出（``.harness/accepted-outputs/``），以及保证通道材料（``.assurance/``）。
#: 底里撞上的扣留；改动集里这些路径不交付。
HARNESS_ROOT = ".harness/"
RESERVED_BASE_PREFIXES = (HARNESS_ROOT, ".assurance/")
_COMMITTER = "SimpleHarness <harness@localhost> 0 +0000"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class BaseError(ValueError):
    """底拍不了或读不了；``reason`` 是给调用方的机器码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class BaseUnavailable(BaseError):
    def __init__(self, message: str) -> None:
        super().__init__("base_unavailable", message)


class BaseTooLarge(BaseError):
    """范围内的文件超过上限；带实际的文件数和字节数，让调用方缩小范围。"""

    def __init__(self, files: int, size: int, limits: CaptureLimits) -> None:
        super().__init__(
            "base_too_large",
            f"底有 {files} 个文件、{size} 字节，超过上限 {limits.max_files} 个 / "
            f"{limits.max_bytes} 字节；缩小范围（base_scope）后重交",
        )
        self.files = files
        self.bytes = size
        self.limits = limits


@dataclass(frozen=True, slots=True)
class CaptureLimits:
    max_files: int = 5000
    max_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BaseFile:
    """底里的一个文件：大小先给，字节按需读（大的底不必先全读进来才知道超限）。"""

    path: str
    size: int
    read: Callable[[], bytes]
    executable: bool = False


@dataclass(frozen=True, slots=True)
class BaseCapture:
    files: Sequence[BaseFile]
    #: 适配器自己扣留的（例如软链）：路径 → 原因
    withheld: Mapping[str, str] = field(default_factory=dict)
    #: 来历，记进任务（例如 ``{"kind": "git", "head": 提交号}``）
    source: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class WorkspaceBase(Protocol):
    """宿主交给 SDK 的底。``scope`` 是路径前缀（空 = 整个底）；适配器可以先按它少列，
    SDK 收到后还会再按它筛一遍。"""

    def capture(self, scope: Sequence[str]) -> BaseCapture: ...


@dataclass(frozen=True, slots=True)
class FileWrite:
    """写回一个文件的结果。``status``：``applied`` 写了、``already`` 现状已是新内容、``conflict``
    现状既不是底也不是新内容（没写）、``refused`` 这个路径不写（``reason`` 说为什么）、``failed``
    写的时候出错。``current`` 是写之前现状的 blob 号（没有这个文件为 None）。"""

    status: str
    current: str | None
    reason: str | None = None
    cleanup_error: str | None = None


@runtime_checkable
class WritableBase(Protocol):
    """能把交付写回去的底。blob 号是 git 的（见 :func:`git_blob_id`），没有这个文件为 None。

    ``write_file`` 自己做比对：现状 == 新内容 → ``already``；现状 == ``expected`` → 写；
    其他 → ``conflict``，不写。磁盘路径经过软链或有大小写、Unicode 规范化别名时，一律 ``refused``。

    ``location()`` 是这个底的身份（例如目录的真实路径）；适配器拍底时要把同一个值记进
    ``BaseCapture.source["location"]``，写回时 SDK 核对两者一致，不把一个项目的交付写进另一个。"""

    def location(self) -> str: ...

    def current_hashes(self, paths: Sequence[str]) -> Mapping[str, str | None]: ...

    def write_file(
        self, path: str, data: bytes, *, expected: str | None, executable: bool
    ) -> FileWrite: ...


def git_blob_id(data: bytes) -> str:
    """git 给这段字节的 blob 号（SHA-1 对象格式）。写回的三方比对只认这个号。"""

    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _version(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size


def _revision(path: Path) -> tuple[int, ...] | None:
    try:
        return _version(path.lstat())
    except (FileNotFoundError, NotADirectoryError):
        return None


def _entry_problem(part: str, names: list[str], require_exact: bool) -> str | None:
    key = spelling_key(part)
    for name in names:
        if name != part and spelling_key(name) == key:
            return "case_conflict" if name.casefold() == part.casefold() else "spelling_alias"
    if require_exact and part not in names:
        return "missing"
    return None


def _spelling_problem(
    root: Path,
    relative: str,
    scanned: dict[Path, tuple[int, ...] | None],
    *,
    require_exact: bool = False,
) -> str | None:
    """逐级核对目录项。只用碰撞键发现别名，文件名保留磁盘上的原拼写。"""

    current = root
    for part in PurePosixPath(relative).parts:
        try:
            scanned[current] = _revision(current)
            names = os.listdir(current)
        except FileNotFoundError:
            return "missing" if require_exact else None
        except NotADirectoryError:
            return "not_a_directory"
        problem = _entry_problem(part, names, require_exact)
        if problem is not None:
            return problem
        current = current / part
    scanned[current] = _revision(current)
    return None


@dataclass(frozen=True, slots=True)
class BaseEntry:
    """工作仓库里底提交的一个文件。"""

    path: str
    blob: str
    sha256: str
    size: int
    executable: bool
    readable: bool

    def to_json(self) -> dict[str, object]:
        return {
            "blob": self.blob,
            "sha256": self.sha256,
            "size": self.size,
            "executable": self.executable,
            "readable": self.readable,
        }


@dataclass(frozen=True, slots=True)
class BaseRecord:
    """一次拍快照的结果，记进任务行（``final_report["workspace_base"]``）。"""

    commit: str
    files: int
    bytes: int
    scope: tuple[str, ...]
    withheld: Mapping[str, str]
    source: Mapping[str, str]

    def to_json(self) -> dict[str, object]:
        return {
            "commit": self.commit,
            "files": self.files,
            "bytes": self.bytes,
            "scope": list(self.scope),
            "withheld": dict(sorted(self.withheld.items())),
            "source": dict(self.source),
        }


# ---------------------------------------------------------------- 适配器


def _in_scope(path: str, scope: Sequence[str]) -> bool:
    if not scope:
        return True
    return any(path == item.rstrip("/") or under_prefix(path, (item,)) for item in scope)


def _through_link(root: Path, relative: str) -> bool:
    """路径上任何一级（含文件本身）是软链：读它就可能读到底以外的东西。"""

    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _file(root: Path, relative: str) -> BaseFile:
    target = root / relative
    stat = target.stat()

    def read() -> bytes:
        # 列出来以后到读之前目录可能被换成软链：读前再查一次，文件本身也不跟随
        if _through_link(root, relative):
            raise BaseError("base_changed", f"{relative} 在拍底时变成了软链")
        try:
            return read_nofollow(target)
        except (OSError, ArtifactStoreError) as error:
            raise BaseError("base_changed", f"{relative} 在拍底时读不了：{error}") from error

    return BaseFile(relative, stat.st_size, read, executable=bool(stat.st_mode & 0o111))


@contextmanager
def _staging_parent(parent: Path) -> Iterator[tuple[Path, int | None]]:
    """使临时文件的创建和清理始终指向同一父目录。"""
    with ExitStack() as close:
        if os.name == "nt":
            import _winapi

            # Windows 没有 dir_fd。逐级打开真实路径并禁止共享删除，持有期间不能移走上级。
            share_read_write = 0x1 | 0x2
            file_flag_backup_semantics = 0x02000000
            stable = parent.resolve(strict=True)
            for folder in (*reversed(stable.parents), stable):
                if folder == folder.parent:
                    continue
                handle = _winapi.CreateFile(
                    str(folder),
                    0,
                    share_read_write,
                    0,
                    _winapi.OPEN_EXISTING,
                    file_flag_backup_semantics,
                    0,
                )
                close.callback(_winapi.CloseHandle, handle)
            yield stable, None
        else:
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            close.callback(os.close, fd)
            yield Path(), fd


def _open_stage(name: Path, parent_fd: int | None, mode: int) -> BinaryIO:
    if parent_fd is None:
        import _winapi
        import msvcrt

        # Windows 须共享删除权限才可持有写句柄改名。这样即使改名失败且文件已设为
        # 只读，仍可用创建它的句柄截断，无需再按路径打开或放宽文件权限。
        handle = _winapi.CreateFile(
            str(name), _winapi.GENERIC_WRITE, 0x1 | 0x2 | 0x4, 0, 1, 0x80, 0
        )  # CREATE_NEW 独占创建普通文件，权限继承自父目录。
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except OSError:
            _winapi.CloseHandle(handle)
            raise
    else:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent_fd)
    return os.fdopen(fd, "wb", buffering=0)


def _discard_stage(
    out: BinaryIO,
    name: Path,
    parent_fd: int | None,
    created: os.stat_result,
    copied_attrs: list[str],
) -> str | None:
    """通过创建句柄截断正文；无法移除自己的目录项时，逐个清理已复制的属性。"""
    errors = []
    try:
        os.ftruncate(out.fileno(), 0)
    except OSError as error:
        errors.append(f"truncate: {type(error).__name__}")
    try:
        if os.path.samestat(created, os.stat(name, dir_fd=parent_fd, follow_symlinks=False)):
            os.unlink(name, dir_fd=parent_fd)
            return ", ".join(errors) or None
    except FileNotFoundError:
        pass
    except OSError as error:
        errors.append(f"unlink: {type(error).__name__}")
    # 条目也可能被移走；始终用原描述符清理，避免误删替代文件的属性。
    for attr in copied_attrs:
        try:
            _xattrs.removexattr(out.fileno(), attr)
        except OSError as error:
            errors.append(f"removexattr {attr}: {type(error).__name__}")
    return ", ".join(errors) or None


class DirectoryBase:
    """本机目录（没有 git 的也行）：全量遍历；软链扣留，不跟随。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._delivery_root: ContextVar[str | None] = ContextVar("delivery_root", default=None)

    @contextmanager
    def at_captured_root(self, root_id: str) -> Iterator[None]:
        # 同一适配器能服务并发交付，各次写回独立核对自己拍底时的身份。
        token = self._delivery_root.set(root_id)
        try:
            yield
        finally:
            self._delivery_root.reset(token)

    def _paths(self, scope: Sequence[str] = ()) -> Iterable[str]:
        for directory, names, filenames in os.walk(self.root):
            base = Path(directory)
            names[:] = [
                name for name in names if name not in IGNORED_DIRS or (base / name).is_symlink()
            ]
            for name in (*names, *filenames):
                target = base / name
                if target.is_symlink() or target.is_file():
                    relative = target.relative_to(self.root).as_posix()
                    if _in_scope(relative, scope):
                        yield relative

    def location(self) -> str:
        return os.path.realpath(self.root)

    def root_id(self) -> str:
        try:
            info = self.root.stat()
        except OSError as error:
            raise BaseUnavailable(f"无法核验底目录身份：{self.root}") from error
        if not info.st_ino or not stat.S_ISDIR(info.st_mode):
            raise BaseUnavailable(f"无法核验底目录身份：{self.root}")
        return f"{info.st_dev}:{info.st_ino}"

    def _source(self) -> dict[str, str]:
        return {"kind": "directory", "location": self.location(), "root_id": self.root_id()}

    def capture(self, scope: Sequence[str]) -> BaseCapture:
        files: list[BaseFile] = []
        withheld: dict[str, str] = {}
        source = self._source()
        for relative in sorted(set(self._paths(scope))):
            target = self.root / relative
            if _through_link(self.root, relative):
                withheld[relative] = "symlink"
            elif target.is_file():
                files.append(_file(self.root, relative))
        if self.root_id() != source["root_id"]:
            raise BaseUnavailable("列举文件期间底目录已被替换")
        return BaseCapture(files, withheld, source)

    # ------------------------------------------------------------ 写回
    def _current(self, relative: str) -> str | None:
        target = self.root / relative
        if not target.exists() and not target.is_symlink():
            return None
        if _through_link(self.root, relative) or not target.is_file():
            # 软链、目录：没有一个 blob 号能等于它，三方比对必然是冲突
            return "!" + ("symlink" if target.is_symlink() else "not_a_file")
        return git_blob_id(read_nofollow(target))

    def _confirm_read(
        self, path: str, scanned: Mapping[Path, tuple[int, ...] | None], blob: str | None
    ) -> FileWrite | None:
        if all(_revision(entry) == before for entry, before in scanned.items()):
            return None

        # 目录时间变化也可能只是增删了兄弟文件。用实际打开的文件复读，
        # 避免把读取期间短暂出现在原路径上的别名内容当作目标内容。
        target = self.root / path
        changed = FileWrite("refused", blob, "path_changed")
        before = scanned.get(target)
        if blob is not None:
            if before is None or not target.is_file():
                return changed
            with open_nofollow(target) as handle:
                opened = _version(os.fstat(handle.fileno()))
                if opened[:2] != before[:2]:
                    return changed
                actual = git_blob_id(handle.read())
        else:
            if before is not None:
                return changed
            opened = None
            actual = None

        # 读后仍须逐级核对精确名字。目录只比设备号和 inode；文件版本
        # 则从打开句柄核对到扫描结束，以捕获扫描期间的就地保存或替换。
        checked = {}
        problem = _spelling_problem(self.root, path, checked, require_exact=blob is not None)
        if problem is not None:
            return FileWrite("refused", None, problem)
        # 复扫下级目录时，上级也可能刚换了名字。只重列这期间发生
        # 变化的父目录，检查通向目标的那个条目；其他兄弟项可以继续变化。
        parent = self.root
        for part in PurePosixPath(path).parts:
            child = parent / part
            if _revision(parent) != checked.get(parent):
                problem = _entry_problem(
                    part, os.listdir(parent), child != target or blob is not None
                )
                if problem is not None:
                    return FileWrite("refused", None, problem)
            parent = child
        for entry, previous in scanned.items():
            if entry == target:
                continue  # 文件在下面同时核对身份和内容。
            current = _revision(entry)
            if previous is None or current is None or current[:2] != previous[:2]:
                return changed
        if _revision(target) != opened:
            now = self._current(path)
            return FileWrite("conflict", now) if now != blob else changed
        if actual != blob:
            return FileWrite("conflict", actual)
        return None

    def current_hashes(self, paths: Sequence[str]) -> dict[str, str | None]:
        # 写后换过名字时，别名的内容即使相同也不能作为原路径的回执。
        hashes = {}
        for path in paths:
            blob = None
            scanned = {}
            if _spelling_problem(self.root, path, scanned, require_exact=True) is None:
                blob = self._current(path)
                if (
                    _spelling_problem(self.root, path, {}, require_exact=True) is not None
                    or self._confirm_read(path, scanned, blob) is not None
                ):
                    blob = None
            hashes[path] = blob
        return hashes

    def write_file(
        self,
        path: str,
        data: bytes,
        *,
        expected: str | None,
        executable: bool,
    ) -> FileWrite:
        root_id = self._delivery_root.get()
        if _path_problem(path) is not None:
            return FileWrite("refused", None, "invalid_path")
        if root_id is not None and self.root_id() != root_id:
            return FileWrite("refused", None, "delivery_target_mismatch")
        if _through_link(self.root, path):
            return FileWrite("refused", None, "symlink")
        scanned: dict[Path, tuple[int, ...] | None] = {}
        spelling = _spelling_problem(self.root, path, scanned)
        if spelling is not None:
            return FileWrite("refused", None, spelling)
        try:
            current = self._current(path)
        except (OSError, ArtifactStoreError) as error:
            return FileWrite("failed", None, f"read_failed: {type(error).__name__}")
        if current == git_blob_id(data):
            spelling = _spelling_problem(self.root, path, {}, require_exact=True)
            if spelling is not None:
                return FileWrite("refused", None, spelling)
            if self._confirm_read(path, scanned, current) is not None:
                return FileWrite("refused", None, "path_changed")
            if root_id is not None and self.root_id() != root_id:
                return FileWrite("refused", None, "delivery_target_mismatch")
            return FileWrite("already", current)
        if current != expected:
            return FileWrite("conflict", current)
        return self._replace_file(
            path, data, current=current, root_id=root_id, executable=executable
        )

    def _replace_file(
        self, path: str, data: bytes, *, current: str | None, root_id: str | None, executable: bool
    ) -> FileWrite:
        target = self.root / path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if _through_link(self.root, path):  # 建目录的同时被换成了软链
                return FileWrite("refused", current, "symlink")
            # 创建、改名和清理共用父目录句柄，父目录移走后也能删除暂存的交付内容。
            with _staging_parent(target.parent) as (directory, parent_fd):
                if parent_fd is not None and (
                    problem := _writeback_metadata.acl_problem(parent_fd, inheritable_only=True)
                ):
                    return FileWrite("refused", current, problem)
                name = directory / f".{uuid.uuid4().hex}.harness"
                mode = 0o600
                if current is None:
                    mode = 0o777 if executable else 0o666
                with (
                    _open_stage(name, parent_fd, mode) as out,
                    ExitStack() as opened,
                ):
                    created = os.fstat(out.fileno())
                    copied_attrs: list[str] = []
                    outcome = None
                    cleanup = None
                    try:
                        outcome = self._write_staged(
                            path,
                            data,
                            current,
                            root_id,
                            copied_attrs,
                            name,
                            parent_fd,
                            out,
                            created,
                            opened,
                        )
                    except (OSError, ArtifactStoreError) as error:
                        outcome = FileWrite(
                            "failed", current, f"write_failed: {type(error).__name__}"
                        )
                    finally:
                        if outcome is None or outcome.status != "applied":
                            cleanup = _discard_stage(out, name, parent_fd, created, copied_attrs)
                    return replace(outcome, cleanup_error=cleanup) if cleanup else outcome
        except (OSError, ArtifactStoreError) as error:
            return FileWrite("failed", current, f"write_failed: {type(error).__name__}")

    def _write_staged(
        self,
        path: str,
        data: bytes,
        current: str | None,
        root_id: str | None,
        copied_attrs: list[str],
        name: Path,
        parent_fd: int | None,
        out: BinaryIO,
        created: os.stat_result,
        opened: ExitStack,
    ) -> FileWrite:
        target = self.root / path
        fd = out.fileno()
        source = None
        mode = None
        if current is None:
            if problem := _writeback_metadata.new_stage_problem(fd, created):
                return FileWrite("refused", current, problem)
        elif _xattrs.AVAILABLE:
            source = opened.enter_context(open_nofollow(target))
            source_info = os.fstat(source.fileno())
            if problem := _writeback_metadata.replacement_problem(
                source.fileno(), fd, source_info, created
            ):
                return FileWrite("refused", current, problem)
            mode = stat.S_IMODE(source_info.st_mode)
        else:
            mode = stat.S_IMODE(target.stat().st_mode)
        # 缓冲区在清理之前关闭，失败后不会再把缓冲正文刷回已截断的文件。
        with open(fd, "wb", closefd=False) as body:
            body.write(data)
        if source is not None:
            try:
                attrs = _xattrs.copy_to_stage(source.fileno(), fd, copied_attrs)
            except OSError:
                return FileWrite("refused", current, "xattr_unpreserved")
        if mode is not None:
            os.chmod(name if parent_fd is None else fd, mode)
            if parent_fd is not None and stat.S_IMODE(os.fstat(fd).st_mode) != mode:
                return FileWrite("refused", current, "metadata_unpreserved: mode")
        scanned: dict[Path, tuple[int, ...] | None] = {}
        spelling = _spelling_problem(self.root, path, scanned)
        if spelling is not None:
            return FileWrite("refused", current, spelling)
        if _through_link(self.root, path):
            return FileWrite("refused", current, "symlink")
        if root_id is not None and self.root_id() != root_id:
            return FileWrite("refused", current, "delivery_target_mismatch")
        parent_info = name.parent.stat() if parent_fd is None else os.fstat(parent_fd)
        if not os.path.samestat(parent_info, target.parent.stat()):
            return FileWrite("refused", current, "parent_changed")
        if source is not None:
            # 复读两端，既防复制期间用户改属性，也防文件系统改写属性值。
            try:
                if (
                    _xattrs.snapshot(source.fileno()) != attrs
                    or _xattrs.snapshot(out.fileno()) != attrs
                ):
                    return FileWrite("refused", current, "xattr_unpreserved")
            except OSError:
                return FileWrite("refused", current, "xattr_unpreserved")
        # 拼写扫描可能耗时，最后才读现状，然后立即以原子改名提交。
        now = self._current(path)
        if now != current:
            return FileWrite("conflict", now)
        if change := self._confirm_read(path, scanned, now):
            return change
        if source is not None and _revision(target) != _version(source_info):
            return FileWrite("refused", current, "path_changed")
        if not os.path.samestat(created, os.stat(name, dir_fd=parent_fd, follow_symlinks=False)):
            return FileWrite("refused", current, "path_changed")
        os.replace(
            name,
            name.with_name(target.name),
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        return FileWrite("applied", current)


class GitRepoBase(DirectoryBase):
    """用户的 git 仓库：工作目录里受版本管理的文件 + 未被忽略的新文件（没提交的改动算在内）。
    只读用户的仓库，不写它的 ``.git``。"""

    def _run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        # 列文件要按用户自己的规矩：仓库的 .gitignore 之外，还有用户全局配置里的 core.excludesFile，
        # 所以这里不隔离用户配置；只读命令，另外关掉 fsmonitor 与钩子，不让用户配置借机执行程序。
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        return subprocess.run(  # noqa: S603
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                str(self.root),
                *args,
            ],
            capture_output=True,
            env=env,
            check=False,
            timeout=120,
        )

    def _paths(self, scope: Sequence[str] = ()) -> Iterable[str]:
        done = self._run("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if done.returncode != 0:
            raise BaseUnavailable(
                f"列不出 {self.root} 的文件：{done.stderr.decode(errors='replace')[:200]}"
            )
        listings: dict[Path, dict[str, set[str]]] = {}
        folded_scope = tuple(spelling_key(item) for item in scope)
        for raw in done.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", errors="surrogateescape")
            # Git 可能给出预组合名或索引旧名。先用折叠范围排除无关路径，
            # 再用工作树的实际前缀决定是否继续深入，精确范围以磁盘为准。
            if scope and not _in_scope(spelling_key(relative), folded_scope):
                continue
            relative = self._worktree_spelling(relative, listings, scope)
            if relative is None or not _in_scope(relative, scope):
                continue
            target = self.root / relative
            # 已跟踪但工作目录里删掉了的不算底；经软链的照样列出，由 capture 扣留
            if _through_link(self.root, relative) or target.is_file():
                yield relative

    def _worktree_spelling(
        self, relative: str, listings: dict[Path, dict[str, set[str]]], scope: Sequence[str]
    ) -> str | None:
        """Git 负责选文件；索引大小写或 Git 预组合的名字须还原成目录项的拼写。"""
        folder = self.root
        parts = list(PurePosixPath(relative).parts)
        for index, part in enumerate(parts):
            if folder.is_symlink() and folder != self.root:
                break  # 剩余路径由拍底入口整体扣留，不枚举链接另一端
            if folder not in listings:
                try:
                    by_key: dict[str, set[str]] = {}
                    for name in os.listdir(folder):
                        by_key.setdefault(spelling_key(name), set()).add(name)
                    listings[folder] = by_key
                except (FileNotFoundError, NotADirectoryError):
                    break
            names = listings[folder].get(spelling_key(part), ())
            if part not in names:
                try:
                    selected = (folder / part).lstat()
                except (FileNotFoundError, NotADirectoryError):
                    break  # 区分大小写的磁盘上，已删除的索引项不能认领被忽略的同名文件
                matches = [
                    name for name in names if os.path.samestat(selected, (folder / name).lstat())
                ]
                if len(matches) != 1:
                    raise BaseUnavailable(f"无法核验 Git 所列路径的工作树拼写：{relative}")
                parts[index] = matches[0]
            if scope and index + 1 < len(parts):
                prefix = "/".join(parts[: index + 1])
                if not _in_scope(prefix, scope) and not any(
                    under_prefix(item, [prefix]) for item in scope
                ):
                    return None
            folder = folder / parts[index]
        return "/".join(parts)

    def _source(self) -> dict[str, str]:
        done = self._run("rev-parse", "--verify", "-q", "HEAD")
        head = done.stdout.decode().strip() if done.returncode == 0 else ""
        source = {**super()._source(), "kind": "git"}
        return {**source, "head": head} if head else source


class InMemoryBase:
    """测试替身：``{路径: 字节}``。"""

    def __init__(
        self,
        files: Mapping[str, bytes],
        *,
        executable: Iterable[str] = (),
        source: Mapping[str, str] | None = None,
    ) -> None:
        self.files = dict(files)
        self.executable = frozenset(executable)
        self.source = {"kind": "memory", **dict(source or {})}
        self.source.setdefault("location", "memory")

    def location(self) -> str:
        return str(self.source["location"])

    def capture(self, scope: Sequence[str]) -> BaseCapture:
        return BaseCapture(
            [
                BaseFile(path, len(data), (lambda data=data: data), path in self.executable)
                for path, data in sorted(self.files.items())
                if _in_scope(path, scope)
            ],
            {},
            self.source,
        )

    def current_hashes(self, paths: Sequence[str]) -> dict[str, str | None]:
        return {
            path: None if path not in self.files else git_blob_id(self.files[path])
            for path in paths
        }

    def write_file(
        self, path: str, data: bytes, *, expected: str | None, executable: bool
    ) -> FileWrite:
        current = self.current_hashes([path])[path]
        if current == git_blob_id(data):
            return FileWrite("already", current)
        if current != expected:
            return FileWrite("conflict", current)
        self.files[path] = data
        if executable and current is None:
            self.executable = self.executable | {path}
        return FileWrite("applied", current)


# ---------------------------------------------------------------- 工作仓库


def _isolated_env(**extra: str) -> dict[str, str]:
    """不读用户 / 系统的 git 配置（换行转换、钩子、过滤器都不生效），输出不翻译。"""

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") or key in {"GIT_EXEC_PATH"}
    }
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    env.update(extra)
    return env


def _text_content(data: bytes) -> str | None:
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _readable(data: bytes) -> bool:
    return len(data) <= READABLE_MAX_BYTES and _text_content(data) is not None


class BaseRepo:
    """SDK 自管的裸仓库。只有本模块写它；引用 ``refs/harness/base/<提交号>`` 保住每个底。"""

    def __init__(self, path: str | Path, *, git: str = "git") -> None:
        self.path = Path(path)
        self._git_program = git
        self.extra_secrets: tuple[str, ...] = ()

    def is_secret(self, text: str) -> bool:
        return bool(find_secrets(text, extra=self.extra_secrets))

    # ------------------------------------------------------------ 底层
    def _git(
        self, *args: str, input: bytes | None = None, env: Mapping[str, str] | None = None
    ) -> bytes:
        done = self._run_git(*args, input=input, env=env)
        if done.returncode != 0:
            raise BaseError(
                "base_repo_failed",
                f"git {args[0]} 失败：{done.stderr.decode(errors='replace')[:300]}",
            )
        return done.stdout

    def _run_git(
        self, *args: str, input: bytes | None = None, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603
            [self._git_program, f"--git-dir={self.path}", *args],
            input=input,
            capture_output=True,
            env=_isolated_env(**dict(env or {})),
            check=False,
            timeout=300,
        )

    def merge_file(self, current: bytes, base: bytes, incoming: bytes) -> bytes | None:
        """合并结果只从 stdout 取；有冲突时丢掉输出，临时文件始终只有输入字节。"""
        with tempfile.TemporaryDirectory(dir=self.path.parent) as directory:
            paths = [Path(directory) / name for name in ("current", "base", "incoming")]
            for path, content in zip(paths, (current, base, incoming), strict=True):
                path.write_bytes(content)
            done = self._run_git("merge-file", "-p", "--", *(str(p) for p in paths))
        if done.returncode == 0:
            return done.stdout
        if 1 <= done.returncode <= 127:
            return None
        raise BaseError(
            "base_repo_failed", f"git merge-file 失败：{done.stderr.decode(errors='replace')[:300]}"
        )

    def probe(self) -> dict[str, object]:
        """git 在不在、够不够新（要 ``merge-tree --write-tree``，2.38 起）。"""

        try:
            done = subprocess.run(  # noqa: S603
                [self._git_program, "--version"],
                capture_output=True,
                env=_isolated_env(),
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"ok": False, "reason": f"找不到 git：{type(error).__name__}"}
        match = re.search(rb"(\d+)\.(\d+)(?:\.(\d+))?", done.stdout)
        if done.returncode != 0 or match is None:
            return {"ok": False, "reason": "git --version 没有给出版本"}
        version = ".".join(part.decode() for part in match.groups() if part is not None)
        if (int(match.group(1)), int(match.group(2))) < GIT_MIN_VERSION:
            return {
                "ok": False,
                "version": version,
                "reason": f"git {version} 太旧，要 {GIT_MIN_VERSION[0]}.{GIT_MIN_VERSION[1]} 以上",
            }
        return {"ok": True, "version": version}

    def ensure(self) -> None:
        if (self.path / "HEAD").is_file():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        self._git("init", "--bare", "-q")
        # macOS 上 init 会按文件系统自动打开 core.ignorecase，fast-import 就会把只差大小写的目录并成
        # 一个；底的路径由 capture_base 守住大小写冲突，仓库本身必须逐字区分
        self._git("config", "core.ignorecase", "false")

    def _manifest_cache(self, commit: str) -> Path:
        return self.path.parent / "manifests" / f"{commit}.json"

    # ------------------------------------------------------------ 写
    def commit_files(
        self, files: Mapping[str, bytes], *, executable: Iterable[str] = (), message: str = "base"
    ) -> str:
        """把一组文件写成一个无父提交（作者、时间固定：同样的内容得到同一个提交号）。"""

        self.ensure()
        executable = frozenset(executable)
        temporary = f"refs/harness/import/{uuid.uuid4().hex}"
        stream = bytearray()
        text = message.encode()
        stream += (
            f"commit {temporary}\ncommitter {_COMMITTER}\ndata {len(text)}\n".encode()
            + text
            + b"\n"
        )
        for path in sorted(files):
            data = files[path]
            mode = "100755" if path in executable else "100644"
            stream += f"M {mode} inline {path}\ndata {len(data)}\n".encode() + data + b"\n"
        stream += b"done\n"
        self._git("fast-import", "--quiet", "--done", input=bytes(stream))
        commit = self._git("rev-parse", "--verify", temporary).decode().strip()
        self._git("update-ref", f"refs/harness/base/{commit}", commit)
        self._git("update-ref", "-d", temporary)
        manifest = {
            path: BaseEntry(
                path,
                blob="",
                sha256=hashlib.sha256(files[path]).hexdigest(),
                size=len(files[path]),
                executable=path in executable,
                readable=_readable(files[path]),
            )
            for path in files
        }
        self._write_manifest(commit, self._with_blobs(commit, manifest))
        return commit

    def commit_on(
        self,
        parent: str,
        files: Mapping[str, bytes],
        *,
        executable: Iterable[str] = (),
        message: str = "delivery\n",
    ) -> str:
        """在 ``parent`` 上叠一组文件（新增或整份替换），提交人、时间固定：同样的改动得到同一个
        提交号。引用 ``refs/harness/delivery/<提交号>`` 保住它。"""

        for path in files:
            # 路径原样进 fast-import 流：换行、引号开头的名字会被 git 读成别的命令
            if _path_problem(path) is not None:
                raise BaseError("base_repo_failed", f"交付提交不收这个路径：{path!r}")
        self.ensure()
        executable = frozenset(executable)
        temporary = f"refs/harness/import/{uuid.uuid4().hex}"
        text = message.encode()
        stream = bytearray(
            f"commit {temporary}\ncommitter {_COMMITTER}\ndata {len(text)}\n".encode()
            + text
            + f"\nfrom {parent}\n".encode()
        )
        for path in sorted(files):
            data = files[path]
            mode = "100755" if path in executable else "100644"
            stream += f"M {mode} inline {path}\ndata {len(data)}\n".encode() + data + b"\n"
        stream += b"done\n"
        self._git("fast-import", "--quiet", "--done", input=bytes(stream))
        commit = self._git("rev-parse", "--verify", temporary).decode().strip()
        self._git("update-ref", "-d", temporary)
        # 写完核对：交付提交 = 父提交的树叠上这些文件，一个不多、一个不少、字节一致
        expected = {path: blob for path, (blob, _mode) in self._tree(parent).items()}
        expected.update({path: git_blob_id(data) for path, data in files.items()})
        got = {path: blob for path, (blob, _mode) in self._tree(commit).items()}
        if got != expected:
            raise BaseError("base_repo_failed", "交付提交与要写的文件对不上")
        self._git("update-ref", f"refs/harness/delivery/{commit}", commit)
        return commit

    def _check_diff_side(self, commit: str, path: str, *, allow_absent: bool) -> None:
        # 按提交与精确路径取对象，不能用改动集自报的 blob、大小或可读标记。
        entry = self._git(
            "--literal-pathspecs", "ls-tree", "-lz", "--full-tree", commit, "--", path
        )
        if not entry and allow_absent:
            return
        meta, _, name = entry.partition(b"\t")
        if name != path.encode("utf-8") + b"\0":
            raise BaseError("unreadable", "不能确认 diff 的路径")
        mode, kind, blob, size = meta.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise BaseError("unreadable", "diff 的一侧不是普通文件")
        if int(size) > READABLE_MAX_BYTES:
            raise BaseError("binary_or_large", "diff 的一侧超过展示上限")
        text = _text_content(self._git("cat-file", "blob", blob.decode("ascii")))
        if text is None:
            raise BaseError("binary_or_large", "diff 的一侧是二进制")
        if self.is_secret(text):
            raise BaseError("secret_content", "diff 的一侧命中凭据")

    def diff(self, old: str, new: str, path: str) -> str:
        """两个提交之间一个文件的 unified diff（不走用户配置的外部 diff、文本转换）。"""

        self._check_diff_side(old, path, allow_absent=True)
        self._check_diff_side(new, path, allow_absent=False)
        out = self._git(
            "--literal-pathspecs",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            old,
            new,
            "--",
            path,
        )
        return out.decode("utf-8", errors="replace")

    def _with_blobs(self, commit: str, manifest: Mapping[str, BaseEntry]) -> dict[str, BaseEntry]:
        blobs = self._tree(commit)
        if set(blobs) != set(manifest):
            raise BaseError("base_repo_failed", "底提交里的路径与写入的不一致")
        return {
            path: BaseEntry(path, blobs[path][0], e.sha256, e.size, e.executable, e.readable)
            for path, e in manifest.items()
        }

    def _write_manifest(self, commit: str, manifest: Mapping[str, BaseEntry]) -> None:
        target = self._manifest_cache(commit)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {path: entry.to_json() for path, entry in sorted(manifest.items())},
            ensure_ascii=False,
            sort_keys=True,
        )
        with tempfile.NamedTemporaryFile(
            "w", dir=target.parent, delete=False, encoding="utf-8"
        ) as out:
            out.write(body)
        os.replace(out.name, target)

    # ------------------------------------------------------------ 读
    def has(self, commit: str) -> bool:
        if not re.fullmatch(r"[0-9a-f]{40}", commit or "") or not (self.path / "HEAD").is_file():
            return False
        try:
            kind = self._git("cat-file", "-t", commit).decode().strip()
        except BaseError:
            return False
        return kind == "commit"

    def _tree(self, commit: str) -> dict[str, tuple[str, str]]:
        """路径 → (blob 号, 模式)。"""

        out = self._git("ls-tree", "-r", "-z", "--full-tree", commit)
        entries: dict[str, tuple[str, str]] = {}
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, raw_path = record.partition(b"\t")
            mode, kind, blob = meta.decode().split(" ")
            if kind == "blob":
                entries[raw_path.decode("utf-8", errors="surrogateescape")] = (blob, mode)
        return entries

    def _blobs(self, ids: Sequence[str]) -> dict[str, bytes]:
        if not ids:
            return {}
        out = self._git("cat-file", "--batch", input=("\n".join(ids) + "\n").encode())
        found: dict[str, bytes] = {}
        cursor = 0
        for _ in ids:
            newline = out.index(b"\n", cursor)
            header = out[cursor:newline].decode().split(" ")
            if len(header) != 3:
                raise BaseError("base_repo_failed", f"底里缺对象：{header[0]}")
            blob, _kind, size = header
            start = newline + 1
            found[blob] = out[start : start + int(size)]
            cursor = start + int(size) + 1
        return found

    def blobs(self, ids: Sequence[str]) -> dict[str, bytes]:
        """按 blob 号取字节（不读整棵树）。"""

        return self._blobs(list(ids))

    def manifest(self, commit: str) -> dict[str, BaseEntry]:
        """底提交的文件清单（路径 → blob 号、sha256、大小、可执行、可读）。缓存是派生数据，
        丢了就从 git 对象重算。"""

        cache = self._manifest_cache(commit)
        if cache.is_file():
            raw = json.loads(cache.read_text(encoding="utf-8"))
            return {
                path: BaseEntry(
                    path,
                    item["blob"],
                    item["sha256"],
                    item["size"],
                    item["executable"],
                    item["readable"],
                )
                for path, item in raw.items()
            }
        tree = self._tree(commit)
        data = self._blobs(sorted({blob for blob, _mode in tree.values()}))
        manifest = {
            path: BaseEntry(
                path,
                blob,
                hashlib.sha256(data[blob]).hexdigest(),
                len(data[blob]),
                mode == "100755",
                _readable(data[blob]),
            )
            for path, (blob, mode) in tree.items()
        }
        self._write_manifest(commit, manifest)
        return manifest

    def read(self, commit: str, path: str) -> bytes:
        entry = self.manifest(commit).get(path)
        if entry is None:
            raise BaseError("base_missing", f"底里没有 {path}")
        return self._blobs([entry.blob])[entry.blob]

    def files(self, commit: str, paths: Iterable[str] | None = None) -> dict[str, bytes]:
        manifest = self.manifest(commit)
        wanted = manifest if paths is None else {p: manifest[p] for p in paths if p in manifest}
        data = self._blobs(sorted({entry.blob for entry in wanted.values()}))
        return {path: data[entry.blob] for path, entry in wanted.items()}

    def materialisable_manifest(self, commit: str) -> dict[str, BaseEntry]:
        """判定和铺底共用同一检查，避免旧底的两个条目覆盖同一物理文件。"""
        manifest = self.manifest(commit)
        if conflicts := ambiguous_paths(manifest):
            raise BaseError(
                "base_path_spelling",
                f"base_path_spelling: 底 {commit} 拼写歧义，判定受阻：{sorted(conflicts)!r}",
            )
        return manifest

    def materialise(self, commit: str, target: Path, *, skip: Iterable[str] = ()) -> list[str]:
        """把底提交的文件逐字节写进 ``target``（不经任何 git 过滤），返回写了哪些路径。"""

        manifest = self.materialisable_manifest(commit)
        skipped = frozenset(skip)
        data = self._blobs(sorted({e.blob for p, e in manifest.items() if p not in skipped}))
        written = []
        for path, entry in sorted(manifest.items()):
            if path in skipped:
                continue
            destination = Path(target) / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data[entry.blob])
            if entry.executable:
                destination.chmod(destination.stat().st_mode | 0o111)
            written.append(path)
        return written


# ---------------------------------------------------------------- 任务里的底


def mission_base_commit(final_report: Mapping[str, object] | None) -> str | None:
    """任务行记的底提交号；没有底返回 None。"""

    base = (final_report or {}).get("workspace_base")
    if isinstance(base, Mapping) and isinstance(base.get("commit"), str) and base["commit"]:
        return str(base["commit"])
    return None


#: 交给模型的工作区文件清单最多列这么多条；底更大时按顶层目录汇总，细节让它用列目录工具看。
LISTING_LIMIT = 400


def workspace_listing(paths: Iterable[str], *, limit: int = LISTING_LIMIT) -> list[str]:
    ordered = sorted(set(paths))
    if len(ordered) <= limit:
        return ordered
    counts: dict[str, int] = {}
    for path in ordered:
        head, sep, _rest = path.partition("/")
        key = head + "/" if sep else head
        counts[key] = counts.get(key, 0) + 1
    return [
        key if count == 1 and not key.endswith("/") else f"{key} ({count} 个文件)"
        for key, count in sorted(counts.items())
    ]


# ---------------------------------------------------------------- 拍快照


def _path_problem(path: str) -> str | None:
    if not isinstance(path, str) or not path or _CONTROL.search(path) or "\\" in path:
        return "invalid_path"
    if path.startswith('"'):
        return "invalid_path"
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:  # 不是 UTF-8 的文件名（Linux 上可能有）：写不进提交，也没法交给模型
        return "invalid_path"
    posix = PurePosixPath(path)
    if (
        posix.is_absolute()
        or str(posix) != path
        or any(part in {"..", "."} for part in posix.parts)
    ):
        return "invalid_path"
    if any(_git_alias(part) for part in posix.parts):
        # git 在 Windows / macOS 上把这些写法当成 .git，拒收；不扣会让整次拍底失败
        return "invalid_path"
    if any(part.casefold() in IGNORED_DIRS for part in posix.parts):
        # 含 .git 文件（工作树、子模块的指针）：git 不收这种路径，也不是使用方的内容
        return "ignored"
    return None


#: git 判断「是不是 .git」时忽略的不可见字符（HFS+ 会忽略它们）
_IGNORABLE = dict.fromkeys(
    [*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF]
)


def _git_alias(part: str) -> bool:
    """git 当成 ``.git`` 拒收的写法（大小写、结尾的空格和点、8.3 短名、NTFS 流、不可见字符）。"""

    folded = part.translate(_IGNORABLE).casefold().split(":", 1)[0].rstrip(" .")
    return part.casefold() != ".git" and folded in {".git", "git~1"}


def is_secret_name(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    return name not in _ENV_TEMPLATES and any(
        pattern.fullmatch(name) for pattern in SECRET_NAME_PATTERNS
    )


def credential_reason(
    path: str,
    content: bytes | str | None = None,
    *,
    is_secret: Callable[[str], object] = find_secrets,
    templates_only: bool = False,
) -> str | None:
    """按末级名字扣凭据。模板始终查正文，拍底与交付还查其他文本。"""
    if is_secret_name(path):
        return "secret_name"
    if content is None or (
        templates_only and PurePosixPath(path).name.casefold() not in _ENV_TEMPLATES
    ):
        return None
    text = _text_content(content) if isinstance(content, bytes) else content
    return "secret_content" if text is not None and is_secret(text) else None


def capture_base(
    base: WorkspaceBase,
    repo: BaseRepo,
    *,
    scope: Sequence[str],
    limits: CaptureLimits,
    is_secret: Callable[[str], bool],
    reserved: Sequence[str] = (),
) -> BaseRecord:
    """按范围拍一次底：先按名字、路径、大小写扣留，再查上限（只看大小，不读字节），
    最后读字节、按内容扣留疑似密钥，写成底提交。"""

    scope = tuple(scope)
    captured = base.capture(scope)
    withheld: dict[str, str] = {
        path: reason for path, reason in captured.withheld.items() if _in_scope(path, scope)
    }
    candidates: dict[str, BaseFile] = {}
    for item in captured.files:
        if not _in_scope(item.path, scope):
            continue
        problem = _path_problem(item.path)
        if problem == "ignored":
            continue
        if problem is not None:
            withheld[item.path] = problem
        elif under_prefix(item.path.casefold(), tuple(r.casefold() for r in reserved)):
            withheld[item.path] = "reserved_path"
        elif reason := credential_reason(item.path):
            withheld[item.path] = reason
        else:
            candidates[item.path] = item
    # 底也会铺到折叠大小写或规范化 Unicode 的磁盘，须先扣留整组歧义路径。
    for path in ambiguous_paths((*candidates, *withheld)):
        if path in candidates:
            withheld[path] = "case_conflict"
            candidates.pop(path)
    total = sum(item.size for item in candidates.values())
    if len(candidates) > limits.max_files or total > limits.max_bytes:
        raise BaseTooLarge(len(candidates), total, limits)
    files: dict[str, bytes] = {}
    executable = []
    for path, item in sorted(candidates.items()):
        try:
            data = item.read()
        except BaseError:
            # 读不了（没有权限、拍的时候被删掉或换成软链）：扣留这一个，不让整次拍底失败
            withheld[path] = "unreadable"
            continue
        if reason := credential_reason(path, data, is_secret=is_secret):
            withheld[path] = reason
            continue
        files[path] = data
        if item.executable:
            executable.append(path)
    source = dict(captured.source)
    if isinstance(base, DirectoryBase) and base.root_id() != source.get("root_id"):
        raise BaseUnavailable("读取文件期间底目录已被替换")
    commit = repo.commit_files(files, executable=executable, message="base\n")
    return BaseRecord(
        commit=commit,
        files=len(files),
        bytes=sum(len(data) for data in files.values()),
        scope=scope,
        withheld=withheld,
        source=source,
    )


__all__ = (
    "BaseCapture",
    "BaseEntry",
    "BaseError",
    "BaseFile",
    "BaseRecord",
    "BaseRepo",
    "BaseTooLarge",
    "BaseUnavailable",
    "CaptureLimits",
    "DirectoryBase",
    "FileWrite",
    "GitRepoBase",
    "InMemoryBase",
    "RESERVED_BASE_PREFIXES",
    "WorkspaceBase",
    "WritableBase",
    "capture_base",
    "git_blob_id",
    "mission_base_commit",
    "workspace_listing",
)
