# -*- coding: utf-8 -*-
"""截图时效刷新（refresh_days）单元测试。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from friend_circle_lite.postprocess.siteshot_merge import merge
from friend_circle_lite.screenshots.runner import _is_expired

NOW = datetime(2026, 8, 24, 12, 0, 0)


def _make_item(shot_at_days_ago=None):
    item = {"name": "a", "link": "https://a.example/", "reachable": True, "siteshot": "https://img.example/a.png"}
    if shot_at_days_ago is not None:
        ts = NOW - timedelta(days=shot_at_days_ago)
        item["sitetshot_at"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    return item


class ScreenshotRefreshTests(unittest.TestCase):
    def test_expired_after_refresh_days(self):
        self.assertTrue(_is_expired(_make_item(shot_at_days_ago=8), 7, now=NOW))
        self.assertFalse(_is_expired(_make_item(shot_at_days_ago=6), 7, now=NOW))

    def test_zero_days_means_never_expires(self):
        self.assertFalse(_is_expired(_make_item(shot_at_days_ago=365), 0, now=NOW))

    def test_missing_timestamp_never_expires(self):
        # 历史图无时间戳：不强制刷新，由 merge-shot 补记起点
        self.assertFalse(_is_expired({"siteshot": "https://img.example/a.png"}, 7, now=NOW))

    def test_thumio_placeholder_not_treated_as_expired(self):
        item = {"siteshot": "https://thum.io/shot", "sitetshot_at": "2020-01-01 00:00:00"}
        self.assertFalse(_is_expired(item, 7, now=NOW))  # 占位图走"缺图"分支而非"过期"

    def test_merge_backfills_timestamp_and_defaults_to_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline = tmp_path / "baseline.json"
            target = tmp_path / "target.json"
            baseline.write_text(
                json.dumps({"link_data": [
                    {"name": "old-ts", "link": "https://x.example/", "siteshot": "https://img/x.png", "sitetshot_at": "2026-08-01 00:00:00"},
                    {"name": "no-ts", "link": "https://y.example/", "siteshot": "https://img/y.png"},
                ]}),
                encoding="utf-8",
            )
            target.write_text(
                json.dumps({"link_data": [{"name": "old-ts", "link": "https://x.example/"}, {"name": "no-ts", "link": "https://y.example/"}]}),
                encoding="utf-8",
            )

            filled = merge(str(baseline), str(target))
            self.assertEqual(filled, 2)

            items = {it["name"]: it for it in json.loads(target.read_text(encoding="utf-8"))["link_data"]}
            self.assertEqual(items["old-ts"]["sitetshot_at"], "2026-08-01 00:00:00")  # 跟随 baseline
            self.assertTrue(items["no-ts"]["sitetshot_at"])  # 缺失时补当前时间作为周期起点


if __name__ == "__main__":
    unittest.main()
