#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CNB 巡检异常回评 —— 对应 GitHub 版 friend_circle_lite.yml 的「友链 Issue 巡检回评」步骤

在巡检流水线的后处理之后运行：
  - 扫描 link.json，找出 可达性失败 或 反链丢失(且非 verified) 的友链；
  - 这些友链若登记过 issue_id 且尚未带 待更新 标签 → 去对应议题回评原因并打标签；
  - 已带 待更新 但本轮恢复正常 → 移除标签并回评恢复通知。

平台无关设计：只要环境有 CNB_TOKEN / CNB_REPO_SLUG 就能跑；
缺令牌或接口异常只告警不中断巡检主流程。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from urllib.parse import quote

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="💬 %(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("issue-report")

API = os.getenv("CNB_API_ENDPOINT", "https://api.cnb.cool").rstrip("/")
TOKEN = os.getenv("CNB_TOKEN", "")
SLUG = os.getenv("CNB_REPO_SLUG", "")
LABEL_RETRY = "待更新"
TIMEOUT = 15


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def api(method: str, path: str, ok_codes=(200, 201, 204), **kwargs):
    resp = requests.request(method, f"{API}{path}", headers=_headers(), timeout=TIMEOUT, **kwargs)
    if resp.status_code not in ok_codes:
        raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


def unwrap_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def norm_link(u: str) -> str:
    return (u or "").strip().lower().replace("https://", "").replace("http://", "").rstrip("/")


def load_friends_source() -> str:
    """读取 conf.yaml 的 spider_settings.source，决定本地维护还是外部端点真源。

    返回 'local' 或 'remote'，解析异常/缺字段时保守回退 'local'。
    """
    try:
        with open("conf.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        raw = (cfg.get("spider_settings") or {}).get("source", "local")
        return str(raw).strip().lower() or "local"
    except Exception:
        return "local"


# ---------------- 纯逻辑：算出该通知谁、该撤销谁 ----------------
def anomaly_reason(entry: dict, verified: bool) -> str:
    """与 GitHub 版同款文案规则。返回空串表示状态正常。"""
    geo = entry.get("geo_status") or ""
    if entry.get("reachable") is False:
        if geo == "geo_blocked":
            hint = entry.get("geo_hint") or ""
            return "疑似地域拦截（geo_blocked）" + (f"：{hint}" if hint else "")
        if geo == "error":
            hint = entry.get("geo_hint") or ""
            return "站点访问错误" + (f"（{hint}）" if hint else "")
        return "站点不可访问"
    if entry.get("has_backlink") is False and not verified:
        return "未检测到指向本站的真实友链"
    return ""


def compute_changes(link_data: list, friends_index: dict) -> tuple[list, list]:
    """
    friends_index: {norm(link): {"issue_id":…, "verified":bool}}
    返回 (to_notify, to_recover)：
      to_notify = [(issue_id, name, reason)]   异常且未带标签
      to_recover = [(issue_id, name)]          曾异常（需查询标签确认），本轮已恢复
    """
    to_notify, candidates = [], []
    for entry in link_data:
        info = friends_index.get(norm_link(entry.get("link")))
        if not info or not info.get("issue_id"):
            continue
        iid = info["issue_id"]
        name = entry.get("name") or entry.get("link") or ""
        reason = anomaly_reason(entry, bool(info.get("verified")))
        if reason:
            to_notify.append((iid, name, reason))
        else:
            candidates.append((iid, name))
    return to_notify, candidates


# ---------------- 主流程 ----------------
def main() -> int:
    if not TOKEN or not SLUG:
        log.warning("缺少 CNB_TOKEN / CNB_REPO_SLUG，跳过议题回评")
        return 0

    # remote 模式：真源在外部博客端点，本地 friends.json 无 issue_id 映射，
    # 异常回评没有落点；自助申请(Issue)也已在 apply_friend 入口关闭。整套 Issue 机制不适用，跳过。
    source = load_friends_source()
    if source == "remote":
        log.warning("source=remote（真源在外部博客端点），巡检回评不适用，跳过")
        return 0

    try:
        link_data = json.load(open("link.json", encoding="utf-8")).get("link_data") or []
        friends = (json.load(open("friends.json", encoding="utf-8")) or {}).get("friends") or []
    except Exception as e:  # noqa: BLE001
        log.warning(f"读取 link.json / friends.json 失败，跳过回评：{e}")
        return 0

    friends_index = {
        norm_link(f.get("link")): {
            "issue_id": f.get("issue_id"),
            "verified": f.get("verified") is True,
        }
        for f in friends
        if f.get("link")
    }

    def labels_of(iid) -> list[str]:
        data = api("GET", f"/{SLUG}/-/issues/{iid}").json()
        return [
            (l.get("name") or l.get("title") or "")
            for l in unwrap_list(data.get("labels"))
        ]

    to_notify, recovered_candidates = compute_changes(link_data, friends_index)
    notified = 0

    for iid, name, reason in to_notify:
        try:
            if LABEL_RETRY in labels_of(iid):
                log.info(f"Issue #{iid}（{name}）已带 待更新，跳过重复评论")
                continue
            api("POST", f"/{SLUG}/-/issues/{iid}/comments", json={
                "body": f"⚠️ **定时巡检异常**：{reason}。\n\n请检查该友链，修复后在本 Issue 下回复任意内容即可重新验证（Issue 保持开放）。"
            })
            api("POST", f"/{SLUG}/-/issues/{iid}/labels", json={"labels": [LABEL_RETRY]})
            log.info(f"已对 Issue #{iid} 评论异常并打 待更新（{name}）")
            notified += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"处理 Issue #{iid} 失败：{e}")

    for iid, name in recovered_candidates:
        try:
            labels = labels_of(iid)
            if LABEL_RETRY not in labels:
                continue
            api(
                "DELETE",
                f"/{SLUG}/-/issues/{iid}/labels/{quote(LABEL_RETRY)}",
            )
            api("POST", f"/{SLUG}/-/issues/{iid}/comments", json={
                "body": "✅ **巡检已恢复正常**：当前可达且反链检测正常，已移除 `待更新` 标签。"
            })
            log.info(f"Issue #{iid} 已恢复正常，移除 待更新（{name}）")
            notified += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"处理恢复通知失败 (Issue #{iid})：{e}")

    log.info(f"巡检回评完成：本轮处理 {notified} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
