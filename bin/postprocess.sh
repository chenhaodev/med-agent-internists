#!/usr/bin/env bash
# postprocess.sh — 校验模型输出是否符合所选模式的 5 段式 schema
# 用法：echo "模型回复" | ./bin/postprocess.sh [--mode patient|doctor]
# 输出：
#   通过 → 原文输出（stdout），退出码 0
#   缺段 → stderr 输出警告，stdout 输出原始回复，退出码 1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SECTIONS_YAML="$ROOT_DIR/schema/sections.yaml"

# ─── 参数解析 ────────────────────────────────────────────────
MODE="patient"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    *)      shift ;;
  esac
done

RESPONSE="$(cat)"

if [[ -z "$RESPONSE" ]]; then
  echo "错误：postprocess.sh 收到空响应。" >&2
  exit 1
fi

# ─── 从 schema/sections.yaml 派生段名 ────────────────────────
# 单行 python 读 YAML，避免在 postprocess.sh 中硬编码段名
read -r -d '' _PY_LOAD_SECTIONS <<'PYEOF' || true
import sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
mode = sys.argv[2]
entry = data.get(mode, {})
for s in entry.get("sections", []):
    print(s)
print("__CITATION_LABEL__=" + entry.get("citation_label", ""))
PYEOF

_SECTIONS_OUTPUT=$(python3 -c "$_PY_LOAD_SECTIONS" "$SECTIONS_YAML" "$MODE" 2>/dev/null) || {
  echo "错误：无法读取 $SECTIONS_YAML（mode=$MODE）。" >&2
  exit 1
}

REQUIRED_SECTIONS=()
CITATION_LABEL=""
while IFS= read -r line; do
  if [[ "$line" == __CITATION_LABEL__=* ]]; then
    CITATION_LABEL="${line#__CITATION_LABEL__=}"
  else
    REQUIRED_SECTIONS+=("$line")
  fi
done <<< "$_SECTIONS_OUTPUT"

if [[ ${#REQUIRED_SECTIONS[@]} -eq 0 ]]; then
  echo "错误：schema/sections.yaml 中无 mode=$MODE 的段名定义。" >&2
  exit 1
fi

# ─── 5 段式结构检查 ───────────────────────────────────────────
MISSING=()
for section in "${REQUIRED_SECTIONS[@]}"; do
  if ! echo "$RESPONSE" | grep -qF "$section"; then
    MISSING+=("$section")
  fi
done

# ─── 安全性检查：必须包含来源引用（OOB 拒答时豁免）──────────
IS_OOB_RESPONSE=false
if echo "$RESPONSE" | grep -qE "超出.*范围|不在.*覆盖|不覆盖|超出了.*范围"; then
  IS_OOB_RESPONSE=true
fi

if [[ "$IS_OOB_RESPONSE" == "false" ]]; then
  if ! echo "$RESPONSE" | grep -qE "第[0-9]+页|p\.[0-9]+|章节|指南|《"; then
    MISSING+=("$CITATION_LABEL")
  fi
fi

# ─── doctor 确定性静态检查（处方剂量泄漏 / 证据等级同质化）──
# 仅 stderr 警告，不改变正文与退出码（不阻断 live 回答）
if [[ "$MODE" == "doctor" ]]; then
  DC=$(printf '%s' "$RESPONSE" | python3 "$SCRIPT_DIR/doctor_checks.py" 2>/dev/null || echo "{}")
  DC_WARN=$(DC="$DC" python3 - <<'PYEOF' 2>/dev/null || true
import json, os
c = json.loads(os.environ.get("DC", "") or "{}")
hits = c.get("dosing_hits") or []
if hits:
    print(f"处方剂量泄漏（doctor 应给药物类别/原则，禁具体剂量+途径/频次）：{hits[0]}")
if c.get("homogeneous_evidence"):
    lv = "、".join(c.get("evidence_levels", [])) or "同一等级"
    print(f"证据等级同质化：{c.get('evidence_count')} 条标注全为「{lv}」，请逐 entry 取级")
PYEOF
)
  [[ -n "$DC_WARN" ]] && while IFS= read -r _w; do
    [[ -n "$_w" ]] && echo "⚠️  $_w" >&2
  done <<< "$DC_WARN"
fi

# ─── 输出处理 ────────────────────────────────────────────────
if [[ ${#MISSING[@]} -eq 0 ]]; then
  echo "$RESPONSE"
  exit 0
fi

MISSING_LIST=$(printf "、%s" "${MISSING[@]}")
MISSING_LIST="${MISSING_LIST:1}"

echo "⚠️  输出结构不完整，缺少：${MISSING_LIST}" >&2
echo "$RESPONSE"
exit 1
