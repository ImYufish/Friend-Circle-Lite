"""反链检测：静态匹配与无头兜底的纯逻辑测试（不依赖网络 / playwright）。"""

import unittest
from types import SimpleNamespace

# 先初始化 crawler 包，让 link_checker <-> crawler 的循环导入自然闭合，
# 再导入 link_checker 子模块，避免反向触发半初始化 ImportError。
import friend_circle_lite.crawler  # noqa: F401  side-effect: break circular import
from friend_circle_lite.link_checker import headless
from friend_circle_lite.link_checker.service import LinkReachabilityService


class BacklinkMatchTest(unittest.TestCase):
    def test_build_variants(self):
        variants = headless._build_variants("x1anyu.cn")
        self.assertIn("https://x1anyu.cn", variants)
        self.assertIn("http://x1anyu.cn", variants)
        self.assertIn("//x1anyu.cn", variants)
        self.assertIn("x1anyu.cn", variants)
        # 已带协议的域名不再重复加 https://
        variants2 = headless._build_variants("https://x1anyu.cn")
        self.assertIn("https://x1anyu.cn", variants2)
        self.assertNotIn("https://https://x1anyu.cn", variants2)

    def test_match_true_cases(self):
        samples = [
            '<a href="https://x1anyu.cn">临渊羡鱼</a>',
            "<a href='https://x1anyu.cn/'>友链</a>",
            "本站友链：http://x1anyu.cn 欢迎互换",
            'window.__data = ["//x1anyu.cn"]',
        ]
        for sample in samples:
            self.assertTrue(headless._contains_author_link(sample, "x1anyu.cn"), msg=f"应命中: {sample!r}")

    def test_match_false_cases(self):
        self.assertFalse(headless._contains_author_link("友链指向 example.com 与 test.cn", "x1anyu.cn"))
        self.assertFalse(headless._contains_author_link("", "x1anyu.cn"))
        self.assertFalse(headless._contains_author_link("x1anyu.cn", ""))

    def test_match_golink_transit(self):
        # WordPress 网址导航中转：真实目标 base64 藏在 ?golink= 参数里
        golink = "aHR0cHM6Ly94MWFueXUuY24="  # https://x1anyu.cn
        samples = [
            f'<a href="https://bk.zhaozhiqiang.pw/?golink={golink}">临渊羡鱼</a>',
            f'<a href="https://bk.zhaozhiqiang.pw/?golink={golink}&nonce=abc">友链</a>',
            f'window.location.href="https://bk.zhaozhiqiang.pw/?golink={golink}"',
        ]
        for sample in samples:
            self.assertTrue(headless._contains_author_link(sample, "x1anyu.cn"), msg=f"应解码命中: {sample!r}")

    def test_match_golink_subdomain(self):
        # 子域名也算命中（与自助申请侧一致）
        golink = "aHR0cHM6Ly9ibG9nLngxYW55dS5jbg=="  # https://blog.x1anyu.cn
        sample = f'<a href="https://bk.zhaozhiqiang.pw/?golink={golink}">临渊羡鱼</a>'
        self.assertTrue(headless._contains_author_link(sample, "x1anyu.cn"))

    def test_match_golink_negative(self):
        # 解码后是别的域名，不应误判
        golink = "aHR0cHM6Ly9leGFtcGxlLmNvbQ=="  # https://example.com
        sample = f'<a href="https://bk.zhaozhiqiang.pw/?golink={golink}">其他</a>'
        self.assertFalse(headless._contains_author_link(sample, "x1anyu.cn"))

    def test_available_is_bool(self):
        # 沙箱未装 playwright 时为 False；装了则为 True。无论如何都应是布尔。
        self.assertIsInstance(headless.available(), bool)

    def test_service_match_author_link_in_content(self):
        config = SimpleNamespace(author_url="x1anyu.cn", backlink_headless=True, timeout=15)
        svc = LinkReachabilityService(config=config, proxy_settings=None, store=None)
        self.assertTrue(svc._match_author_link_in_content('<a href="https://x1anyu.cn">x</a>'))
        golink = "aHR0cHM6Ly94MWFueXUuY24="
        self.assertTrue(
            svc._match_author_link_in_content(
                f'<a href="https://bk.zhaozhiqiang.pw/?golink={golink}">x</a>'
            )
        )
        self.assertFalse(svc._match_author_link_in_content("no link here"))


if __name__ == "__main__":
    unittest.main()
