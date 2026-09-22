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
#   PUSH_LOG_FILE 推送审计日志路径，默认 ./push_log.jsonl；置空则不同步该日志。
#                 （对应 notify.run 的 push_log_path；CI 里想留存审计日志就保持默认）
#
# 前提: 当前工作区是完整 clone，且 origin 具备推送权限
#       （GitHub Actions checkout 默认注入 token；CNB 内置 CNB_TOKEN）。
#       推送使用 git plumbing 直接构造单文件 orphan 提交，无需额外配置。
# ============================================================
set -euo pipefail

ACTION="${1:-}"
CACHE_BRANCH="${CACHE_BRANCH:-cache}"
CACHE_FILE="${CACHE_FILE:-./temp/cache.sqlite3}"
PUSH_LOG_FILE="${PUSH_LOG_FILE:-./push_log.jsonl}"
REMOTE_NAME="origin"

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

# 收集本轮需要同步的本地文件（存在才纳入；推送日志可缺省）。
collect_files() {
  local -n _out=$1
  _out=()
  [ -f "$CACHE_FILE" ] && _out+=("$CACHE_FILE")
  if [ -n "$PUSH_LOG_FILE" ] && [ -f "$PUSH_LOG_FILE" ]; then
    _out+=("$PUSH_LOG_FILE")
  fi
}

do_pull() {
  mkdir -p "$(dirname "$CACHE_FILE")"
  if ! git fetch "$REMOTE_NAME" "refs/heads/${CACHE_BRANCH}" 2>/dev/null; then
    echo "[缓存] 远端无 ${CACHE_BRANCH} 分支，视为首轮运行"
    echo "[缓存] 将从零开始建立缓存"
    return 0
  fi
  # 缓存文件
  if git show "FETCH_HEAD:$(basename "$CACHE_FILE")" > "$CACHE_FILE" 2>/dev/null; then
    echo "[缓存] 已从 ${CACHE_BRANCH} 分支恢复上一轮缓存:"
    ls -la "$CACHE_FILE"
  else
    echo "[缓存] ${CACHE_BRANCH} 分支存在但无 $(basename "$CACHE_FILE")，视为首轮"
  fi
  # 推送审计日志（可缺省）
  if [ -n "$PUSH_LOG_FILE" ] && git show "FETCH_HEAD:$(basename "$PUSH_LOG_FILE")" > "$PUSH_LOG_FILE" 2>/dev/null; then
    echo "[缓存] 已恢复上一轮推送审计日志:"
    ls -la "$PUSH_LOG_FILE"
  fi
}

# 用 git plumbing 把若干本地文件拼成一个 tree，返回 tree hash（无文件返回空）。
# 注意：mktree 要求按「树内路径 = basename」字典序排列，故按 basename 排序而非完整路径。
build_tree() {
  local -n _files=$1
  local entries="" line
  local -a ordered=()
  # 先按 basename 排序（保留完整路径供后续读取）
  mapfile -t ordered < <(printf '%s\n' "${_files[@]}" | while IFS= read -r f; do printf '%s\t%s\n' "$(basename "$f")" "$f"; done | sort | cut -f2-)
  for f in "${ordered[@]}"; do
    local blob mode="100644"
    blob="$(git hash-object -w "$f")"
    printf -v line '%s blob %s\t%s' "$mode" "$blob" "$(basename "$f")"
    entries+="$line"$'\n'
  done
  if [ -z "$entries" ]; then
    echo ""
    return
  fi
  printf '%s' "$entries" | git mktree
}

do_push() {
  local files=()
  collect_files files
  if [ ${#files[@]} -eq 0 ]; then
    echo "[缓存] 本地无可同步文件（${CACHE_FILE} 与 ${PUSH_LOG_FILE} 均不存在），跳过回写"
    return 0
  fi

  # 内容与远端一致时跳过，避免每轮产生空提交
  local local_tree remote_tree=""
  local_tree="$(build_tree files)"
  if git fetch "$REMOTE_NAME" "refs/heads/${CACHE_BRANCH}" 2>/dev/null; then
    remote_tree="$(git rev-parse "FETCH_HEAD^{tree}" 2>/dev/null || true)"
    remote_tree="${remote_tree:-}"
  fi
  if [ -n "$remote_tree" ] && [ "$local_tree" = "$remote_tree" ]; then
    echo "[缓存] 内容与远端 ${CACHE_BRANCH} 分支一致，跳过回写"
    return 0
  fi

  local stamp commit names
  stamp="$(date +"%Y年%m月%d日-%H时%M分")"
  names="$(basename "$CACHE_FILE")${PUSH_LOG_FILE:+, $(basename "$PUSH_LOG_FILE")}"
  commit="$(git -c user.name="ci-bot" -c user.email="ci-bot@users.noreply.local" \
    commit-tree "$local_tree" -m "🔄 ${stamp} 更新巡检缓存 (${names})")"
  git push --force "$REMOTE_NAME" "$commit:refs/heads/${CACHE_BRANCH}"
  echo "[缓存] 已回写至远端 ${CACHE_BRANCH} 分支: ${names}"
}

case "$ACTION" in
  pull) do_pull ;;
  push) do_push ;;
esac
