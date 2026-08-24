# -*- coding: utf-8 -*-
"""对 link.json 中不可达的友链做二次诊断，区分"地域屏蔽"与"真实故障"。

移植自 check-flink 的 geo_diagnose 思路：检测核心只产出 reachable 布尔值，
此处旁路读取 link.json，对所有 reachable=false 的站点调用
``link_checker.geo_diagnose.diagnose_access_failure`` 补充：

- ``geo_status``: "geo_blocked" | "error" | "unknown"
- ``geo_hint``:  "CN-block" | "response-403-text" | "cdn-waf" | "tcp-ok-http-fail" | null

结果直接写回 link.json，供前端展示与 Issue 回评脚本消费。
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def enrich(link_json_path: str, enable_cn_probe: bool | None = None) -> dict:
    """为 link.json 中不可达站点补充地域诊断字段。返回统计信息。

    幂等：已有 geo_status 的条目跳过。
    """
    # 延迟导入：link_checker 包的 __init__ 会拉起 service/crawler 链，
    # 顶层导入在 `python -m friend_circle_lite.postprocess` 入口下触发循环导入。
    from friend_circle_lite.link_checker.geo_diagnose import diagnose_access_failure

    if enable_cn_probe is None:
        enable_cn_probe = os.getenv("GEO_CN_PROBE", "1") != "0"

    try:
        with open(link_json_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"[geo] {link_json_path} 不存在，跳过地域诊断（请先运行检测主流程）")
        return {"checked": 0, "geo_blocked": 0, "error": 0}

    items = data.get("link_data") or []
    targets = [it for it in items if not it.get("reachable") and not it.get("geo_status")]
    if not targets:
        logger.info("[geo] 无新增不可达站点，跳过诊断")
        return {"checked": 0, "geo_blocked": 0, "error": 0}

    logger.info(f"[geo] 开始二次诊断 {len(targets)} 个不可达站点（国内探测={'开' if enable_cn_probe else '关'}）")
    stats = {"checked": 0, "geo_blocked": 0, "error": 0}
    for item in targets:
        url = item.get("link") or ""
        if not url:
            continue
        try:
            status, hint = diagnose_access_failure(url, None, None, enable_cn_probe=enable_cn_probe)
        except Exception as exc:  # 诊断本身绝不能影响主流程
            logger.warning(f"[geo] 诊断异常 {url}: {exc}")
            status, hint = "unknown", None
        item["geo_status"] = status
        item["geo_hint"] = hint
        stats["checked"] += 1
        if status == "geo_blocked":
            stats["geo_blocked"] += 1
        elif status == "error":
            stats["error"] += 1
        logger.info(f"[geo] {item.get('name', url)} -> {status}" + (f" ({hint})" if hint else ""))

    with open(link_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    logger.info(f"[geo] 诊断完成：{stats}")
    return stats
