# 部署方式选择：GitHub Actions 与 CNB

本仓库同时提供两套 CI 配置，**二选一**即可，fork 后按自己的情况删掉另一套：

| 文件 | 平台 | 说明 |
|---|---|---|
| `.github/workflows/friend_circle_lite.yml` | GitHub Actions | 完整功能（含 Issue 自助申请、巡检回评） |
| `.cnb.yml` | CNB (cnb.cool) | 主链路 + Issue 自助申请（`scripts/apply_friend_cnb.py`） |

两套跑的是同一组命令，检测核心完全一致，产物格式相同。

## 公共概念（两个平台通用）

- **`friends.json`** —— `local` 模式下的友链真源，手动编辑推送后会自动触发一轮巡检；`remote` 模式下真源改为外部博客端点，`friends.json` 不再被维护（见下方 `spider_settings.source` 说明）。
- **`cache` 分支** —— 巡检缓存的存放地（SQLite）。流水线开场由 `scripts/sync_cache.sh pull` 恢复，收尾 `push` 回写。首轮运行时远端没有这个分支属正常现象，会自动创建并从零建立缓存。
- **`page` 分支** —— 巡检产物的发布地（`link.json` / `all.json` / 清册页静态文件等）。内容无变化时流水线自动跳过更新。
- **`conf.yaml`** —— 行为开关全部集中在这里（缓存时长、截图周期、告警渠道等）；密钥类一律走 CI 环境变量。
- **友链真源模式（`spider_settings.source`）** —— `local`（默认）/ `remote`：
  - `local`：真源在 FCL 仓库 `friends.json`，`json_url` 指向本仓库 raw（或本地/镜像地址）；Issue 自助申请通过会自动写回并触发巡检。
  - `remote`：真源在**外部博客友链端点**（把 `json_url` 改成你博客的 `/friends.json`，如 `https://你的博客/friends.json`）。FCL 仅作巡检展示，**不维护/写回**本地 `friends.json`；读取层已对博客格式（`title`/`siteurl`/`imgurl`）做了别名兼容，填博客端点也能直接解析。
    - 对应地，**remote 模式整套 Issue 机制关闭**：自助申请（`[友链申请]` 议题）开箱即被告知「功能已关闭」并关单，不做任何验证/写回；巡检回评（异常友链→对应议题回评）也整体跳过（本地无 `issue_id` 映射）。这套 Issue 机制依赖 FCL 本地真源，remote 模式真源在外部时本就不适用。要加友链请直接改博客仓库（见下方「在博客本地仓库申请友链」）。
    - 若别人端点字段名或顶层键名和 FCL 不一致（例如用 `link_list` 或非标准字段），用 `spider_settings.friends_input` / `list_key` 在 FCL 侧映射即可，无需改动你自己的端点，详见下方「别人已有端点、字段命名不同怎么办」。

## 在博客本地仓库申请友链（remote 模式）

remote 模式下 FCL 不托管友链，真源是你的博客仓库。要加一条友链，直接在博客仓库里编辑友链配置文件、提交（或开 PR）即可，FCL 下一轮巡检会自动从博客端点拉取并展示——**不需要走 FCL 的 Issue 自助申请**。

> ⚠️ 防循环：FCL 在 remote 模式会从博客端点读 `friends.json`，因此博客侧应让自己的友链**来自本地配置**而非反读 FCL。Firefly/Fuwari 主题即把 `src/config/friendsConfig.ts` 的 `friendsPageConfig.useRemote` 设为 `false`，让博客用本地 `friendsConfig` 数组作真源。否则 FCL 读博客、博客又读 FCL，会形成循环。

以 **Firefly / Fuwari 主题博客**为例，友链真源在 `src/config/friendsConfig.ts` 的 `friendsConfig` 数组，按以下格式追加一项（字段用博客侧命名 `title/siteurl/imgurl`，**不是** FCL 的 `name/link/avatar`）：

```ts
// src/config/friendsConfig.ts
export const friendsConfig: FriendLink[] = [
  // ……已有友链……
  {
    title: "朋友站点名",                                  // 必填，展示名称
    imgurl: "https://friend.example.com/avatar.png",      // 必填，头像
    desc: "一句话介绍这个站点",                           // 必填，描述
    siteurl: "https://friend.example.com",                // 必填，站点主页
    linkpage: "https://friend.example.com/friends/",      // 选填，友链页（反链检测目标，建议填）
    rss: "https://friend.example.com/rss.xml",            // 选填，RSS
    tags: ["Blog"],                                       // 选填，标签
    weight: 5,                                            // 选填，排序权重（越大越靠前）
    enabled: true,                                        // 必填，是否启用
  },
];
```

提交并等博客重新构建后，FCL 的 `json_url`（指向该博客的 `/friends.json`）下一轮巡检即生效。字段含义与 FCL 端 `friends.json` 一一对应（仅命名不同，FCL 读取层已做别名兼容）。

## 纯净博客如何暴露 /friends.json 端点（remote 模式前置）

如果你的博客是「什么都没改过」的纯净 Firefly / Fuwari，**默认是没有 `/friends.json` 端点的**——主题只把 `friendsConfig.ts` 当作本地数据源渲染友链页，不会主动对外吐 JSON。要让 FCL 在 `remote` 模式下来读，必须先让博客把这个端点暴露出来。两步：

### 1. 确认/建立友链数据源

端点读的是博客的友链配置数组。Firefly / Fuwari 自带 `src/config/friendsConfig.ts`，里面有 `friendsConfig: FriendLink[]` 数组；如果你的主题还没有这份配置，先按主题文档建一个（结构就是一个 `FriendLink[]` 数组，字段 `title` / `siteurl` / `imgurl` / `desc` / `linkpage` / `enabled` 等）。没有数据源，端点吐出来也是空的。

### 2. 新增 API 路由 `src/pages/friends.json.ts`

在博客仓库新建下面这个文件，构建后访问 `https://你的博客/friends.json` 就会返回一份 **FCL 原生 `friends` 格式** JSON（顶层 `friends` 数组，字段 `name` / `link` / `avatar` / `linkpage` 等与 FCL 仓库里的 `friends.json` 完全一致），FCL `remote` 模式直接读取，不需要任何兼容层：

```ts
// src/pages/friends.json.ts
import type { APIRoute } from "astro";
import { friendsConfig } from "@/config/friendsConfig";

// 暴露 /friends.json 端点，供 Friend-Circle-Lite（remote 模式）读取。
// 直接输出 FCL 原生 friends 格式（顶层 friends 数组 + name/link/avatar 字段），
// FCL 无需任何兼容即可解析。
export const GET: APIRoute = () => {
  const friendList = friendsConfig
    .filter((f) => f.enabled)
    .map((f) => ({
      name: f.title,
      link: f.siteurl.trim(),              // 去首尾空格，避免 FCL 匹配失败
      avatar: f.imgurl,
      desc: f.desc,
      linkpage: f.linkpage?.trim() || "",   // 友链页 URL，FCL 反链检测目标
      verified: false,
      rss: f.rss || "",
      tags: f.tags || [],
      enabled: true,
      weight: f.weight ?? 0,
    }));
  return new Response(
    JSON.stringify({ friends: friendList }),
    {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=60, s-maxage=60",
      },
    },
  );
};
```

> 注：如果你的主题把 `friendsConfig.ts` 放在别的路径、或用别的导出名（例如 `getEnabledFriends()` 而不是裸数组），按实际改 import 即可，字段映射逻辑不变。

### 3. 验证端点

部署后浏览器直接开 `https://你的博客/friends.json`，应看到 `{"friends":[...]}`（结构与 FCL 仓库里的 `friends.json` 一致）。拿不到就先排查博客构建是否包含该路由（Astro 的 `src/pages/*.json.ts` 会被编译成同名 JSON 端点）。

### 4. 接回 FCL

端点通了之后，在 FCL 的 `conf.yaml` 设：

```yaml
spider_settings:
  source: remote
  json_url: "https://你的博客/friends.json"   # 刚暴露的端点
```

> ⚠️ **防循环（和上面「在博客本地申请」是同一件事，这里再强调一次）**：FCL 在 `remote` 模式会从博客端点读 `friends.json`，所以博客侧必须让自己读**本地配置**而非反读 FCL。把博客 `friendsPageConfig.useRemote` 设为 `false`，让博客用本地 `friendsConfig` 数组作真源。否则 FCL 读博客、博客又读 FCL，闭环死循环。

## 别人已有端点、字段命名不同怎么办（friends_input / list_key）

前面「纯净博客」节给你的端点模板直接输出 FCL 原生 `friends` 格式，remote 模式零配置可读。但如果你**已经在用别的友链端点**（别的友链聚合站、或博客主题自带的非标准格式），它的字段名、甚至顶层键名都和 FCL 不一样——**不用改你自己的端点**，在 FCL 侧用 `friends_input` 映射一下即可。

`friends_input` 是**读取与发布共用的一份字段桥接映射**（单一真相源，避免读写两份 rename 不一致）：

- 键 = FCL 内部字段名（`name` / `link` / `avatar` / `linkpage` / `verified`）
- 值 = 你的端点/博客里的实际字段名

`list_key` 是端点 JSON 的**顶层键名**（默认 `friends`），别人的端点可能是 `link_list` / `data` 等，配这里就行。

```yaml
spider_settings:
  source: remote
  json_url: "https://别人的博客/api/friends"
  list_key: "link_list"            # 别人端点顶层键（默认 friends）
  friends_input:
    rename:
      name: blog_title            # 外部 blog_title → FCL name
      link: site_url               # 外部 site_url   → FCL link
      avatar: logo                 # 外部 logo       → FCL avatar
      linkpage: friend_page
```

效果：

- **读取**：FCL 按 `friends_input.rename` 从端点取字段（外部 `blog_title` 当成 FCL `name`），别人端点零改动即可被巡检。
- **发布**：FCL 每轮把巡检结果发布成 `friends.json` 时，按**同一份** `rename` 把字段名还原成你的格式（`name → blog_title`）。发布的 `friends.json` 和你端点格式一致，别人/check-flink 又能直接读——正反复用同一份映射，不用维护两份。
- 完全没配 `friends_input` 时：读取退回内置别名（`title`/`siteurl`/`imgurl` 等），发布原样输出。

> 注：`local` 模式（真源在 FCL 仓库）下，`friends_input.rename` 仅用于**发布**时把 `friends.json` 重命名成外部格式（如对齐博客 my-blog 的 `title/siteurl/imgurl`），读取侧不读远程因此用不到。一份配置两边语义统一。

## GitHub 版

1. 保留 `.github/`，删除 `.cnb.yml`
2. 在仓库 Settings → Secrets and variables → Actions 配置密钥（按需，均可选）：

   | Secret | 用途 |
   |---|---|
   | `SMTP_PWD` | RSS 订阅邮件推送 |
   | `IMG_UPLOAD_URL` / `IMG_AUTH_CODE` | 截图上传图床 |
   | `PROXY_URL` | 检测/抓取代理 |
   | `EO_PING_URL` | 国内延迟探测云函数 |
   | `QQ_BOT_ALERT_URL` / `QQ_BOT_ALERT_TOKEN` | QQ 机器人状态告警 |
   | `WECOM_WEBHOOK_URL` | 企业微信状态告警 |

3. 发布：让任意支持「盯分支」的托管平台接管 `page` 分支（Vercel、Cloudflare Pages 等），绑自定义域名即可。

## CNB 版

1. 删除 `.github/`（或留着不碍事），保留 `.cnb.yml`
2. 密钥配置：CNB 没有 GitHub 那种仓库级 Secrets 界面，官方姿势是**密钥仓库**——
   新建仓库时类型选「密钥仓库」（只能网页编辑，带水印和引用审计），里面放一个 `envs.yml`：

   ```yaml
   # envs.yml —— 键名与 GitHub 版 Secret 同名同义，按需增减
   IMG_UPLOAD_URL: "https://imgbed.example.com/upload"
   IMG_AUTH_CODE: "your-token"
   ```

   然后给需要这些变量的 stage 加 `imports`，键值对自动注入为环境变量：

   ```yaml
   - name: 安装浏览器并执行截图（增量）
     imports:
       - https://cnb.cool/<你的组织>/<密钥仓>/-/blob/main/envs.yml
     script: |
       ...
   ```

3. 发布：`page` 分支推在本仓库内，接托管的方式任选——
   - 最省事：取消 `.cnb.yml` 里 EdgeOne Pages 段的注释，直接推 EO Pages（国内访问快）；
   - 或用任何支持 API 上传的静态托管在流水线末尾加一个 stage。

### CNB 版注意事项

- **定时任务负责人机制**：crontab 以最后修改该配置的用户身份执行，别把自己移出仓库，否则定时任务会失败。
- **push 触发已按真源过滤**：通过 `ifModify` 只在 `friends.json` 变化时自动巡检，改其他文件推送不触发流水线。
- **密钥仓库已接入**：本仓库流水线引用的是 `x1anyu/key` 密钥仓的 `fcl.yml`（fork 者请替换成自己的密钥仓文件地址）。
- **友链自助申请（CNB 版）**：在 CNB 议题新建标题以 `[友链申请]` 开头的议题，正文按行填写：

  ```
  网站名称：xxx
  网站链接：https://example.com
  友链页面：https://example.com/friends
  网站描述：一句话介绍
  网站头像：https://example.com/avatar.png
  ```

  提交后自动验证可访问性 + 反链（与 GitHub 版同逻辑），通过即写入 friends.json 并自动联动一轮巡检；
  未通过会打 `待更新` 标签，修复后回复议题即可重验。反链判定目标读 conf.yaml 的 `link_check.author_url`。
- **chromedriver 走系统包**：CNB 构建机在国内，脚本默认用 Debian 的 `chromium-driver` 包（通过 `CHROMEDRIVER_PATH` 环境变量生效），不走 webdriver-manager 的 Google 源下载。
- **`.cnb.yml` 尚未经真实流水线验证**：首次运行如遇平台语法校验问题，按 CNB 控制台提示微调即可，结构已对齐官方模板。

## 注意：不要两家同时开定时

两边定时都开的话会重复构建、互相覆盖 page 分支的快照（数据不会坏，但浪费且时序混乱）。建议：一家挂定时，另一家只留手动触发当备份入口。

## 移植到其他平台

只要平台满足三个条件就能接入：**支持定时任务、任务里能执行 shell/python、能用凭据 push 回自己仓库**。要做的只是把下面的命令清单翻译成该家的流水线语法：

```bash
pip install -r requirements.txt && playwright install --with-deps chromium
bash scripts/sync_cache.sh pull                                   # 恢复缓存
git fetch origin page && git show origin/page:link.json > link.baseline.json || true
python run.py                                                     # 检测主流程
python -m friend_circle_lite.postprocess all                      # 后处理
# 组装 pages/ 并强推 page 分支（见任一现有流水线的对应段）
python -m friend_circle_lite.friends_publish --src friends.json --dst pages/friends.json
apt-get install -y chromium chromium-driver fonts-noto-cjk        # 截图环境
export CHROME_BIN=/usr/bin/chromium CHROMEDRIVER_PATH=/usr/bin/chromedriver
python -m friend_circle_lite.screenshots.runner                   # 增量截图
# 将带截图的 link.json 提交回 page 分支
bash scripts/sync_cache.sh push                                   # 回写缓存
```

需要注入的环境变量与 GitHub 版 Secret 同名同义（上表）。
