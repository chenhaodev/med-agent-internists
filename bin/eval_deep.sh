#!/usr/bin/env bash
# eval_deep.sh — 带 verify_claims 原子声明核验的全量评估（并发版）
# 在模型回答后增加：
#   C. verify_claims.py grep 核验
#   D. 有 ✗ 声明时一次回炉（build_prompt --reroll）
# 结果 JSON 包含每题 judge 分数 + verify_claims 数据
# 末尾输出 verify_claims 聚合报告（按专科分布）
#
# 用法：./bin/eval_deep.sh [--mode patient|doctor|both] [--limit N] [--id ID]
#                          [--judge-model M] [--concurrency N] [--cache]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

LIMIT=999
FILTER_ID=""
JUDGE_MODEL="${JUDGE_MODEL:-deepseek-v4-flash}"
EVAL_MODE="patient"
EVAL_CONCURRENCY="${EVAL_CONCURRENCY:-8}"
EVAL_NO_CACHE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)        LIMIT="$2";            shift 2 ;;
    --id)           FILTER_ID="$2";        shift 2 ;;
    --judge-model)  JUDGE_MODEL="$2";      shift 2 ;;
    --concurrency)  EVAL_CONCURRENCY="$2"; shift 2 ;;
    --cache)        EVAL_NO_CACHE=0;       shift ;;
    --mode)         EVAL_MODE="$2";        shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

if [[ "$EVAL_MODE" == "both" ]]; then
  CACHE_FLAG=()
  [[ "$EVAL_NO_CACHE" == "0" ]] && CACHE_FLAG=(--cache)
  "$0" --mode patient ${LIMIT:+--limit "$LIMIT"} ${FILTER_ID:+--id "$FILTER_ID"} \
       --judge-model "$JUDGE_MODEL" --concurrency "$EVAL_CONCURRENCY" "${CACHE_FLAG[@]+"${CACHE_FLAG[@]}"}"
  "$0" --mode doctor  ${LIMIT:+--limit "$LIMIT"} ${FILTER_ID:+--id "$FILTER_ID"} \
       --judge-model "$JUDGE_MODEL" --concurrency "$EVAL_CONCURRENCY" "${CACHE_FLAG[@]+"${CACHE_FLAG[@]}"}"
  exit 0
fi

if [[ -f "$ROOT_DIR/.env" ]]; then
  source "$ROOT_DIR/.env"
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY。" >&2
  exit 1
fi

if [[ "$EVAL_MODE" == "doctor" ]]; then
  JUDGE_PROMPT_FILE="$ROOT_DIR/eval/judge_prompt_doctor.md"
else
  JUDGE_PROMPT_FILE="$ROOT_DIR/eval/judge_prompt.md"
fi
RESULTS_DIR="$ROOT_DIR/eval/results"
mkdir -p "$RESULTS_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
RESULT_FILE="$RESULTS_DIR/deep_${TIMESTAMP}_${EVAL_MODE}.json"
SUMMARY_FILE="$RESULTS_DIR/deep_${TIMESTAMP}_${EVAL_MODE}_summary.txt"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Deep Eval（含 verify_claims）— $(date '+%Y-%m-%d %H:%M:%S')  [mode: ${EVAL_MODE}]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

TOTAL=$(python3 - <<PYEOF
import yaml, json, os

with open("${ROOT_DIR}/eval/gold.yaml") as f:
    data = yaml.safe_load(f)

questions = data.get("questions", [])
questions = [q for q in questions if q.get("mode", "both") in ("${EVAL_MODE}", "both")]
filter_id = "${FILTER_ID}"
if filter_id:
    questions = [q for q in questions if q.get("id") == filter_id]
else:
    questions = questions[:${LIMIT}]

for i, q in enumerate(questions):
    with open(f"${WORKDIR}/q_{i:04d}.json", "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)

print(len(questions))
PYEOF
)

if [[ "$TOTAL" -eq 0 ]]; then
  echo "没有符合条件的题目，退出。" >&2
  exit 0
fi

echo "题目总数：$TOTAL  |  并发数：$EVAL_CONCURRENCY  |  生成缓存：$([[ "$EVAL_NO_CACHE" == "0" ]] && echo "开" || echo "关")"
echo ""

JUDGE_SYSTEM=$(cat "$JUDGE_PROMPT_FILE")

export ROOT_DIR EVAL_MODE JUDGE_SYSTEM JUDGE_MODEL EVAL_NO_CACHE WORKDIR SCRIPT_DIR
export DEEPSEEK_API_KEY DEEPSEEK_MODEL DEEPSEEK_TIMEOUT DEEPSEEK_MAX_RETRIES 2>/dev/null || true

DISPATCHER="$WORKDIR/dispatch.sh"
cat > "$DISPATCHER" << 'DISPATCH_EOF'
#!/usr/bin/env bash
idx=$(printf '%04d' "$1")
QUESTION_OBJ=$(cat "$WORKDIR/q_${idx}.json") \
  "$SCRIPT_DIR/eval_deep_worker.sh" "$WORKDIR/r_${idx}.json"
DISPATCH_EOF
chmod +x "$DISPATCHER"

seq 0 $((TOTAL - 1)) | xargs -P "$EVAL_CONCURRENCY" -n1 "$DISPATCHER"

# ─── 聚合：verify 数据 + eval 汇总 ───────────────────
{
python3 - <<PYEOF
import json, os, glob, sys

workdir     = "${WORKDIR}"
result_file = "${RESULT_FILE}"
timestamp   = "${TIMESTAMP}"
eval_mode   = "${EVAL_MODE}"
total_q     = int("${TOTAL}")

rows = []
for path in sorted(glob.glob(f"{workdir}/r_*.json")):
    try:
        with open(path, encoding="utf-8") as f:
            rows.append(json.load(f))
    except Exception as e:
        print(f"警告：无法读取 {path}: {e}", file=sys.stderr)

evaluated = len(rows)
errors    = sum(1 for r in rows if "error" in r)
scored_n  = evaluated - errors
passed_n  = sum(1 for r in rows if r.get("pass") is True)
failed_n  = scored_n - passed_n

def _sum(key): return sum(r.get("scores", {}).get(key, 0) for r in rows if "error" not in r)
sum_cov = _sum("coverage")
sum_acc = _sum("accuracy")
sum_saf = _sum("safety")
sum_grd = _sum("grounding")

def avg(s): return round(s / scored_n, 1) if scored_n else 0

pass_rate = round(passed_n * 100 / scored_n, 1) if scored_n else 0
avg_total = avg(sum_cov + sum_acc + sum_saf + sum_grd)

# verify 聚合
spec_claims  = {}
total_fail   = 0
rerolled_ids = []
for r in rows:
    v = r.get("verify", {})
    total_fail += v.get("fail_count", 0)
    if v.get("did_reroll"):
        rerolled_ids.append(r["id"])
    domain = (r.get("domains") or "unknown").split()[0]
    spec = domain.split(":")[0] if ":" in domain else domain
    if spec not in spec_claims:
        spec_claims[spec] = {"fail_count": 0, "reroll_count": 0, "questions": []}
    spec_claims[spec]["fail_count"] += v.get("fail_count", 0)
    if v.get("did_reroll"):
        spec_claims[spec]["reroll_count"] += 1
    for c in v.get("claims", []):
        if c.get("status") == "✗":
            spec_claims[spec]["questions"].append({
                "id":       r["id"],
                "claim":    c.get("claim", ""),
                "kind":     c.get("kind", ""),
                "evidence": c.get("evidence", ""),
            })

summary = {
    "timestamp":      timestamp,
    "total_questions": total_q,
    "evaluated":      evaluated,
    "errors":         errors,
    "passed":         passed_n,
    "failed":         failed_n,
    "pass_rate_pct":  pass_rate,
    "avg_scores": {
        "coverage":  avg(sum_cov),
        "accuracy":  avg(sum_acc),
        "safety":    avg(sum_saf),
        "grounding": avg(sum_grd),
        "total":     avg_total,
    },
    "verify_summary": {
        "total_fail_claims": total_fail,
        "total_rerolled":    len(rerolled_ids),
        "rerolled_ids":      rerolled_ids,
        "by_specialty":      spec_claims,
    },
}
with open(result_file, "w", encoding="utf-8") as f:
    json.dump({"summary": summary, "results": rows}, f, ensure_ascii=False, indent=2)

print(f"\n════════════════════════════════════════════")
print(f" Deep Eval 汇总 — {timestamp}  [mode: {eval_mode}]")
print(f"════════════════════════════════════════════")
print(f" 总题数：{total_q}  |  有效评分：{scored_n}  |  错误：{errors}")
print(f" 通过：{passed_n}  |  未通过：{failed_n}  |  通过率：{pass_rate}%")
print()
print(f" 平均分（满分各 10 分）：")
print(f"   覆盖度  (Coverage) ：{avg(sum_cov)}")
print(f"   准确度  (Accuracy) ：{avg(sum_acc)}")
print(f"   安全性  (Safety)   ：{avg(sum_saf)}")
print(f"   溯源性  (Grounding)：{avg(sum_grd)}")
print(f"   综合    (Total)    ：{avg_total} / 40")
print()
print(f" verify_claims 统计：")
print(f"   ✗ 声明总数（所有题）：{total_fail}")
print(f"   触发回炉题数         ：{len(rerolled_ids)}")
print()
target_met = avg_total >= 34
print(f" 目标（平均 ≥34/40）：{'达成 ✓' if target_met else f'未达成（当前 {avg_total}/40）'}")
print(f"════════════════════════════════════════════")
print()
print(f" 结果文件：{result_file}")
PYEOF
} | tee "$SUMMARY_FILE"
