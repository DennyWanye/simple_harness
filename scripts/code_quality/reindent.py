"""代码质量整治：搬一段代码到别的缩进层级时，只动代码行的缩进，多行字符串内部的行原样保留。

textwrap.dedent / indent 会把三引号字符串的续行也一起挪，字面量内容（空格）就变了。
"""

from __future__ import annotations

import io
import tokenize


def string_interior_lines(text: str) -> set[int]:
    """text 里落在多行字符串（含 f-string）内部的行号（从 1 起，不含字符串开头那一行）。"""
    out: set[int] = set()
    starts: list[int] = []
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if fstring_start is not None and tok.type == fstring_start:
            starts.append(tok.start[0])
        elif fstring_end is not None and tok.type == fstring_end:
            begin = starts.pop()
            if not starts:
                out.update(range(begin + 1, tok.end[0] + 1))
        elif tok.type == tokenize.STRING and not starts:
            out.update(range(tok.start[0] + 1, tok.end[0] + 1))
    return out


def reindent(
    text: str, new_indent: str, first_line: int = 1, interior: set[int] | None = None
) -> str:
    """把 text（若干完整的行）整体改成 new_indent 缩进。

    first_line：text 第一行在原文件里的行号；interior：原文件里多行字符串内部的行号（由调用者对
    整个文件算一次，避免对片段单独分词出错）。没给 interior 时对 text 自己分词。
    """
    lines = text.splitlines(keepends=True)
    if interior is None:
        interior = string_interior_lines(text)
        first_line = 1
    code = [ln for i, ln in enumerate(lines, start=first_line) if i not in interior and ln.strip()]
    common = min((len(ln) - len(ln.lstrip(" ")) for ln in code), default=0)
    out = []
    for i, ln in enumerate(lines, start=first_line):
        if i in interior:
            out.append(ln)
        elif not ln.strip():
            out.append("\n" if ln.endswith("\n") else "")
        else:
            out.append(new_indent + ln[common:])
    return "".join(out)
