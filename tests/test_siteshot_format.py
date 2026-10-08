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


def test_pillow_missing_fallback_still_clears_existing_png(monkeypatch):
    """Pillow 缺失回退 PNG 时，上传前仍必须清掉图床上已存在的同名 .png。

    线上事故复盘：配置了 webp 但 CI 截图 job 没装 Pillow（该 job 硬编码 pip、不读
    requirements.txt），本轮回退为上传 example.com.png，而图床上留下的正是上一轮
    的 example.com.png。旧逻辑只在上传「之后」删另一格式的 .webp——图床上压根没有
    webp，那次删除是空操作，真正的碰撞本体 png 没清，于是重名被追加序号，产出
    x1anyu.cn(1).png 这类漂移名。
    """
    events = []

    def fake_selenium(url, host, driver_path=None):
        return _make_png()

    def fake_convert(png_bytes, lossless, quality):
        return None  # 模拟 Pillow 缺失

    def fake_upload(payload, filename, mime="image/png"):
        events.append(("upload", filename))
        return "https://img.example.com/friends/" + filename

    def fake_delete(filename):
        events.append(("delete", filename))
        return True

    monkeypatch.setattr(ss, "_take_screenshot_with_selenium", fake_selenium)
    monkeypatch.setattr(ss, "_convert_to_webp", fake_convert)
    monkeypatch.setattr(ss, "upload_to_imagebed", fake_upload)
    monkeypatch.setattr(ss, "delete_from_imagebed", fake_delete)

    url = ss.take_screenshot(
        "https://example.com", "example.com", None, image_format="webp_lossless"
    )
    # 回退成 PNG，但上传的必须是干净基础名，不能是 (1) 漂移名
    assert url == "https://img.example.com/friends/example.com.png"
    deleted = {e[1] for e in events if e[0] == "delete"}
    # 核心：上一轮遗留的 png 本体必须先被删掉（旧逻辑在这儿删的是不存在的 webp）
    assert "example.com.png" in deleted
    # 清场必须发生在上传之前
    del_idx = next(i for i, e in enumerate(events) if e[0] == "delete")
    up_idx = next(i for i, e in enumerate(events) if e[0] == "upload")
    assert del_idx < up_idx


def test_take_screenshot_frees_base_name_before_upload(monkeypatch):
    """上传前清场：删除基础名 + (1)~(3) 副本 + 另一扩展名，确保重传得到干净文件名（无 (1) 漂移）。"""
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
    # 顺序：先 delete 后 upload（图床重名不覆盖，必须先让出基础名）
    assert events[0][0] == "delete"
    up_idx = next(i for i, e in enumerate(events) if e[0] == "upload")
    del_idx = next(i for i, e in enumerate(events) if e[0] == "delete")
    assert del_idx < up_idx
    # 清场覆盖基础名、另一扩展名、以及 (1)~(3) 序号副本
    deleted = {e[1] for e in events if e[0] == "delete"}
    assert "example.com.webp" in deleted          # 基础名
    assert "example.com(1).webp" in deleted       # 历史碰撞副本
    assert "example.com.png" in deleted            # 另一扩展名（切换格式遗留）
    assert "example.com(1).png" in deleted
    # 最终上传文件名是干净的基础名，绝不带 (1)
    assert ("upload", "example.com.webp") in events
    assert ("upload", "example.com(1).webp") not in events


def test_take_screenshot_deletes_before_upload_even_on_failure(monkeypatch):
    """上传失败时清场已发生（图床重名需先让名）；代价是旧图已被删、回退 thum.io（下一轮可重试）。"""
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
    # 即便上传失败，清场（delete）也已在 upload 之前执行
    assert events[0][0] == "delete"
    assert ("delete", "example.com.webp") in events
