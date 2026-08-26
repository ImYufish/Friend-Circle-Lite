# -*- coding: utf-8 -*-
"""后处理 CLI：python -m friend_circle_lite.postprocess <geo|merge-shot|alert|all>

典型 workflow 顺序（主检测 job 内，run.py 之后）：
  1. merge-shot  : 用 page 分支 baseline 回填 siteshot（避免截图丢失）
  2. geo         : 对不可达站点做地域屏蔽二次诊断
  3. alert       : 与 baseline 对比状态翻轉并推送告警（QQ 机器人为主，企业微信兜底）；
                    含「持续不可达」提醒（跟随 link_checker 退避档位 10/30/60 天触发）
                    与「反链长期缺失」提醒（见 conf.yaml alert.backlink_lost_days_threshold）

各步骤的开关集中在 conf.yaml 的 postprocess 段（enable / 子项 enable）；
环境变量仅用于敏感信息与 CI 场景的临时覆盖，语义与原生配置一致。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="😋 %(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

BASELINE = os.getenv("LINK_BASELINE", "./link.baseline.json")
CURRENT = os.getenv("LINK_CURRENT", "./link.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="FCL 检测后处理")
    parser.add_argument("command", choices=["geo", "merge-shot", "alert", "all"], help="要执行的后处理步骤")
    args = parser.parse_args()

    # FCL 已知导入顺序约束：link_checker <-> crawler 存在包级循环，
    # 独立入口必须先完整加载 crawler 打破环路（与 tests/ 同款处理）。
    import friend_circle_lite.crawler  # noqa: F401

    from friend_circle_lite.postprocess import geo_enrich, notify, siteshot_merge
    from friend_circle_lite.utils.config import load_config

    cfg = load_config("./conf.yaml").postprocess

    def _step(name: str, enabled: bool, fn) -> None:
        """统一开关判断：总开关关闭或子功能关闭时跳过并说明原因。"""
        if not cfg.enable:
            logger.info(f"[postprocess] 总开关 postprocess.enable=false，跳过 {name}")
            return
        if not enabled:
            logger.info(f"[postprocess] {name} 未启用（postprocess 配置），跳过")
            return
        fn()

    if args.command in ("merge-shot", "all"):
        _step("merge-shot(截图回填)", cfg.siteshot.enable, lambda: siteshot_merge.merge(BASELINE, CURRENT))
    if args.command in ("geo", "all"):
        _step(
            "geo(地域诊断)",
            cfg.geo_diagnose.enable,
            lambda: geo_enrich.enrich(CURRENT, enable_cn_probe=cfg.geo_diagnose.cn_probe),
        )
    if args.command in ("alert", "all"):
        _step("alert(状态告警)", cfg.alert.enable, lambda: notify.run(BASELINE, CURRENT, settings=cfg.alert))
    return 0


if __name__ == "__main__":
    sys.exit(main())
