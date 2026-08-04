from __future__ import annotations

import re
import zipfile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

from deskpet.tools import ppt_tools
from deskpet.workflows.definitions import ppt_pro_nodes
from deskpet.workflows.definitions.v1 import ppt_pro_initial_state


def test_new_ppt_run_defaults_to_editable_native_objects() -> None:
    state = ppt_pro_initial_state(
        topic=(
            "生成可编辑PPT；第3页用表格对比缺陷；"
            "第6页用可编辑柱状图展示覆盖率"
        ),
        run_id="run-editable",
        pages=8,
    )

    values = state["values"]
    assert values["editable_required"] is True
    assert values["image_mode"] is True
    assert values["full_page_images"] is False
    assert values["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart:bar"],
    }


def test_page_requirements_do_not_leak_from_later_validation_instructions() -> None:
    state = ppt_pro_initial_state(
        topic=(
            "第3页必须使用原生 PowerPoint 表格（4列5行）。\n"
            "第6页必须使用原生 PowerPoint 柱状图（chart）。\n"
            "逐页进行结构检查（验证表格/图表/标题/正文是否存在）。"
        ),
        run_id="run-page-local-requirements",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart:bar"],
    }


@pytest.mark.parametrize(
    "global_editability_tail",
    [
        "所有标题、正文、表格、图表都必须可编辑。",
        "全部元素必须保持原生可编辑。",
        "All titles, body text, tables and charts must be editable.",
    ],
)
def test_page_requirements_do_not_absorb_global_editability_tail(
    global_editability_tail: str,
) -> None:
    state = ppt_pro_initial_state(
        topic=(
            "第3页必须使用原生 PowerPoint 表格（4列5行）。\n"
            "第6页必须使用原生 PowerPoint 柱状图。\n"
            "第8页是结论页，必须完整保留四条可编辑结论。\n"
            f"{global_editability_tail}"
        ),
        run_id="run-global-editability-tail",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart:bar"],
    }


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (
            "第3页使用表格；第6页使用柱状图；第8页是结论页，总结核心观点；"
            "标题、正文、表格、图表全部必须可编辑",
            {"3": ["table"], "6": ["chart:bar"]},
        ),
        (
            "第3页使用表格；第6页使用柱状图；第8页是结论页。"
            "Every title, body text, table and chart must be editable",
            {"3": ["table"], "6": ["chart:bar"]},
        ),
        ("第8页所有表格图表保持可编辑", {}),
        ("第8页所有表格和图表都必须可编辑", {}),
        ("第8页现有表格和图表保持可编辑", {}),
        (
            "第8页所有表格和图表保持可编辑，并添加一个表格",
            {"8": ["table"]},
        ),
        (
            "第8页保持现有表格图表可编辑；另添加柱状图",
            {"8": ["chart:bar"]},
        ),
        ("第8页必须是 editable table", {"8": ["table"]}),
        ("第8页的表格和图表需要能编辑", {}),
        ("第8页不新增表格和图表，保持现有对象可编辑", {}),
        ("第8页的 table 和 chart 必须是 editable", {}),
        (
            "On slide 8, insert an editable table and chart",
            {"8": ["table", "chart"]},
        ),
        (
            "Page 8 must contain an editable table and chart",
            {"8": ["table", "chart"]},
        ),
        (
            "Slide 8 should include a table and line chart",
            {"8": ["table", "chart:line"]},
        ),
        (
            "On slide 8, keep existing charts editable and insert a table",
            {"8": ["table"]},
        ),
        (
            "Slide 8: all tables must remain editable; add a bar chart",
            {"8": ["chart:bar"]},
        ),
        (
            "Page 8 existing tables must be editable, then insert a new chart",
            {"8": ["chart"]},
        ),
    ],
)
def test_editability_guards_are_separate_from_object_creation(
    topic: str,
    expected: dict[str, list[str]],
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-editability-versus-creation",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == expected


@pytest.mark.parametrize(
    ("topic", "expected_image_mode", "expected_requirements"),
    [
        ("不能不使用图片", True, {}),
        ("不得不使用图片", True, {}),
        ("不要生成不含图片的PPT", True, {}),
        ("避免无图片的演示文稿", True, {}),
        ("禁止生成无图片幻灯片", True, {}),
        ("不要做无图PPT", True, {}),
        ("不要交付无图演示稿", True, {}),
        ("请勿生成不含图片的幻灯片", True, {}),
        ("我不接受无图PPT", True, {}),
        ("生成一个无图片的演示文稿", False, {}),
        ("制作无图PPT", False, {}),
        ("Generate image-free slides", False, {}),
        ("do not use images", False, {}),
        (
            "第8页原样保留文案：“不要使用图片”；另外添加柱状图",
            True,
            {"8": ["chart:bar"]},
        ),
        (
            "第8页不要把表格或图表转成图片；并添加表格",
            True,
            {"8": ["table"]},
        ),
        (
            "第8页保持现有图表可编辑，不要转成图片；添加表格",
            True,
            {"8": ["table"]},
        ),
        (
            "第8页原样保留：“不是不要使用图片”；添加表格",
            True,
            {"8": ["table"]},
        ),
        (
            "第8页完整保留以下文案：\n不要使用图片\n正文之后插入饼图",
            True,
            {"8": ["chart:pie"]},
        ),
        (
            "第8页表格不能是图片\n需要添加柱状图",
            True,
            {"8": ["chart:bar"]},
        ),
    ],
)
def test_image_guards_and_preserved_copy_do_not_hide_later_object_creation(
    topic: str,
    expected_image_mode: bool,
    expected_requirements: dict[str, list[str]],
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-image-guard-with-later-creation",
        pages=8,
        image_mode=True,
    )

    assert state["values"]["image_mode"] is expected_image_mode
    assert state["values"]["slide_requirements"] == expected_requirements


def test_editable_chart_does_not_accidentally_mean_editable_table() -> None:
    state = ppt_pro_initial_state(
        topic=(
            "第3页必须是 editable table，不能是图片。\n"
            "第6页必须是 editable chart，不能是图片。"
        ),
        run_id="run-editable-word-boundary",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart"],
    }


@pytest.mark.parametrize("length", [79, 80, 81, 160])
def test_template_text_replacement_preserves_complete_long_copy(length: int) -> None:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    shape = slide.shapes.add_textbox(
        Inches(1), Inches(1), Inches(5), Inches(1.5)
    )
    paragraph = shape.text_frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = "template"
    run.font.size = Pt(18)
    expected = "长" * length

    ppt_tools._set_text_keep_style(shape, [expected])

    assert shape.text == expected
    assert len(shape.text) == length


def test_real_request_ignores_negative_and_preserved_copy_object_mentions() -> None:
    state = ppt_pro_initial_state(
        topic=(
            "第3页必须使用原生4x5表格。\n"
            "第6页必须使用原生柱状图展示85/90/75/80；"
            "图表不能是图片，不能是表格。\n"
            "第8页是结论页，必须完整保留以下4条要点："
            "原生表格与图表确保交付物可编辑。\n"
            "第6页不要把 chart 误判为 table；"
            "第6页的 chart/table 混淆需要避免。"
        ),
        run_id="run-real-negative-copy-regression",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart:bar"],
    }


def test_actual_harness_request_disables_images_and_does_not_misread_conclusion() -> None:
    state = ppt_pro_initial_state(
        topic=(
            "生成一个新的 8 页、16:9、全部元素可编辑的中文 PowerPoint，"
            "主题为 DeskPet Agent Harness 架构与后续路线。"
            "第1页封面；第2页用分层图说明 Product、Harness、Execution Kernel；"
            "第3页必须是原生可编辑表格，比较 Text、Code、Voice 三入口；"
            "第4页解释 workflow_spawn、ProfileLaunchTicket、Workflow Child Run；"
            "第5页展示六项架构缺陷和修复优先级；"
            "第6页必须是原生可编辑柱状图，展示六项缺陷的 P0/P1/P2 数量；"
            "第7页展示已完成修复和测试证据；"
            "第8页必须恰好四条可编辑结论。"
            "不要使用图片，不要把表格或图表栅格化。"
        ),
        run_id="run-actual-harness-request",
        pages=8,
        image_mode=True,
    )

    assert state["values"]["image_mode"] is False
    assert state["values"]["slide_requirements"] == {
        "3": ["table"],
        "6": ["chart:bar"],
    }


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("请勿使用任何图像", False),
        ("不是不要使用图片", True),
        ("not without images", True),
        ("no image rasterization", True),
        ("no image-based rasterization", True),
        ("no image–based rasterization", True),
        ("No image‑based rasterization", True),
        ("No image―based rasterization", True),
        ("No image−based rasterization", True),
        ("I do not want a deck without images", True),
        ("not a presentation without images", True),
        ("don't make it without images", True),
        ("never produce slides without images", True),
        ("This is not meant to be without images", True),
        ("We cannot deliver a deck without images", True),
        ("Never ship a presentation without images", True),
        ("no images; rasterize the table natively", False),
        ("slide 8 must preserve the following copy: no images", True),
        ("第8页原样保留文案：“请勿使用任何图像”", True),
        ("Preserve exactly: “no images”", True),
        ("Preserve this exact sentence — ‘without images’", True),
        ("原样保留：“不要使用图片”", True),
        ("正文写明【请勿使用任何图像】，不要把它当成要求。", True),
        (
            "第8页必须完整保留以下文案：不是要求，只需写出‘不要使用图片’",
            True,
        ),
        ("第8页标题写“不要使用图片”，正文说明这是引用。", True),
        ("Slide 8 quotes “no images” as a slogan.", True),
        ("It shouldn't be without images.", True),
        ("Avoid a deck without images.", True),
        ("The deck mustn't be without images.", True),
        ("A deck without images is not acceptable.", True),
        ("We refuse to create slides without images.", True),
        ("We won't accept slides without images.", True),
        ("设计要求：“不要使用图片”。", False),
        ("The requirement is “no images”.", False),
        ('Please follow this rule: "no images".', False),
        ("第8页标题写「不要使用图片」，仅作为文案。", True),
        ("设计规则是「不要使用图片」。", False),
        ("限制条件：“不要使用图片”。", False),
    ],
)
def test_natural_no_image_phrases_respect_scope_and_double_negation(
    topic: str,
    expected: bool,
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-natural-no-image",
        pages=3,
        image_mode=True,
    )

    assert state["values"]["image_mode"] is expected


@pytest.mark.parametrize(
    "topic",
    [
        "第8页不得将表格或图表栅格化。",
        "第8页表格和图表均不得栅格化。",
        "第8页 table and chart must not be rasterized.",
        "第8页 neither table nor chart should be rasterized.",
        "第8页 table, chart — neither may be rasterized.",
        "第8页表格与图表保持原生可编辑，不得转成图片。",
        "第8页只需保证表格与图表可编辑且不转图片。",
        "第8页 keep table and chart native and editable; never convert them to images.",
        "第8页表格/图表需要保持原生；本页不要求新增它们。",
    ],
)
def test_non_rasterization_guards_do_not_create_slide_objects(topic: str) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-non-rasterization-guard",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == {}


def test_topic_image_mode_false_overrides_spawn_default() -> None:
    state = ppt_pro_initial_state(
        topic="生成可编辑演示文稿；image_mode=false；第3页使用表格。",
        run_id="run-image-mode-false",
        pages=8,
        image_mode=True,
    )

    assert state["values"]["image_mode"] is False
    assert state["values"]["slide_requirements"] == {"3": ["table"]}


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("第3页避免使用表格，改用柱状图", {"3": ["chart:bar"]}),
        ("第3页不推荐表格，必须柱状图", {"3": ["chart:bar"]}),
        (
            "第3页 do not classify chart as table; use bar chart",
            {"3": ["chart:bar"]},
        ),
        (
            "第3页 do not confuse chart with table; use pie chart",
            {"3": ["chart:pie"]},
        ),
        (
            "第3页 must use bar chart; do not confuse chart and table",
            {"3": ["chart:bar"]},
        ),
        ("第3页使用表格区分不同方案", {"3": ["table"]}),
        ("第3页 use table for confusion matrix", {"3": ["table"]}),
        (
            "第3页 use bar chart for misclassification rates",
            {"3": ["chart:bar"]},
        ),
        (
            "第3页 use pie chart for mistake categories",
            {"3": ["chart:pie"]},
        ),
        (
            "第3页必须完整保留以下内容：正文提到 chart/table；并使用表格",
            {"3": ["table"]},
        ),
        (
            "第3页 must preserve following text: chart table; use bar chart",
            {"3": ["chart:bar"]},
        ),
        (
            "第3页使用表格；第3页不要表格，改用柱状图",
            {"3": ["chart:bar"]},
        ),
        ("第3页使用表格；第3页不要表格", {}),
        ("第3页 chart is not needed; use table", {"3": ["table"]}),
        ("第3页 table is not required; use bar chart", {"3": ["chart:bar"]}),
        ("第3页 remove the table; use bar chart", {"3": ["chart:bar"]}),
        ("第3页 avoid using table; use line chart", {"3": ["chart:line"]}),
        ("第3页 instead of table, use pie chart", {"3": ["chart:pie"]}),
        (
            "第3页必须完整保留以下内容：使用表格能提升可读性",
            {},
        ),
        (
            "第3页 must preserve following text: add chart to explain growth",
            {},
        ),
        (
            "第3页必须完整保留以下内容：使用表格能提升可读性；"
            "正文之后创建柱状图",
            {"3": ["chart:bar"]},
        ),
        (
            "第3页 bar chart of classification accuracy",
            {"3": ["chart:bar"]},
        ),
        ("第3页 classification chart", {"3": ["chart"]}),
        ("第3页 classify options using table", {"3": ["table"]}),
        ("第3页 use bar chart; 第3页 remove chart", {}),
    ],
)
def test_requirement_parser_handles_negation_copy_and_diagnostic_words(
    topic, expected,
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-parser-strict-regressions",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == expected


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("do not set image_mode=false", True),
        ("不要设置 image_mode=false", True),
        ("image mode is false", False),
        ("image_mode is off", False),
        ("never set image_mode=false", True),
        ("不应设置 image_mode=false", True),
    ],
)
def test_image_mode_false_understands_copula_and_ignores_negated_setting(
    topic, expected,
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-image-mode-natural-language",
        pages=8,
        image_mode=True,
    )

    assert state["values"]["image_mode"] is expected


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (
            "第3页表格；第6页柱状图；后续逐页检查表格/图表",
            {"3": ["table"], "6": ["chart:bar"]},
        ),
        ("第4页 flowcharting method", {}),
        ("第4页 chartreuse theme", {}),
        (
            "第3页 editable table；第3页 bar chart",
            {"3": ["table", "chart:bar"]},
        ),
        (
            "第3页 editable table; also add bar chart",
            {"3": ["table", "chart:bar"]},
        ),
        (
            "第3页表格；同时添加柱状图",
            {"3": ["table", "chart:bar"]},
        ),
        (
            "第3页先说明背景。必须使用表格",
            {"3": ["table"]},
        ),
        (
            "第6页柱状图：逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart: every slide validates table/chart",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图 后续逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图\n- 逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第4页 baseline chart",
            {"4": ["chart"]},
        ),
        ("第4页 foobar charting", {}),
        ("第4页 pie chartreuse theme", {}),
        (
            "第6页柱状图；后续需要逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图；接下来逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart; then validate every slide table/chart",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart; all pages must validate table/chart",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图；各页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图；每个页面检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图；每张幻灯片检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图\n+ 逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图\n> 逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图\n- [ ] 逐页检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第3页主题：所有页面表格设计",
            {"3": ["table"]},
        ),
        ("第4页 bar chart2", {}),
        ("第4页 line chart_data", {}),
        ("第4页 table2", {}),
        (
            "第4页 bar chart and line chart",
            {"4": ["chart:bar", "chart:line"]},
        ),
        (
            "第6页柱状图；所有页面，检查表格/图表",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart; all slides, validate table/chart",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页柱状图；确保每页都有表格",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart; ensure every slide has a table",
            {"6": ["chart:bar"]},
        ),
        (
            "第6页 bar chart afterwards validate every slide table/chart",
            {"6": ["chart:bar"]},
        ),
        ("第4页 bar-chart", {"4": ["chart:bar"]}),
        (
            "第3页主题：审查所有页面的表格设计",
            {"3": ["table"]},
        ),
        (
            "第3页标题为：审查所有页面的表格设计",
            {"3": ["table"]},
        ),
        (
            "第3页主题说明：审查所有页面的表格设计",
            {"3": ["table"]},
        ),
        (
            "第3页内容为：每页表格检查清单",
            {"3": ["table"]},
        ),
        (
            "第3页 content: review all pages table design",
            {"3": ["table"]},
        ),
    ],
)
def test_page_requirement_parser_honors_delimiters_words_and_repeated_pages(
    topic, expected,
) -> None:
    state = ppt_pro_initial_state(
        topic=topic,
        run_id="run-page-requirement-boundaries",
        pages=8,
    )

    assert state["values"]["slide_requirements"] == expected


def test_explicit_full_page_mode_remains_available() -> None:
    state = ppt_pro_initial_state(
        topic="生成每页一张完整海报图，不需要编辑",
        run_id="run-poster",
        pages=2,
        full_page_images=True,
    )

    values = state["values"]
    assert values["editable_required"] is False
    assert values["image_mode"] is True
    assert values["full_page_images"] is True


def test_editable_gate_rejects_picture_only_deck_even_with_valid_package(
    tmp_path,
) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 900), (12, 24, 48)).save(image_path)
    deck_path = tmp_path / "flattened.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(
        str(image_path), 0, 0, width=deck.slide_width, height=deck.slide_height
    )
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
    )

    assert result["passed"] is False
    assert result["shape_inventory"] == [
        {
            "page": 1,
            "pictures": 1,
            "text_shapes": 0,
            "visible_text_shapes": 0,
            "tables": 0,
            "charts": 0,
            "chart_types": [],
        }
    ]
    assert result["hard_failures"] == [
        {"code": "ppt_flattened_slide_forbidden", "page": 1}
    ]


def test_editable_gate_rejects_off_canvas_decoy_text(tmp_path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 900), (12, 24, 48)).save(image_path)
    deck_path = tmp_path / "flattened-with-decoy.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(
        str(image_path), 0, 0, width=deck.slide_width, height=deck.slide_height
    )
    slide.shapes.add_textbox(
        -Inches(2), -Inches(2), Inches(0.1), Inches(0.1)
    ).text = "."
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
    )

    assert result["passed"] is False
    assert result["shape_inventory"][0]["text_shapes"] == 1
    assert result["shape_inventory"][0]["visible_text_shapes"] == 0
    assert result["hard_failures"] == [
        {"code": "ppt_flattened_slide_forbidden", "page": 1}
    ]


def test_editable_gate_rejects_picture_with_title_but_baked_in_body(
    tmp_path,
) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 900), (12, 24, 48)).save(image_path)
    deck_path = tmp_path / "title-only-overlay.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(
        str(image_path), 0, 0, width=deck.slide_width, height=deck.slide_height
    )
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "Editable title"
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "bullet",
                    "title": "Editable title",
                    "bullets": ["First editable fact", "Second editable fact"],
                }
            }
        ],
    )

    assert result["passed"] is False
    missing = [
        item
        for item in result["hard_failures"]
        if item["code"] == "ppt_expected_editable_text_missing"
    ]
    assert [item["field"] for item in missing] == ["bullet", "bullet"]


def test_editable_gate_accepts_metric_split_into_native_callout(tmp_path) -> None:
    deck_path = tmp_path / "native-metric-callout.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "测试证据"
    slide.shapes.add_textbox(
        Inches(1), Inches(1.5), Inches(7), Inches(0.8)
    ).text = "基于轨迹的方法带来约 的性能提升"
    slide.shapes.add_textbox(
        Inches(6.5), Inches(1.3), Inches(1), Inches(0.6)
    ).text = "6.3%"
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "bullet",
                    "title": "测试证据",
                    "bullets": ["基于轨迹的方法带来约 6.3% 的性能提升"],
                }
            }
        ],
    )

    assert result["passed"] is True


def test_editable_gate_accepts_single_digit_native_callout(tmp_path) -> None:
    deck_path = tmp_path / "native-single-digit-callout.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "测试证据"
    slide.shapes.add_textbox(
        Inches(1), Inches(1.5), Inches(7), Inches(0.8)
    ).text = "共有项关键风险"
    slide.shapes.add_textbox(
        Inches(6.5), Inches(1.3), Inches(1), Inches(0.6)
    ).text = "1"
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "bullet",
                    "title": "测试证据",
                    "bullets": ["共有1项关键风险"],
                }
            }
        ],
    )

    assert result["passed"] is True


def test_editable_gate_still_rejects_truncated_prose_around_callout(
    tmp_path,
) -> None:
    deck_path = tmp_path / "truncated-native-metric-callout.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "测试证据"
    slide.shapes.add_textbox(
        Inches(1), Inches(1.5), Inches(7), Inches(0.8)
    ).text = "得分从 跃升至 66.5%，验证了基础设施层的..."
    slide.shapes.add_textbox(
        Inches(6.5), Inches(1.3), Inches(1), Inches(0.6)
    ).text = "52.8%"
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "bullet",
                    "title": "测试证据",
                    "bullets": [
                        "得分从 52.8% 跃升至 66.5%，"
                        "验证了基础设施层的价值"
                    ],
                }
            }
        ],
    )

    assert result["passed"] is False
    assert {
        "code": "ppt_expected_editable_text_missing",
        "page": 1,
        "field": "bullet",
    } in result["hard_failures"]


@pytest.mark.parametrize(
    ("expected", "actual_shapes", "present"),
    [
        (
            "性能提升 6.3% 且保持稳定",
            ["性能提升且保持稳定", "6.3%"],
            True,
        ),
        (
            "Performance improved by 12.5% year over year",
            ["Performance improved by year over year", "12.5%"],
            True,
        ),
        (
            "从 6.3% 提升到 6.3% 并保持稳定",
            ["从提升到并保持稳定", "6.3%"],
            False,
        ),
        (
            "从 6.3% 提升到 6.3% 并保持稳定",
            ["从提升到并保持稳定", "6.3%", "6.3%"],
            True,
        ),
        (
            "性能提升 6.3% 且保持稳定",
            ["性能提升且保持稳定", "指标为 6.3%"],
            False,
        ),
        (
            "性能提升 6.3% 且保持稳定",
            ["且保持稳定", "6.3%"],
            False,
        ),
        (
            "性能提升 6.3% 且保持稳定",
            ["性能提升", "6.3%"],
            False,
        ),
        (
            "从 6.3% 提升到 12.5%",
            ["从提升到", "6.3%", "12.5%"],
            True,
        ),
        (
            "从 6.3% 提升到 12.5%",
            ["从提升到", "12.5%", "6.3%"],
            False,
        ),
        ("A 6.3% B", ["B A", "6.3%"], False),
        ("引用“42”作为样本编号", ["引用作为样本编号", "42"], True),
        ("2025 年目标保持稳定", ["年目标保持稳定", "2025"], True),
        (
            "得分从 52.8% 跃升至 66.5%，验证了基础设施层的价值",
            ["得分从跃升至 66.5%，验证了基础设施层的", "52.8%"],
            False,
        ),
        (
            "Score rose from 52.8% to 66.5%, proving infrastructure value",
            ["Score rose from to 66.5%, proving infrastructure", "52.8%"],
            False,
        ),
        (
            "P1缺陷（2项）：核心模块过度膨胀",
            ["P1缺陷（项）：核心模块过度膨胀", "2"],
            True,
        ),
        (
            "P0/P1缺陷（1项）：Voice绕过ProductTurnPreparer",
            ["P0/P1缺陷（项）：Voice绕过ProductTurnPreparer", "1"],
            True,
        ),
        (
            "P0/P1缺陷（1项）：Voice绕过ProductTurnPreparer，影响安全与一致性",
            ["P0/P1缺陷（项）：Voice绕过ProductTurnPreparer", "1"],
            False,
        ),
        (
            "P0/P1缺陷：Voice绕过ProductTurnPreparer",
            ["P/P缺陷：Voice绕过ProductTurnPreparer", "0", "1"],
            False,
        ),
    ],
)
def test_editable_callout_metric_attack_matrix(
    expected: str,
    actual_shapes: list[str],
    present: bool,
) -> None:
    normalized_expected = ppt_pro_nodes._normalized_ppt_text(expected)
    normalized_actual = [
        ppt_pro_nodes._normalized_ppt_text(item) for item in actual_shapes
    ]

    assert (
        ppt_pro_nodes._editable_copy_present(
            normalized_expected,
            normalized_actual,
        )
        is present
    )


@pytest.mark.parametrize(
    ("callouts", "passed"),
    [
        (
            [
                ("12.5%", Inches(6.0)),
                ("6.3%", Inches(2.0)),
            ],
            True,
        ),
        (
            [
                ("6.3%", Inches(6.0)),
                ("12.5%", Inches(2.0)),
            ],
            False,
        ),
    ],
)
def test_editable_metric_callouts_follow_visual_order_not_z_order(
    tmp_path,
    callouts: list[tuple[str, int]],
    passed: bool,
) -> None:
    deck_path = tmp_path / f"metric-order-{passed}.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "指标变化"
    slide.shapes.add_textbox(
        Inches(1), Inches(2.2), Inches(7), Inches(0.8)
    ).text = "从提升到"
    for text, left in callouts:
        slide.shapes.add_textbox(
            left, Inches(1.4), Inches(1), Inches(0.6)
        ).text = text
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "bullet",
                    "title": "指标变化",
                    "bullets": ["从 6.3% 提升到 12.5%"],
                }
            }
        ],
    )

    assert result["passed"] is passed


def test_editable_gate_rejects_image_full_layout_even_with_native_title(
    tmp_path,
) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 900), (12, 24, 48)).save(image_path)
    deck_path = tmp_path / "image-full.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(
        str(image_path), 0, 0, width=deck.slide_width, height=deck.slide_height
    )
    slide.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(7), Inches(0.6)
    ).text = "Editable title"
    deck.save(deck_path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[
            {
                "slide": {
                    "layout": "image_full",
                    "title": "Editable title",
                    "bullets": ["Baked body"],
                }
            }
        ],
    )

    assert result["passed"] is False
    assert {
        "code": "ppt_editable_layout_forbidden",
        "page": 1,
        "layout": "image_full",
    } in result["hard_failures"]


def test_conclusion_slide_keeps_every_editable_bullet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ppt_tools, "_default_template", lambda: None)
    output = tmp_path / "conclusion.pptx"
    conclusion = {
        "layout": "image",
        "title": "Conclusion",
        "bullets": [
            "Acceptance result is ready",
            "Delivery materials are complete",
            "Thanks to the delivery team",
            "Continue monitoring recovery metrics",
        ],
    }

    result = ppt_tools.ppt_create(
        [conclusion],
        theme="dark",
        title="Editable conclusion",
        output_path=str(output),
        skip_image_gen=True,
    )
    quality = ppt_pro_nodes.inspect_rendered_deck(
        str(output),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[{"slide": conclusion}],
    )

    assert result["ok"] is True
    assert quality["passed"] is True
    rendered = Presentation(str(output))
    actual_text = "\n".join(
        shape.text
        for shape in rendered.slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    for bullet in conclusion["bullets"]:
        assert bullet in actual_text


def test_native_table_and_bar_chart_are_rendered_and_enforced(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(ppt_tools, "_default_template", lambda: None)
    output = tmp_path / "editable.pptx"
    chart_slide = {
        "layout": "chart",
        "title": "测试覆盖率",
        "bullets": [
            "单元测试覆盖核心行为",
            "契约测试保护边界",
            "恢复测试保护持久状态",
            "视觉检查保护页面可读性",
        ],
        "chart": {
            "type": "bar",
            "categories": ["单元", "契约", "恢复", "视觉"],
            "series": [{"name": "示例值", "data": [85, 90, 75, 80]}],
        },
    }
    result = ppt_tools.ppt_create(
        [
            {
                "layout": "table",
                "title": "缺陷对比",
                "table": {
                    "header": True,
                    "rows": [
                        ["缺陷", "影响", "验证"],
                        ["路由漂移", "错用工具", "契约测试"],
                    ],
                },
            },
            chart_slide,
        ],
        theme="dark",
        title="Editable",
        output_path=str(output),
        skip_image_gen=True,
    )
    assert result["ok"] is True

    quality = ppt_pro_nodes.inspect_rendered_deck(
        str(output),
        expected_slide_count=2,
        editable_required=True,
        slide_requirements={"1": ["table"], "2": ["chart:bar"]},
        expected_slides=[{}, {"slide": chart_slide}],
    )

    assert quality["passed"] is True
    assert quality["shape_inventory"][0]["tables"] == 1
    assert quality["shape_inventory"][1]["charts"] == 1
    with zipfile.ZipFile(output) as archive:
        chart_xml = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/charts/chart") and name.endswith(".xml")
        )
    assert not re.search(r'<c:(?:axId|crossAx) val="-', chart_xml)
    axis_ids = re.findall(r'<c:axId val="(\d+)"', chart_xml)
    cross_axis_ids = re.findall(r'<c:crossAx val="(\d+)"', chart_xml)
    assert axis_ids
    assert set(axis_ids) == set(cross_axis_ids)
    visible_text = "\n".join(
        shape.text
        for shape in Presentation(output).slides[1].shapes
        if getattr(shape, "has_text_frame", False)
    )
    for bullet in chart_slide["bullets"]:
        assert bullet in visible_text


def test_editable_visual_revision_changes_real_slide_output(tmp_path) -> None:
    image_path = tmp_path / "background.png"
    Image.new("RGB", (1600, 900), (12, 32, 72)).save(image_path)
    plain_path = tmp_path / "plain.pptx"
    revised_path = tmp_path / "revised.pptx"
    common = {
        "layout": "bullet",
        "title": "Harness revision",
        "bullets": ["First long editable point", "Second long editable point"],
        "image_path": str(image_path),
    }

    assert ppt_tools.ppt_create(
        [common],
        output_path=str(plain_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]
    assert ppt_tools.ppt_create(
        [{**common, "image_variant": "top", "font_scale": 0.81}],
        output_path=str(revised_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    with zipfile.ZipFile(plain_path) as archive:
        plain_xml = archive.read("ppt/slides/slide1.xml")
    with zipfile.ZipFile(revised_path) as archive:
        revised_xml = archive.read("ppt/slides/slide1.xml")
    assert revised_xml != plain_xml

    revised = Presentation(revised_path)
    visible_text = "\n".join(
        shape.text
        for shape in revised.slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    assert "First long editable point" in visible_text
    assert "Second long editable point" in visible_text


def test_bullet_layout_preserves_long_parenthetical_count_copy(tmp_path) -> None:
    deck_path = tmp_path / "long-parenthetical-count-copy.pptx"
    long_bullet = (
        "P0/P1缺陷（1项）：Voice绕过ProductTurnPreparer，直接影响用户入口的"
        "历史、Memory、Context OS、Profile Catalog 和请求级工具快照的一致性"
    )
    slide = {
        "layout": "bullet",
        "title": "缺陷优先级与影响面分析",
        "bullets": [
            long_bullet,
            "P1缺陷（2项）：核心模块过度膨胀、核心层依赖方向不干净",
            (
                "P2缺陷（3项）：workflow.db→state.db投影一致性窗口、"
                "跨Run指代仍是启发式、兼容代码与现行代码混居"
            ),
            "缺陷分布呈金字塔型，高风险缺陷数量少但影响面广，已优先闭环处理",
        ],
    }

    assert ppt_tools.ppt_create(
        [slide],
        output_path=str(deck_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    visible_text = "\n".join(
        shape.text
        for shape in Presentation(deck_path).slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    assert long_bullet in visible_text
    gate = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[{"slide": slide}],
    )
    assert gate["passed"] is True


def test_dense_bullet_layout_preserves_every_long_editable_paragraph(tmp_path) -> None:
    deck_path = tmp_path / "dense-editable-prose.pptx"
    bullets = [
        (
            "依赖倒置：ProductTurnPreparer 与 Harness 通过抽象端口协作，"
            "具体 Workflow 可以独立替换和测试，不再反向控制核心入口。"
        ),
        (
            "模块拆分：将 ReAct 循环、恢复策略和工具契约拆成边界清晰的模块，"
            "修改其中一块时不必跨越巨型文件，也不会把产品策略重新带回执行核心。"
        ),
        (
            "Voice 当前暂时禁用，尚未纳入统一入口；后续 Realtime 接入必须经过 "
            "ProductTurnPreparer，继续获得一致的历史、Memory、Context OS、"
            "Profile Catalog 和请求级工具快照。"
        ),
        (
            "六项修复已经完成，并通过自动化测试与真实端到端操作共同验证，"
            "所有正文必须作为原生 PowerPoint 文本完整保留且可继续编辑。"
        ),
    ]
    slide = {
        "layout": "bullet",
        "title": "架构改进要点",
        "bullets": bullets,
    }

    assert ppt_tools.ppt_create(
        [slide],
        output_path=str(deck_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    visible_text = "\n".join(
        shape.text
        for shape in Presentation(deck_path).slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    for bullet in bullets:
        assert bullet in visible_text
    gate = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[{"slide": slide}],
    )
    assert gate["passed"] is True


@pytest.mark.parametrize(
    ("title", "repeat"),
    [
        ("Dense prose", 14),
        ("Conclusion", 7),
    ],
)
def test_six_dense_bullets_are_never_shortened(
    tmp_path, title: str, repeat: int
) -> None:
    deck_path = tmp_path / f"{title.lower().replace(' ', '-')}.pptx"
    bullets = [
        (
            f"Editable paragraph {index}: "
            + "complete architecture evidence remains native and reviewable; "
            * repeat
        ).strip()
        for index in range(1, 7)
    ]
    slide = {
        "layout": "bullet",
        "title": title,
        "bullets": bullets,
    }

    assert ppt_tools.ppt_create(
        [slide],
        output_path=str(deck_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    visible_text = "\n".join(
        shape.text
        for shape in Presentation(deck_path).slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    for bullet in bullets:
        assert bullet in visible_text
    assert "..." not in visible_text
    gate = ppt_pro_nodes.inspect_rendered_deck(
        str(deck_path),
        expected_slide_count=1,
        editable_required=True,
        expected_slides=[{"slide": slide}],
    )
    assert gate["passed"] is True


def test_dense_chart_notes_get_room_and_visual_revision_never_enlarges_text(
    tmp_path,
) -> None:
    plain_path = tmp_path / "dense-chart-plain.pptx"
    revised_path = tmp_path / "dense-chart-revised.pptx"
    bullets = [
        "根据优先级矩阵对六项缺陷进行统计：P0/P1 共 1 项、P1 共 2 项、P2 共 3 项，确保修复路线合理性",
        "P0/P1 缺陷数量为 1：Voice 绕过 ProductTurnPreparer，兼具 P0 紧急性与 P1 重要性",
        "P1 缺陷数量为 2：核心模块过度膨胀与核心层依赖方向不干净，计划通过模块拆分与依赖倒置解决",
        "P2 缺陷数量为 3：投影一致性窗口、跨 Run 启发式指代与兼容代码混居，纳入长期技术债管理",
    ]
    common = {
        "layout": "chart",
        "title": "架构缺陷优先级分布（P0/P1、P1、P2）",
        "bullets": bullets,
        "chart": {
            "type": "bar",
            "categories": ["P0/P1", "P1", "P2"],
            "series": [{"name": "缺陷数量", "data": [1, 2, 3]}],
        },
    }

    assert ppt_tools.ppt_create(
        [common],
        output_path=str(plain_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]
    assert ppt_tools.ppt_create(
        [{**common, "font_scale": 0.9}],
        output_path=str(revised_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    def _notes_shape_and_sizes(path):
        presentation = Presentation(path)
        notes_shape = next(
            shape
            for shape in presentation.slides[0].shapes
            if getattr(shape, "has_text_frame", False)
            and bullets[0] in shape.text
        )
        sizes = [
            run.font.size.pt
            for paragraph in notes_shape.text_frame.paragraphs
            for run in paragraph.runs
            if run.font.size is not None
        ]
        return presentation, notes_shape, sizes

    plain_presentation, plain_notes, plain_sizes = _notes_shape_and_sizes(plain_path)
    _, revised_notes, revised_sizes = _notes_shape_and_sizes(revised_path)
    assert plain_notes.top.inches < 3
    assert plain_notes.height.inches >= 2.2
    assert plain_notes.top + plain_notes.height <= plain_presentation.slide_height
    assert revised_notes.height == plain_notes.height
    assert plain_sizes and revised_sizes
    assert max(revised_sizes) <= max(plain_sizes)
    assert min(revised_sizes) <= min(plain_sizes)


def test_editable_table_visual_revision_scales_cell_text(tmp_path) -> None:
    plain_path = tmp_path / "table-plain.pptx"
    revised_path = tmp_path / "table-revised.pptx"
    rows = [
        ["Priority", "Defect", "Impact", "Owner", "Status"],
        *[
            [
                f"P{index % 3}",
                f"Architecture defect {index}",
                "Dense editable explanation that may wrap",
                "Harness",
                "Fixed",
            ]
            for index in range(1, 7)
        ],
    ]
    common = {
        "layout": "table",
        "title": "Architecture defects",
        "table": {"header": True, "rows": rows},
    }

    assert ppt_tools.ppt_create(
        [common],
        output_path=str(plain_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]
    assert ppt_tools.ppt_create(
        [{**common, "font_scale": 0.81}],
        output_path=str(revised_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    plain_table = next(
        shape.table
        for shape in Presentation(plain_path).slides[0].shapes
        if getattr(shape, "has_table", False)
    )
    revised_table = next(
        shape.table
        for shape in Presentation(revised_path).slides[0].shapes
        if getattr(shape, "has_table", False)
    )

    def _cell_font_points(table) -> list[float]:
        return [
            run.font.size.pt
            for row in table.rows
            for cell in row.cells
            for paragraph in cell.text_frame.paragraphs
            for run in paragraph.runs
            if run.font.size is not None
        ]

    plain_sizes = _cell_font_points(plain_table)
    revised_sizes = _cell_font_points(revised_table)
    assert plain_sizes
    assert revised_sizes
    assert max(plain_sizes) == 13
    assert min(plain_sizes) == 12
    assert max(revised_sizes) == 10
    assert min(revised_sizes) == 9
    assert [
        cell.text for row in revised_table.rows for cell in row.cells
    ] == [
        cell.text for row in plain_table.rows for cell in row.cells
    ]


@pytest.mark.parametrize("layout", ["chart", "toc"])
def test_change_variant_changes_chart_and_toc_slide_output(
    tmp_path, monkeypatch, layout
) -> None:
    monkeypatch.setattr(ppt_tools, "_default_template", lambda: None)
    common = {
        "layout": layout,
        "title": f"{layout.title()} revision",
        "bullets": ["Alpha", "Beta", "Gamma", "Delta"],
    }
    if layout == "chart":
        common["chart"] = {
            "type": "bar",
            "categories": ["A", "B", "C", "D"],
            "series": [{"name": "Score", "data": [85, 90, 75, 80]}],
        }

    plain_path = tmp_path / f"{layout}-plain.pptx"
    revised_path = tmp_path / f"{layout}-revised.pptx"
    assert ppt_tools.ppt_create(
        [common],
        output_path=str(plain_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]
    assert ppt_tools.ppt_create(
        [{**common, "image_variant": "top"}],
        output_path=str(revised_path),
        skip_image_gen=True,
        force_fromscratch=True,
    )["ok"]

    with zipfile.ZipFile(plain_path) as archive:
        plain_xml = archive.read("ppt/slides/slide1.xml")
    with zipfile.ZipFile(revised_path) as archive:
        revised_xml = archive.read("ppt/slides/slide1.xml")
    assert revised_xml != plain_xml

    visible_text = "\n".join(
        shape.text
        for shape in Presentation(revised_path).slides[0].shapes
        if getattr(shape, "has_text_frame", False)
    )
    for bullet in common["bullets"]:
        assert bullet in visible_text


def test_native_object_requirements_are_page_specific(tmp_path) -> None:
    path = tmp_path / "wrong-pages.pptx"
    deck = Presentation()
    first = deck.slides.add_slide(deck.slide_layouts[6])
    first.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "表格页"
    second = deck.slides.add_slide(deck.slide_layouts[6])
    second.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "图表页"
    deck.save(path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(path),
        expected_slide_count=2,
        editable_required=True,
        slide_requirements={"1": ["table"], "2": ["chart:bar"]},
    )

    assert result["passed"] is False
    assert {item["code"] for item in result["hard_failures"]} == {
        "ppt_required_table_missing",
        "ppt_required_chart_missing",
    }


def test_native_object_requirement_cannot_target_missing_page(tmp_path) -> None:
    path = tmp_path / "one-page.pptx"
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = (
        "Editable page"
    )
    deck.save(path)

    result = ppt_pro_nodes.inspect_rendered_deck(
        str(path),
        expected_slide_count=1,
        editable_required=True,
        slide_requirements={"2": ["table"]},
    )

    assert result["passed"] is False
    assert {"code": "ppt_required_page_out_of_range", "page": "2"} in result[
        "hard_failures"
    ]
