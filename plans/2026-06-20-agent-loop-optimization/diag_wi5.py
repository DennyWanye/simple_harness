"""WI-5 诊断：知识片段 triggers 在 loader 层是否丢失。

复现 main.py 构造 SkillLoader 的方式（dir[0]=builtin pkg, dir[1]=user），
分别在 knowledge_enabled False / True 下 reload，打印每个 skill 的
name / user_invocable / triggers，重点看 3 个知识片段。

跑：cd backend && .venv/Scripts/python.exe ../plans/2026-06-20-agent-loop-optimization/diag_wi5.py
"""
from pathlib import Path

import deskpet.skills.builtin as _builtin_pkg
from deskpet.skills.loader import SkillLoader


def dump(flag: bool) -> None:
    builtin_dir = Path(_builtin_pkg.__file__).parent
    loader = SkillLoader(
        skill_dirs=[builtin_dir],
        enable_watch=False,
        knowledge_enabled=flag,
    )
    loader.reload()
    metas = loader.all()
    print(f"\n===== knowledge_enabled={flag} -> total={len(metas)} =====")
    for m in metas:
        print(
            f"  name={m.name!r:30} user_invocable={m.user_invocable!s:5} "
            f"triggers={m.triggers}"
        )


if __name__ == "__main__":
    dump(False)
    dump(True)
