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

另有 `--fix-summary` 模式（stdin→stdout）：按【循证管理】正文标注计数确定性重写
【证据等级汇总】表（幂等；相符则原样输出），把易错的手工计数从模型卸载到脚本。

被 eval_worker.sh / eval_deep_worker.sh 与 postprocess.sh 复用：
  - 证据等级汇总表计数不符 → `--fix-summary` 确定性修复（postprocess + 两个 eval worker，零 API）
  - 证据等级全标同级 → 同质化触发一次回炉（eval_deep_worker / ask.sh --deep），并作 flag
  - 循证管理里写出具体处方剂量 → flag-only（处方红线主要靠 prompt 约束）
三类检查对应 eval 中反复出现的 doctor 失分模式（同质化 ENDO_NUTR_DR_01 / RESP_PLEURAL_DR_01；
处方剂量 DIGE_GI_DR_01 泮托拉唑80mg iv / SUBS_ALC_DR_01；汇总计数 AKI_MONITOR_01 / GERI_FALL_01）。
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


# ── 汇总表确定性改写 ──────────────────────────────────────────
# body 关键词 → 表格等级显示名（_TABLE_TO_BODY 的反向）
_BODY_TO_TABLE = {b: d for d, b in _TABLE_TO_BODY.items()}
# 表格行固定顺序（与 output_schema_doctor.md 一致）
_LEVEL_ORDER = ("高级别", "中级别", "低级别", "指南推荐", "临床常用")
# 三列数据行：| 等级 | N | 代表来源 |
_TABLE_ROW3 = re.compile(r"\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|")


def _existing_sources(summary_text):
    """从原汇总表抽取每个 body 等级的「代表来源」单元格，供改写时保留。"""
    sources: dict = {}
    for row in _TABLE_ROW3.finditer(summary_text):
        raw = row.group(1).strip()
        if any(h in raw for h in _TABLE_HEADER_KEYS):
            continue
        norm = _TABLE_TO_BODY.get(raw, raw)
        if norm in _LEVEL_KEYS:
            sources[norm] = row.group(3).strip() or "—"
    return sources


def _build_table(body_counts, sources):
    """按正文计数 + 保留的来源，重建【证据等级汇总】表块（仅 >0 的等级行）。"""
    lines = ["| 等级 | 条目数 | 代表来源 |", "|------|--------|----------|"]
    for key in _LEVEL_ORDER:
        n = body_counts.get(key, 0)
        if n <= 0:
            continue
        display = _BODY_TO_TABLE.get(key, key)
        lines.append(f"| {display} | {n} | {sources.get(key, '—')} |")
    return "\n".join(lines)


def fix_summary(text):
    """若【证据等级汇总】表计数与正文不符，用正文计数确定性重写该表。幂等：相符则原样返回。"""
    text = text or ""
    mismatch, _ = _check_summary_mismatch(text)
    if not mismatch:
        return text
    summ_m = _SUMMARY_SECTION.search(text)
    body_m = _MGMT_SECTION.search(text)
    if not summ_m or not body_m:
        return text
    body = _body_level_counts(body_m.group(1))
    if not body:
        return text

    content = summ_m.group(1)
    lines = content.split("\n")
    tbl_idx = [i for i, ln in enumerate(lines) if "|" in ln]
    if not tbl_idx:
        return text

    new_table = _build_table(body, _existing_sources(content))
    start, end = tbl_idx[0], tbl_idx[-1]
    new_lines = lines[:start] + new_table.split("\n") + lines[end + 1:]
    new_content = "\n".join(new_lines)
    return text[: summ_m.start(1)] + new_content + text[summ_m.end(1):]


# 证据等级同质化时注入回炉 prompt 的指令（ask.sh / eval_deep_worker.sh 共用此唯一来源）
_HOMOGENEOUS_REROLL_NOTE = (
    "证据等级同质化——【循证管理】各条证据等级被统一标成同一级。"
    "请逐 entry 依注入片段的「证据质量」字段分别取级"
    "（高→高级别证据、中→中级别证据、未注明→临床常用），勿为图省事压成同一级。"
)


def reroll_note(text):
    """若证据等级同质化，返回回炉指令文本；否则返回空串。供回炉触发判定单次调用。"""
    return _HOMOGENEOUS_REROLL_NOTE if _evidence_levels(text or "")[0] else ""


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
    # --fix-summary：读全文，确定性改写汇总表后整篇输出（零 API，幂等）。
    # --reroll-note：读全文，同质化则输出回炉指令文本（否则空）——单次调用即得回炉判定。
    # 否则默认：输出检查结果 JSON。
    if "--fix-summary" in sys.argv[1:]:
        sys.stdout.write(fix_summary(sys.stdin.read()))
    elif "--reroll-note" in sys.argv[1:]:
        sys.stdout.write(reroll_note(sys.stdin.read()))
    else:
        print(json.dumps(check(sys.stdin.read()), ensure_ascii=False))


if __name__ == "__main__":
    main()
