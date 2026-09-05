#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CNB 议题版友链自助申请 —— 对应 GitHub 版 .github/workflows/apply-friend.yml

触发事件（在 .cnb.yml 的 $ 级配置）：issue.open / issue.reopen / issue.comment
处理流程：
  标题前缀 [友链申请] 门禁 → 解析正文（表单渲染格式 / 行式格式双兼容）
  → 必填校验 / 去重 → SSRF 防护 → 可访问性 + 反链核验（Playwright，纯静态兜底）
  → 通过（仅 local 模式）：写 friends.json 并推回 main（push 命中 ifModify 自动联动巡检），
           回评成功、移除 待更新、关闭议题
  → 失败（仅 local 模式）：回评原因、打 待更新 标签（申请人修复后回复即自动重验）
  → remote 模式（真源在外部博客端点）：**自助申请整套关闭**——开 [友链申请] 议题即回评
           「功能已关闭」并关闭议题，不做任何验证/写回；申请人请到博客仓库本地提交友链

与 GitHub 版的两处差异：
  - CNB 议题无表单模板，正文按「字段名：值」逐行填写（同时兼容 ### 表单渲染格式）
  - 重验防死循环不靠机器人账号识别，而是「最新评论者 != 令牌账号」才继续
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="💛 %(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("apply-friend")

API = os.getenv("CNB_API_ENDPOINT", "https://api.cnb.cool").rstrip("/")
TOKEN = os.getenv("CNB_TOKEN", "")
SLUG = os.getenv("CNB_REPO_SLUG", "")
EVENT = os.getenv("CNB_EVENT", "")
IID = os.getenv("CNB_ISSUE_IID", "")

TITLE_PREFIX = "[友链申请]"
LABEL_RETRY = "待更新"
UA = "Mozilla/5.0 (compatible; FriendLinkBot/1.0)"
TIMEOUT = 15


# ---------------- HTTP 基础 ----------------
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def api(method: str, path: str, ok_codes=(200, 201, 204), **kwargs):
    """调用 OpenAPI；失败抛异常，由调用方决定是否吞掉。"""
    url = f"{API}{path}"
    resp = requests.request(method, url, headers=_headers(), timeout=TIMEOUT, **kwargs)
    if resp.status_code not in ok_codes:
        raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


def unwrap_list(data) -> list:
    """接口返回可能是裸数组或 {list/items/data: [...]}，统一解包。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def get_issue():
    data = api("GET", f"/{SLUG}/-/issues/{IID}").json()
    # 列表模型可能不带 body，缺了就从动态流里找创建事件的正文
    if not (data.get("body") or "").strip():
        try:
            acts = unwrap_list(api("GET", f"/{SLUG}/-/issues/{IID}/activities").json())
            for act in acts:
                for key in ("body", "content"):
                    if isinstance(act.get(key), str) and act[key].strip():
                        data["body"] = act[key]
                        break
                if (data.get("body") or "").strip():
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("读取议题动态失败（不影响主流程）：%s", e)
    return data


def get_labels(issue: dict) -> list[str]:
    names = [
        (l.get("name") or l.get("title") or "")
        for l in unwrap_list(issue.get("labels"))
    ]
    return [n for n in names if n]


def comment_issue(text: str) -> None:
    api("POST", f"/{SLUG}/-/issues/{IID}/comments", json={"body": text})


def add_label(name: str) -> None:
    api("POST", f"/{SLUG}/-/issues/{IID}/labels", json={"labels": [name]})


def remove_label(name: str) -> None:
    api("DELETE", f"/{SLUG}/-/issues/{IID}/labels/{quote(name)}")


def close_issue() -> None:
    api("PATCH", f"/{SLUG}/-/issues/{IID}", json={"state": "closed"})


def token_username() -> str:
    """当前流水线令牌对应的账号，用于识别自己的回评、防止重验死循环。"""
    try:
        return (api("GET", "/user").json() or {}).get("username", "")
    except Exception as e:  # noqa: BLE001
        log.warning("获取令牌账号失败：%s", e)
        return ""


def latest_comment() -> dict:
    comments = unwrap_list(
        api("GET", f"/{SLUG}/-/issues/{IID}/comments", params={"page": 1, "page_size": 20}).json()
    )
    return comments[-1] if comments else {}


# ---------------- 正文解析（双格式兼容） ----------------
FIELDS = {
    "title": ("网站名称",),
    "siteurl": ("网站链接",),
    "linkpage": ("友链页面 URL", "友链页面URL", "友链页面"),
    "desc": ("网站描述",),
    "imgurl": ("网站头像 URL", "网站头像URL", "网站头像"),
}


def clean_url(raw: str) -> str:
    s = (raw or "").strip()
    md = re.match(r"\[[^\]]*\]\(\s*(https?://[^\s)]+)\s*\)", s, re.I)
    if md:
        return md.group(1)
    m = re.search(r"https?://[^\s)]+", s, re.I)
    return m.group(0) if m else s


def parse_body(body: str) -> dict:
    """GitHub 表单渲染格式（### 标签）与行式「标签：值」双兼容。"""
    out: dict[str, str] = {}

    def grab(labels: tuple[str, ...]) -> str:
        for label in labels:
            # 格式一：### 标签 换行 值
            m = re.search(rf"###\s*{re.escape(label)}\s*\n+([\s\S]*?)(?=\n###|$)", body)
            if m and m.group(1).strip():
                return m.group(1).strip()
            # 格式二：标签：值 / 标签: 值（整行匹配）
            m = re.search(rf"^\s*[*#\-\s]*{re.escape(label)}[*\s]*[:：]\s*(.+)$", body, re.M)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return ""

    for key, labels in FIELDS.items():
        out[key] = grab(labels)
    out["title"] = re.sub(r"\s+", " ", out["title"]).strip()
    out["siteurl"] = clean_url(out["siteurl"])
    out["linkpage"] = clean_url(out["linkpage"])
    return out


# ---------------- SSRF 防护 ----------------
def is_private_ip(ip: str) -> bool:
    if not ip:
        return True
    if ":" in ip:
        v = ip.lower()
        return v in ("::1", "::", "0:0:0:0:0:0:0:0") or v.startswith(("fe80", "fc", "fd"))
    parts = ip.split(".")
    if len(parts) != 4 or any(not p.isdigit() for p in parts):
        return True
    a, b = int(parts[0]), int(parts[1])
    return (
        a in (0, 10, 127)
        or (a == 169 and b == 254)
        or (a == 172 and 16 <= b <= 31)
        or (a == 192 and b == 168)
    )


def target_is_safe(url: str) -> bool:
    m = re.match(r"^https?://", url, re.I)
    if not m:
        return False
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split("@")[-1].split(":")[0]
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return all(not is_private_ip(info[4][0]) for info in infos)


# ---------------- 反链核验 ----------------
def load_author_url() -> str:
    env = os.getenv("AUTHOR_URL", "").strip()
    if env:
        return env
    try:
        import yaml

        cfg = yaml.safe_load(open("conf.yaml", encoding="utf-8")) or {}
        v = ((cfg.get("link_check") or {}).get("author_url") or "").strip()
        if v:
            return v if v.startswith("http") else f"https://{v}"
    except Exception as e:  # noqa: BLE001
        log.warning("读取 conf.yaml 的 author_url 失败，使用默认值：%s", e)
    return "https://x1anyu.cn"


def contains_author_link(html: str, author_url: str) -> bool:
    bare = re.sub(r"^https?://", "", author_url, flags=re.I).rstrip("/").lower()
    variants = {bare, "www." + bare}
    if bare.startswith("www."):
        variants.add(bare[4:])
    attr_re = re.compile(r"(?:href|data-url|data-link|data-href)\s*=\s*[\"']([^\"']+)[\"']", re.I)
    candidates = attr_re.findall(html)
    expanded = list(candidates)
    for c in candidates:
        expanded += ["https://" + x for x in re.findall(r"https?://([^/?#\s\"'>]+)", c)]
        expanded += ["//" + x for x in re.findall(r"(?<!:)//([^/?#\s\"'>]+)", c)]
    for u in expanded:
        host = re.sub(r"^https?://", "", u, flags=re.I).lstrip("/").split("/")[0].lower()
        if host in variants:
            return True
    return False


def verify(target: str, author_url: str) -> dict:
    result = {
        "reachable": False,
        "has_reciprocal": False,
        "status": 0,
        "reason": "",
        "pass": False,
        "engine": "playwright",
    }

    # 先试 Playwright 无头渲染（兼容 Astro/VitePress 等 JS 渲染友链页）
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(user_agent=UA)
            page.set_default_timeout(12000)
            resp = page.goto(target, wait_until="domcontentloaded", timeout=12000)
            result["status"] = resp.status if resp else 0
            if resp and resp.ok:
                result["reachable"] = True
                page.wait_for_timeout(2500)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
                result["has_reciprocal"] = contains_author_link(page.content(), author_url)
                if not result["has_reciprocal"]:
                    result["reason"] = f"未在你的友链页检测到指向 {author_url} 的真实友链"
            else:
                result["reason"] = f"站点不可访问（HTTP {result['status']}）"
    except Exception as e:  # noqa: BLE001
        log.warning("Playwright 验证异常，降级纯静态检测：%s", e)
        result["engine"] = "fetch-fallback"
        try:
            r = requests.get(target, timeout=12, headers={"User-Agent": UA}, allow_redirects=True)
            result["status"] = r.status_code
            if 200 <= r.status_code < 400:
                result["reachable"] = True
                result["has_reciprocal"] = contains_author_link(r.text, author_url)
                if not result["has_reciprocal"]:
                    result["reason"] = f"未检测到指向 {author_url} 的真实友链"
            else:
                result["reason"] = f"站点不可访问（HTTP {r.status_code}）"
        except Exception as e2:  # noqa: BLE001
            result["reason"] = f"访问失败：{e2}"
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    result["pass"] = result["reachable"] and result["has_reciprocal"]
    return result


# ---------------- 友链真源模式识别 ----------------
def load_friends_source() -> str:
    """读取 conf.yaml 的 spider_settings.source，决定本地维护还是外部端点真源。

    返回 'local' 或 'remote'，解析异常/缺字段时保守回退 'local'。
    """
    try:
        with open("conf.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        raw = (cfg.get("spider_settings") or {}).get("source", "local")
        return str(raw).strip().lower() or "local"
    except Exception as e:  # noqa: BLE001
        log.warning("读取 conf.yaml source 失败，按 local 处理：%s", e)
        return "local"


# ---------------- friends.json 写入 ----------------
def norm_link(u: str) -> str:
    return (u or "").strip().lower().replace("https://", "").replace("http://", "").rstrip("/")


def _dump_friends_json(obj) -> str:
    """序列化友链数据为 JSON：对象正常缩进，但纯标量短数组（如 tags）收成单行。

    标准库 json.dump(indent=2) 会把每个数组元素拆到独立一行，导致 "tags": ["Blog"]
    变成三行。这里用自定义递归排版，仅当数组内全是标量且紧凑形式不超过 70 字符时才
    内联成 ["Blog"]，其余结构仍按 2 空格缩进，保证 friends.json 可读且 diff 干净。
    """

    def _scalar(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        return json.dumps(v, ensure_ascii=False)

    def _is_scalar(v: object) -> bool:
        return isinstance(v, (str, int, float, bool)) or v is None

    def _fmt(v: object, level: int) -> str:
        pad = "  " * level
        pad_in = "  " * (level + 1)
        if isinstance(v, dict):
            if not v:
                return "{}"
            parts = [
                f"{pad_in}{json.dumps(k, ensure_ascii=False)}: {_fmt(val, level + 1)}"
                for k, val in v.items()
            ]
            return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
        if isinstance(v, list):
            if not v:
                return "[]"
            inline = "[" + ", ".join(_scalar(x) for x in v) + "]"
            if all(_is_scalar(x) for x in v) and len(inline) <= 70:
                return inline
            parts = [pad_in + _fmt(x, level + 1) for x in v]
            return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
        return _scalar(v)

    return _fmt(obj, 0)


def upsert_friend(title, siteurl, imgurl, desc, linkpage, issue_id, reverify: bool) -> str:
    """返回动作说明（新增/更新），并就地更新 friends.json。"""
    with open("friends.json", encoding="utf-8") as f:
        data = json.load(f)
    friends = data.setdefault("friends", [])

    entry = next((f for f in friends if norm_link(f.get("link")) == norm_link(siteurl)), None)
    if entry is None:
        if reverify:
            return "skip-missing"
        entry = {
            "name": title,
            "link": siteurl,
            "avatar": imgurl,
            "linkpage": "",
            "verified": False,
            "rss": "",
            "desc": desc,
            "tags": ["Blog"],
            "enabled": True,
            "weight": 5,
            "issue_id": issue_id,
        }
        friends.append(entry)
        action = "created"
    else:
        entry["name"] = entry.get("name") or title
        entry["avatar"] = entry.get("avatar") or imgurl
        entry["desc"] = entry.get("desc") or desc
        entry["enabled"] = True
        if not entry.get("issue_id"):
            entry["issue_id"] = issue_id
        action = "updated"

    if linkpage:
        entry["linkpage"] = linkpage

    data["version"] = data.get("version", 1)
    now = datetime.now(timezone(timedelta(hours=8)))
    data["updatedAt"] = now.strftime("%Y-%m-%d")

    with open("friends.json", "w", encoding="utf-8") as f:
        f.write(_dump_friends_json(data))
        f.write("\n")
    return action


def git_push_main(title: str) -> None:
    subprocess.run(["git", "config", "user.name", "cnb-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "cnb-bot@cnb.cool"], check=True)
    subprocess.run(["git", "add", "friends.json"], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"💛 新增友链：{title}"],
        check=True,
    )
    r = subprocess.run(["git", "push", "origin", "HEAD:main"])
    if r.returncode != 0:
        # 远端地址没带凭据时，显式用内置令牌重试一次
        url = f"https://cnb:{TOKEN}@cnb.cool/{SLUG}.git"
        subprocess.run(["git", "push", url, "HEAD:main"], check=True)


# ---------------- 主流程 ----------------
def main() -> int:
    if not (TOKEN and SLUG and IID and EVENT.startswith("issue.")):
        log.error("缺少 CNB 内置环境变量（CNB_TOKEN/CNB_REPO_SLUG/CNB_ISSUE_IID/CNB_EVENT），或非 Issue 事件")
        return 1
    log.info("事件=%s 议题=#%s 仓库=%s", EVENT, IID, SLUG)

    source = load_friends_source()
    log.info("友链真源模式：%s", source)

    issue = get_issue()
    title_full = issue.get("title") or os.getenv("CNB_ISSUE_TITLE", "")
    if not title_full.strip().startswith(TITLE_PREFIX):
        log.info("非友链申请议题，忽略")
        return 0

    # remote 模式：真源在外部博客端点，FCL 不再维护本地 friends.json，
    # 自助申请（Issue）整套关闭——直接告知并关闭议题，不做任何验证/写回。
    if source == "remote":
        comment_issue(
            "⛔ 本仓库 **友链自助申请功能已关闭**（spider_settings.source=remote，友链真源在外部博客友链端点）。\n\n"
            "如需添加友链，请直接在你的博客仓库本地修改友链配置文件并提交（参考部署文档「在博客本地仓库申请友链」一节），"
            "FCL 会自动从博客端点读取并巡检。"
        )
        close_issue()
        log.info("remote 模式：自助申请已关闭，议题 #%s 已关闭", IID)
        return 0

    labels = get_labels(issue)

    is_reverify = EVENT == "issue.comment"

    if is_reverify:
        # 重验闸门：只有失败态（带 待更新）的议题回复才会重新走验证
        if LABEL_RETRY not in labels:
            log.info("议题不在 待更新 状态，忽略该评论")
            return 0
        # 防死循环：自己刚发的评论触发的本轮直接跳过
        last = latest_comment()
        last_author = ((last.get("author") or {}).get("username")) or ""
        me = token_username()
        if last_author and me and last_author == me:
            log.info("最新评论来自机器人自身，跳过")
            return 0

    body = issue.get("body") or ""
    fields = parse_body(body)
    missing = [
        name
        for key, name in zip(
            ("title", "siteurl", "desc", "imgurl"),
            ("网站名称", "网站链接", "网站描述", "网站头像 URL"),
        )
        if not fields.get(key)
    ]
    if missing:
        comment_issue(
            "⚠️ 申请信息不完整，缺少：" + "、".join(missing)
            + "。\n\n请按以下格式补充到议题正文后回复任意内容重新验证：\n\n"
            + "```\n网站名称：xxx\n网站链接：https://example.com\n友链页面：https://example.com/friends\n网站描述：一句话介绍\n网站头像：https://example.com/avatar.png\n```"
        )
        try:
            add_label(LABEL_RETRY)
        except Exception as e:  # noqa: BLE001
            log.warning("打标签失败：%s", e)
        return 0

    siteurl = fields["siteurl"]
    if not re.match(r"^https?://", siteurl, re.I):
        siteurl = "https://" + siteurl.lstrip("/")

    # 去重检查：本地 friends.json 即真源（remote 模式已在上方提前返回，不会走到这里）
    with open("friends.json", encoding="utf-8") as f:
        exists = any(norm_link(x.get("link")) == norm_link(siteurl) for x in json.load(f)["friends"])
    if not is_reverify and exists:
        comment_issue(f"ℹ️ 友链 **{fields['title']}**（{siteurl}）已存在，无需重复添加。")
        close_issue()
        return 0

    target = fields["linkpage"] or siteurl
    if not target_is_safe(target):
        comment_issue("⚠️ 友链地址不合法或指向内网/保留地址，已拒绝访问（安全策略）。")
        return 0

    author_url = load_author_url()
    log.info("开始验证 %s（反链目标 %s）", target, author_url)
    result = verify(target, author_url)

    if not result["pass"]:
        reason = result.get("reason") or "验证未通过"
        comment_issue(
            f"⚠️ **验证未通过**：{reason}。\n\n请修复后在本议题下回复任意内容即可重新验证（议题保持开放）。"
        )
        try:
            add_label(LABEL_RETRY)
        except Exception as e:  # noqa: BLE001
            log.warning("打标签失败：%s", e)
        return 0

    # ---- 验证通过（仅 local 模式；remote 模式已提前关闭申请） ----
    action = upsert_friend(
        fields["title"], siteurl, fields["imgurl"], fields["desc"], fields["linkpage"],
        int(IID) if IID.isdigit() else IID, is_reverify,
    )
    if action == "skip-missing":
        comment_issue(f"ℹ️ 友链 **{fields['title']}** 已不在清单中，如需重新添加请关闭本议题后重新提交申请。")
        return 0
    git_push_main(fields["title"])

    try:
        if LABEL_RETRY in get_labels(get_issue()):
            remove_label(LABEL_RETRY)
    except Exception as e:  # noqa: BLE001
        log.warning("移除标签失败（不影响结果）：%s", e)
    comment_issue(
        f"✅ 反链核验与可访问性检查均通过（引擎：{result['engine']}），已自动添加友链 "
        f"**{fields['title']}**（{siteurl}）。\n\nfriends.json 已更新，主检测流程即将自动运行生成最新数据。"
    )
    close_issue()
    log.info("申请处理完成：%s", fields["title"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
