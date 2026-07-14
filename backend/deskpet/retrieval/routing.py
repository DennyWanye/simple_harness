from __future__ import annotations
import re

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")

def stable_batches(query: str, configured: list[str], *, searxng: bool, google_reachable: bool) -> list[list[str]]:
    enabled = set(configured)
    chinese = bool(_CJK.search(query))
    first = (["searxng"] if searxng else []) + (["baidu"] if chinese else ["duckduckgo"])
    if not chinese and not searxng and google_reachable: first.append("google-cdp")
    second = (["google-cdp"] if google_reachable else []) + ["bing-cdp"]
    first = [name for name in first if name in enabled or name == "searxng"][:2]
    second = [name for name in second if name in enabled and name not in first][:2]
    ordered = first + second
    remaining = [name for name in configured if name not in ordered]
    batches = [batch for batch in (first, second) if batch]
    batches.extend(remaining[i:i + 2] for i in range(0, len(remaining), 2))
    return batches
