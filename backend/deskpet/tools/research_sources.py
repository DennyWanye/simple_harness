# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase-2 中文一手源直连适配器（deep-research）。

绕开搜索引擎,直接打权威数据库 API 拿一手数据,从源头避开搜狐/百家号转帖:

* :func:`cninfo_search` — 巨潮资讯(深交所旗下)``hisAnnouncement/query`` →
  上市公司公告(年报/财报等),下 PDF 用 pypdf 抽正文。中国大陆可直连。
* :func:`openstd_search` — 国家标准全文公开系统 ``openstd.samr.gov.cn`` 搜索 →
  解析标准号/名称(全文在 JS viewer 后,只取元数据)。中国大陆可直连。

两者返回与 research 管线一致的 passage dict ``{url,title,text,fetched_at}``;
命中域名(cninfo.com.cn / openstd.samr.gov.cn=.gov.cn) 天然 TIER_1,后续打分/精排
自然把一手源顶上来。全程 best-effort,任何失败返空 list,绝不抛出。

意图路由由 :func:`direct_source_for` 决定:子问题谈"上市公司/公告/财报"→cninfo;
谈"国标/标准/规范/GB"→openstd;否则 None(只走普通搜索)。
"""
from __future__ import annotations

import io
import logging
import re
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT = 18.0

_CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_STATIC = "http://static.cninfo.com.cn/"
_OPENSTD_LIST = "https://openstd.samr.gov.cn/bzgk/gb/std_list"

_PDF_MAX_PAGES = 8         # 年报常数百页;只抽前 N 页(摘要/要点/主要财务通常在前面)
_PDF_MAX_CHARS = 12_000    # 单篇正文上限,控 token


# ── 意图路由 ──────────────────────────────────────────────────────────
_CNINFO_KW = ("上市公司", "公告", "财报", "年报", "季报", "半年报", "业绩",
              "营收", "净利润", "招股", "招股书", "问询函", "巨潮", "披露")
_OPENSTD_KW = ("国标", "国家标准", "技术规范", "标准号", "gb/t", "gb ",
               "强制性标准", "推荐性标准", "标准全文")


def direct_source_for(text: str) -> Optional[str]:
    """子问题命中 → "cninfo" / "openstd";否则 None。"""
    t = (text or "").lower()
    if any(k in t for k in _OPENSTD_KW):
        return "openstd"
    if any(k in t for k in _CNINFO_KW):
        return "cninfo"
    return None


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


# 巨潮 searchkey 走标题全文匹配:噪声句("X公司2024年财报营收")命中 0,纯实体名
# ("X公司")命中。下表去掉年报/财报类噪声词 + 年份/数字 → 留公司/实体名。
_CNINFO_NOISE = (
    "年度报告", "年报", "半年报", "季报", "财报", "业绩快报", "业绩预告",
    "业绩", "营收", "营业收入", "净利润", "利润", "公告", "披露", "报告",
    "招股说明书", "招股书", "问询函", "怎么样", "如何", "多少", "情况",
    "的", "和", "与", "及", "了",
)


def _cninfo_category(q: str) -> str:
    """从原始问题判断公告类别 → cninfo category 过滤,显著提升相关性。
    谈年报/财报/营收/净利润 → 年度报告类;否则空(全部公告按时间)。"""
    t = q or ""
    if any(k in t for k in ("年报", "年度报告", "财报", "营收", "营业收入",
                            "净利润", "利润", "业绩")):
        return "category_ndbg_szsh"   # 年度报告(深沪)
    if any(k in t for k in ("半年报", "中报", "半年度")):
        return "category_bndbg_szsh"  # 半年度报告
    if "季报" in t or "一季" in t or "三季" in t:
        return "category_yjdbg_szsh"  # 季度报告
    return ""


def _clean_cninfo_kw(q: str) -> str:
    """把噪声子问题压成实体名(公司名)。压没了就退回原串。"""
    s = q or ""
    for n in _CNINFO_NOISE:
        s = s.replace(n, "")
    s = re.sub(r"\d{4}\s*年?", "", s)   # 年份
    s = re.sub(r"\d+", "", s)            # 残留数字
    s = re.sub(r"\s+", " ", s).strip()
    return s or (q or "").strip()


# ── 巨潮资讯 ──────────────────────────────────────────────────────────
def _extract_pdf_text(data: bytes) -> str:
    """pypdf 抽 PDF 前 N 页文本(capped)。失败/无库 → 空串。"""
    try:
        import pypdf  # type: ignore
    except Exception:  # noqa: BLE001
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages[:_PDF_MAX_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
            if sum(len(p) for p in parts) >= _PDF_MAX_CHARS:
                break
        return "\n".join(parts)[:_PDF_MAX_CHARS].strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("pypdf extract failed: %s", exc)
        return ""


async def cninfo_search(
    keyword: str, *, max_results: int = 4,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """巨潮公告直连 → passages(含 PDF 抽取正文)。best-effort,失败返 []。"""
    kw = _clean_cninfo_kw(keyword)
    if not kw:
        return []
    category = _cninfo_category(keyword)   # 用原始问题判类别(年报/季报/...)
    owns = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True
    )
    try:
        try:
            resp = await cli.post(
                _CNINFO_QUERY,
                data={"pageNum": 1, "pageSize": max(max_results, 5),
                      "column": "szse", "tabName": "fulltext",
                      "searchkey": kw, "category": category, "seDate": "",
                      "sortName": "", "sortType": "", "isHLtitle": "true"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            anns = (resp.json() or {}).get("announcements") or []
        except Exception as exc:  # noqa: BLE001
            log.debug("cninfo query failed for %r: %s", kw, exc)
            return []
        out: list[dict[str, Any]] = []
        for a in anns[:max_results]:
            adj = a.get("adjunctUrl") or ""
            if not adj:
                continue
            pdf_url = _CNINFO_STATIC + adj.lstrip("/")
            sec = _strip_tags(a.get("secName") or "")
            title = _strip_tags(a.get("announcementTitle") or "")
            full_title = (sec + " " + title).strip() or title or sec
            text = ""
            try:
                pr = await cli.get(pdf_url)
                if pr.status_code == 200 and pr.content:
                    text = _extract_pdf_text(pr.content)
            except Exception as exc:  # noqa: BLE001
                log.debug("cninfo pdf fetch failed %s: %s", pdf_url, exc)
            # PDF 抽不到正文时退化为元数据(公司+标题仍是一手信号)
            if not text:
                text = f"{full_title}（巨潮资讯公告，PDF：{pdf_url}）"
            out.append({
                "ok": True, "url": pdf_url, "title": full_title[:200],
                "text": text, "fetched_at": time.time(), "source": "cninfo",
            })
        return out
    finally:
        if owns:
            await cli.aclose()


# ── 国家标准全文公开系统 ──────────────────────────────────────────────
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")          # 发布/实施日期 cell
_STATUS_WORDS = ("推标", "国标", "现行", "废止", "即将实施", "查看详细",
                 "查看", "详细", "强标")


def _extract_hcno(row: Any) -> str:
    """从行内 <a> 的 onclick/href 抽内部 hcno(newGbInfo 真正需要的 hash)。"""
    for a in row.css("a"):
        blob = (a.attributes.get("onclick") or "") + " " + (a.attributes.get("href") or "")
        m = (re.search(r"hcno=([0-9A-Za-z]+)", blob)
             or re.search(r"showInfo\w*\(\s*['\"]([0-9A-Za-z]+)", blob))
        if m:
            return m.group(1)
    return ""


def parse_openstd(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """解析 openstd std_list 表格 → [{std_no, name, hcno}]。全文在 JS viewer 后,
    只取元数据(标准号 + 名称 + 内部 hcno)。纯解析,可单测。

    name 选取要排除**日期 cell**(``2024-08-23 00:00:00.0`` 比真名长会误选)和
    状态词(推标/现行/查看详细)。hcno 来自行内 ``<a onclick>``,是 newGbInfo 真正
    需要的 hash;抽不到则退回 std_no(链接退化但元数据仍可用)。
    """
    out: list[dict[str, Any]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return out
    tree = HTMLParser(html)
    for row in tree.css("table tr"):
        if len(out) >= max_results:
            break
        tds = row.css("td")
        if len(tds) < 3:
            continue
        cells = [(td.text() or "").strip() for td in tds]
        # 标准号形如 GB/T 32895-2016;在某个 cell 里
        num = next((c for c in cells if re.search(r"GB[/ ]?T?\s*\d{3,}", c)), "")
        if not num:
            continue
        # 名称: 排除标准号/日期/状态词/纯数字后,取最长且含中文的 cell
        name = ""
        for c in cells:
            if not c or c == num or c.isdigit():
                continue
            if _DATE_RE.search(c) or c in _STATUS_WORDS:
                continue
            if not re.search(r"[一-鿿]", c):  # 名称必含中文
                continue
            if len(c) > len(name):
                name = c
        out.append({"std_no": num.strip(), "name": name.strip(),
                    "hcno": _extract_hcno(row)})
    return out


async def openstd_search(
    keyword: str, *, max_results: int = 4,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """国标系统搜索 → 标准元数据 passages(标准号+名称)。best-effort,失败返 []。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    owns = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True
    )
    try:
        try:
            resp = await cli.get(_OPENSTD_LIST, params={"p.p1": "2", "p.p2": kw})
            resp.raise_for_status()
            rows = parse_openstd(resp.text, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            log.debug("openstd search failed for %r: %s", kw, exc)
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            std_no = row["std_no"]
            name = row["name"] or std_no
            # newGbInfo 需要内部 hcno hash;抽到就用它,抽不到退回标准号(链接退化)。
            hcno = row.get("hcno") or std_no
            text = (f"国家标准 {std_no}：{name}。（来源：国家标准全文公开系统 "
                    f"openstd.samr.gov.cn，全文可在该系统在线查阅。）")
            out.append({
                "ok": True,
                "url": f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}",
                "title": f"{std_no} {name}"[:200],
                "text": text, "fetched_at": time.time(), "source": "openstd",
            })
        return out
    finally:
        if owns:
            await cli.aclose()


__all__ = ["direct_source_for", "cninfo_search", "openstd_search",
           "parse_openstd"]
