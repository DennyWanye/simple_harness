"""阶段 7 收窄的失败路径：工具目录里由宽捕获收窄成具体异常的几处，原来要吞的失败仍被吞掉。"""

from __future__ import annotations

import sys

from deskpet.tools import browser_use_tool, ppt_template_picker, research_sources, search_provider


def test_load_job_returns_none_for_corrupt_job_file(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_use_tool, "_jobs_dir", lambda: tmp_path)
    (tmp_path / "job-1.json").write_text("{not json", encoding="utf-8")

    assert browser_use_tool._load_job("job-1") is None


def test_parse_pick_id_falls_back_when_json_id_is_not_a_number():
    assert ppt_template_picker._parse_pick_id('{"id": "abc"} 选 7', {7}) == 7
    assert ppt_template_picker._parse_pick_id('{"id": null}', {7}) is None


def test_parse_pick_id_skips_token_too_long_for_int():
    huge = "9" * 5000  # 超过整数转换的位数上限，int() 抛 ValueError
    assert ppt_template_picker._parse_pick_id(f"{huge} 然后 3", {3}) == 3


def test_extract_pdf_text_without_pypdf_returns_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)

    assert research_sources._extract_pdf_text(b"%PDF-1.4") == ""


def test_clean_url_keeps_url_that_cannot_be_parsed():
    url = "http://[::1/l/?uddg=https%3A%2F%2Fexample.com"

    assert search_provider._clean_url(url) == url
