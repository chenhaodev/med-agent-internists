#!/usr/bin/env bash
# ask.sh — 西氏内科学精要 家属问答 Agent 入口
# 用法：./bin/ask.sh "问题文本"
#       ./bin/ask.sh "我爸有高血压，平时饮食要注意什么？"
#
# 执行模式（速度 ↔ 质量，二选一；默认 Fast）：
#   --fast          快速模式（默认）：单次生成，最低延迟
#   --accurate      精确模式：原子声明 grep 核验 + 必要时回炉自纠（降幻觉，~2-3 倍调用）
#   --deep          --accurate 的别名（向后兼容）
#
# 可选参数：
#   --debug         打印路由和 payload 信息（写入 stderr）
#   --mode patient|doctor  受众模式（默认 patient；与执行模式正交）
#   --domain XXX    强制指定领域，跳过自动路由（例如 --domain cardiology:hypertension）
#   --stream        流式输出（增量 token 实时显示，默认关）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# ─── 参数解析 ────────────────────────────────────────────────
DEBUG=false
DEEP=false
STREAM=false
MODE="patient"
FORCE_DOMAIN=""
QUESTION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug)
      DEBUG=true
      shift
      ;;
    --deep|--accurate)
      DEEP=true
      shift
      ;;
    --fast)
      DEEP=false
      shift
      ;;
    --stream)
      STREAM=true
      shift
      ;;
    --mode)
      MODE="$2"
      shift 2
      ;;
    --domain)
      FORCE_DOMAIN="$2"
      shift 2
      ;;
    *)
      QUESTION="$1"
      shift
      ;;
  esac
done

if [[ -z "$QUESTION" ]]; then
  echo "用法：./bin/ask.sh \"问题文本\"" >&2
  echo "示例：./bin/ask.sh \"我爸有高血压，平时饮食要注意什么？\"" >&2
  echo "" >&2
  echo "执行模式（默认 Fast）：" >&2
  echo "  --fast                    快速模式（默认）：单次生成，最低延迟" >&2
  echo "  --accurate                精确模式：原子声明核验 + 回炉自纠（降幻觉，更慢）" >&2
  echo "" >&2
  echo "可选参数：" >&2
  echo "  --debug                   打印路由调试信息" >&2
  echo "  --stream                  流式输出（增量 token 实时显示，默认关）" >&2
  echo "  --mode patient|doctor     受众模式（默认 patient）" >&2
  echo "  --domain DOMAIN           强制使用指定领域（跳过自动路由）" >&2
  exit 1
fi

# ─── 0. 越界检测（确定性拦截）────────────────────────────────
OOB_RESULT=$("$SCRIPT_DIR/oob_check.sh" --mode "$MODE" "$QUESTION" 2>/dev/null || echo "in_scope")
[[ "$DEBUG" == "true" ]] && echo "[DEBUG] OOB 检测 → $OOB_RESULT" >&2

if [[ "$OOB_RESULT" != "in_scope" ]]; then
  OOB_TYPE=$(echo "$OOB_RESULT" | cut -d: -f2-)
  export ROOT_DIR OOB_TYPE QUESTION MODE
  OOB_REPLY=$(python3 - <<'PYEOF'
import re, os, sys

mode = os.environ.get("MODE", "patient")
if mode == "doctor":
    templates_file = os.path.join(os.environ["ROOT_DIR"], "prompts/oob_templates_doctor.md")
else:
    templates_file = os.path.join(os.environ["ROOT_DIR"], "prompts/oob_templates.md")
oob_type = os.environ["OOB_TYPE"]
question = os.environ["QUESTION"]

with open(templates_file) as f:
    content = f.read()

pattern = rf"## {re.escape(oob_type)}\n(.*?)(?=\n## |\Z)"
match = re.search(pattern, content, re.DOTALL)
if match:
    template = match.group(1).strip()
    print(template)
else:
    print("很抱歉，您的问题超出了本系统依据《西氏内科学精要》的覆盖范围，建议咨询专科医生。")
PYEOF
)

  echo ""
  echo "═══════════════════════════════════════════════════════"
  echo "$OOB_REPLY"
  echo "═══════════════════════════════════════════════════════"
  echo ""
  exit 0
fi

# ─── 1. 路由：确定专科:疾病 ──────────────────────────────────
if [[ -n "$FORCE_DOMAIN" ]]; then
  DOMAINS="$FORCE_DOMAIN"
  [[ "$DEBUG" == "true" ]] && echo "[DEBUG] 强制路由 → $DOMAINS" >&2
else
  DOMAINS=$("$SCRIPT_DIR/router.sh" "$QUESTION" 2>&1) || {
    echo "警告：路由失败，使用默认领域 cardiology:general。" >&2
    DOMAINS="cardiology:general"
  }
  [[ "$DEBUG" == "true" ]] && echo "[DEBUG] 自动路由 → $DOMAINS" >&2
fi

# ─── 2. 构建 prompt payload ──────────────────────────────────
[[ "$DEBUG" == "true" ]] && echo "[DEBUG] 正在构建 prompt (领域: $DOMAINS)..." >&2

PAYLOAD=$("$SCRIPT_DIR/build_prompt.sh" --mode "$MODE" "$DOMAINS" "$QUESTION") || {
  echo "错误：构建 prompt 失败。" >&2
  exit 1
}

if [[ "$DEBUG" == "true" ]]; then
  PAYLOAD_SIZE=$(echo "$PAYLOAD" | wc -c)
  echo "[DEBUG] Payload 大小：${PAYLOAD_SIZE} 字节" >&2
fi

# ─── 3. 调用 DeepSeek API ────────────────────────────────────
[[ "$DEBUG" == "true" ]] && echo "[DEBUG] 正在调用 DeepSeek API (stream=${STREAM})..." >&2

if [[ "$STREAM" == "true" ]]; then
  # 流式路径：增量 token 实时打到 stderr；全文从 stdout 捕获供后处理
  echo "" >&2
  RESPONSE=$(echo "$PAYLOAD" | "$SCRIPT_DIR/call_deepseek_stream.sh") || {
    echo "错误：流式 API 调用失败。" >&2
    exit 1
  }
else
  RESPONSE=$(echo "$PAYLOAD" | "$SCRIPT_DIR/call_deepseek.sh") || {
    echo "错误：API 调用失败。" >&2
    exit 1
  }
fi

# ─── 3b. --deep: 原子声明 grep 核验 + 必要时回炉 ────────────
if [[ "$DEEP" == "true" ]]; then
  # 定位首个 domain 对应的章节文件
  FIRST_DOMAIN=$(echo "$DOMAINS" | awk '{print $1}')
  FIRST_SP="${FIRST_DOMAIN%%:*}"
  FIRST_DS="${FIRST_DOMAIN##*:}"
  CHAPTER_FILE="$ROOT_DIR/source/chapters/${FIRST_SP}/${FIRST_DS}.md"
  YAML_FILE="$ROOT_DIR/knowledge/${FIRST_SP}/${FIRST_DS}.yaml"

  [[ "$DEBUG" == "true" ]] && \
    echo "[DEBUG --deep] 核验文件: ${FIRST_SP}/${FIRST_DS}" >&2

  # C. 运行 verify_claims.py
  set +e
  VERIFY_JSON=$(python3 "$SCRIPT_DIR/verify_claims.py" \
    --chapter "$CHAPTER_FILE" \
    --yaml "$YAML_FILE" \
    --mode "$MODE" \
    --answer "$RESPONSE")
  VERIFY_EXIT=$?
  set -e

  if [[ "$DEBUG" == "true" ]]; then
    FAIL_COUNT=$(echo "$VERIFY_JSON" | python3 -c \
      "import json,sys; d=json.load(sys.stdin); print(d.get('fail_count',0))" 2>/dev/null || echo "?")
    echo "[DEBUG --deep] 核验完成，✗ 声明数: $FAIL_COUNT" >&2
    echo "$VERIFY_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for c in d.get('claims', []):
    print(f'  {c[\"status\"]} [{c[\"kind\"]}] {c[\"claim\"]} — {c[\"evidence\"]}')
" >&2 2>/dev/null || true
  fi

  # D. 有 ✗ → 回炉一次
  if [[ "$VERIFY_EXIT" == "1" ]]; then
    [[ "$DEBUG" == "true" ]] && echo "[DEBUG --deep] 发现 ✗ 声明，启动回炉..." >&2

    export _VERIFY_JSON="$VERIFY_JSON"

    set +e
    REROLL_RESPONSE=$(
      "$SCRIPT_DIR/build_prompt.sh" --reroll --mode "$MODE" "$DOMAINS" "$QUESTION" \
      | "$SCRIPT_DIR/call_deepseek.sh"
    )
    REROLL_EXIT=$?
    set -e

    if [[ "$REROLL_EXIT" == "0" && -n "$REROLL_RESPONSE" ]]; then
      RESPONSE="$REROLL_RESPONSE"
      [[ "$DEBUG" == "true" ]] && echo "[DEBUG --deep] 回炉完成" >&2
    else
      [[ "$DEBUG" == "true" ]] && echo "[DEBUG --deep] 回炉失败，保留首轮回答" >&2
      # Annotate residual failures in debug output
      echo "[DEBUG --deep] RESIDUAL_UNVERIFIED: 首轮核验有 ✗ 但回炉失败，请人工复查。" >&2
    fi
  fi

  # Naive 对照（--debug --deep 时才运行，不影响最终答案）
  if [[ "$DEBUG" == "true" ]]; then
    echo "[DEBUG --deep] 运行 naive 对照（不注入知识库）..." >&2
    set +e
    NAIVE_RESPONSE=$(
      "$SCRIPT_DIR/build_prompt.sh" --naive --mode "$MODE" "$DOMAINS" "$QUESTION" \
      | "$SCRIPT_DIR/call_deepseek.sh" 2>/dev/null
    )
    NAIVE_EXIT=$?
    set -e

    if [[ "$NAIVE_EXIT" == "0" && -n "$NAIVE_RESPONSE" ]]; then
      echo "" >&2
      echo "[DEBUG --deep] ══════ 【naive vs 接地】差异（- naive / + 接地）══════" >&2
      diff <(echo "$NAIVE_RESPONSE") <(echo "$RESPONSE") >&2 || true
      echo "[DEBUG --deep] ═════════════════════════════════════════════════════" >&2
    else
      echo "[DEBUG --deep] naive 调用失败，跳过 diff。" >&2
    fi
  fi
fi

# ─── 4. 后处理：校验结构 ─────────────────────────────────────
VALIDATED=$(echo "$RESPONSE" | "$SCRIPT_DIR/postprocess.sh" --mode "$MODE") || {
  VALIDATED="$RESPONSE"
}

# ─── 5. 输出结果 ─────────────────────────────────────────────
if [[ "$STREAM" == "true" ]]; then
  # 流式模式：正文已在步骤 3 实时流出；这里只补结构校验提示
  # （postprocess 的 ⚠️ 已通过 stderr 警告打出；无需重新输出正文）
  :
else
  echo ""
  echo "═══════════════════════════════════════════════════════"
  echo "$VALIDATED"
  echo "═══════════════════════════════════════════════════════"
  echo ""
fi
