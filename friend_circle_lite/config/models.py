"""Application configuration models.

This module converts the raw YAML structure into typed configuration objects so
that the rest of the application can depend on explicit fields instead of a
loosely typed nested dictionary.

The external YAML keys are preserved for backward compatibility. Internally,
snake_case names are used consistently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_CACHE_FILE = "./temp/cache.sqlite3"
DEFAULT_ALL_JSON = "./all.json"
DEFAULT_ERRORS_JSON = "./errors.json"
DEFAULT_LINK_JSON = "./link.json"


def _as_bool(value: object, default: bool = False) -> bool:
    """稳健解析布尔值，兼容 YAML 布尔与字符串写法。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_flag(name: str) -> bool | None:
    """读取布尔环境变量；未配置时返回 None，避免覆盖配置文件。"""
    value = os.getenv(name)
    if value is None:
        return None
    return _as_bool(value)


@dataclass(slots=True)
class MergeSettings:
    """Options for merging local crawl results with remote data sources."""

    enable: bool = False
    remote_base_url: str = ""
    merge_article_data: bool = True
    merge_link_check_data: bool = True


@dataclass(slots=True)
class FriendsInputSettings:
    """友链字段桥接映射（读取外部端点与发布 friends.json 共用一份）。

    rename: 键 = FCL 内部字段名，值 = 外部（端点/博客）字段名；仅重命名，不增删数据。
    例：{name: title, link: siteurl, avatar: imgurl} 表示外部用 title/siteurl/imgurl。
    读取时按此映射从端点取 FCL 字段；发布时按同一映射把 FCL 字段重命名为外部字段名。
    未配置映射的字段按原字段名处理；映射整体留空则读取退回内置别名、发布原样输出。
    """

    rename: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProxySettings:
    """Proxy configuration for both link checking and RSS crawling."""

    proxy_url: str = ""


@dataclass(slots=True)
class SpiderSettings:
    """Crawler settings controlling source list and output density."""

    enable: bool = True
    # 友链真源模式：
    #   local（默认）= 真源在 FCL 仓库 checkout 下来的 friends.json（直接读本地文件 local_friends_file，
    #                  不依赖实时 github main，避免镜像竞态下 raw 端点翻面导致爬不到数据）。
    #   remote        = 真源在外部博客友链端点（json_url 指向该端点，如 https://你的博客/friends.json）。
    #   remote 模式下 FCL 仅作巡检展示，不维护/写回本地 friends.json，
    #   对应的 Issue 自助申请与巡检回评整套关闭（见 apply_friend / cnb_issue_report）。
    source: str = "local"
    json_url: str = ""
    # local 模式下读取的本地友链真源文件路径（相对仓库根/工作目录），默认 friends.json。
    local_friends_file: str = "friends.json"
    article_count: int = 5
    # 真源在外部端点（source=remote）时，端点 JSON 的顶层键名，默认 friends。
    # 别人的端点可能用 link_list / data 等，配这里即可，FCL 不再硬编码 friends。
    list_key: str = "friends"
    # 外部端点字段名 ↔ FCL 内部字段名 的桥接映射（键=FCL字段，值=外部字段名）。
    # 读取时按此映射从端点取 FCL 字段；发布 friends.json 时按同一份映射重命名输出（正反复用）。
    # 不配置则读取侧退回 name/title 等内置别名，发布侧原样输出。
    friends_input: FriendsInputSettings = field(default_factory=FriendsInputSettings)


@dataclass(slots=True)
class LinkCheckConfig:
    """Settings for friend link reachability checks."""

    # 兼容旧配置字段。当前抓取流程依赖可达性检测，因此运行时会始终视为启用。
    enable: bool = True
    max_age_hours: int = 24
    timeout: int = 15
    max_workers: int = 10
    status_api_url: str = "https://v2.xxapi.cn/api/status?url={url}"
    enable_backlink_check: bool = False
    author_url: str = ""
    eo_ping_url: str = ""
    # 静态 HTML 未命中作者域名时，退化用无头浏览器渲染后再判定。
    # 仅对 VitePress / Astro 等客户端渲染友链的站点生效；未安装 playwright 时自动跳过。
    backlink_headless: bool = True


@dataclass(slots=True)
class EmailPushConfig:
    """Reserved configuration for the not-yet-implemented email push feature."""

    enable: bool = False
    to_email: str = ""
    subject: str = ""
    body_template: str = ""


@dataclass(slots=True)
class WebsiteInfo:
    """Display metadata for outbound notifications."""

    title: str = ""


@dataclass(slots=True)
class RssSubscribeConfig:
    """Configuration for GitHub issue based email subscriptions."""

    enable: bool = False
    github_username: str = ""
    github_repo: str = ""
    your_blog_url: str = ""
    email_template: str = ""
    website_info: WebsiteInfo = field(default_factory=WebsiteInfo)


@dataclass(slots=True)
class SmtpConfig:
    """SMTP connection settings used by all mail sending features."""

    email: str = ""
    server: str = ""
    port: int = 0
    use_tls: bool = True


@dataclass(slots=True)
class GeoDiagnoseSettings:
    """不可达站点的地域屏蔽二次诊断（旁路写回 link.json）。"""

    enable: bool = True
    cn_probe: bool = True


@dataclass(slots=True)
class SiteshotSettings:
    """友链主页截图回填与图床上传（增量，只补缺口）。"""

    enable: bool = True
    upload_folder: str = "friends"
    max_workers: int = 2
    upload_url: str = ""
    # 截图有效期（天）：图龄超过该天数的站点重新截图；0=永久有效（上游默认行为）
    refresh_days: int = 0


@dataclass(slots=True)
class AlertSettings:
    """状态翻转告警渠道。密钥类仅走环境变量，不落配置文件。"""

    enable: bool = True
    qq_bot_alert_url: str = ""
    qq_bot_alert_token: str = ""
    wecom_webhook_url: str = ""
    # 持续不可达天数阈值：站点已挂超过该天数才追加「持续离线」提醒（0=关闭该功能）。
    # 仅在“本轮天数跨过阈值且上轮已处于不可达”时触发一次，避免每次巡检重复推送。
    down_days_threshold: int = 0


@dataclass(slots=True)
class PostprocessSettings:
    """主检测完成后的旁路处理总成（geo 诊断 / 截图回填 / 状态告警）。"""

    enable: bool = True
    geo_diagnose: GeoDiagnoseSettings = field(default_factory=GeoDiagnoseSettings)
    siteshot: SiteshotSettings = field(default_factory=SiteshotSettings)
    alert: AlertSettings = field(default_factory=AlertSettings)


@dataclass(slots=True)
class RuntimePaths:
    """Filesystem locations used by the runtime."""

    cache_file: str = DEFAULT_CACHE_FILE
    all_json_file: str = DEFAULT_ALL_JSON
    errors_json_file: str = DEFAULT_ERRORS_JSON
    link_json_file: str = DEFAULT_LINK_JSON


@dataclass(slots=True)
class ApplicationConfig:
    """Root application configuration assembled from the YAML file."""

    spider_settings: SpiderSettings
    proxy_settings: ProxySettings
    merge_settings: MergeSettings
    link_check: LinkCheckConfig
    email_push: EmailPushConfig
    rss_subscribe: RssSubscribeConfig
    smtp: SmtpConfig
    specific_rss: list[dict]
    postprocess: PostprocessSettings = field(default_factory=PostprocessSettings)
    runtime_paths: RuntimePaths = field(default_factory=RuntimePaths)
    future_article_tolerance_days: int = 2
    debug: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ApplicationConfig":
        """Create a typed config object from the raw YAML dictionary."""
        spider_raw = data.get("spider_settings", {})
        proxy_raw = data.get("proxy_settings", {})
        merge_raw = data.get("merge_settings", {})
        link_check_raw = data.get("link_check", {})
        email_push_raw = data.get("email_push", {})
        rss_subscribe_raw = data.get("rss_subscribe", {})
        website_info_raw = rss_subscribe_raw.get("website_info", {})
        smtp_raw = data.get("smtp", {})
        runtime_raw = data.get("runtime_paths", {})
        post_raw = data.get("postprocess", {}) or {}
        friends_input_raw = spider_raw.get("friends_input", {}) or {}
        rename_raw = friends_input_raw.get("rename", {}) or {}
        geo_raw = post_raw.get("geo_diagnose", {}) or {}
        siteshot_raw = post_raw.get("siteshot", {}) or {}
        alert_raw = post_raw.get("alert", {}) or {}
        debug_from_env = _env_flag("FCL_DEBUG")
        debug_enabled = debug_from_env if debug_from_env is not None else _as_bool(data.get("debug"), False)
        geo_probe_env = _env_flag("GEO_CN_PROBE")

        return cls(
            spider_settings=SpiderSettings(
                enable=bool(spider_raw.get("enable", True)),
                source=str(spider_raw.get("source", "local")).strip().lower() or "local",
                json_url=str(spider_raw.get("json_url", "")).strip(),
                local_friends_file=str(spider_raw.get("local_friends_file", "friends.json")).strip() or "friends.json",
                article_count=int(spider_raw.get("article_count", 5)),
                list_key=str(spider_raw.get("list_key", "friends")).strip() or "friends",
                friends_input=FriendsInputSettings(
                    rename={
                        str(k).strip(): str(v).strip()
                        for k, v in rename_raw.items()
                        if str(k).strip() and str(v).strip() and str(k).strip() != str(v).strip()
                    },
                ),
            ),
            proxy_settings=ProxySettings(
                proxy_url=os.getenv("PROXY_URL") or str(proxy_raw.get("proxy_url", "")).strip(),
            ),
            merge_settings=MergeSettings(
                enable=bool(merge_raw.get("enable", False)),
                remote_base_url=str(merge_raw.get("remote_base_url", "")).strip(),
                merge_article_data=bool(merge_raw.get("merge_article_data", True)),
                merge_link_check_data=bool(merge_raw.get("merge_link_check_data", True)),
            ),
            link_check=LinkCheckConfig(
                enable=True,
                max_age_hours=int(link_check_raw.get("max_age_hours", 24)),
                timeout=int(link_check_raw.get("timeout", 15)),
                max_workers=int(link_check_raw.get("max_workers", 10)),
                status_api_url=str(link_check_raw.get("status_api_url", "https://v2.xxapi.cn/api/status?url={url}")).strip(),
                enable_backlink_check=bool(link_check_raw.get("enable_backlink_check", False)),
                author_url=str(link_check_raw.get("author_url", "")).strip(),
                eo_ping_url=os.getenv("EO_PING_URL") or str(link_check_raw.get("eo_ping_url", "")).strip(),
                backlink_headless=_env_flag("BACKLINK_HEADLESS")
                if _env_flag("BACKLINK_HEADLESS") is not None
                else _as_bool(link_check_raw.get("backlink_headless", True), True),
            ),
            email_push=EmailPushConfig(
                enable=bool(email_push_raw.get("enable", False)),
                to_email=str(email_push_raw.get("to_email", "")).strip(),
                subject=str(email_push_raw.get("subject", "")).strip(),
                body_template=str(email_push_raw.get("body_template", "")).strip(),
            ),
            rss_subscribe=RssSubscribeConfig(
                enable=bool(rss_subscribe_raw.get("enable", False)),
                github_username=str(rss_subscribe_raw.get("github_username", "")).strip(),
                github_repo=str(rss_subscribe_raw.get("github_repo", "")).strip(),
                your_blog_url=str(rss_subscribe_raw.get("your_blog_url", "")).strip(),
                email_template=str(rss_subscribe_raw.get("email_template", "")).strip(),
                website_info=WebsiteInfo(
                    title=str(website_info_raw.get("title", "")).strip(),
                ),
            ),
            smtp=SmtpConfig(
                email=str(smtp_raw.get("email", "")).strip(),
                server=str(smtp_raw.get("server", "")).strip(),
                port=int(smtp_raw.get("port", 0) or 0),
                use_tls=bool(smtp_raw.get("use_tls", True)),
            ),
            postprocess=PostprocessSettings(
                enable=_as_bool(post_raw.get("enable"), True),
                geo_diagnose=GeoDiagnoseSettings(
                    enable=_as_bool(geo_raw.get("enable"), True),
                    cn_probe=geo_probe_env if geo_probe_env is not None else _as_bool(geo_raw.get("cn_probe"), True),
                ),
                siteshot=SiteshotSettings(
                    enable=_as_bool(siteshot_raw.get("enable"), True),
                    upload_folder=str(siteshot_raw.get("upload_folder", "friends")).strip() or "friends",
                    max_workers=int(siteshot_raw.get("max_workers", 2) or 2),
                    upload_url=os.getenv("IMG_UPLOAD_URL") or str(siteshot_raw.get("upload_url", "")).strip(),
                    refresh_days=int(siteshot_raw.get("refresh_days", 0) or 0),
                ),
                alert=AlertSettings(
                    enable=_as_bool(alert_raw.get("enable"), True),
                    # 敏感/部署相关项：环境变量优先（同 proxy_url / eo_ping_url 惯例）
                    qq_bot_alert_url=os.getenv("QQ_BOT_ALERT_URL") or str(alert_raw.get("qq_bot_alert_url", "")).strip(),
                    # 密钥类仅走环境变量，不提供 yaml 字段（同 SMTP_PWD 惯例）
                    qq_bot_alert_token=os.getenv("QQ_BOT_ALERT_TOKEN", "").strip(),
                    wecom_webhook_url=os.getenv("WECOM_WEBHOOK_URL") or str(alert_raw.get("wecom_webhook_url", "")).strip(),
                    down_days_threshold=int(alert_raw.get("down_days_threshold", 0) or 0),
                ),
            ),
            specific_rss=list(data.get("specific_RSS", []) or []),
            runtime_paths=RuntimePaths(
                cache_file=str(runtime_raw.get("cache_file", DEFAULT_CACHE_FILE)).strip() or DEFAULT_CACHE_FILE,
                all_json_file=str(runtime_raw.get("all_json_file", DEFAULT_ALL_JSON)).strip() or DEFAULT_ALL_JSON,
                errors_json_file=str(runtime_raw.get("errors_json_file", DEFAULT_ERRORS_JSON)).strip() or DEFAULT_ERRORS_JSON,
                link_json_file=str(runtime_raw.get("link_json_file", DEFAULT_LINK_JSON)).strip() or DEFAULT_LINK_JSON,
            ),
            debug=debug_enabled,
        )


@dataclass(slots=True)
class MailRuntime:
    """Runtime SMTP credentials resolved from configuration and environment."""

    sender_email: str
    smtp_server: str
    port: int
    password: str
    use_tls: bool

    @property
    def is_ready(self) -> bool:
        """Whether enough information is available to send email."""
        return bool(self.sender_email and self.smtp_server and self.port and self.password)
