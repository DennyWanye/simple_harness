#!/bin/bash
# 用法：check_split.sh <原文件快照> <源文件（backend 相对）> <类名> <子包目录（backend 相对）>
S=$(cd "$(dirname "$0")" && pwd); ORIG=$1; SRC=$2; CLS=$3; PKG=$4
cd "$S/../../../backend"
MOD=$(echo "${SRC%.py}" | tr / .)
python3 $S/same_methods.py "$ORIG" "$CLS" "$PKG" "$SRC" | head -12
.venv/bin/python $S/dir_cmp.py "$ORIG" "$MOD" "$CLS"
.venv/bin/python $S/ext_names.py "$MOD" | head -12
python3 $S/patch_check.py "$MOD" | head -12
if grep -q "getLogger(__name__)\|get_logger(__name__)\|structlog.get_logger()" "$ORIG"; then
  echo "原文件有日志记录器："; grep -rn "getLogger(__name__)\|get_logger(__name__)\|structlog.get_logger()" "$PKG" || echo "  子包里没有按 __name__ 取的记录器"
fi
grep -n "^def __getattr__" "$SRC" && echo "注意：模块有 __getattr__"
true
# 函数身份（模块名:限定名）会进工具处理函数身份、工作流节点清单、工具生命周期哈希：搬走的代码里不许有交出函数的地方
grep -rnE "ToolSpec\(|\.register\(|handler=|stage=|add_node\(|_callable_(id|record|identity)" "$PKG" && echo "注意：子包里有交出函数身份的写法，逐个核对"
true
