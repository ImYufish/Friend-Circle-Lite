# -*- coding: utf-8 -*-
"""推送审计日志：每次推送尝试（成功/失败）都落盘一条 JSONL，便于事后排查漏推。

为什么需要它：QQ 经 blog-bot Worker 中转，偶发「发出去但没收到」。Worker 可能返回
200+ok=false（OpenAPI 实际发送失败）、可能超时、可能被限流——这些情况原代码只在
logger.warning 里闪一下，CI 跑完日志就没了，无法回看哪一轮漏了。这里把每一次
推送尝试（含 HTTP 状态码、响应、异常）持久化到文件，成功失败都写。

写盘策略：append-only JSONL，线程安全（模块级锁）；路径可配：
- 环境变量 PUSH_LOG_PATH 优先；
- 否则 AlertSettings.push_log_path（conf.yaml alert.push_log_path）；
- 两者都未设置则**不写盘**（默认关闭），需显式配置才启用，避免无配置时
  在仓库/CI 工作目录里产生 stray 文件。约定文件名 push_log.jsonl。
传入空路径（""）则完全禁用，不写文件（测试与一次性调用可借此避免产生文件）。
目标地址只记域名（netloc），不落 webhook key / token 等敏感片段。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_LOG = "push_log.jsonl"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _mask_target(url: str) -> str:
    """只保留域名，避免把 webhook key / token 写进日志。"""
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc
        return netloc or url
    except Exception:
        return url


def _trunc(s: str | None, n: int = 400) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _write(path: str, entry: dict) -> None:
    if not path:
        return
    line = json.dumps(entry, ensure_ascii=False)
    try:
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:  # 日志写盘失败绝不能影响主流程
        logger.warning(f"[push-log] 写入推送日志失败（{path}）：{exc}")


def log_push(
    path: str,
    channel: str,
    target: str,
    text: str,
    ok: bool,
    status: int | None = None,
    resp: str | None = None,
    error: str | None = None,
    note: str | None = None,
) -> None:
    """记录一次推送尝试（成功失败都记）。

    channel: "qq" / "wecom"
    target : 推送地址（仅记域名）
    ok     : 是否推送成功（Worker/接口确认收到）
    status : HTTP 状态码（异常时为 None）
    resp   : 响应体（截断）
    error  : 异常信息（无则 None）
    note   : 补充说明，如 "fallback after qq failure"
    """
    _write(
        path,
        {
            "ts": _now(),
            "event": "push",
            "channel": channel,
            "target": _mask_target(target),
            "ok": bool(ok),
            "status": status,
            "resp": _trunc(resp),
            "error": _trunc(error),
            "note": note or "",
            "text": _trunc(text),
        },
    )


def log_skip(path: str, reason: str, changes: int = 0) -> None:
    """记录一次「本应/可能推送但被跳过」的决策（配置关闭、渠道未配等）。

    用于区分「流水线跑了但决定不推」与「推了但没收到」，避免把正常跳过误判成漏推。
    """
    _write(
        path,
        {
            "ts": _now(),
            "event": "skip",
            "channel": "alert",
            "target": "",
            "ok": False,
            "status": None,
            "resp": "",
            "error": "",
            "note": reason,
            "text": f"变化数 {changes}",
        },
    )


def resolve_log_path(settings=None) -> str:
    """解析实际日志路径：环境变量 > 配置 > 默认（默认关闭写盘）。

    未配置任何路径时返回空串，调用方据此不写文件。conf.yaml 已设
    push_log_path: "push_log.jsonl"，故真实运行会写；仅测试/未配置文件不落盘。
    """
    env = os.getenv("PUSH_LOG_PATH", "").strip()
    if env:
        return env
    if settings is not None:
        cfg = (getattr(settings, "push_log_path", "") or "").strip()
        if cfg:
            return cfg
    return ""
