# -*- coding: utf-8 -*-
"""friends_publish（发布字段重命名）单元测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from friend_circle_lite.friends_publish import publish, transform_document, transform_entry


class FriendsPublishTests(unittest.TestCase):
    def test_transform_entry_renames_only_mapped_fields(self):
        entry = {"name": "a", "link": "https://a.b", "avatar": "x.png", "weight": 5}
        out = transform_entry(entry, {"name": "title"})
        self.assertEqual(out, {"title": "a", "link": "https://a.b", "avatar": "x.png", "weight": 5})

    def test_transform_document_keeps_top_level_and_non_dict_items(self):
        doc = {
            "version": 1,
            "updatedAt": "2026-08-24",
            "friends": [{"name": "a", "link": "u"}, ["raw", "list"]],
        }
        out = transform_document(doc, {"name": "title", "link": "siteurl"})
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["updatedAt"], "2026-08-24")
        self.assertEqual(out["friends"][0], {"title": "a", "siteurl": "u"})
        self.assertEqual(out["friends"][1], ["raw", "list"])

    def test_publish_with_rename_writes_blog_style_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "friends.json"
            dst = tmp_path / "pages" / "friends.json"
            conf = tmp_path / "conf.yaml"
            src.write_text(
                json.dumps({"friends": [{"name": "羡鱼", "link": "https://x1anyu.cn", "avatar": "a.png", "enabled": True}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            conf.write_text(
                "spider_settings:\n"
                "  friends_input:\n"
                "    rename:\n"
                "      name: title\n"
                "      link: siteurl\n"
                "      avatar: imgurl\n",
                encoding="utf-8",
            )

            renamed = publish(str(src), str(dst), str(conf))
            self.assertTrue(renamed)

            data = json.loads(dst.read_text(encoding="utf-8"))
            self.assertEqual(
                data["friends"][0],
                {"title": "羡鱼", "siteurl": "https://x1anyu.cn", "imgurl": "a.png", "enabled": True},
            )
            # 源文件不被修改
            self.assertEqual(json.loads(src.read_text(encoding="utf-8"))["friends"][0]["name"], "羡鱼")

    def test_publish_without_rename_outputs_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "friends.json"
            dst = tmp_path / "out.json"
            conf = tmp_path / "conf.yaml"
            original = {"friends": [{"name": "a", "link": "u"}]}
            src.write_text(json.dumps(original), encoding="utf-8")
            # 不含 friends_input 段（即不配置字段重命名），publish 应原样输出
            conf.write_text("debug: false\n", encoding="utf-8")

            renamed = publish(str(src), str(dst), str(conf))
            self.assertFalse(renamed)
            self.assertEqual(json.loads(dst.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
