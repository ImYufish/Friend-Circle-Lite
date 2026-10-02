# 全项目统一的 User-Agent：HTTP 请求（链接检测/RSS 抓取）与无头浏览器
# （Playwright 反链兜底、Selenium 截图）共用同一来源，避免各处硬编码漂移。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36 "
    "(Friend-Circle-Lite/2.0; +https://github.com/ImYufish/Friend-Circle-Lite)"
)

# 标准化的请求头
HEADERS_JSON = {
    "User-Agent": USER_AGENT,
    "X-Friend-Circle": "1.0"
}

HEADERS_XML = {
    "User-Agent": USER_AGENT,
    "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "X-Friend-Circle": "1.0"
}

timeout = (10, 15)