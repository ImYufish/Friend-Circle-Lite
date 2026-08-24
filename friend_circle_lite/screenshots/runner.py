# -*- coding: utf-8 -*-
"""
runner.py - 截图运行入口（FCL 版）

读取检测主流程产出的 link.json，对可达但缺截图（或截图已过期）的友链执行：
  Selenium 本地截图 → 上传图床 → 失败兜底 thum.io 在线截图
完成后把 siteshot / sitetshot_at 字段写回 link.json。

增量策略：已有有效 siteshot（非空且非 thum.io 占位）的站点直接跳过，
因此本入口可以每轮检测后安全地重复执行，只补缺口。
时效策略：conf.yaml postprocess.siteshot.refresh_days > 0 时，
截图时间（sitetshot_at）超过该天数的站点视为过期，重新截图刷新。
"""

from __future__ import annotations

import json
import logging
import os
import concurrent.futures
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from friend_circle_lite.screenshots.screenshot import take_screenshot, resolve_driver_path, _build_thumio_url

logging.basicConfig(
    level=logging.INFO,
    format="📸 %(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

SHOT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

LINK_FILE = os.getenv("LINK_CURRENT", "./link.json")
_RAW_TARGET = os.getenv("TARGET_LINK", "").strip()
TARGET_LINK_LIST: list[str] = [p.strip() for p in _RAW_TARGET.replace(",", "|").split("|") if p.strip()]


def host_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


def _is_usable(shot: str | None) -> bool:
    """有效截图 = 已上传图床的真图；thum.io 是占位兜底，视为待重试。"""
    return bool((shot or "").strip()) and "thum.io" not in shot


def shot_now() -> str:
    """当前截图时间戳（上海时区），随 siteshot 一起写入 link.json。"""
    return datetime.now(SHANGHAI_TZ).strftime(SHOT_TIME_FORMAT)


def _is_expired(item: dict, refresh_days: int, now: datetime | None = None) -> bool:
    """已有有效截图但图龄超过 refresh_days 天 → 过期需重截。

    refresh_days <= 0 表示永不过期（上游默认行为）；
    无时间戳的历史图不在此处强制刷新，由 merge-shot 补记时间起点后进入周期。
    """
    if refresh_days <= 0 or not _is_usable(item.get("siteshot")):
        return False
    ts = (item.get("sitetshot_at") or "").strip()
    if not ts:
        return False
    try:
        shot_time = datetime.strptime(ts, SHOT_TIME_FORMAT).replace(tzinfo=SHANGHAI_TZ)
    except ValueError:
        return False
    now = now or datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)
    return (now - shot_time).total_seconds() >= refresh_days * 86400


def main() -> None:
    # 开关与参数按 FCL 原生配置语义：conf.yaml 的 postprocess.siteshot 为准，
    # 显式设置的环境变量（CI Secrets / 临时覆盖）优先于 yaml 值。
    workers_default = 2
    refresh_days = 0
    try:
        from friend_circle_lite.utils.config import load_config

        site_cfg = load_config("./conf.yaml").postprocess.siteshot
        if not site_cfg.enable:
            logger.info("postprocess.siteshot.enable=false，跳过截图回填")
            return
        os.environ.setdefault("IMG_UPLOAD_FOLDER", site_cfg.upload_folder)
        if site_cfg.upload_url:
            os.environ.setdefault("IMG_UPLOAD_URL", site_cfg.upload_url)
        if site_cfg.max_workers and site_cfg.max_workers > 0:
            workers_default = int(site_cfg.max_workers)
        refresh_days = max(0, int(site_cfg.refresh_days or 0))
        logger.info(
            f"[config] 截图配置：目录={os.environ.get('IMG_UPLOAD_FOLDER', 'friends')}，并发默认 {workers_default}，"
            f"截图刷新周期 {refresh_days if refresh_days > 0 else '无限'} 天"
        )
    except Exception as e:
        logger.warning(f"[config] 读取 conf.yaml 失败，使用内置默认：{e}")

    workers = int(os.getenv("SCREENSHOT_WORKERS") or workers_default)

    if not os.path.exists(LINK_FILE):
        logger.error(f"找不到 {LINK_FILE}，请先运行检测主流程")
        return

    with open(LINK_FILE, encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("link_data") or []
    if not items:
        logger.warning("link_data 为空，跳过截图")
        return

    # 只对可达、且（缺有效截图 或 截图已过刷新周期）的友链截图（增量 + 时效）
    targets = [
        it for it in items
        if it.get("reachable") and (not _is_usable(it.get("siteshot")) or _is_expired(it, refresh_days))
    ]
    skipped = len(items) - len([it for it in items if it.get("reachable")]) - sum(
        1 for it in items if not it.get("reachable") and _is_usable(it.get("siteshot"))
    )
    if TARGET_LINK_LIST:
        targets = [
            it for it in items
            if any(t in it.get("name", "") or t in it.get("link", "") for t in TARGET_LINK_LIST)
        ]
        logger.info(f"指定目标过滤 '{_RAW_TARGET}'：匹配到 {len(targets)} 个友链")

    if not targets:
        logger.info(f"所有可达友链均已有有效截图（本次扫描 {len(items)} 条），无需补截")
        return

    expired = sum(1 for it in targets if _is_usable(it.get("siteshot")))
    logger.info(f"开始截图 {len(targets)} 个友链（缺图 {len(targets) - expired}，过期刷新 {expired}），并发数 {workers}")

    # 线程池启动前单线程预解析 chromedriver，避免并发下载竞态
    try:
        driver_path = resolve_driver_path()
        logger.info(f"chromedriver 路径已预解析：{driver_path}")
    except Exception as e:
        logger.warning(f"[driver] 预解析失败，worker 将各自回退解析：{e}")
        driver_path = None

    success = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_item = {
            executor.submit(take_screenshot, it["link"], host_from_url(it["link"]), driver_path): it
            for it in targets
        }
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                shot = future.result()
                item["siteshot"] = shot
                if "thum.io" in shot:
                    failed += 1
                    item.pop("sitetshot_at", None)  # 兜底占位图不算有效截图，清空时间戳以便下轮重试
                    logger.warning(f"⚠️ {item.get('name', item['link'])} → thum.io 兜底")
                else:
                    success += 1
                    item["sitetshot_at"] = shot_now()
                    logger.info(f"✅ {item.get('name', item['link'])} → {shot}")
            except Exception as e:
                failed += 1
                logger.error(f"❌ {item.get('name', item['link'])}: {e}")
                item["siteshot"] = _build_thumio_url(item["link"])
                item.pop("sitetshot_at", None)

    with open(LINK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    logger.info(f"截图完成：成功 {success}，兜底/失败 {failed}；结果已写回 {LINK_FILE}")


if __name__ == "__main__":
    main()
