# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase-2 中文一手源直连适配器测试 (research_sources)。"""
from __future__ import annotations

import pytest

from deskpet.tools import research_sources as rs


# --- 意图路由 ---

def test_direct_source_for():
    assert rs.direct_source_for("宁德时代2025年财报营收") == "cninfo"
    assert rs.direct_source_for("某上市公司年度报告披露") == "cninfo"
    assert rs.direct_source_for("钠离子电池国家标准 GB/T") == "openstd"
    assert rs.direct_source_for("锂电池技术规范标准号") == "openstd"
    assert rs.direct_source_for("今天天气如何") is None
    # openstd 优先级高于 cninfo(同时含"标准"和"公告"时)
    assert rs.direct_source_for("国家标准公告") == "openstd"


# --- openstd 纯解析 ---

_OPENSTD_HTML = """
<table>
  <tr><th>序号</th><th>标准号</th><th>标准名称</th><th>状态</th></tr>
  <tr><td>1</td><td>GB/T 32895-2016</td><td>钠离子电池通用规范</td>
      <td>推标</td><td>现行</td><td>2016-08-29 00:00:00.0</td>
      <td><a onclick="showInfoHref('ABC123DEF456'); return false;"
             href="javascript:void(0)">查看详细</a></td></tr>
  <tr><td>2</td><td>GB/T 27930-2015</td><td>电动汽车充电通信协议</td>
      <td>现行</td><td>2015-12-28 00:00:00.0</td>
      <td><a href="newGbInfo?hcno=ZZZ999">查看详细</a></td></tr>
  <tr><td>3</td><td>无标准号行</td><td>不该被收</td><td>x</td></tr>
</table>
"""


def test_parse_openstd():
    out = rs.parse_openstd(_OPENSTD_HTML, max_results=5)
    assert len(out) == 2  # 第3行无 GB 标准号 → 丢
    assert out[0]["std_no"] == "GB/T 32895-2016"
    # 名称不能被日期 cell(2016-08-29...) 或状态词(推标/现行) 抢走
    assert out[0]["name"] == "钠离子电池通用规范"
    # hcno 从 <a onclick=showInfoHref('...')> 抽到(非标准号)
    assert out[0]["hcno"] == "ABC123DEF456"
    # 第2行 hcno 从 href 的 ?hcno= 抽
    assert out[1]["hcno"] == "ZZZ999"


def test_parse_openstd_respects_max():
    out = rs.parse_openstd(_OPENSTD_HTML, max_results=1)
    assert len(out) == 1


def test_openstd_keyword():
    # 短词原样
    assert rs._openstd_keyword("钠离子电池") == "钠离子电池"
    # 长子问题压成核心技术词(剥填充词 + 砍脚手架词)
    assert rs._openstd_keyword("钠离子电池有哪些国家标准 GB/T 标准号") == "钠离子电池"
    assert rs._openstd_keyword(
        "围绕钠离子电池 GB/T 标准仍存在哪些争议或空白") == "钠离子电池"
    # 句首"中国/截至目前"等填充词被循环剥离
    assert rs._openstd_keyword(
        "截至目前中国钠离子电池国家标准的制定进展") == "钠离子电池"
    # 无技术词(纯脚手架)→ 空 → openstd_search 跳过
    assert rs._openstd_keyword("这些 GB/T 标准由哪些主管部门管理") == ""


# --- cninfo: mock httpx 验证 query→PDF→passage 流程 ---

@pytest.mark.asyncio
async def test_cninfo_search_builds_passages(monkeypatch):
    class _Resp:
        def __init__(self, *, json_data=None, content=b"", status=200):
            self._j = json_data
            self.content = content
            self.status_code = status

        def raise_for_status(self):
            return None

        def json(self):
            return self._j

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, **kw):
            return _Resp(json_data={"announcements": [
                {"secName": "<em>宁德时代</em>", "announcementTitle": "2025年<em>年度报告</em>",
                 "adjunctUrl": "finalpage/2026-03-10/123.PDF"},
            ]})

        async def get(self, url, **kw):
            return _Resp(content=b"%PDF-1.4 fake")  # pdf 抽取会失败→退化元数据

        async def aclose(self):
            return None

    # pypdf 抽取失败 → 退化为元数据文本(仍是一手信号)
    out = await rs.cninfo_search("宁德时代 年报", client=_FakeClient())
    assert len(out) == 1
    assert out[0]["source"] == "cninfo"
    assert "宁德时代" in out[0]["title"]  # <em> 已清
    assert "<em>" not in out[0]["title"]
    assert out[0]["url"].startswith("http://static.cninfo.com.cn/")
    assert out[0]["text"]  # 有内容(PDF 抽到 或 元数据退化)


def test_rank_cninfo_anns_summary_first():
    """财报类: '年度报告摘要' 排到 '年度报告' 正报之前(摘要财务表召回性价比高)。"""
    anns = [
        {"announcementTitle": "2024年年度报告"},
        {"announcementTitle": "2024年年度报告摘要"},
        {"announcementTitle": "关于召开股东大会的公告"},
    ]
    ranked = rs._rank_cninfo_anns(anns)
    assert "摘要" in ranked[0]["announcementTitle"]   # 摘要置顶
    titles = [a["announcementTitle"] for a in ranked]
    assert titles == ["2024年年度报告摘要", "2024年年度报告", "关于召开股东大会的公告"]


@pytest.mark.asyncio
async def test_cninfo_search_empty_keyword():
    assert await rs.cninfo_search("") == []


@pytest.mark.asyncio
async def test_cninfo_search_network_fail(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, **kw):
            raise RuntimeError("network down")

        async def aclose(self):
            return None

    out = await rs.cninfo_search("x", client=_FakeClient())
    assert out == []  # best-effort
