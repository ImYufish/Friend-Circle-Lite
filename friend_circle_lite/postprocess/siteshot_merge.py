# -*- coding: utf-8 -*-
"""把上一轮 link.json（page 分支 baseline）中的 siteshot 回填到本轮结果。

截图是低频操作（图床旧图长期有效），而 link.json 每轮全量重写会丢掉
siteshot 字段。此处用 baseline 做多键匹配回填，避免重复截图：

匹配优先级：精确 link → 归一化 link → (name, host) → 唯一 host。
移植自 check-flink workflow 内联脚本的「纵深防守交叉补位」逻辑。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _norm(u: str) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    return u.rstrip("/")


def _host(u: str) -> str:
    n = _norm(u)
    return n.split("/", 1)[0] if n else ""


def _build_index(baseline_items: list[dict]) -> dict:
    idx = {"exact": {}, "norm": {}, "name_host": {}, "host": {}}
    for e in baseline_items:
        link = e.get("link", "") or ""
        name = (e.get("name", "") or "").strip().lower()
        if not (e.get("siteshot") or "").strip():
            continue  # 只索引有截图的条目
        if link:
            idx["exact"].setdefault(link, e)
        n, h = _norm(link), _host(link)
        if n and n not in idx["norm"]:
            idx["norm"][n] = e
        if name and h and (name, h) not in idx["name_host"]:
            idx["name_host"][(name, h)] = e
        if h:
            idx["host"].setdefault(h, []).append(e)
    return idx


def _lookup(idx: dict, link: str, name: str) -> dict:
    if link in idx["exact"]:
        return idx["exact"][link]
    n, h = _norm(link), _host(link)
    nm = (name or "").strip().lower()
    if n and n in idx["norm"]:
        return idx["norm"][n]
    if nm and h and (nm, h) in idx["name_host"]:
        return idx["name_host"][(nm, h)]
    candidates = idx["host"].get(h) or []
    if len(candidates) == 1:
        return candidates[0]
    for c in candidates:  # 同 host 多站点时按名称模糊兜底
        on = (c.get("name", "") or "").strip().lower()
        if nm and on and (nm == on or nm in on or on in nm):
            return c
    return {}


def merge(baseline_path: str, target_path: str) -> int:
    """将 baseline 中的 siteshot 回填进本轮 link.json，返回回填条数。"""
    try:
        with open(baseline_path, encoding="utf-8") as f:
            baseline = json.load(f)
    except FileNotFoundError:
        logger.info("[siteshot] 无 baseline（首轮运行），跳过回填")
        return 0
    except json.JSONDecodeError as exc:
        logger.warning(f"[siteshot] baseline 解析失败，跳过回填: {exc}")
        return 0

    with open(target_path, encoding="utf-8") as f:
        data = json.load(f)

    idx = _build_index(baseline.get("link_data") or [])
    filled = 0
    before_empty = 0
    for item in data.get("link_data") or []:
        if (item.get("siteshot") or "").strip():
            continue
        before_empty += 1
        hit = _lookup(idx, item.get("link", ""), item.get("name", ""))
        shot = (hit.get("siteshot") or "").strip()
        if shot:
            item["siteshot"] = shot
            # 时间戳跟随回填，供截图时效（refresh_days）计算图龄；
            # baseline 无时间戳的历史图以当前时间作为刷新周期起点
            item["sitetshot_at"] = (hit.get("sitetshot_at") or "").strip() or datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
            filled += 1

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    logger.info(f"[siteshot] 回填完成：找回 {filled} 张，仍缺 {before_empty - filled} 张")
    return filled
