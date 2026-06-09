#!/usr/bin/env bash
# call_deepseek.sh — 调用 DeepSeek API
# 用法：echo '<json_payload>' | ./bin/call_deepseek.sh [--no-cache]
# 输出：模型回复的文本内容（纯文本，不含 JSON 包装）
#
# 响应缓存（默认开）：
#   按 payload 的 sha256 做内容寻址磁盘缓存（.cache/deepseek/<sha>.txt）。
#   命中即零网络返回。绕过：NO_CACHE=1 环境变量 或 --no-cache 参数。
#   清理：rm -rf .cache/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# ─── 参数解析 ────────────────────────────────────────────────
NO_CACHE="${NO_CACHE:-0}"
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=1 ;;
  esac
done

if [[ -f "$ROOT_DIR/.env" ]]; then
  source "$ROOT_DIR/.env"
fi

DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-flash}"
DEEPSEEK_TIMEOUT="${DEEPSEEK_TIMEOUT:-60}"
DEEPSEEK_MAX_RETRIES="${DEEPSEEK_MAX_RETRIES:-3}"
API_URL="https://api.deepseek.com/v1/chat/completions"
CACHE_DIR="$ROOT_DIR/.cache/deepseek"

PAYLOAD="$(cat)"

if [[ -z "$PAYLOAD" ]]; then
  echo "错误：call_deepseek.sh 未收到任何 JSON payload（stdin 为空）。" >&2
  exit 1
fi

# ─── 缓存读取（命中即零网络返回，无需 API key）──────────────
CACHE_FILE=""
if [[ "$NO_CACHE" != "1" ]]; then
  CACHE_KEY=$(printf '%s' "$PAYLOAD" | shasum -a 256 2>/dev/null | cut -d' ' -f1) || CACHE_KEY=""
  if [[ -n "$CACHE_KEY" ]]; then
    CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.txt"
    if [[ -s "$CACHE_FILE" ]]; then
      cat "$CACHE_FILE"
      exit 0
    fi
  fi
fi

# 缓存未命中 → 需要 API key 才能真实调用
if [[ -z "$DEEPSEEK_API_KEY" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填入 key。" >&2
  exit 1
fi

attempt=0
while true; do
  attempt=$((attempt + 1))

  HTTP_RESPONSE=$(curl -s -w "\n__HTTP_STATUS__%{http_code}" \
    --max-time "$DEEPSEEK_TIMEOUT" \
    -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
    -d "$PAYLOAD" 2>&1) || {
    echo "错误：curl 请求失败（网络问题或超时）。" >&2
    exit 1
  }

  HTTP_BODY="$(echo "$HTTP_RESPONSE" | sed '$d')"
  HTTP_STATUS="$(echo "$HTTP_RESPONSE" | tail -1 | sed 's/__HTTP_STATUS__//')"

  if [[ "$HTTP_STATUS" == "200" ]]; then
    CONTENT=$(echo "$HTTP_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['choices'][0]['message']['content']
if not content or not content.strip():
    print('错误：API 返回空 content', file=sys.stderr)
    sys.exit(1)
print(content)
" 2>&1) || {
      echo "错误：解析 API 响应失败或 content 为空，将重试。" >&2
      if [[ $attempt -ge $DEEPSEEK_MAX_RETRIES ]]; then
        echo "响应：$HTTP_BODY" >&2
        exit 1
      fi
      sleep $((attempt * 2))
      continue
    }
    # 写缓存（未绕过且 key 可算时）
    if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
      mkdir -p "$CACHE_DIR" 2>/dev/null || true
      printf '%s\n' "$CONTENT" > "$CACHE_FILE" 2>/dev/null || true
    fi
    echo "$CONTENT"
    exit 0
  fi

  if [[ "$HTTP_STATUS" == "429" || "$HTTP_STATUS" == "500" || "$HTTP_STATUS" == "502" || "$HTTP_STATUS" == "503" ]]; then
    if [[ $attempt -ge $DEEPSEEK_MAX_RETRIES ]]; then
      echo "错误：API 返回 HTTP ${HTTP_STATUS}，已重试 $attempt 次，放弃。" >&2
      echo "响应：$HTTP_BODY" >&2
      exit 1
    fi
    SLEEP_SEC=$((attempt * 2))
    echo "警告：HTTP ${HTTP_STATUS}，${SLEEP_SEC}s 后重试（第 ${attempt}/${DEEPSEEK_MAX_RETRIES} 次）..." >&2
    sleep "$SLEEP_SEC"
    continue
  fi

  echo "错误：API 返回 HTTP ${HTTP_STATUS}。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
done
