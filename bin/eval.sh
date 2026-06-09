#!/usr/bin/env bash
# eval.sh — 全量评估脚本（并发版）
# 用法：./bin/eval.sh [--mode patient|doctor|both] [--limit N] [--id ID]
#                     [--judge-model M] [--concurrency N] [--cache]
#
# 注意：eval 测的是子管线（build_prompt | call_deepseek | judge）的模型质量，
# 不是出厂产品。运行前必须通过四道静态门禁 + E2E 冒烟：
#
#   python3 bin/audit_routing.py   # 路由可达性
#   python3 bin/audit_grounding.py # 证据接地可靠性
#   python3 bin/audit_schema.py    # 契约一致性 + 横切传播完整性
#   ./bin/smoke.sh                 # E2E 冒烟（ask.sh 双模式）
#
# 四者全 exit 0 后方可运行本脚本。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

LIMIT=999
FILTER_ID=""
JUDGE_MODEL="${JUDGE_MODEL:-deepseek-v4-flash}"
EVAL_MODE="patient"
EVAL_CONCURRENCY="${EVAL_CONCURRENCY:-8}"
EVAL_NO_CACHE=1  # 默认：生成不走缓存（eval 度量新鲜模型质量）

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
RESULT_FILE="$RESULTS_DIR/${TIMESTAMP}_${EVAL_MODE}.json"
SUMMARY_FILE="$RESULTS_DIR/${TIMESTAMP}_${EVAL_MODE}_summary.txt"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " 西氏内科精要 Eval — $(date '+%Y-%m-%d %H:%M:%S')  [mode: ${EVAL_MODE}]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── 工作目录（每题独立的 input/output json 文件）──────
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# ─── 抽题 + 写 q_NNNN.json ────────────────────────────
TOTAL=$(python3 - <<PYEOF
import yaml, json, os, sys

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

# ─── 导出 worker 所需环境变量 ─────────────────────────
export ROOT_DIR EVAL_MODE JUDGE_SYSTEM JUDGE_MODEL EVAL_NO_CACHE WORKDIR SCRIPT_DIR
export DEEPSEEK_API_KEY DEEPSEEK_MODEL DEEPSEEK_TIMEOUT DEEPSEEK_MAX_RETRIES 2>/dev/null || true

# ─── 调度脚本（与 bash 版本无关，避免 export -f 移植性问题）
DISPATCHER="$WORKDIR/dispatch.sh"
cat > "$DISPATCHER" << 'DISPATCH_EOF'
#!/usr/bin/env bash
# 由 eval.sh 生成；$WORKDIR / $SCRIPT_DIR 在运行时从环境变量取
idx=$(printf '%04d' "$1")
QUESTION_OBJ=$(cat "$WORKDIR/q_${idx}.json") \
  "$SCRIPT_DIR/eval_worker.sh" "$WORKDIR/r_${idx}.json"
DISPATCH_EOF
chmod +x "$DISPATCHER"

# ─── 并发扇出（xargs -P）──────────────────────────────
seq 0 $((TOTAL - 1)) | xargs -P "$EVAL_CONCURRENCY" -n1 "$DISPATCHER"

# ─── 一次聚合：所有 r_*.json → 最终 results + summary ─
{
python3 - <<PYEOF
import json, os, glob, sys

workdir    = "${WORKDIR}"
result_file = "${RESULT_FILE}"
timestamp  = "${TIMESTAMP}"
eval_mode  = "${EVAL_MODE}"
total_q    = int("${TOTAL}")

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

pass_rate  = round(passed_n * 100 / scored_n, 1) if scored_n else 0
avg_total  = avg(sum_cov + sum_acc + sum_saf + sum_grd)

summary = {
    "timestamp": timestamp,
    "total_questions": total_q,
    "evaluated": evaluated,
    "errors": errors,
    "passed": passed_n,
    "failed": failed_n,
    "pass_rate_pct": pass_rate,
    "avg_scores": {
        "coverage":  avg(sum_cov),
        "accuracy":  avg(sum_acc),
        "safety":    avg(sum_saf),
        "grounding": avg(sum_grd),
        "total":     avg_total,
    },
}
with open(result_file, "w", encoding="utf-8") as f:
    json.dump({"summary": summary, "results": rows}, f, ensure_ascii=False, indent=2)

print(f"\n════════════════════════════════════════════")
print(f" Eval 汇总报告 — {timestamp}  [mode: {eval_mode}]")
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
target_met = avg_total >= 34
print(f" 目标（平均 ≥85% 即 34/40）：{'达成 ✓' if target_met else f'未达成（当前 {avg_total}/40）'}")
print(f"════════════════════════════════════════════")
print()
print(f" 结果文件：{result_file}")
PYEOF
} | tee "$SUMMARY_FILE"
