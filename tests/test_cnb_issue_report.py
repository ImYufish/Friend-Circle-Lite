# -*- coding: utf-8 -*-
"""CNB Issue 巡检回评宽限期单测：should_report / anomaly_days 判定。

纯函数、离线，不发任何真实请求。
"""
import os
import sys
from pathlib import Path

# 让 scripts/ 进入导入路径（cnb_issue_report.py 在仓库根 scripts/ 下）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cnb_issue_report import anomaly_days, should_report  # noqa: E402

REASON_DOWN = "站点不可访问"
REASON_BACKLINK = "未检测到指向本站的真实友链"


def test_no_grace_period_always_reports():
    # min_days<=0 沿用旧行为：首日即回评
    assert should_report(None, REASON_DOWN, 0)[0] is True
    entry = {"unreachable_days": 1}
    assert should_report(entry, REASON_DOWN, 0)[0] is True


def test_missing_days_falls_back_to_report():
    # 缺天数元数据（旧数据/字段缺失）保守回评，避免漏报真实故障
    assert should_report({"unreachable_days": None}, REASON_DOWN, 10)[0] is True
    assert should_report({}, REASON_DOWN, 10)[0] is True


def test_below_threshold_skips():
    entry = {"unreachable_days": 3}
    ok, skip = should_report(entry, REASON_DOWN, 10)
    assert ok is False
    assert "3" in skip and "10" in skip


def test_at_or_above_threshold_reports():
    assert should_report({"unreachable_days": 10}, REASON_DOWN, 10)[0] is True
    assert should_report({"unreachable_days": 25}, REASON_DOWN, 10)[0] is True


def test_backlink_reason_uses_backlink_lost_days():
    # 反链缺失类异常取 backlink_lost_days，而非 unreachable_days
    entry = {"unreachable_days": 99, "backlink_lost_days": 2}
    assert anomaly_days(entry, REASON_BACKLINK) == 2
    ok, skip = should_report(entry, REASON_BACKLINK, 7)
    assert ok is False  # 2 < 7，跳过


def test_reachable_reason_uses_unreachable_days():
    entry = {"unreachable_days": 5, "backlink_lost_days": 99}
    assert anomaly_days(entry, REASON_DOWN) == 5


def test_env_override_min_days(monkeypatch, tmp_path):
    # 环境变量 CNB_ISSUE_REPORT_MIN_DAYS 覆盖 conf.yaml
    import importlib

    monkeypatch.setenv("CNB_ISSUE_REPORT_MIN_DAYS", "3")
    # 临时放一个 conf.yaml 验证「环境变量优先于配置」
    (tmp_path / "conf.yaml").write_text(
        "postprocess:\n  alert:\n    issue_report_min_days: 10\n", encoding="utf-8"
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mod = importlib.import_module("cnb_issue_report")
        imported = importlib.reload(mod)
        assert imported.load_min_days() == 3
    finally:
        os.chdir(cwd)
