# -*- coding: utf-8 -*-
"""URL 处理工具：链接归一化、对外请求安全校验、相对地址修正。"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from functools import lru_cache
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


def norm_link(url: str) -> str:
    """链接归一化：去 scheme、去尾部斜杠、统一小写，用于跨轮次比对同一友链。

    只归一到 host+path 级别，适用于「按站点归组」的场景（告警 diff、截图回填匹配）。
    不要用于需要保留完整 URL 结构的场合，同族函数语义各有侧重：
    - ``domain.models.normalize_homepage_url``：保留 scheme、补尾斜杠，用于缓存匹配；
    - ``link_checker.service._normalize_linkpage``：保留 scheme/path/query，用于友链页比较。
    """
    return re.sub(r"^https?://", "", (url or "").strip().lower()).rstrip("/")


def _ip_is_public(ip) -> bool:
    """IP 是否公网可路由（``is_global`` 为假即私网/环回/链路本地/保留/多播/未指定）。"""
    return bool(ip.is_global)


@lru_cache(maxsize=1024)
def _host_is_public(host: str) -> bool:
    """host 是否指向公网：IP 字面量直接判定，域名解析后要求全部记录均为公网。

    解析失败（DNS 错误）时返回 ``True``：解析不出 IP 就无法访问内网目标，交给正常
    请求流程去失败，避免 CI 的 DNS 抖动误伤正常友链。缓存以进程为生命周期，CI 单次
    运行内足够；结果不跨进程持久，避免长期缓存掩盖 DNS 变化。
    """
    host = (host or "").strip()
    if host.startswith("[") and host.endswith("]"):  # IPv6 字面量带方括号
        host = host[1:-1]
    if not host:
        return False
    try:
        return _ip_is_public(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    ips = {info[4][0] for info in infos if info[4]}
    if not ips:
        return True
    try:
        return all(_ip_is_public(ipaddress.ip_address(ip)) for ip in ips)
    except ValueError:
        return True


def is_safe_public_url(url: str) -> bool:
    """URL 是否可安全对外请求（SSRF 纵深防御）。

    规则：仅允许 http/https；host 为内网/保留段 IP 字面量则拒绝；host 为域名时解析后
    要求所有地址均为公网（同时挡住解析到私网的 DNS rebinding）。非法/无法判定的输入按
    「不安全」处理，调用方据此跳过请求并记日志——对外请求公共博客不应命中内网地址。
    """
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    return _host_is_public(host)


def replace_non_domain(link: str, blog_url: str) -> str:
    """把相对地址 / 本地地址修正为博客域名下的绝对地址。

    - 相对地址（无 scheme 与 netloc）→ 用 ``blog_url`` 拼接；
    - localhost / IP 字面量（含端口与 IPv6）→ 保留 path/query/fragment 拼到 ``blog_url``；
    - 其他绝对地址 → 原样返回。

    比旧的子串判断更精确：``notlocalhost.com`` 不再被误判为 localhost，IP 判定覆盖
    端口与 IPv6，不再依赖只认无端口 IPv4 的正则。
    """
    if not link:
        return link

    try:
        parsed = urlparse(link)

        # 情况1: 相对地址（没有 scheme 和 netloc）
        # 例如: "/post/article.html" 或 "post/article.html"
        if not parsed.scheme and not parsed.netloc:
            return urljoin(blog_url, link)

        # 情况2: localhost 或 IP 地址（精确匹配 host，避免误伤含 localhost 字样的正常域名）
        host = (parsed.hostname or "").lower()
        is_local = host == "localhost" or host.endswith(".localhost")
        if not is_local:
            try:
                ipaddress.ip_address(host)
                is_local = True
            except ValueError:
                is_local = False
        if is_local:
            # 提取 path + query + fragment
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            if parsed.fragment:
                path += "#" + parsed.fragment
            return urljoin(blog_url.rstrip("/") + "/", path.lstrip("/"))

        # 情况3: 正常的绝对地址，直接返回
        return link

    except Exception as e:
        logger.warning(f"替换链接时出错：{link}, error: {e}")
        return link
