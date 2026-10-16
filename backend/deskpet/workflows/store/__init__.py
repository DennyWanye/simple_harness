"""Durable local storage for DeskPet workflows."""

# 包根只为禁区里的工作流定义文件（节点源码计入工作流身份，按字节冻结）保留这三个名字；
# 其余代码一律从定义所在的子模块导入（阶段 6E 解环）。
from .blob_store import BlobRef, BlobStore
from .registered_blob_store import RegisteredBlobStore

__all__ = ["BlobRef", "BlobStore", "RegisteredBlobStore"]
