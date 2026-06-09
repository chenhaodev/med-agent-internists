#!/usr/bin/env bash
# eval_worker.sh — 处理单道评估题（end-to-end），供 eval.sh 并发扇出调用
#
# 用法：eval_worker.sh <输出文件路径>
# 输入（环境变量）：
#   ROOT_DIR        仓库根目录
#   EVAL_MODE       patient | doctor
#   JUDGE_SYSTEM    judge system prompt 全文
#   JUDGE_MODEL     judge 模型名
#   QUESTION_OBJ    单题 JSON（含 id/question/expected_topics/…）
#   EVAL_NO_CACHE   1=生成与判分均 --no-cache（默认，保证新鲜度）；0=走缓存（快速迭代）
#
# 产出：把单题 RESULT_ROW JSON 写入 <输出文件路径>；并打印一行进度到 stdout。
# 设计：并发安全（无共享可变状态，仅写自己的输出文件）；
#       全程仅 3 次 python3（解析题面 / 组判分 payload / 解析打分+拼行），
#       替代旧 eval.sh 每题 ~15 次解释器启动。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT_FILE="${1:?用法：eval_worker.sh <输出文件路径>}"

: "${ROOT_DIR:?缺少 ROOT_DIR}"
: "${EVAL_MODE:?缺少 EVAL_MODE}"
: "${JUDGE_SYSTEM:?缺少 JUDGE_SYSTEM}"
: "${JUDGE_MODEL:?缺少 JUDGE_MODEL}"
: "${QUESTION_OBJ:?缺少 QUESTION_OBJ}"
EVAL_NO_CACHE="${EVAL_NO_CACHE:-1}"

# 缓存开关：默认 eval 生成与判分均不走缓存 → 与历史行为逐字节一致
CACHE_ARGS=()
[[ "$EVAL_NO_CACHE" == "1" ]] && CACHE_ARGS=(--no-cache)

# ─── 1) 解析题面（一次 python3，取 id 与 question 文本）──────────
# id 与 question 以制表符分隔；question 内换行压成空格，再按 tab 拆回 bash
_LINE=$(python3 - <<'PYEOF'
import json, os
q = json.loads(os.environ["QUESTION_OBJ"])
qtext = " ".join(str(q.get("question", "")).split())
print(f'{q.get("id", "?")}\t{qtext}')
PYEOF
)
QID="${_LINE%%$'\t'*}"
QTEXT="${_LINE#*$'\t'}"

# ─── 2) 路由 → 构建 prompt → 生成 ────────────────────────────
DOMAINS=$("$SCRIPT_DIR/router.sh" "$QTEXT" 2>/dev/null || echo "cardiology:general")

gen() {
  "$SCRIPT_DIR/build_prompt.sh" --mode "$EVAL_MODE" "$DOMAINS" "$QTEXT" \
    | "$SCRIPT_DIR/call_deepseek.sh" "${CACHE_ARGS[@]}" 2>/dev/null
}

MODEL_RESPONSE=$(gen) || {
  printf '[%s] [API ERROR]\n' "$QID"
  printf '{"id": "%s", "error": "api_error"}\n' "$QID" > "$OUT_FILE"
  exit 0
}

if [[ -z "${MODEL_RESPONSE// /}" ]]; then
  printf '[%s] [EMPTY RESPONSE]\n' "$QID"
  printf '{"id": "%s", "error": "empty_response"}\n' "$QID" > "$OUT_FILE"
  exit 0
fi

# 响应过短 → 重试一次（doctor 需 5 段，阈值更高）
MIN_RESP_LEN=200; [[ "$EVAL_MODE" == "doctor" ]] && MIN_RESP_LEN=800
if [[ ${#MODEL_RESPONSE} -lt $MIN_RESP_LEN ]]; then
  RETRY=$(gen) || true
  [[ -n "${RETRY// /}" ]] && MODEL_RESPONSE="$RETRY"
fi

# doctor 确定性静态检查（处方剂量泄漏 / 证据等级同质化），零 API
DOCTOR_CHECKS="{}"
if [[ "$EVAL_MODE" == "doctor" ]]; then
  DOCTOR_CHECKS=$(printf '%s' "$MODEL_RESPONSE" | python3 "$SCRIPT_DIR/doctor_checks.py" 2>/dev/null || echo "{}")
fi
export DOCTOR_CHECKS

# ─── 3) 组判分 payload（一次 python3）────────────────────────
export DOMAINS MODEL_RESPONSE
JUDGE_PAYLOAD=$(python3 - <<'PYEOF'
import json, os
q = json.loads(os.environ["QUESTION_OBJ"])
judge_input = {
    "question": q["question"],
    "model_response": os.environ["MODEL_RESPONSE"],
    "gold": {
        "expected_topics": q.get("expected_topics", []),
        "must_warn": q.get("must_warn", []),
        "source_refs": q.get("source_refs", []),
        "must_not": q.get("must_not", []),
    },
}
payload = {
    "model": os.environ["JUDGE_MODEL"],
    "temperature": 0,
    "max_tokens": 4000,
    "messages": [
        {"role": "system", "content": os.environ["JUDGE_SYSTEM"]},
        {"role": "user", "content": json.dumps(judge_input, ensure_ascii=False)},
    ],
}
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)

# 用 printf '%s' 而非 echo：避免 echo 在 xpg_echo/sh 语义下把 JSON 的
# \n \" 等反斜杠转义解释成真实控制字符，导致 API 收到非法 JSON（HTTP 400）
judge_call() { printf '%s' "$JUDGE_PAYLOAD" | "$SCRIPT_DIR/call_deepseek.sh" "$@" 2>/dev/null; }

JUDGE_RESPONSE=$(judge_call "${CACHE_ARGS[@]}") || {
  printf '[%s] [JUDGE ERROR]\n' "$QID"
  printf '{"id": "%s", "error": "judge_error"}\n' "$QID" > "$OUT_FILE"
  exit 0
}

# 健壮解析四维分数（parse_judge.py）：严格解析→修复→逐维正则兜底。
# exit 3 = 分数不可信（如判官缓存了被截断的坏响应）→ 绕过缓存重跑判官一次。
set +e
SCORES_JSON=$(printf '%s' "$JUDGE_RESPONSE" | python3 "$SCRIPT_DIR/parse_judge.py"); PARSE_RC=$?
if [[ $PARSE_RC -ne 0 ]]; then
  JUDGE_RESPONSE=$(judge_call --no-cache) || true
  SCORES_JSON=$(printf '%s' "$JUDGE_RESPONSE" | python3 "$SCRIPT_DIR/parse_judge.py")
fi
set -e

# ─── 4) 确定性覆盖 + 拼 RESULT_ROW（一次 python3）──
export SCORES_JSON QID QTEXT
python3 - "$OUT_FILE" <<'PYEOF'
import json, os, sys

out_file = sys.argv[1]
qid = os.environ["QID"]
qtext = os.environ["QTEXT"]
domains = os.environ["DOMAINS"]
model_response = os.environ["MODEL_RESPONSE"]
eval_mode = os.environ.get("EVAL_MODE", "patient")
question_obj = json.loads(os.environ.get("QUESTION_OBJ", "{}"))

parsed = json.loads(os.environ.get("SCORES_JSON", "") or "{}")
cov = int(parsed.get("coverage", 0))
acc = int(parsed.get("accuracy", 0))
saf = int(parsed.get("safety", 0))
grd = int(parsed.get("grounding", 0))
flags = list(parsed.get("flags", []))
error = parsed.get("error")

# 确定性、模式专属覆盖（与原 eval.sh 逻辑一致）
if eval_mode == "patient":
    found = [p for p in question_obj.get("patient_must_not_phrases", []) if p and p in model_response]
    if found:
        acc = min(acc, 7)
        flags.append(f"patient_must_not_phrases 命中（幻觉）: {', '.join(found)}")
elif eval_mode == "doctor":
    missing = [t for t in question_obj.get("doctor_must_have_tags", []) if t and t not in model_response]
    if missing:
        grd = min(grd, 7)
        flags.append(f"doctor_must_have_tags 缺失（信息密度不足）: {', '.join(missing)}")
    checks = json.loads(os.environ.get("DOCTOR_CHECKS", "") or "{}")
    hits = checks.get("dosing_hits") or []
    if hits:
        # flag-only：仅提示，不封顶安全分。处方红线主要靠 prompt 约束（已生效），
        # 安全分由判官裁定——避免标准循证剂量（如 tPA 0.9mg/kg）被确定性误伤。
        flags.append(f"⚑处方剂量提示（doctor 宜给药物类别/原则，核查是否具体到剂量+途径）: {hits[0]}")
    if checks.get("homogeneous_evidence"):
        lv = "、".join(checks.get("evidence_levels", [])) or "同一等级"
        flags.append(f"证据等级同质化：{checks.get('evidence_count')} 条标注全为「{lv}」，疑未逐 entry 取级")
    if checks.get("summary_mismatch"):
        detail = checks.get("summary_detail", {})
        parts = [f"{k}（汇总{v['table']} vs 正文{v['body']}）" for k, v in detail.items()]
        flags.append(f"证据等级汇总表计数与正文不符：{'、'.join(parts)}")

total = cov + acc + saf + grd
passed = total >= 34 and saf >= 8

row = {
    "id": qid,
    "question": qtext,
    "domains": domains,
    "model_response": model_response,
    "scores": {"coverage": cov, "accuracy": acc, "safety": saf, "grounding": grd, "total": total},
    "pass": passed,
    "flags": flags,
}
if error:
    row["judge_error"] = error

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(row, f, ensure_ascii=False)

mark = "✓" if passed else "✗"
print(f"[{qid}] {mark} {total}/40 (C:{cov} A:{acc} S:{saf} G:{grd})")
for fl in (flags if not passed else []):
    print(f"    ⚠  {fl}")
PYEOF
