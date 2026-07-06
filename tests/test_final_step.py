import unittest
from pathlib import Path

import seasonal_service as seasonal


class FinalStepTest(unittest.TestCase):
    def test_unwatched_display_gate_accepts_japanese_tv(self):
        item = {"type": 2, "name": "テストアニメ", "platform": "TV", "tags": [{"name": "日本动画"}]}
        self.assertTrue(seasonal.is_displayable_japanese_seasonal_anime(item))
        self.assertTrue(seasonal.is_tv_seasonal_anime(item))

    def test_display_gate_rejects_foreign_and_non_animation(self):
        foreign = {"type": 2, "name": "The Bad Guys", "platform": "TV", "tags": [{"name": "美国动画"}]}
        live_action = {"type": 6, "name": "日本ドラマ", "platform": "TV"}
        self.assertFalse(seasonal.is_displayable_japanese_seasonal_anime(foreign))
        self.assertFalse(seasonal.is_displayable_japanese_seasonal_anime(live_action))

    def test_unwatched_non_tv_is_detected(self):
        for platform in ("剧场版", "OVA", "WEB", "SP"):
            item = {"type": 2, "name": "日本アニメ", "platform": platform, "tags": [{"name": "日本动画"}]}
            self.assertFalse(seasonal.is_tv_seasonal_anime(item))

    def test_hidden_edit_fields_and_account_binding_copy_are_absent(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertNotIn('text_area("私人备注', source)
        self.assertNotIn('selectbox("这些标签的分类', source)
        self.assertNotIn('button("Bangumi 账号', source)
        self.assertIn('"使用此数据"', source)


if __name__ == "__main__":
    unittest.main()
