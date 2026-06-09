#!/usr/bin/env bash
# call_deepseek_stream.sh — 流式调用 DeepSeek API（SSE）
# 用法：echo '<json_payload>' | ./bin/call_deepseek_stream.sh [--no-cache]
#
# 行为：
#   增量 token 实时打到 stderr（终端可见）；
#   完整正文打到 stdout（供 ask.sh 管线后处理）。
#   缓存命中时：全文秒回，同时打到 stderr（无延迟感）。
#   缓存键与 call_deepseek.sh 共享（非流式/流式同一 payload 共享缓存）。
#
# 绕过缓存：NO_CACHE=1 或 --no-cache

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

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
API_URL="https://api.deepseek.com/v1/chat/completions"
CACHE_DIR="$ROOT_DIR/.cache/deepseek"

PAYLOAD="$(cat)"

if [[ -z "$PAYLOAD" ]]; then
  echo "错误：call_deepseek_stream.sh 未收到 JSON payload。" >&2
  exit 1
fi

# ─── 缓存读取（与 call_deepseek.sh 共享同一 key）────────
CACHE_FILE=""
if [[ "$NO_CACHE" != "1" ]]; then
  CACHE_KEY=$(printf '%s' "$PAYLOAD" | shasum -a 256 2>/dev/null | cut -d' ' -f1) || CACHE_KEY=""
  if [[ -n "$CACHE_KEY" ]]; then
    CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.txt"
    if [[ -s "$CACHE_FILE" ]]; then
      CACHED=$(cat "$CACHE_FILE")
      printf '%s\n' "$CACHED" >&2   # 终端可见（模拟即时回显）
      printf '%s\n' "$CACHED"       # stdout 供后处理
      exit 0
    fi
  fi
fi

if [[ -z "$DEEPSEEK_API_KEY" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY。" >&2
  exit 1
fi

# ─── 注入 "stream": true 并调用 ──────────────────────────
export PAYLOAD DEEPSEEK_API_KEY API_URL DEEPSEEK_TIMEOUT

FULL_TEXT=$(python3 - <<'PYEOF'
import sys, json, subprocess, os

payload      = json.loads(os.environ["PAYLOAD"])
payload["stream"] = True
stream_json  = json.dumps(payload, ensure_ascii=False)
api_key      = os.environ["DEEPSEEK_API_KEY"]
api_url      = os.environ["API_URL"]
timeout      = os.environ["DEEPSEEK_TIMEOUT"]

cmd = [
    "curl", "-s", "--no-buffer",
    "--max-time", timeout,
    "-X", "POST", api_url,
    "-H", "Content-Type: application/json",
    "-H", f"Authorization: Bearer {api_key}",
    "-d", stream_json,
]

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
accumulated = []

try:
    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or ""
            if content:
                sys.stderr.write(content)
                sys.stderr.flush()
                accumulated.append(content)
        except (json.JSONDecodeError, IndexError):
            pass
finally:
    proc.wait()

sys.stderr.write("\n")
sys.stderr.flush()

full = "".join(accumulated)
if not full.strip():
    print("错误：流式调用未收到有效内容。", file=sys.stderr)
    sys.exit(1)

print(full)
PYEOF
) || {
  echo "错误：流式 API 调用失败。" >&2
  exit 1
}

# ─── 写缓存（与非流式共享）──────────────────────────────
if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  printf '%s\n' "$FULL_TEXT" > "$CACHE_FILE" 2>/dev/null || true
fi

printf '%s\n' "$FULL_TEXT"
