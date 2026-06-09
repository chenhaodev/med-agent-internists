#!/usr/bin/env python3
"""doctor_checks.py — doctor 模式回答的确定性静态检查（零 API）。

输入：模型回答全文（stdin）。
输出：JSON（stdout）
    {
      "homogeneous_evidence": bool,   # 【循证管理】证据等级是否「偷懒同质化」
      "evidence_levels": [...],       # 出现过的去重等级
      "evidence_count": int,          # 标注总数
      "dosing_hits": [...],           # 命中的「具体药物剂量+给药途径/频次」处方片段
      "summary_mismatch": bool,       # 【证据等级汇总】表条目数与正文标注数不符
      "summary_detail": {...}         # 不符的等级及差异，如 {"高级别": {"table":8,"body":4}}
    }

被 eval_worker.sh / eval_deep_worker.sh（确定性 flag）与 postprocess.sh（live WARN）复用。
三类检查对应 eval 中反复出现的 doctor 失分模式：
  - 证据等级全标同级（ENDO_NUTR_DR_01 / RESP_PLEURAL_DR_01）
  - 循证管理里写出具体处方剂量（DIGE_GI_DR_01 泮托拉唑80mg iv / SUBS_ALC_DR_01）
  - 证据等级汇总表条目数与正文不一致（AKI_MONITOR_01 / CNS_ENCEPH_01 / GERI_FALL_01）
"""
import json
import re
import sys

# ── 证据等级同质化 ────────────────────────────────────────────
_LEVEL_KEYS = ("高级别", "中级别", "低级别", "临床常用", "指南推荐")
# 段落标题：【循证管理】之后、下一个【之前
_MGMT_SECTION = re.compile(r"【循证管理】(.*?)(?=【|\Z)", re.DOTALL)
# 【证据等级汇总】段落
_SUMMARY_SECTION = re.compile(r"【证据等级汇总】(.*?)(?=【|\Z)", re.DOTALL)
# 括号内含「证据/临床常用/指南推荐」的标注（全角或半角括号）
_ANNOT = re.compile(r"[（(]([^（）()]*?(?:证据|临床常用|指南推荐)[^（）()]*?)[）)]")
# 汇总表数据行：| 等级名 | N | ... |
_TABLE_ROW = re.compile(r"\|\s*([^|\-][^|]*?)\s*\|\s*(\d+)\s*\|")
# 汇总表表头关键字（用于跳过 header）
_TABLE_HEADER_KEYS = ("等级", "条目数", "代表来源", "---")
# 表格等级名 → body 关键词映射
_TABLE_TO_BODY = {
    "高级别证据": "高级别",
    "中级别证据": "中级别",
    "低级别证据": "低级别",
    "指南推荐":   "指南推荐",
    "临床常用":   "临床常用",
}


def _evidence_levels(text):
    m = _MGMT_SECTION.search(text)
    section = m.group(1) if m else ""
    levels = []  # 每个标注内可能含多个等级（如「低级别证据 + 临床常用」）
    count = 0
    for annot in _ANNOT.findall(section):
        count += 1
        for key in _LEVEL_KEYS:
            if key in annot:
                levels.append(key)
    distinct = sorted(set(levels))
    # 仅当 ≥4 条标注被全部压成单一**教材证据等级**（高/中级别）时判同质化——
    # 这是 P2 指出的「偷懒同质化」信号。全为「指南推荐」或「临床常用」属合法
    # （指南叠加答案的来源天然同级），不应误伤。低级别全同亦不典型，不触发。
    LAZY = ({"高级别"}, {"中级别"})
    homogeneous = count >= 4 and set(distinct) in LAZY
    return homogeneous, distinct, count


# ── 处方剂量泄漏 ──────────────────────────────────────────────
# 剂量单位（刻意不含 mmHg / mmol / % / 页 等非给药单位）。
# 不含 ml（"30 ml/min" 是 eGFR/肌酐清除率阈值，非给药）；
# 负向预查排除「浓度/清除率」后缀（/dl /L /min /天…），避免把
# 「血糖 70mg/dl」「eGFR 30 ml/min」这类化验/阈值误判为处方剂量。
_DOSE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mg|µg|μg|mcg|g|IU|U|单位)\b"
    r"(?!\s*/\s*(?:dl|dL|d|L|天|日|周|h|hr|min|分钟|小时))"
)
# 给药途径 / 频次关键词
_ROUTE = re.compile(
    r"iv|静脉|口服|肌注|皮下|泵入|持续输注|顿服|po\b|im\b|sc\b"
    r"|q\d+\s*h|qd\b|bid\b|tid\b|qid\b|每日\s*\d+\s*次|每\s*\d+\s*小时",
    re.IGNORECASE,
)
# 速率型剂量（mg/h、µg/kg/min 等）本身即处方，无需邻近途径。
# 刻意只认药物级单位（mg/µg/mcg/U），不含裸 g/kg——后者多为营养蛋白摄入目标（如 1.5 g/kg），非处方。
_RATE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|µg|μg|mcg|U)\s*/\s*(?:h|kg|min|小时|公斤)", re.IGNORECASE)


def _dosing_hits(text):
    hits = []
    for m in _RATE.finditer(text):
        hits.append(m.group(0).strip())
    for m in _DOSE.finditer(text):
        s, e = m.start(), m.end()
        window = text[max(0, s - 18) : min(len(text), e + 18)]
        if _ROUTE.search(window):
            hits.append(window.strip())
    # 去重并截断
    seen, out = set(), []
    for h in hits:
        key = re.sub(r"\s+", "", h)
        if key not in seen:
            seen.add(key)
            out.append(h[:60])
    return out


def _body_level_counts(section):
    """Per-level annotation counts from 【循证管理】 body section text."""
    counts: dict = {}
    for annot in _ANNOT.findall(section):
        for key in _LEVEL_KEYS:
            if key in annot:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _check_summary_mismatch(text):
    """Compare 【证据等级汇总】 table counts against 【循证管理】 body annotation counts."""
    body_m = _MGMT_SECTION.search(text)
    body_section = body_m.group(1) if body_m else ""
    body = _body_level_counts(body_section)

    summ_m = _SUMMARY_SECTION.search(text)
    if not summ_m:
        return False, {}

    table: dict = {}
    for row in _TABLE_ROW.finditer(summ_m.group(1)):
        raw = row.group(1).strip()
        if any(h in raw for h in _TABLE_HEADER_KEYS):
            continue
        n_str = row.group(2)
        norm = _TABLE_TO_BODY.get(raw, raw)
        if norm in _LEVEL_KEYS:
            table[norm] = int(n_str)

    detail: dict = {}
    for k in set(body) | set(table):
        b, t = body.get(k, 0), table.get(k, 0)
        if b != t:
            detail[k] = {"table": t, "body": b}
    return bool(detail), detail


def check(text):
    text = text or ""
    homogeneous, levels, count = _evidence_levels(text)
    mismatch, mismatch_detail = _check_summary_mismatch(text)
    return {
        "homogeneous_evidence": homogeneous,
        "evidence_levels": levels,
        "evidence_count": count,
        "dosing_hits": _dosing_hits(text),
        "summary_mismatch": mismatch,
        "summary_detail": mismatch_detail,
    }


def main():
    print(json.dumps(check(sys.stdin.read()), ensure_ascii=False))


if __name__ == "__main__":
    main()
