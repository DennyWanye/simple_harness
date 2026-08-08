#!/usr/bin/env bash
# 按侧栏导航按钮序号点击（1=会话 2=技能中心 3=产物库 4=更多 5=设置）。
#
# 定位方式：AX 里侧栏导航按钮固定 **宽 223**，会话行/重命名删除图标宽度都不同，
# 据此过滤即可在「会话视图（列表展开）」和「其他视图（列表收起）」两种布局下
# 都稳定拿到同一组 5 个按钮。不能用 `head -N` —— 会话视图里会话行也是 AXButton，
# 会把导航项顶出前 N（实测踩过）。每次都重查，不缓存坐标。
set -uo pipefail
A=/Users/denny/projects/simple_harness/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260809-r9/artifacts
pos=$("$A/ax.sh" all 2>/dev/null | grep "^AXButton" | grep -F "223x" | sed -n "${1}p" | awk -F'\t' '{print $3}')
x=${pos%,*}; y=${pos#*,}
[ -z "$x" ] && { echo "NAV_NOT_FOUND idx=$1" >&2; exit 1; }
cliclick m:$((x+111)),$((y+18)) w:150 c:$((x+111)),$((y+18)) >/dev/null
echo "nav#$1 clicked @$((x+111)),$((y+18))"
