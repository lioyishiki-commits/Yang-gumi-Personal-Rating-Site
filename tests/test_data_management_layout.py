from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_data_management_groups_actions_into_compact_panels() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    for key in (
        'key="data_share_panel"',
        'key="data_transfer_panel"',
        'key="data_maintenance_panel"',
        'key="data_appearance_panel"',
    ):
        assert key in source
    assert 'with st.expander("从本机备份恢复", expanded=False)' in source
    assert 'with st.expander("加载外部数据库", expanded=False)' in source
    assert "export_top = st.columns(2" in source
    assert "export_bottom = st.columns(2" in source
    assert 'key="data_share_metrics"' in source
    assert 'key="data_transfer_metrics"' in source
    assert 'caption="扫码远程查看", width=220' in source


def test_data_management_panels_have_scoped_layout_styles() -> None:
    source = (ROOT / "ui_components.py").read_text(encoding="utf-8")

    assert ".st-key-data_share_panel" in source
    assert ".st-key-data_transfer_panel" in source
    assert ".st-key-data_maintenance_panel" in source
    assert ".st-key-data_appearance_panel" in source
    assert ".st-key-data_share_metrics" in source
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in source
    assert 'width:220px!important' in source
