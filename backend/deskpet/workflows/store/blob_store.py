"""Content-addressed storage for values too large for checkpoints."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BlobRef:
    sha256: str
    size_bytes: int
    media_type: str

    def to_json(self) -> dict[str, str | int]:
        return {"sha256": self.sha256, "size_bytes": self.size_bytes, "media_type": self.media_type}


class BlobStore:
    """Crash-safe immutable blobs addressed by SHA-256."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise ValueError("invalid SHA-256 digest")
        return self.root / sha256[:2] / sha256[2:]

    def put(self, data: bytes, *, media_type: str = "application/octet-stream") -> BlobRef:
        digest = hashlib.sha256(data).hexdigest()
        target = self.path_for(digest)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            try:
                with temp.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    temp.replace(target)
                except FileExistsError:
                    pass
            finally:
                temp.unlink(missing_ok=True)
        return BlobRef(digest, len(data), media_type)

    def get(self, ref: BlobRef | str) -> bytes:
        digest = ref.sha256 if isinstance(ref, BlobRef) else ref
        data = self.path_for(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"workflow blob failed integrity check: {digest}")
        return data
