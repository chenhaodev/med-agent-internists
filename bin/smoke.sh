#!/usr/bin/env bash
# smoke.sh — E2E 冒烟测试：直接跑 ask.sh（本尊，不是 eval 替身管线）
#
# 覆盖目标：
#   · patient 模式 happy-path（取自 README 快速上手）
#   · doctor 模式 happy-path（取自 README 快速上手）
#   · OOB 拒答分支（确认拒答路径不崩溃）
#
# 断言：退出码 0；stderr 无 ⚠️；该模式 5 段名全出现；═══ 边框在；非 OOB 回答不含 OOB 标志词
#
# 需要 DEEPSEEK_API_KEY：
#   · 有 KEY → 真实调用，全量断言
#   · 无 KEY → SKIP 并提示（CI 配 secret 后自动生效）
#
# 用法：
#   ./bin/smoke.sh                  # 跑全部 3 个用例
#   SMOKE_FAST=1 ./bin/smoke.sh     # 只跑 patient（节省配额，本地快速验证）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SECTIONS_YAML="$ROOT_DIR/schema/sections.yaml"
ASK="$SCRIPT_DIR/ask.sh"

# ─── 颜色 ────────────────────────────────────────────────────
GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[0;33m"
NC="\033[0m"

pass=0
fail=0
skip=0

_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
_fail() { echo -e "  ${RED}✗${NC} $*"; }
_skip() { echo -e "  ${YELLOW}○${NC} $*"; }

# ─── 前置条件 ────────────────────────────────────────────────
if [[ ! -f "$ASK" ]]; then
  echo "错误：bin/ask.sh 不存在，请确认工作目录正确。" >&2
  exit 1
fi

# 加载 .env（若存在）以获取 DEEPSEEK_API_KEY
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env" 2>/dev/null || true
  set +a
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo -e "${YELLOW}[SKIP] DEEPSEEK_API_KEY 未设置，跳过所有冒烟用例。${NC}"
  echo "       在 .env 中填入 DEEPSEEK_API_KEY，或 export DEEPSEEK_API_KEY=... 后重试。"
  exit 0
fi

# 从 sections.yaml 读取每个模式的段名（python one-liner）
_sections_for_mode() {
  python3 - "$SECTIONS_YAML" "$1" <<'PYEOF'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
for s in data.get(sys.argv[2], {}).get("sections", []):
    print(s)
PYEOF
}

PATIENT_SECTIONS=()
while IFS= read -r line; do PATIENT_SECTIONS+=("$line"); done < <(_sections_for_mode patient)
DOCTOR_SECTIONS=()
while IFS= read -r line; do DOCTOR_SECTIONS+=("$line"); done < <(_sections_for_mode doctor)

# ─── 用例执行器 ──────────────────────────────────────────────
run_case() {
  local label="$1"
  local mode="$2"
  local question="$3"
  shift 3
  local required_sections=("$@")

  echo ""
  echo "── $label ──────────────────────────────────────────────"

  local stdout_file stderr_file
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)

  local rc=0
  "$ASK" --mode "$mode" "$question" >"$stdout_file" 2>"$stderr_file" || rc=$?

  local stdout_content stderr_content
  stdout_content=$(cat "$stdout_file")
  stderr_content=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file"

  local case_fail=0

  # 1. 退出码 0
  if [[ "$rc" -eq 0 ]]; then
    _ok "退出码 0"
  else
    _fail "退出码 $rc（期望 0）"
    case_fail=1
  fi

  # 2. stderr 无 ⚠️
  if echo "$stderr_content" | grep -qF "⚠️"; then
    _fail "stderr 含 ⚠️ 警告：$(echo "$stderr_content" | grep '⚠️')"
    case_fail=1
  else
    _ok "stderr 无 ⚠️"
  fi

  # 3. 边框存在
  if echo "$stdout_content" | grep -qF "═══"; then
    _ok "═══ 边框存在"
  else
    _fail "stdout 缺少 ═══ 边框"
    case_fail=1
  fi

  # 4. 5 段名全出现
  local missing_sections=()
  for section in "${required_sections[@]}"; do
    if ! echo "$stdout_content" | grep -qF "$section"; then
      missing_sections+=("$section")
    fi
  done
  if [[ ${#missing_sections[@]} -eq 0 ]]; then
    _ok "所有 ${#required_sections[@]} 个段名均存在"
  else
    _fail "缺少段名：${missing_sections[*]}"
    case_fail=1
  fi

  if [[ "$case_fail" -eq 0 ]]; then
    (( pass++ )) || true
    echo -e "  ${GREEN}PASS${NC}"
  else
    (( fail++ )) || true
    echo -e "  ${RED}FAIL${NC}"
  fi
}

run_oob_case() {
  local label="$1"
  local question="$2"

  echo ""
  echo "── $label ──────────────────────────────────────────────"

  local stdout_file stderr_file
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)

  local rc=0
  "$ASK" "$question" >"$stdout_file" 2>"$stderr_file" || rc=$?

  local stdout_content stderr_content
  stdout_content=$(cat "$stdout_file")
  stderr_content=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file"

  local case_fail=0

  # 1. 退出码 0（OOB 应优雅退出）
  if [[ "$rc" -eq 0 ]]; then
    _ok "退出码 0"
  else
    _fail "退出码 $rc（OOB 应以 0 退出）"
    case_fail=1
  fi

  # 2. stderr 无 ⚠️（OOB 走不到 postprocess，不应有格式警告）
  if echo "$stderr_content" | grep -qF "⚠️"; then
    _fail "stderr 含 ⚠️（OOB 分支不应触发格式检查）"
    case_fail=1
  else
    _ok "stderr 无 ⚠️"
  fi

  # 3. stdout 含拒答标志词
  if echo "$stdout_content" | grep -qE "超出.*范围|不在.*覆盖|不覆盖|超出了.*范围|建议咨询"; then
    _ok "含 OOB 拒答标志词"
  else
    _fail "stdout 不含 OOB 拒答标志词（疑似未走拒答路径）"
    case_fail=1
  fi

  if [[ "$case_fail" -eq 0 ]]; then
    (( pass++ )) || true
    echo -e "  ${GREEN}PASS${NC}"
  else
    (( fail++ )) || true
    echo -e "  ${RED}FAIL${NC}"
  fi
}

# ─── 执行用例 ────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
echo " smoke.sh — E2E 冒烟测试（ask.sh 本尊）"
echo "═══════════════════════════════════════════════════════"

# 用例 1：patient 模式（README 快速上手原句）
run_case \
  "用例 1: patient 模式（README 示例）" \
  "patient" \
  "我爸有高血压，平时饮食要注意什么？" \
  "${PATIENT_SECTIONS[@]}"

# 用例 2：doctor 模式（README 快速上手原句）
if [[ "${SMOKE_FAST:-0}" != "1" ]]; then
  run_case \
    "用例 2: doctor 模式（README 示例）" \
    "doctor" \
    "高血压血压控制目标？" \
    "${DOCTOR_SECTIONS[@]}"
else
  _skip "用例 2 (doctor) 已跳过（SMOKE_FAST=1）"
  (( skip++ )) || true
fi

# 用例 3：OOB 拒答路径
if [[ "${SMOKE_FAST:-0}" != "1" ]]; then
  run_oob_case \
    "用例 3: OOB 拒答路径" \
    "请推荐一首治疗高血压的音乐"
else
  _skip "用例 3 (OOB) 已跳过（SMOKE_FAST=1）"
  (( skip++ )) || true
fi

# ─── 汇总 ────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo -e " 结果：${GREEN}PASS $pass${NC}  ${RED}FAIL $fail${NC}  ${YELLOW}SKIP $skip${NC}"
echo "═══════════════════════════════════════════════════════"
echo ""

if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
exit 0
