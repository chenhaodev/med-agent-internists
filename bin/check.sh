#!/usr/bin/env bash
# check.sh — 一键门禁：跑齐 eval 前所有静态/确定性检查，任一失败即非零退出
#
# 默认（无需 API key）：
#   audit_routing · audit_grounding · audit_grounding_sample · audit_schema
# 有 DEEPSEEK_API_KEY 时追加：
#   smoke（E2E 冒烟，无 key 自动跳过）
# 加 --with-oob 时再追加：
#   eval_oob（OOB/危机拦截评估，消耗 API 额度）
#
# 用法：
#   ./bin/check.sh              # 静态门禁 + smoke
#   ./bin/check.sh --with-oob   # 再跑 OOB 评估
#
# 退出码：0 = 全过；1 = 有 FAIL（可挂 pre-commit / CI）

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

GREEN="\033[0;32m"; RED="\033[0;31m"; YELLOW="\033[0;33m"; NC="\033[0m"

WITH_OOB=false
for a in "$@"; do
  case "$a" in
    --with-oob) WITH_OOB=true ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
  esac
done

# 载入 .env 以判断是否有 key
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a; source "$ROOT_DIR/.env" 2>/dev/null || true; set +a
fi

pass=0; fail=0; skip=0
declare -a FAILED=()

run_gate() {
  local name="$1"; shift
  printf "── %-26s " "$name"
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [[ $rc -eq 0 ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    (( pass++ )) || true
  else
    echo -e "${RED}✗ FAIL${NC}"
    echo "$out" | sed 's/^/     /' | tail -6
    (( fail++ )) || true
    FAILED+=("$name")
  fi
}

echo "═══════════════════════════════════════════════════════"
echo " check.sh — 西氏内科学精要 内科 Agent 门禁"
echo "═══════════════════════════════════════════════════════"

# ── 静态/确定性门禁（无需 API key）──
run_gate "audit_routing"          python3 "$SCRIPT_DIR/audit_routing.py"
run_gate "audit_grounding"        python3 "$SCRIPT_DIR/audit_grounding.py"
run_gate "audit_grounding_sample" python3 "$SCRIPT_DIR/audit_grounding_sample.py"
run_gate "audit_schema"           python3 "$SCRIPT_DIR/audit_schema.py"

# ── 需要 API key 的门禁 ──
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  run_gate "smoke (E2E)"          bash "$SCRIPT_DIR/smoke.sh"
  if [[ "$WITH_OOB" == "true" ]]; then
    run_gate "eval_oob"           bash "$SCRIPT_DIR/eval_oob.sh"
  else
    printf "── %-26s ${YELLOW}○ SKIP${NC}（加 --with-oob 运行）\n" "eval_oob"
    (( skip++ )) || true
  fi
else
  printf "── %-26s ${YELLOW}○ SKIP${NC}（未设置 DEEPSEEK_API_KEY）\n" "smoke / eval_oob"
  (( skip++ )) || true
fi

echo "═══════════════════════════════════════════════════════"
echo -e " 结果：${GREEN}PASS $pass${NC}  ${RED}FAIL $fail${NC}  ${YELLOW}SKIP $skip${NC}"
[[ $fail -gt 0 ]] && echo -e " 失败门禁：${RED}${FAILED[*]}${NC}"
echo "═══════════════════════════════════════════════════════"

[[ $fail -eq 0 ]]
