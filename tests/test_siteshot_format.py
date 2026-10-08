# -*- coding: utf-8 -*-
"""截图输出格式（png / webp_lossless / webp）可切换的单测。

纯函数 + 轻量 monkeypatch，不启动浏览器、不发真实请求。
"""
import io

from friend_circle_lite.config.models import ApplicationConfig, _normalize_image_format
from friend_circle_lite.screenshots import screenshot as ss


def _make_png() -> bytes:
    from PIL import Image

    img = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_safe_filename_ext():
    assert ss._safe_filename("blog.example.com") == "blog.example.com.png"
    assert ss._safe_filename("blog.example.com", "webp") == "blog.example.com.webp"
    assert ss._safe_filename("a b/c", "webp") == "a_b_c.webp"


def test_normalize_image_format():
    assert _normalize_image_format("png") == "png"
    assert _normalize_image_format("WEBP_LOSSLESS") == "webp_lossless"
    assert _normalize_image_format("webp") == "webp"
    assert _normalize_image_format("jpg") == "png"  # 非法回退
    assert _normalize_image_format("") == "png"
    assert _normalize_image_format(None) == "png"


def test_config_from_dict_image_format():
    cfg = ApplicationConfig.from_dict(
        {"postprocess": {"siteshot": {"image_format": "webp_lossless", "webp_quality": 70}}}
    )
    assert cfg.postprocess.siteshot.image_format == "webp_lossless"
    assert cfg.postprocess.siteshot.webp_quality == 70
    # 非法值归一到 png
    cfg2 = ApplicationConfig.from_dict({"postprocess": {"siteshot": {"image_format": "bogus"}}})
    assert cfg2.postprocess.siteshot.image_format == "png"


def test_convert_to_webp_lossless_is_pixel_identical():
    from PIL import Image

    png = _make_png()
    webp = ss._convert_to_webp(png, lossless=True, quality=85)
    assert webp is not None
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    got = Image.open(io.BytesIO(webp)).convert("RGBA")
    orig = Image.open(io.BytesIO(png)).convert("RGBA")
    assert got.tobytes() == orig.tobytes()


def test_convert_to_webp_lossy_smaller_than_png():
    from PIL import Image

    img = Image.new("RGB", (64, 64), (123, 45, 67))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    webp = ss._convert_to_webp(png, lossless=False, quality=50)
    assert webp is not None
    assert len(webp) < len(png)


def test_take_screenshot_chooses_webp(monkeypatch):
    calls = {}

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_upload(payload, filename, mime="image/png"):
        calls["filename"] = filename
        calls["mime"] = mime
        calls["payload_head"] = payload[:4]
        return "https://img.example.com/friends/" + filename

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", lambda f: True)

    url = ss.take_screenshot(
        "https://example.com", "example.com", None, image_format="webp_lossless", webp_quality=85
    )
    assert url.endswith(".webp")
    assert calls["filename"] == "example.com.webp"
    assert calls["mime"] == "image/webp"
    assert calls["payload_head"] == b"RIFF"


def test_take_screenshot_png_default(monkeypatch):
    calls = {}

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_upload(payload, filename, mime="image/png"):
        calls["filename"] = filename
        calls["mime"] = mime
        calls["payload_head"] = payload[:8]
        return "https://img.example.com/friends/" + filename

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", lambda f: True)

    url = ss.take_screenshot("https://example.com", "example.com", None)
    assert url.endswith(".png")
    assert calls["filename"] == "example.com.png"
    assert calls["mime"] == "image/png"
    assert calls["payload_head"] == b"\x89PNG\r\n\x1a\n"


def test_take_screenshot_pillow_missing_falls_back_png(monkeypatch):
    # 模拟 Pillow 缺失：_convert_to_webp 返回 None，主流程回退 PNG 不阻断
    calls = {}

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_convert(png_bytes, lossless, quality):
        return None

    def fake_upload(payload, filename, mime="image/png"):
        calls["filename"] = filename
        calls["mime"] = mime
        return "https://img.example.com/friends/" + filename

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "_convert_to_webp", fake_convert)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", lambda f: True)

    url = ss.take_screenshot("https://example.com", "example.com", None, image_format="webp_lossless")
    assert url.endswith(".png")  # 回退
    assert calls["mime"] == "image/png"


def test_take_screenshot_deletes_stale_format_only_after_upload(monkeypatch):
    """上传成功后才删另一扩展名旧图；且绝不在上传前删有效旧图（避免瞬时上传失败丢图）。"""
    events = []

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_upload(payload, filename, mime="image/png"):
        events.append(("upload", filename))
        return "https://img.example.com/friends/" + filename

    def fake_delete(filename):
        events.append(("delete", filename))
        return True

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", fake_delete)

    url = ss.take_screenshot(
        "https://example.com", "example.com", None, image_format="webp_lossless"
    )
    assert url.endswith(".webp")
    # 顺序：先 upload 后 delete（确认不在上传前删）
    assert events[0][0] == "upload"
    # 切换 webp 后清理旧 .png
    assert ("delete", "example.com.png") in events
    assert ("delete", "example.com.webp") not in events


def test_take_screenshot_upload_failure_keeps_old_image(monkeypatch):
    """上传失败时不应删除任何图床文件（保留旧好图，仅回退 thum.io）。"""
    events = []

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_upload(payload, filename, mime="image/png"):
        events.append(("upload", filename))
        return None  # 上传失败

    def fake_delete(filename):
        events.append(("delete", filename))
        return True

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", fake_delete)

    url = ss.take_screenshot(
        "https://example.com", "example.com", None, image_format="webp_lossless"
    )
    assert "thum.io" in url  # 降级兜底
    # 上传失败全程未删除任何图床文件
    assert events == [("upload", "example.com.webp")]
