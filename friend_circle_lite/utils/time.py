import logging
from dateutil import parser
from datetime import datetime, timezone, timedelta

def format_published_time(time_str):
    """
    格式化发布时间为统一格式 YYYY-MM-DD HH:MM

    参数:
    time_str (str): 输入的时间字符串，可能是多种格式。

    返回:
    str: 格式化后的时间字符串，若解析失败返回空字符串。
    """
    # 尝试自动解析输入时间字符串
    try:
        parsed_time = parser.parse(time_str, fuzzy=True)
    except (ValueError, parser.ParserError):
        # 定义支持的时间格式
        time_formats = [
            '%a, %d %b %Y %H:%M:%S %z',  # Mon, 11 Mar 2024 14:08:32 +0000
            '%a, %d %b %Y %H:%M:%S GMT',   # Wed, 19 Jun 2024 09:43:53 GMT
            '%Y-%m-%dT%H:%M:%S%z',         # 2024-03-11T14:08:32+00:00
            '%Y-%m-%dT%H:%M:%SZ',          # 2024-03-11T14:08:32Z
            '%Y-%m-%d %H:%M:%S',           # 2024-03-11 14:08:32
            '%Y-%m-%d'                     # 2024-03-11
        ]
        for fmt in time_formats:
            try:
                parsed_time = datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        else:
            logging.warning(f"无法解析时间字符串：{time_str}")
            return ''

    # 处理时区转换
    # 无时区偏移的时间：RSS 规范下应按发布站本地时间解释。本站友链以中文博客为主，
    # 其本地时间即北京时间(+8)；若仍按 UTC 解释会被再多 +8，导致显示时间比真实
    # 发布时间早 8 小时。故无偏移时直接按 +8 解释，与最终展示时区一致（astimezone(+8) 后不变）。
    if parsed_time.tzinfo is None:
        parsed_time = parsed_time.replace(tzinfo=timezone(timedelta(hours=8)))
    shanghai_time = parsed_time.astimezone(timezone(timedelta(hours=8)))
    return shanghai_time.strftime('%Y-%m-%d %H:%M')