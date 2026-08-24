#!/bin/bash
# ============================================================
# 缓存分支同步脚本（平台无关）
#
# 供 GitHub Actions / CNB / 其他任何能跑 git 的 CI 使用，
# 替代依赖单一平台的能力（如 GitHub artifact 跨轮传递）。
#
# 用法:
#   bash scripts/sync_cache.sh pull   # 流水线开场: 从 cache 分支恢复上一轮缓存
#   bash scripts/sync_cache.sh push   # 检测收尾: 把本地缓存强推回 cache 分支
#
# 环境变量（均有默认值，一般无需设置）:
#   CACHE_BRANCH  缓存所在分支，默认 cache
#   CACHE_FILE    本地缓存文件路径，默认 ./temp/cache.sqlite3
#
# 前提: 当前工作区是完整 clone，且 origin 具备推送权限
#       （GitHub Actions checkout 默认注入 token；CNB 内置 CNB_TOKEN）。
#       推送使用 git plumbing 直接构造单文件 orphan 提交，无需额外配置。
# ============================================================
set -euo pipefail

ACTION="${1:-}"
CACHE_BRANCH="${CACHE_BRANCH:-cache}"
CACHE_FILE="${CACHE_FILE:-./temp/cache.sqlite3}"
REMOTE_NAME="origin"
REMOTE_PATH="$(basename "$CACHE_FILE")"

case "$ACTION" in
  pull) ;;
  push) ;;
  *)
    echo "[缓存] 用法: $0 pull|push" >&2
    exit 1
    ;;
esac

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[缓存] 当前目录不是 git 工作区，无法同步缓存" >&2
  exit 1
fi

do_pull() {
  mkdir -p "$(dirname "$CACHE_FILE")"
  if git fetch "$REMOTE_NAME" "refs/heads/${CACHE_BRANCH}" 2>/dev/null; then
    if git show "FETCH_HEAD:${REMOTE_PATH}" > "$CACHE_FILE" 2>/dev/null; then
      echo "[缓存] 已从 ${CACHE_BRANCH} 分支恢复上一轮缓存:"
      ls -la "$CACHE_FILE"
      return 0
    fi
    echo "[缓存] ${CACHE_BRANCH} 分支存在但无 ${REMOTE_PATH}，视为首轮"
  else
    echo "[缓存] 远端无 ${CACHE_BRANCH} 分支，视为首轮运行"
  fi
  echo "[缓存] 将从零开始建立缓存"
}

do_push() {
  if [ ! -f "$CACHE_FILE" ]; then
    echo "[缓存] 本地不存在 ${CACHE_FILE}，跳过回写"
    return 0
  fi

  # 内容与远端一致时跳过，避免每轮产生空提交
  local local_hash remote_hash=""
  local_hash="$(git hash-object "$CACHE_FILE")"
  if git fetch "$REMOTE_NAME" "refs/heads/${CACHE_BRANCH}" 2>/dev/null; then
    remote_hash="$(git show "FETCH_HEAD:${REMOTE_PATH}" 2>/dev/null | git hash-object --stdin 2>/dev/null || true)"
    remote_hash="${remote_hash:-}"
  fi
  if [ -n "$remote_hash" ] && [ "$local_hash" = "$remote_hash" ]; then
    echo "[缓存] 内容与远端 ${CACHE_BRANCH} 分支一致，跳过回写"
    return 0
  fi

  # 构造仅含缓存文件的单提交（orphan），强推为缓存分支
  local blob tree commit stamp
  stamp="$(date +"%Y年%m月%d日-%H时%M分")"
  blob="$(git hash-object -w "$CACHE_FILE")"
  tree="$(printf '100644 blob %s\t%s\n' "$blob" "$REMOTE_PATH" | git mktree)"
  commit="$(git -c user.name="ci-bot" -c user.email="ci-bot@users.noreply.local" \
    commit-tree "$tree" -m "🔄 ${stamp} 更新巡检缓存")"
  git push --force "$REMOTE_NAME" "$commit:refs/heads/${CACHE_BRANCH}"
  echo "[缓存] 已回写至远端 ${CACHE_BRANCH} 分支 (blob ${local_hash})"
}

case "$ACTION" in
  pull) do_pull ;;
  push) do_push ;;
esac
