"""Domain models for Friend-Circle-Lite.

These models centralize the core concepts used across the crawler so that the
transport layer, parsing logic, cache logic, and output formatting can evolve
independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MIN_RECORDED_LATENCY = 0.01
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def normalize_latency(value: float | int | str | None, default: float = MIN_RECORDED_LATENCY) -> float:
    """规范化延迟值，对外记录时不使用 0 或负数表示未知。"""
    try:
        latency = float(value)
    except (TypeError, ValueError):
        return default
    if latency <= 0:
        return default
    return max(round(latency, 2), default)


def pick_display_latency(latency: float | int | None, latency_cn: float | int | None) -> float:
    """自动选取展示用延迟：取美国视角(latency)与国内视角(latency_cn)中更小的一个。

    语义：对国内源站，latency_cn(广州->国内)通常更小 -> 显示国内真实延迟；
    对国外源站，latency(美国->海外)通常更小 -> 自动退回显示美国视角(即该站自身响应速度)。
    两者皆无效(-1/None)时返回 -1。由此无需手动区分各友链源站地域。
    """
    cands: list[float] = []
    for v in (latency, latency_cn):
        if isinstance(v, (int, float)) and v > 0:
            cands.append(float(v))
    return min(cands) if cands else -1


def normalize_homepage_url(url: str) -> str:
    """规范化站点主页 URL，用于缓存匹配和持久化。"""
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme and parts.netloc:
            path = (parts.path.rstrip("/") + "/") if parts.path else "/"
            return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment))
    except Exception:
        pass
    return value.rstrip("/") + "/"


def calculate_elapsed_days(started_at: str) -> int | None:
    """Return rounded-up elapsed days for a persisted timestamp."""
    if not started_at:
        return None
    try:
        since = datetime.strptime(started_at, DATETIME_FORMAT)
    except ValueError:
        return None
    elapsed_seconds = max(0, (datetime.now() - since).total_seconds())
    return max(1, ceil(elapsed_seconds / 86400))


@dataclass(slots=True)
class Website:
    """Represents a friend website entry from the upstream friend list."""

    name: str
    url: str
    avatar: str = ""
    linkpage: str = ""
    verified: bool = False

    def __post_init__(self) -> None:
        self.url = normalize_homepage_url(self.url)

    @classmethod
    def from_friend_item(cls, raw_friend: list | tuple | dict, mapping: dict[str, str] | None = None) -> "Website":
        """Create a website from common friend link structures.

        字典结构兼容两种来源：
        - FCL 原生：name / link(或 url) / avatar
        - 博客(my-blog)真源：title / siteurl / imgurl（内置别名兜底）
        - 外部端点自定义字段：通过 mapping（键=FCL字段，值=外部字段名）显式桥接，
          例如 {name: blog_title, link: site_url} 表示外部用 blog_title/site_url。
          配置了 mapping 时优先用映射字段，未配置 mapping 才退回内置别名。
        """
        if isinstance(raw_friend, dict):
            mp = mapping or {}

            def _pick(fcl_field: str, *builtin_aliases: str) -> object:
                # 优先用 mapping 指定的外部字段名；未配置 mapping 时退回内置别名兜底
                # （空串/None 均视为缺失，与原 name/title 的 `or` 行为一致）
                ext_key = mp.get(fcl_field, fcl_field)
                val = raw_friend.get(ext_key)
                if not mp and not val:
                    for alias in builtin_aliases:
                        v = raw_friend.get(alias)
                        if v:
                            val = v
                            break
                return val

            return cls(
                name=str(_pick("name", "title") or "").strip(),
                url=normalize_homepage_url(_pick("link", "siteurl", "url") or ""),
                avatar=str(_pick("avatar", "imgurl") or "").strip(),
                linkpage=str(_pick("linkpage") or "").strip(),
                verified=bool(_pick("verified")),
            )

        name = raw_friend[0]
        url = raw_friend[1]
        if len(raw_friend) > 3:
            linkpage = raw_friend[2]
            avatar = raw_friend[3]
        else:
            linkpage = ""
            avatar = raw_friend[2] if len(raw_friend) > 2 else ""
        return cls(name=str(name).strip(), url=normalize_homepage_url(url), avatar=str(avatar or "").strip(), linkpage=str(linkpage or "").strip(), verified=False)

    def to_error_payload(self) -> list[str]:
        """Return the legacy structure used by `errors.json`."""
        return [self.name, self.url, self.avatar]

    def to_public_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "url": self.url,
            "avatar": self.avatar,
            "linkpage": self.linkpage,
        }


@dataclass(slots=True)
class LinkMethodStatus:
    """Status for one link-check method."""

    success: bool = False
    status_code: int | None = None
    latency: float = -1

    def to_dict(self) -> dict[str, bool | int | float | None]:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "latency": self.latency,
        }


@dataclass(slots=True)
class LinkCheckRecord:
    """Reachability status for one friend website."""

    name: str
    url: str
    avatar: str = ""
    linkpage: str = ""
    checked_at: str = ""
    reachable: bool = False
    crawl_allowed: bool = False
    best_method: str = "none"
    best_latency: float = -1
    backlink_checked: bool = False
    has_author_link: bool = False
    rss_crawl_reason: str = "blocked_unreachable"
    last_post_published: str = ""
    last_post_days_ago: int | None = None
    unreachable_since: str = ""
    rss_unavailable_since: str = ""
    direct: LinkMethodStatus = field(default_factory=LinkMethodStatus)
    proxy: LinkMethodStatus = field(default_factory=LinkMethodStatus)
    api: LinkMethodStatus = field(default_factory=LinkMethodStatus)
    verified: bool = False
    latency_cn: float = -1
    latency_display: float = -1

    @classmethod
    def unchecked(cls, website: Website, checked_at: str = "") -> "LinkCheckRecord":
        return cls(name=website.name, url=website.url, avatar=website.avatar, linkpage=website.linkpage, checked_at=checked_at, verified=website.verified)

    def __post_init__(self) -> None:
        self.url = normalize_homepage_url(self.url)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "avatar": self.avatar,
            "linkpage": self.linkpage,
            "checked_at": self.checked_at,
            "reachable": self.reachable,
            "crawl_allowed": self.crawl_allowed,
            "best_method": self.best_method,
            "best_latency": self.best_latency,
            "backlink_checked": self.backlink_checked,
            "has_author_link": self.has_author_link,
            "rss_crawl_reason": self.rss_crawl_reason,
            "last_post_published": self.last_post_published,
            "last_post_days_ago": self.last_post_days_ago,
            "unreachable_since": self.unreachable_since,
            "unreachable_days": self.unreachable_days,
            "rss_unavailable_since": self.rss_unavailable_since,
            "rss_unavailable_days": self.rss_unavailable_days,
            "verified": self.verified,
            "latency_cn": self.latency_cn,
            "latency_display": self.latency_display,
            "methods": {
                "direct": self.direct.to_dict(),
                "proxy": self.proxy.to_dict(),
                "api": self.api.to_dict(),
            },
        }

    @property
    def unreachable_days(self) -> int | None:
        """Return rounded-up calendar duration for the current unreachable period."""
        if self.reachable:
            return None
        return calculate_elapsed_days(self.unreachable_since)

    @property
    def rss_unavailable_days(self) -> int | None:
        """Return rounded-up duration for continuous RSS unavailability."""
        if not self.reachable or self.crawl_allowed:
            return None
        return calculate_elapsed_days(self.rss_unavailable_since)

    def to_link_dict(self) -> dict[str, object]:
        latency = normalize_latency(self.best_latency) if self.reachable else -1
        return {
            "name": self.name,
            "link": self.url,
            "link_page": self.linkpage,
            "avatar": self.avatar,
            "reachable": self.reachable,
            "crawlable": self.crawl_allowed,
            "latency": latency,
            # latency_cn / latency_display 在未配置国内探针时为 -1（未知），直接透传 -1，
            # 不要交给 normalize_latency（会把 -1 转成 0.01 这类伪正常值）。
            "latency_cn": normalize_latency(self.latency_cn) if (self.reachable and self.latency_cn > 0) else -1,
            "latency_display": normalize_latency(self.latency_display) if (self.reachable and self.latency_display > 0) else -1,
            "verified": self.verified,
            "unreachable_days": self.unreachable_days,
            "unreachable_since": self.unreachable_since if not self.reachable else "",
            "has_backlink": self.has_author_link if self.backlink_checked else None,
            "updated": self.last_post_published,
            "stale_days": self.last_post_days_ago,
        }


@dataclass(slots=True)
class Article:
    """Represents one crawled article belonging to a website."""

    title: str
    author: str
    link: str
    published: str
    summary: str = ""
    content: str = ""
    avatar: str = ""

    def to_public_dict(self) -> dict[str, str]:
        """Return the legacy public article schema used by `all.json`."""
        return {
            "title": self.title,
            "created": self.published,
            "link": self.link,
            "author": self.author,
            "avatar": self.avatar,
        }

    def to_tracking_dict(self) -> dict[str, str]:
        """Return the article schema used by the latest article tracker."""
        return {
            "title": self.title,
            "author": self.author,
            "link": self.link,
            "published": self.published,
            "summary": self.summary,
            "content": self.content,
        }


@dataclass(slots=True)
class FeedEndpoint:
    """Represents a concrete feed endpoint and how it was found."""

    url: str
    feed_type: str
    source: str


@dataclass(slots=True)
class CacheRecord:
    """Represents one cached RSS endpoint mapping for a website."""

    name: str
    url: str
    source: str = "cache"

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "url": self.url,
        }


@dataclass(slots=True)
class CacheUpdate:
    """Describes how a crawl should update the persisted RSS cache."""

    action: str = "none"
    name: str | None = None
    url: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, str | None]:
        return {
            "action": self.action,
            "name": self.name,
            "url": self.url,
            "reason": self.reason,
        }


@dataclass(slots=True)
class CrawlResult:
    """Represents the crawl result for a single website."""

    website: Website
    status: str
    articles: list[Article] = field(default_factory=list)
    feed_url: str | None = None
    feed_type: str = "none"
    source_used: str = "none"
    cache_update: CacheUpdate = field(default_factory=CacheUpdate)

    def to_legacy_dict(self) -> dict[str, object]:
        return {
            "name": self.website.name,
            "status": self.status,
            "articles": [article.to_public_dict() for article in self.articles],
            "feed_url": self.feed_url,
            "feed_type": self.feed_type,
            "cache_update": self.cache_update.to_dict(),
            "source_used": self.source_used,
        }


@dataclass(slots=True)
class CrawlStatistics:
    """Aggregated crawl statistics for the generated `all.json` output."""

    friends_num: int = 0
    active_num: int = 0
    error_num: int = 0
    article_num: int = 0
    last_updated_time: str = ""

    @classmethod
    def create(cls, friends_num: int, active_num: int, error_num: int, article_num: int) -> "CrawlStatistics":
        return cls(
            friends_num=friends_num,
            active_num=active_num,
            error_num=error_num,
            article_num=article_num,
            last_updated_time=datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "friends_num": self.friends_num,
            "active_num": self.active_num,
            "error_num": self.error_num,
            "article_num": self.article_num,
            "last_updated_time": self.last_updated_time,
        }
