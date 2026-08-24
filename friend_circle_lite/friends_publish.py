# -*- coding: utf-8 -*-
"""friends.json 输出格式转换：按 conf.yaml 的 spider_settings.friends_input.rename 配置重命名字段后发布。

用途：仓库源 friends.json 始终保持 FCL 原生字段命名（name/link/avatar/...）；
对外发布（page 分支 / fc.yufish.cn/friends.json）时可配置字段重命名，
例如对齐博客 my-blog 格式：

    spider_settings:
      friends_input:
        rename:
          name: title
          link: siteurl
          avatar: imgurl

不设置或留空则原样输出（仅透传）。用法：
    python -m friend_circle_lite.friends_publish --src friends.json --dst pages/friends.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from friend_circle_lite.utils.config import load_config

logging.basicConfig(level=logging.INFO, format="😋 %(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)


def transform_entry(entry: dict, rename: dict[str, str]) -> dict:
    """按映射重命名单条记录的字段；未映射字段保留原名，值原样透传。"""
    return {rename.get(key, key): value for key, value in entry.items()}


def transform_document(document: dict, rename: dict[str, str]) -> dict:
    """转换整个 friends.json 文档：顶层结构保留，仅对 friends 数组内条目做字段重命名。"""
    result = dict(document)
    friends = document.get("friends")
    if isinstance(friends, list):
        result["friends"] = [transform_entry(item, rename) if isinstance(item, dict) else item for item in friends]
    return result


def publish(src: str, dst: str, conf_path: str = "./conf.yaml") -> bool:
    """读取 src 的友链清单，按配置重命名字段后写入 dst。返回是否发生了重命名。"""
    settings = load_config(conf_path).spider_settings.friends_input
    rename = settings.rename

    source_path = Path(src)
    if not source_path.exists():
        logger.warning(f"[friends-publish] 源文件 {src} 不存在，跳过转换")
        return False

    document = json.loads(source_path.read_text(encoding="utf-8"))
    transformed = transform_document(document, rename) if rename else document

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps(transformed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    if rename:
        logger.info(f"[friends-publish] 已按映射 {rename} 输出 {dst}（{len(transformed.get('friends', []))} 条）")
    else:
        logger.info(f"[friends-publish] 未配置 friends_input.rename，{dst} 按源字段名原样输出")
    return bool(rename)


def main() -> int:
    parser = argparse.ArgumentParser(description="friends.json 发布格式转换")
    parser.add_argument("--src", default="./friends.json", help="源 friends.json 路径")
    parser.add_argument("--dst", default="./pages/friends.json", help="输出路径")
    parser.add_argument("--conf", default="./conf.yaml", help="配置文件路径")
    args = parser.parse_args()
    try:
        publish(args.src, args.dst, args.conf)
    except Exception as exc:  # 发布转换失败不应阻断整条流水线，但要让失败可见
        logger.error(f"[friends-publish] 转换失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
