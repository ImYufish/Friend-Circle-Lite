# -*- coding: utf-8 -*-
"""notify 多渠道告警单测：diff 规则 / 排版 / QQ 推送 payload / 企微降级逻辑。

全程离线：requests.post 用 monkeypatch 替身，不发任何真实请求。
"""

import friend_circle_lite.crawler  # noqa: F401  # 先加载打破 link_checker<->crawler 包级循环

import json

from friend_circle_lite.config.models import AlertSettings
from friend_circle_lite.postprocess import notify


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _rounds(tmp_path):
    old = {"link_data": [
        {"name": "A站", "link": "https://a.com/", "reachable": True, "has_backlink": True},
        {"name": "B站", "link": "https://b.com", "reachable": False, "has_backlink": False},
        {"name": "V站", "link": "https://v.com", "reachable": True, "has_backlink": False, "verified": True},
    ]}
    new = {"link_data": [
        {"name": "A站", "link": "https://a.com", "reachable": False, "has_backlink": True,
         "geo_status": "geo_blocked", "geo_hint": "CN-block"},
        {"name": "B站", "link": "https://b.com", "reachable": True, "has_backlink": False},
        {"name": "V站", "link": "https://v.com", "reachable": True, "has_backlink": True, "verified": True},
    ]}
    return _write(tmp_path, "old.json", old), _write(tmp_path, "new.json", new)


def test_diff_rules(tmp_path):
    old, new = _rounds(tmp_path)
    ch = notify.diff(old, new)
    assert [it["name"] for it in ch["down"]] == ["A站"]
    assert "geo_blocked/CN-block" in ch["down"][0]["note"]
    # B站 reachable 恢复；反链 False→False 不产生恢复条目
    assert any(it["name"] == "B站" for it in ch["recovered"])
    # V站 verified 豁免反链误报（false→true 只算恢复）
    assert all(it["name"] == "B站" or it["name"] == "V站" for it in ch["recovered"])
    assert ch["backlink_lost"] == []


def test_diff_no_baseline_silent(tmp_path):
    new = _write(tmp_path, "new.json", {"link_data": [{"name": "A站", "link": "https://a.com", "reachable": False}]})
    ch = notify.diff(str(tmp_path / "not_exist.json"), new)
    assert ch == {"down": [], "backlink_lost": [], "recovered": [], "sustained_down": [], "sustained_backlink_lost": []}


def test_format_plain_no_markdown(tmp_path):
    old, new = _rounds(tmp_path)
    text = notify.format_plain(notify.diff(old, new))
    assert "**" not in text and "> [" not in text and "](" not in text
    assert "友链异常" in text and "恢复正常" in text


def _fake_resp(status=200, body=None):
    class R:
        def __init__(self):
            self.status_code = status
            self.text = json.dumps(body or {})

        def json(self):
            if isinstance(body, dict):
                return body
            raise ValueError("no json")
    return R()


def test_push_qq_payload_and_result(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"], captured["json"], captured["timeout"] = url, json, timeout
        return _fake_resp(200, {"ok": True})

    monkeypatch.setattr(notify.requests, "post", fake_post)
    assert notify.push_qq("https://bot.yufish.cn/api/alert", "tk", "告警文本") is True
    assert captured["url"] == "https://bot.yufish.cn/api/alert"
    assert captured["json"] == {"token": "tk", "text": "告警文本"}
    assert captured["timeout"] == 15


def test_push_qq_failure(monkeypatch):
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: _fake_resp(500, {"ok": False}))
    assert notify.push_qq("https://bot.yufish.cn/api/alert", "tk", "x") is False


def test_run_qq_success_skips_wecom(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setenv("QQ_BOT_ALERT_TOKEN", "tk")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/x")
    calls = []
    monkeypatch.setattr(notify, "push_qq", lambda u, t, x, log_path="": calls.append("qq") or True)
    monkeypatch.setattr(notify, "push_wecom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应走企微")))
    assert notify.run(old, new) is True
    assert calls == ["qq"]


def test_run_qq_fail_falls_back_to_wecom(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/x")
    order = []
    monkeypatch.setattr(notify, "push_qq", lambda *a, **k: order.append("qq") or False)
    monkeypatch.setattr(notify, "push_wecom", lambda *a, **k: order.append("wecom") or True)
    assert notify.run(old, new) is True
    assert order == ["qq", "wecom"]


def test_run_no_channels_configured(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.delenv("QQ_BOT_ALERT_URL", raising=False)
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    assert notify.run(old, new) is False


def test_run_no_changes_skips_all(monkeypatch, tmp_path):
    data = {"link_data": [{"name": "A站", "link": "https://a.com", "reachable": True, "has_backlink": True}]}
    old = _write(tmp_path, "o.json", data)
    new = _write(tmp_path, "n.json", dict(data))
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setattr(notify, "push_qq", lambda *a: (_ for _ in ()).throw(AssertionError("无变化不应推送")))
    assert notify.run(old, new) is False


def test_run_with_settings_config(monkeypatch, tmp_path):
    """conf.yaml 路径：AlertSettings 提供渠道，env 不参与。"""
    old, new = _rounds(tmp_path)
    monkeypatch.delenv("QQ_BOT_ALERT_URL", raising=False)
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    cfg = AlertSettings(
        enable=True,
        qq_bot_alert_url="https://bot.example.com/api/alert",
        qq_bot_alert_token="yaml-token",
        wecom_webhook_url="",
    )
    captured = {}
    monkeypatch.setattr(notify, "push_qq", lambda u, t, x, log_path="": captured.update(url=u, token=t) or True)
    monkeypatch.setattr(notify, "push_wecom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应走企微")))
    assert notify.run(old, new, settings=cfg) is True
    assert captured == {"url": "https://bot.example.com/api/alert", "token": "yaml-token"}


def test_run_settings_disabled(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setattr(notify, "push_qq", lambda *a: (_ for _ in ()).throw(AssertionError("禁用后不应推送")))
    cfg = AlertSettings(enable=False, qq_bot_alert_url="https://bot.yufish.cn/api/alert")
    assert notify.run(old, new, settings=cfg) is False


def test_push_log_writes_on_success_and_failure(monkeypatch, tmp_path):
    """推送审计日志：成功/失败都落盘，且目标地址只记域名（不泄 key）。"""
    log = str(tmp_path / "push_log.jsonl")

    def fake_post_ok(url, json=None, timeout=None):
        # QQ 看 ok=True；企微看 errcode==0，这里按调用顺序分别返回
        body = {"ok": True}
        if "wecom" in url or "webhook" in url:
            body = {"errcode": 0, "errmsg": "ok"}
        return _fake_resp(200, body)

    def fake_post_fail(url, json=None, timeout=None):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    assert notify.push_qq("https://bot.yufish.cn/api/alert", "tk", "ok文本", log_path=log) is True

    monkeypatch.setattr(notify.requests, "post", fake_post_fail)
    assert notify.push_qq("https://bot.yufish.cn/api/alert", "tk", "fail文本", log_path=log) is False

    # 企微：webhook key 在 query 里，日志里必须只剩域名
    monkeypatch.setattr(notify.requests, "post", fake_post_ok)
    assert notify.push_wecom("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET", "md", log_path=log) is True

    lines = [json.loads(l) for l in (tmp_path / "push_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    # 第 1 条成功、第 2 条异常、第 3 条成功
    assert lines[0]["event"] == "push" and lines[0]["channel"] == "qq" and lines[0]["ok"] is True
    assert lines[1]["ok"] is False and lines[1]["error"] == "connection reset by peer"
    assert lines[2]["channel"] == "wecom" and lines[2]["ok"] is True
    # 域名脱敏：不得出现完整 webhook 路径与 key
    assert all("SECRET" not in l["target"] for l in lines)
    assert lines[2]["target"] == "qyapi.weixin.qq.com"
    assert lines[0]["target"] == "bot.yufish.cn"


def test_run_writes_push_log_on_qq_success(monkeypatch, tmp_path):
    """run() 经 settings.push_log_path 把真实推送写入审计日志（走真实 push_qq）。"""
    old, new = _rounds(tmp_path)
    log = str(tmp_path / "push_log.jsonl")
    cfg = AlertSettings(
        enable=True,
        qq_bot_alert_url="https://bot.example.com/api/alert",
        qq_bot_alert_token="t",
        wecom_webhook_url="",
        push_log_path=log,
    )
    # 只替 requests.post，保留真实 push_qq 以验证其写盘逻辑
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: _fake_resp(200, {"ok": True}))
    monkeypatch.setattr(notify, "push_wecom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应走企微")))
    assert notify.run(old, new, settings=cfg) is True

    lines = [json.loads(l) for l in (tmp_path / "push_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "push" and lines[0]["channel"] == "qq" and lines[0]["ok"] is True


def test_run_no_channels_logs_skip(monkeypatch, tmp_path):
    """未配置任何渠道时，run() 记一笔 skip（便于区分『没推』与『漏推』）。"""
    old, new = _rounds(tmp_path)
    log = str(tmp_path / "push_log.jsonl")
    monkeypatch.delenv("QQ_BOT_ALERT_URL", raising=False)
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    assert notify.run(old, new, settings=AlertSettings(push_log_path=log)) is False

    lines = [json.loads(l) for l in (tmp_path / "push_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "skip" and "未配置推送渠道" in lines[0]["note"]


def test_push_log_disabled_when_no_path(monkeypatch, tmp_path):
    """未配置日志路径时完全不写盘（不产生 stray 文件）。"""
    monkeypatch.setattr(notify.requests, "post", lambda url, json=None, timeout=None: _fake_resp(200, {"ok": True}))
    assert notify.push_qq("https://bot.yufish.cn/api/alert", "tk", "x") is True
    assert not (tmp_path / "push_log.jsonl").exists()


def test_models_postprocess_env_priority(monkeypatch):
    """models 层 env 优先语义：env 有值覆盖 yaml，无值回落 yaml；密钥仅来自 env。"""
    from friend_circle_lite.config.models import ApplicationConfig

    raw = {
        "postprocess": {
            "enable": True,
            "geo_diagnose": {"enable": True, "cn_probe": False},
            "siteshot": {"enable": True, "upload_folder": "friends", "max_workers": 3, "upload_url": ""},
            "alert": {
                "enable": True,
                "qq_bot_alert_url": "https://from-yaml.example.com/api/alert",
                "wecom_webhook_url": "",
            },
        }
    }
    monkeypatch.delenv("GEO_CN_PROBE", raising=False)
    monkeypatch.delenv("QQ_BOT_ALERT_URL", raising=False)
    monkeypatch.delenv("QQ_BOT_ALERT_TOKEN", raising=False)
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    cfg = ApplicationConfig.from_dict(raw).postprocess
    assert cfg.geo_diagnose.cn_probe is False
    assert cfg.siteshot.max_workers == 3
    assert cfg.alert.qq_bot_alert_url == "https://from-yaml.example.com/api/alert"
    assert cfg.alert.qq_bot_alert_token == ""

    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://from-env.example.com/api/alert")
    monkeypatch.setenv("QQ_BOT_ALERT_TOKEN", "env-token")
    monkeypatch.setenv("GEO_CN_PROBE", "1")
    cfg2 = ApplicationConfig.from_dict(raw).postprocess
    assert cfg2.alert.qq_bot_alert_url == "https://from-env.example.com/api/alert"
    assert cfg2.alert.qq_bot_alert_token == "env-token"
    assert cfg2.geo_diagnose.cn_probe is True
