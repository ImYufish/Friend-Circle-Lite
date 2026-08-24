"""无头浏览器反链检测兜底。

部分友链站（VitePress / Astro 等 SPA）在初始 HTML 中不含友链锚点，友链由
客户端 JavaScript 运行时渲染或经 fetch 拉取。静态抓取会漏判，此处用 Playwright
无头渲染页面后再做作者域名子串匹配。

仅在静态检测未命中且配置 ``backlink_headless=true`` 时作为兜底调用。若运行环境
未安装 ``playwright`` / ``chromium``，``available()`` 返回 ``False``，调用方静默
回退到静态结果，不影响既有流程，也不产生额外开销。

线程模型（重要）：友链检测跑在 ``ThreadPoolExecutor`` 的多个 worker 线程里，而
Playwright 同步 API 绑定创建它的线程（内部依赖 greenlet），跨线程复用同一实例
会抛 ``Cannot switch to a different thread``。因此这里用一个专属后台线程独占
playwright 与 chromium 实例，所有渲染请求经队列串行投递给该线程执行，调用线程
只通过 ``Future`` 取结果。
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future

_PW_AVAILABLE = False
_sync_playwright = None
try:
    from playwright.sync_api import sync_playwright as _sync_playwright  # noqa: F401

    _PW_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖可选
    _sync_playwright = None

_LINK_CHECK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 "
    "(Friend-Circle-Lite/2.0; +https://github.com/willow-god/Friend-Circle-Lite)"
)

_state_lock = threading.Lock()
_task_queue: "queue.Queue | None" = None
_worker_thread: threading.Thread | None = None


def available() -> bool:
    """运行环境是否具备无头渲染能力（已安装 playwright）。"""
    return _PW_AVAILABLE


def _build_variants(author_url: str) -> set[str]:
    if not author_url:
        return set()
    # 先剥离已有协议，避免重复拼接出 https://https://... 这类非法串。
    bare = author_url
    for scheme in ("https://", "http://"):
        if bare.startswith(scheme):
            bare = bare[len(scheme):]
            break
    bare = bare.rstrip("/")
    base = "https://" + bare
    return {
        base,
        base.replace("https://", "http://"),
        base.replace("https://", "//"),
        base.replace("https://", ""),
        bare,
        "//" + bare,
        "https://" + bare,
        "http://" + bare,
    }


def _match(variants: set[str], content: str) -> bool:
    if not content:
        return False
    for variant in variants:
        if (
            f'href="{variant}"' in content
            or f"href='{variant}'" in content
            or f'href="{variant}/"' in content
            or f"href='{variant}/'" in content
            or variant in content
        ):
            return True
    return False


def _render_and_match(browser, linkpage_url: str, variants: set[str], timeout: int) -> bool:
    """在专职线程内执行：开 context 渲染页面并匹配作者域名。"""
    context = browser.new_context(user_agent=_LINK_CHECK_UA)
    page = context.new_page()
    try:
        try:
            page.goto(linkpage_url, wait_until="networkidle", timeout=timeout * 1000)
        except Exception:
            page.goto(linkpage_url, wait_until="load", timeout=timeout * 1000)
        # 给客户端渲染 / 异步 fetch 一点时间收尾。
        page.wait_for_timeout(1200)
        content = page.content()
    finally:
        try:
            context.close()
        except Exception:
            pass
    return _match(variants, content)


def _worker_loop(tasks: "queue.Queue") -> None:
    """专职渲染线程：惰性启动 chromium，串行消费渲染任务直到哨兵。"""
    pw = None
    browser = None
    try:
        while True:
            item = tasks.get()
            if item is None:
                return
            future: Future = item["future"]
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if browser is None:
                    pw = _sync_playwright().start()
                    browser = pw.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                    )
                result = _render_and_match(browser, item["linkpage"], item["variants"], item["timeout"])
                future.set_result(result)
            except BaseException as exc:  # noqa: BLE001 - 必须回填 Future，否则调用方死等
                future.set_exception(exc)
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


def _ensure_worker() -> "queue.Queue":
    global _task_queue, _worker_thread
    with _state_lock:
        if _task_queue is None or _worker_thread is None or not _worker_thread.is_alive():
            _task_queue = queue.Queue()
            _worker_thread = threading.Thread(
                target=_worker_loop,
                args=(_task_queue,),
                name="fcl-headless-checker",
                daemon=True,
            )
            _worker_thread.start()
        return _task_queue


def check_or_false(linkpage_url: str, author_url: str, timeout: int = 20) -> bool:
    """渲染页面并判定是否包含作者域名。任何失败均返回 ``False``。

    可在任意线程调用：内部把任务转交给专职渲染线程，规避 Playwright 同步 API
    的单线程限制。
    """
    if not _PW_AVAILABLE:
        return False
    variants = _build_variants(author_url)
    if not variants or not linkpage_url:
        return False
    try:
        tasks = _ensure_worker()
    except Exception as exc:
        logging.warning(f"[反链-无头] 渲染线程启动失败 {linkpage_url}: {exc}")
        return False

    future: Future = Future()
    tasks.put({"future": future, "linkpage": linkpage_url, "variants": variants, "timeout": timeout})
    try:
        # 单任务自身有 goto 超时；这里再兜底排队等待与浏览器冷启动的开销。
        return bool(future.result(timeout=max(180, timeout * 24)))
    except Exception as exc:
        logging.warning(f"[反链-无头] 渲染失败 {linkpage_url}: {exc}")
        return False


def shutdown() -> None:
    """释放无头浏览器进程。重复调用安全。"""
    global _task_queue, _worker_thread
    with _state_lock:
        if _task_queue is not None and _worker_thread is not None and _worker_thread.is_alive():
            _task_queue.put(None)
            # 最坏情况：worker 正卡在一个 goto 超时里，等它处理完哨兵。
            _worker_thread.join(timeout=45)
        _task_queue = None
        _worker_thread = None
