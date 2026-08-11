from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import ui_components as components


ROOT = Path(__file__).resolve().parents[1]


def test_long_page_outline_is_semantic_and_escaped() -> None:
    with patch.object(components.st, "markdown") as markdown:
        components.render_page_outline([
            ("settings-details", "评分细则"),
            ('unsafe\"anchor', "一组 <内容>"),
        ])

    rendered = markdown.call_args.args[0]
    assert '<nav class="yg-page-outline" aria-label="本页导航">' in rendered
    assert 'href="#settings-details"' in rendered
    assert 'href="#unsafe&quot;anchor"' in rendered
    assert "一组 &lt;内容&gt;" in rendered


def test_global_css_covers_focus_mobile_and_long_page_navigation() -> None:
    with patch.object(components.st, "markdown") as markdown:
        components.inject_css({"global": {}})

    css = markdown.call_args.args[0]
    assert ":focus-visible" in css
    assert ".yg-skip-link" in css
    assert ".yg-page-outline" in css
    assert "min-height:44px!important" in css
    assert ".st-key-library_filter_bar,.st-key-bangumi_filter_bar" in css
    assert "prefers-reduced-motion" in css


def test_navigation_updates_refreshable_view_state() -> None:
    state: dict[str, object] = {"edit_id": 9}
    query: dict[str, str] = {}
    with (
        patch.object(components.st, "session_state", state),
        patch.object(components.st, "query_params", query),
    ):
        components._navigate_to("评分设置")

    assert state["nav_page"] == "评分设置"
    assert state["_last_url_view"] == "评分设置"
    assert query["view"] == "评分设置"
    assert "edit_id" not in state


def test_app_installs_document_semantics_and_compacts_scoring_groups() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    for expected in (
        "doc.documentElement.lang = 'zh-CN'",
        "main.setAttribute('role', 'main')",
        "跳到主要内容",
        "button.setAttribute('aria-current', 'page')",
        "host.__ygA11yObserver",
        "st.query_params[\"view\"]",
        "group_tabs = st.tabs",
        'render_page_outline([',
    ):
        assert expected in source


def test_navigation_feedback_clears_without_dimming_the_page() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "ui_components.py").read_text(encoding="utf-8")

    assert "resetNavigationState" in app_source
    assert "classList.remove('yg-is-navigating')" in app_source
    assert "removeAttribute('aria-busy')" in app_source
    assert "setTimeout(resetNavigationState, 4500)" in app_source
    assert 'html.yg-is-navigating [data-testid="stMain"] {{opacity:.76' not in ui_source
    assert 'html.yg-is-navigating [data-testid="stMain"] {{opacity:1;}}' in ui_source
