#!/usr/bin/env python3
"""
allowlist_probe.py — 验证 plan §D3 寒暄 allowlist 规格的实现可行性。

依据 plan 文本中描述的规则（D3 + WI-1）逐条实现一个 _is_obvious_chitchat()：
  - 高精度白名单词表（招呼/感谢/告别/纯 emoji/纯标点）
  - 否决项：问号 / 祈使动词 / code token / 中文疑问词 / 过长

然后用 plan §6.1 的 7 个边界样本 + 额外攻击样本真跑，报告实际结论 vs 期望。
"""
import re
import unicodedata

# ──────────────────────────────────────────────────────────────
# 参数（可调）
# ──────────────────────────────────────────────────────────────
MAX_LEN = 12          # 字符数上限（超过即否决）

# ──────────────────────────────────────────────────────────────
# 招呼/感谢/告别/寒暄词根白名单（全小写，fullmatch 之前会 strip）
# ──────────────────────────────────────────────────────────────
_GREET_ROOTS = re.compile(
    r"^("
    r"你好|您好|嗨|哈喽|hi|hello|hey|howdy|哦|哈哈|嘿嘿|嘿|呵呵|哈|嗯嗯|嗯|哦哦|哦|嘻嘻|哈哈哈|haha|lol"
    r"|谢谢|谢|感谢|多谢|thanks|thank you|thx|ty"
    r"|再见|拜拜|拜|bye|goodbye|晚安|早安|早上好|下午好|晚上好|早|晚"
    r"|好的|好|ok|okay|okey|好哒|好的好的"
    r"|啊|哟|耶|哦耶|嘻|呀|呵|哦嗯|嗯哦|嗯啊|嗯呢"
    r"|在|在的|在呀|在哦"
    r")$",
    re.IGNORECASE,
)

# ──────────────────────────────────────────────────────────────
# 否决正则（命中任意一个即拒绝进入 allowlist）
# ──────────────────────────────────────────────────────────────
_VETO_QUESTION_MARK = re.compile(r"[?？]")

_VETO_IMPERATIVE_ZH = re.compile(
    r"(帮我|帮你|告诉我|告诉你|解释|说明|怎么|如何|为什么|为啥|"
    r"是什么|什么是|什么叫|有没有|有什么|能不能|可以吗|可以不|能帮|教我|给我|"
    r"看看|看一下|查一下|查查|搜一下|搜搜|做一下|做个|写一下|写个|"
    r"修一下|修个|改一下|改下|debug|fix|check|show|tell|explain|how|why|what|who|when|where)"
)

_VETO_CODE_TOKEN = re.compile(
    r"[(){}\[\]<>/\|@#$%^&*=+~`]|"
    r"\b(def|class|import|from|return|print|var|let|const|function|if|else|for|while|try|catch|int|str|list|dict)\b",
    re.IGNORECASE,
)

_VETO_CONTAINS_CHINESE_QUESTION = re.compile(
    r"(吗|么|呢|嘛|啥|咋|几|多少|哪|哪里|哪儿|谁|啥|干嘛|干什么)"
)

# ──────────────────────────────────────────────────────────────
# 纯 emoji / 纯标点 快路径
# ──────────────────────────────────────────────────────────────
def _is_only_emoji_or_punct(text: str) -> bool:
    """True 当且仅当文本全由 emoji、标点、空白组成（无实意文字）。"""
    for ch in text:
        cat = unicodedata.category(ch)
        # 允许：So（其他符号/emoji）、Sm（数学符号）、Ps/Pe/Po/Pd（标点）、Z（空白）
        if cat.startswith("Z"):
            continue
        if cat.startswith("P"):
            continue
        if cat in ("So", "Sm", "Sk", "Sc"):
            continue
        # emoji 范围补丁（unicodedata 不总给 So）
        cp = ord(ch)
        if (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or (0xFE00 <= cp <= 0xFE0F):
            continue
        return False
    return len(text.strip()) > 0


def _is_obvious_chitchat(text: str) -> bool:
    """高精度寒暄 allowlist。True = 确定是寒暄，可短路；False = 不确定，走 LLM。"""
    t = text.strip()
    if not t:
        return False

    # 1. 长度否决（过长一定有信息量）
    if len(t) > MAX_LEN:
        return False

    # 2. 问号否决
    if _VETO_QUESTION_MARK.search(t):
        return False

    # 3. 祈使/疑问动词否决
    if _VETO_IMPERATIVE_ZH.search(t):
        return False

    # 4. code token 否决
    if _VETO_CODE_TOKEN.search(t):
        return False

    # 5. 中文疑问助词否决
    if _VETO_CONTAINS_CHINESE_QUESTION.search(t):
        return False

    # 6. 纯 emoji / 纯标点 放行
    if _is_only_emoji_or_punct(t):
        return True

    # 7. 白名单词根放行
    if _GREET_ROOTS.match(t.lower()):
        return True

    return False


# ──────────────────────────────────────────────────────────────
# 测试用例表
# format: (input, expected_short_circuit, label)
# ──────────────────────────────────────────────────────────────
CASES = [
    # plan §6.1 官方边界（应短路）
    ("你好",                True,  "plan-官方 ✓ 你好"),
    ("谢谢",                True,  "plan-官方 ✓ 谢谢"),
    ("晚安",                True,  "plan-官方 ✓ 晚安"),
    ("😄",                  True,  "plan-官方 ✓ 纯emoji"),

    # plan §6.1 官方边界（不应短路）
    ("你好，帮我看下这段为什么报错", False, "plan-官方 ✗ 你好+真问题"),
    ("光合作用为什么需要光",   False, "plan-官方 ✗ 真问题"),
    ("在吗？我代码崩了",       False, "plan-官方 ✗ 问号+代码"),

    # 额外攻击样本
    ("你好呀",               True,  "变体 ✓ 你好呀"),
    ("早上好",               True,  "变体 ✓ 早上好"),
    ("😊😎👍",               True,  "攻击 ✓ 多emoji"),
    ("。。。",               True,  "攻击 ✓ 纯标点"),

    # 中英混合
    ("hi 你好",              True,  "混合 ✓ hi你好 (短)"),
    ("hello 帮我查下",        False, "混合 ✗ hello+祈使"),

    # 繁体
    ("謝謝",                 False, "繁体 - 谢谢(繁) 白名单无繁体→拒"),
    ("晚安啊",               True,  "变体 ✓ 晚安啊 (短,无否决)"),

    # 刁钻用例
    ("在吗在吗",             True,  "攻击 ✓ 在吗在吗 (无问号版,8字符)"),
    ("早上代码崩了",          False, "攻击 ✗ 早上+代码崩了 (含code关键词)"),
    ("哈哈哈哈哈哈哈哈哈哈哈哈", False, "长度 ✗ 超长哈哈 (>12)"),
    ("在吗？",               False, "攻击 ✗ 在吗+问号"),
    ("ok 那你帮我改一下",     False, "攻击 ✗ ok+祈使"),
    ("嗯嗯",                 True,  "变体 ✓ 嗯嗯"),
    ("哈哈",                 True,  "变体 ✓ 哈哈"),
    ("",                     False, "边界 ✗ 空字符串"),
]


def main():
    print("=" * 68)
    print(f"allowlist_probe.py — MAX_LEN={MAX_LEN}")
    print("=" * 68)
    pass_count = 0
    fail_count = 0
    mismatches = []

    for text, expected, label in CASES:
        actual = _is_obvious_chitchat(text)
        status = "OK" if actual == expected else "MISMATCH"
        if actual == expected:
            pass_count += 1
        else:
            fail_count += 1
            mismatches.append((text, expected, actual, label))
        exp_str = "short-circuit" if expected else "no-short-circuit"
        act_str = "short-circuit" if actual else "no-short-circuit"
        mark = "" if actual == expected else " <<< MISMATCH"
        print(f"  [{status}] {label!r:42s}  期望={exp_str:17s}  实际={act_str}{mark}")

    print()
    print(f"结果: {pass_count} PASS / {fail_count} MISMATCH")
    if mismatches:
        print()
        print("不一致项（期望 vs 实际）：")
        for text, expected, actual, label in mismatches:
            exp_str = "short-circuit" if expected else "no-short-circuit"
            act_str = "short-circuit" if actual else "no-short-circuit"
            print(f"  输入={text!r:30s}  期望={exp_str}  实际={act_str}  ({label})")
    print()
    print("结论：见上方 MISMATCH 行。期望=True 但实际=False → allowlist 漏放行（安全,可扩词表）。")
    print("     期望=False 但实际=True → allowlist 误放行（危险,需收窄）。")


if __name__ == "__main__":
    main()
