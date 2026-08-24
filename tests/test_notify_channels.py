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
    assert ch == {"down": [], "backlink_lost": [], "recovered": []}


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
    monkeypatch.setattr(notify, "push_qq", lambda u, t, x: calls.append("qq") or True)
    monkeypatch.setattr(notify, "push_wecom", lambda *a: (_ for _ in ()).throw(AssertionError("不应走企微")))
    assert notify.run(old, new) is True
    assert calls == ["qq"]


def test_run_qq_fail_falls_back_to_wecom(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/x")
    order = []
    monkeypatch.setattr(notify, "push_qq", lambda *a: order.append("qq") or False)
    monkeypatch.setattr(notify, "push_wecom", lambda *a: order.append("wecom") or True)
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
    monkeypatch.setattr(notify, "push_qq", lambda u, t, x: captured.update(url=u, token=t) or True)
    monkeypatch.setattr(notify, "push_wecom", lambda *a: (_ for _ in ()).throw(AssertionError("不应走企微")))
    assert notify.run(old, new, settings=cfg) is True
    assert captured == {"url": "https://bot.example.com/api/alert", "token": "yaml-token"}


def test_run_settings_disabled(monkeypatch, tmp_path):
    old, new = _rounds(tmp_path)
    monkeypatch.setenv("QQ_BOT_ALERT_URL", "https://bot.yufish.cn/api/alert")
    monkeypatch.setattr(notify, "push_qq", lambda *a: (_ for _ in ()).throw(AssertionError("禁用后不应推送")))
    cfg = AlertSettings(enable=False, qq_bot_alert_url="https://bot.yufish.cn/api/alert")
    assert notify.run(old, new, settings=cfg) is False


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
