# -*- coding: utf-8 -*-
"""友链检测强制重检（force）单元测试：忽略可达性缓存，全部重新检测。"""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 先初始化 crawler 包，避免 link_checker.service -> crawler.feed_service -> crawler 的循环导入
# （生产链路里 crawler 总先于 link_checker.service 被加载，此处复刻该顺序）。
import friend_circle_lite.crawler  # noqa: F401

from friend_circle_lite.config.models import LinkCheckConfig, ProxySettings
from friend_circle_lite.domain.models import LinkCheckRecord, Website
from friend_circle_lite.link_checker.service import LinkReachabilityService
from friend_circle_lite.storage.sqlite_store import LinkCheckStore

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _website() -> Website:
    return Website(name="site-a", url="https://a.example/", linkpage="https://a.example/friends/")


def _fresh_cached() -> LinkCheckRecord:
    # 一条新鲜的已检记录：可达、有测速、未参与 RSS 抓取 → 非强制时会被复用。
    return LinkCheckRecord(
        name="site-a",
        url="https://a.example/",
        linkpage="https://a.example/friends/",
        checked_at=NOW,
        reachable=True,
        best_latency="0.5",
        crawl_allowed=False,
    )


class ForceLinkCheckTests(unittest.TestCase):
    def test_non_force_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LinkCheckStore(Path(tmp) / "cache.db")
            store.save_records([_fresh_cached()])
            svc = LinkReachabilityService(
                config=LinkCheckConfig(max_age_hours=24),
                proxy_settings=ProxySettings(),
                store=store,
                force=False,
            )
            with patch.object(svc, "_check_fresh_websites", return_value=[]) as fresh, patch.object(
                svc, "_refresh_backlinks_only"
            ):
                recs = svc.check_websites([_website()])
            # 非强制且缓存新鲜 → 复用，不发起实际检测请求
            self.assertEqual(fresh.call_count, 0)
            self.assertTrue(recs[0].reachable)  # 返回的是复用的缓存记录

    def test_force_ignores_cache_and_rechecks_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LinkCheckStore(Path(tmp) / "cache.db")
            store.save_records([_fresh_cached()])
            svc = LinkReachabilityService(
                config=LinkCheckConfig(max_age_hours=24),
                proxy_settings=ProxySettings(),
                store=store,
                force=True,
            )
            marker = LinkCheckRecord(
                name="site-a",
                url="https://a.example/",
                linkpage="https://a.example/friends/",
                checked_at="FRESH",
                reachable=False,
            )
            with patch.object(svc, "_check_fresh_websites", return_value=[marker]) as fresh, patch.object(
                svc, "_refresh_backlinks_only"
            ):
                recs = svc.check_websites([_website()])
            # 强制：即便缓存新鲜也重新检测，返回的是新检测结果
            self.assertEqual(fresh.call_count, 1)
            self.assertEqual(recs[0].checked_at, "FRESH")
            self.assertFalse(recs[0].reachable)


if __name__ == "__main__":
    unittest.main()
