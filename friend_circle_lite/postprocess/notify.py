# -*- coding: utf-8 -*-
"""友链异常告警（多渠道）：对比上一轮与本轮 link.json，状态翻转即推送。

监控规则（verified 人工核验站点不因反链误报）：
- 异常：reachable true→false；has_backlink true→false
- 恢复：reachable false→true；has_backlink false→true（且本轮无其他异常）
- 持续离线：站点已不可达，且 unreachable_days 跨过 link_checker 退避档位
  （RetryBackoffPolicy.NOTIFY_TIERS：挂满 10/30/60 天各提醒一次）。告警节奏与
  系统实际复探节奏一致，仅在「上轮已不可达、本轮天数刚跨过某档位」时触发一次，
  避免每次巡检重复推送，也与「刚掉线」的异常提醒区分开。
- 反链长期缺失：has_backlink 连续为 False 超过 backlink_lost_days_threshold
  （默认 7 天，0=关闭）才追加提醒。仅「上轮已缺失、本轮天数刚跨过阈值」触发一次。

渠道优先级：
1. QQ 单聊（主）：经 blog-bot Worker 中转 —— POST ``QQ_BOT_ALERT_URL``
   （如 https://bot.yufish.cn/api/alert，body {"text", "token"}），
   由 Worker 用官方 OpenAPI 主动推给 QQ_OWNER_OPENID；QQ 凭据不落 GitHub。
2. 企业微信（备选）：``WECOM_WEBHOOK_URL`` 群机器人 markdown，仅在 QQ
   未配置或发送失败时兜底。

首轮运行（无 baseline）不告警；两轮状态无变化不推送。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from math import ceil

import requests

logger = logging.getLogger(__name__)

# 持续不可达的通知档位，与 link_checker.RetryBackoffPolicy.NOTIFY_TIERS 保持一致
# （挂满 10/30/60 天各提醒一次）。导入失败时回退到硬编码默认值，避免循环导入拖垮模块。
try:
    from friend_circle_lite.link_checker.service import RetryBackoffPolicy

    SUSTAINED_TIERS = tuple(RetryBackoffPolicy.NOTIFY_TIERS)
except Exception:  # pragma: no cover - 仅在 import 异常时触发
    SUSTAINED_TIERS = (10, 30, 60)


def _norm(u: str) -> str:
    return re.sub(r"^https?://", "", (u or "").strip().lower()).rstrip("/")


def _as_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _elapsed_days(since: str | None) -> int | None:
    """从 persisted 时间戳算「挂了几天」（向上取整，最少 1）。无值返回 None。"""
    if not since:
        return None
    try:
        dt = datetime.strptime(since, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(1, ceil((datetime.now() - dt).total_seconds() / 86400))


def _load_items(path: str) -> dict[str, dict]:
    """按归一化 link 建索引；文件缺失/损坏返回空表。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        logger.warning(f"[alert] {path} 解析失败: {exc}")
        return {}
    return {_norm(it.get("link")): it for it in (data.get("link_data") or []) if it.get("link")}


def diff(old_path: str, new_path: str, backlink_lost_days_threshold: int = 0) -> dict[str, list[dict]]:
    """对比两轮结果，返回 {down, backlink_lost, recovered, sustained_down, sustained_backlink_lost}。"""
    old = _load_items(old_path)
    new = _load_items(new_path)
    result: dict[str, list[dict]] = {
        "down": [],
        "backlink_lost": [],
        "recovered": [],
        "sustained_down": [],
        "sustained_backlink_lost": [],
    }
    if not old:
        logger.info("[alert] 无上一轮 baseline（首轮），跳过告警")
        return result

    for key, cur in new.items():
        prev = old.get(key)
        if prev is None:
            continue  # 新增友链不参与翻转判断
        verified = bool(cur.get("verified"))
        name = cur.get("name", key)

        was_up, now_up = bool(prev.get("reachable")), bool(cur.get("reachable"))
        if was_up and not now_up:
            geo = f"（{cur.get('geo_status')}" + (f"/{cur['geo_hint']}" if cur.get("geo_hint") else "") + "）" if cur.get("geo_status") else ""
            result["down"].append({"name": name, "link": cur.get("link", ""), "note": geo})
        elif not was_up and now_up:
            result["recovered"].append({"name": name, "link": cur.get("link", ""), "note": "站点恢复可访问"})

        # 反链丢失：仅对非 verified 生效；恢复同理
        was_link, now_link = prev.get("has_backlink"), cur.get("has_backlink")
        if was_link is True and now_link is False and not verified and now_up:
            result["backlink_lost"].append({"name": name, "link": cur.get("link", ""), "note": "友链页未再检测到本站反链"})
        elif was_link is False and now_link is True:
            result["recovered"].append({"name": name, "link": cur.get("link", ""), "note": "反链重新检测到"})

        # 持续不可达：站点已连续不可达，且 unreachable_days 本轮刚跨过某个退避档位
        # （10/30/60 天）。仅触发一次、不重复，也不与「刚掉线」的 down 提醒重叠。
        if not now_up and not was_up:
            cur_days = _as_int(cur.get("unreachable_days")) or _elapsed_days(cur.get("unreachable_since"))
            prev_days = _as_int(prev.get("unreachable_days")) or _elapsed_days(prev.get("unreachable_since"))
            for tier in SUSTAINED_TIERS:
                if cur_days and cur_days >= tier and (prev_days is None or prev_days < tier):
                    result["sustained_down"].append({
                        "name": name,
                        "link": cur.get("link", ""),
                        "days": cur_days,
                        "note": f"已持续不可达 {cur_days} 天",
                    })
                    break  # 一次 run 最多跨一个档位

        # 反链长期缺失：has_backlink 连续为 False 且天数刚跨过阈值，触发一次。
        if backlink_lost_days_threshold and cur.get("has_backlink") is False and prev.get("has_backlink") is False:
            cur_bl = _as_int(cur.get("backlink_lost_days")) or _elapsed_days(cur.get("backlink_lost_since"))
            prev_bl = _as_int(prev.get("backlink_lost_days")) or _elapsed_days(prev.get("backlink_lost_since"))
            if cur_bl and cur_bl >= backlink_lost_days_threshold and (prev_bl is None or prev_bl < backlink_lost_days_threshold):
                result["sustained_backlink_lost"].append({
                    "name": name,
                    "link": cur.get("link", ""),
                    "days": cur_bl,
                    "note": f"已持续 {cur_bl} 天未检测到本站反链",
                })

    return result


def format_markdown(changes: dict[str, list[dict]]) -> str:
    """企微 markdown 排版。"""
    lines: list[str] = []
    if changes["down"]:
        lines.append("**🔴 友链异常**")
        for it in changes["down"]:
            lines.append(f"> [{it['name']}]({it['link']}) 不可访问{it['note']}")
    if changes["backlink_lost"]:
        lines.append("**🟠 反链丢失**")
        for it in changes["backlink_lost"]:
            lines.append(f"> [{it['name']}]({it['link']}) {it['note']}")
    if changes["recovered"]:
        lines.append("**🟢 恢复正常**")
        for it in changes["recovered"]:
            lines.append(f"> [{it['name']}]({it['link']}) {it['note']}")
    if changes["sustained_down"]:
        lines.append("**⏰ 持续离线**")
        for it in changes["sustained_down"]:
            lines.append(f"> [{it['name']}]({it['link']}) {it['note']}")
    if changes["sustained_backlink_lost"]:
        lines.append("**⏳ 反链长期缺失**")
        for it in changes["sustained_backlink_lost"]:
            lines.append(f"> [{it['name']}]({it['link']}) {it['note']}")
    return "\n".join(lines)


def format_plain(changes: dict[str, list[dict]]) -> str:
    """QQ 纯文本排版（QQ 文本通道不渲染 markdown）。"""
    lines: list[str] = []
    if changes["down"]:
        lines.append("🔴 友链异常")
        for it in changes["down"]:
            lines.append(f"· {it['name']} {it['link']} 不可访问{it['note']}")
    if changes["backlink_lost"]:
        lines.append("🟠 反链丢失")
        for it in changes["backlink_lost"]:
            lines.append(f"· {it['name']} {it['link']} {it['note']}")
    if changes["recovered"]:
        lines.append("🟢 恢复正常")
        for it in changes["recovered"]:
            lines.append(f"· {it['name']} {it['link']} {it['note']}")
    if changes["sustained_down"]:
        lines.append("⏰ 持续离线")
        for it in changes["sustained_down"]:
            lines.append(f"· {it['name']} {it['link']} {it['note']}")
    if changes["sustained_backlink_lost"]:
        lines.append("⏳ 反链长期缺失")
        for it in changes["sustained_backlink_lost"]:
            lines.append(f"· {it['name']} {it['link']} {it['note']}")
    return "\n".join(lines)


def push_qq(url: str, token: str, text: str) -> bool:
    """经 blog-bot Worker 的 /api/alert 推送（QQ 单聊主动消息）。"""
    try:
        resp = requests.post(url, json={"token": token, "text": text}, timeout=15)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        ok = resp.status_code == 200 and isinstance(data, dict) and data.get("ok") is True
        if not ok:
            logger.warning(f"[alert] QQ 推送失败：HTTP {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as exc:
        logger.warning(f"[alert] QQ 推送异常：{exc}")
        return False


def push_wecom(webhook_url: str, markdown: str) -> bool:
    payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        ok = resp.status_code == 200 and resp.json().get("errcode") == 0
        if not ok:
            logger.warning(f"[alert] 企业微信推送失败：HTTP {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as exc:
        logger.warning(f"[alert] 企业微信推送异常：{exc}")
        return False


def run(old_path: str, new_path: str, settings: object | None = None) -> bool:
    """主入口：diff + 多渠道推送（QQ 主、企微兜底）。返回是否实际发送。

    settings 为 conf.yaml 解析出的 AlertSettings（config.models.AlertSettings，
    duck-typing 引用避免循环导入）；未传时回退读环境变量（兼容旧调用方式）。
    """
    if settings is not None:
        backlink_threshold = int(getattr(settings, "backlink_lost_days_threshold", 0) or 0)
    else:
        backlink_threshold = int(os.getenv("BACKLINK_LOST_DAYS_THRESHOLD", "0") or 0)
    changes = diff(old_path, new_path, backlink_lost_days_threshold=backlink_threshold)
    total = sum(len(v) for v in changes.values())
    if total == 0:
        logger.info("[alert] 两轮状态无变化，不推送")
        return False

    if settings is not None:
        if not getattr(settings, "enable", True):
            logger.info("[alert] postprocess.alert.enable=false，跳过告警推送")
            return False
        qq_url = (getattr(settings, "qq_bot_alert_url", "") or "").strip()
        qq_token = (getattr(settings, "qq_bot_alert_token", "") or "").strip()
        wecom = (getattr(settings, "wecom_webhook_url", "") or "").strip()
    else:
        qq_url = os.getenv("QQ_BOT_ALERT_URL", "").strip()
        wecom = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        qq_token = os.getenv("QQ_BOT_ALERT_TOKEN", "").strip()

    if not qq_url and not wecom:
        logger.info("[alert] 未配置 QQ_BOT_ALERT_URL / WECOM_WEBHOOK_URL，跳过告警推送")
        return False

    if qq_url:
        text = format_plain(changes)
        logger.info(f"[alert] 检测到 {total} 条状态变化，推送 QQ（blog-bot）\n{text}")
        if push_qq(qq_url, qq_token, text):
            return True
        logger.warning("[alert] QQ 推送未成功，降级企业微信")

    if not wecom:
        logger.error("[alert] 企业微信未配置且 QQ 已失败，本轮告警丢失")
        return False
    markdown = format_markdown(changes)
    logger.info(f"[alert] 推送企业微信\n{markdown}")
    return push_wecom(wecom, markdown)
