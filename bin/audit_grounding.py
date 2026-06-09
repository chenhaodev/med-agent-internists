#!/usr/bin/env python3
"""
audit_grounding.py — knowledge YAML 证据接地可靠性审计（无 LLM 调用，<5s）

三项静态检查，覆盖「template 过高」类缺陷的结构性根因：

  [G1] 词汇合法性：疾病基线 YAML 的每条 evidence_level ∈ {高,中,低,未注明}；
       标记提取噪声（如混入 A/B 等指南专用等级）。
       注：guidelines/ 子目录原生使用 A/B/C 分级，属合法词汇，故不在 G1 扫描范围内。
  [G2] 映射覆盖：断言 prompts/output_schema_doctor.md 含「源等级→模板标注」映射节，
       且为每个实际出现的源取值（含 未注明）都提供了一行映射（源取值→模板标注）。
       Part-C 完成后应通过；若任一映射行缺失则 FAIL。
  [G3] 内嵌冲突：key_points 含细粒度等级词（B级/C级/I类/RCT 等）
       而 entry 级 evidence_level 为粗粒度 → WARN（待和解，不硬阻断）

用法：
  python3 bin/audit_grounding.py              # 全量
  python3 bin/audit_grounding.py --only g1    # 仅词汇检查
  python3 bin/audit_grounding.py --only g2    # 仅映射覆盖检查
  python3 bin/audit_grounding.py --only g3    # 仅内嵌冲突检查
  退出码 1 = 有 FAIL（G1/G2；G3 仅 WARN，不影响退出码）
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KN = ROOT / "knowledge"
SCHEMA_DOCTOR = ROOT / "prompts" / "output_schema_doctor.md"

# 合法的源词汇（提取时应产生的取值）
VALID_VOCAB = {"高", "中", "低", "未注明"}

# output_schema_doctor.md 中映射节必须包含的标记
MAPPING_MARKER = "源等级→模板标注"

# 源取值 → 期望的模板标注（G2 逐行校验映射表是否覆盖每个取值）
TEMPLATE_LABELS = {
    "高": "高级别证据",
    "中": "中级别证据",
    "低": "低级别证据",
    "未注明": "临床常用",
}

# 内嵌细粒度等级词（与 entry 粒度冲突的信号）
INLINE_GRADE_RE = re.compile(
    r"[A-C]级证据|[A-C]级推荐"
    r"|I{1,3}类适应证|IV类适应证"
    r"|RCT|随机对照试验|Meta分析|系统综述|队列研究|专家共识|病例系列"
)


def load_yamls():
    """加载疾病基线 YAML，跳过 guidelines/ 子目录。

    返回 (results, parse_errors)：
      results      = [(path, data|None), ...]
      parse_errors = [(rel_path, msg), ...]  解析失败的文件（不静默吞掉）
    """
    results, parse_errors = [], []
    for p in sorted(KN.rglob("*.yaml")):
        if "guidelines" in p.parts:
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            data = None
            parse_errors.append((p.relative_to(ROOT), str(exc).splitlines()[0]))
        results.append((p, data))
    return results, parse_errors


# ── G1 ───────────────────────────────────────────────────────────────────────

def audit_g1(yamls):
    """
    词汇合法性。返回 (noise_list, total_count)。
    noise_list = [(rel_path, entry_id, bad_value), ...]
    """
    noise, total = [], 0
    for path, data in yamls:
        if not data or not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            lv = entry.get("evidence_level")
            if lv is None:
                continue
            total += 1
            if str(lv) not in VALID_VOCAB:
                noise.append((path.relative_to(ROOT), entry.get("id", "?"), lv))
    return noise, total


# ── G2 ───────────────────────────────────────────────────────────────────────

def audit_g2(yamls):
    """
    映射覆盖。返回 (missing_marker, uncovered_values, actual_vocab).
    missing_marker=True  → 映射节不存在（Part-C 未完成）
    uncovered_values      → 映射节存在但遗漏了某些源取值
    actual_vocab          → 全库实际出现的 evidence_level 取值集合
    """
    actual_vocab: set[str] = set()
    for _, data in yamls:
        if not data or not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if isinstance(entry, dict):
                lv = entry.get("evidence_level")
                if lv is not None:
                    actual_vocab.add(str(lv))

    if SCHEMA_DOCTOR.exists():
        schema_text = SCHEMA_DOCTOR.read_text(encoding="utf-8")
    else:
        schema_text = ""
    missing_marker = MAPPING_MARKER not in schema_text

    if missing_marker:
        # 映射节不存在 → 所有合法取值均未覆盖
        uncovered = actual_vocab & VALID_VOCAB
    else:
        # 映射节存在 → 逐取值校验「确有一行把该源取值映射到模板标注」，
        # 而非裸字符子串匹配（裸字符会因 高血压/高级别证据 等无关文本误判为已覆盖）
        legal = actual_vocab & VALID_VOCAB
        uncovered = {v for v in legal if not _has_mapping_row(schema_text, v)}

    return missing_marker, uncovered, actual_vocab


def _has_mapping_row(schema_text, value):
    """映射表中是否存在一行：首个单元格为 value，且右侧含其期望模板标注。

    示例匹配 `| 高 | \\`(高级别证据)\\` |`，对仅 value 偶然出现的散文不误判。
    """
    label = TEMPLATE_LABELS.get(value, "")
    row_re = re.compile(
        rf"^\s*\|\s*{re.escape(value)}\s*\|.*{re.escape(label)}",
        re.MULTILINE,
    )
    return bool(row_re.search(schema_text))


# ── G3 ───────────────────────────────────────────────────────────────────────

def audit_g3(yamls):
    """
    内嵌冲突（WARN，不影响退出码）。
    每个 entry 最多报一次：若 key_points 含细粒度等级词，而 entry-level 为粗粒度。
    返回 [(rel_path, entry_id, entry_level, matched_token, snippet), ...]
    """
    conflicts = []
    for path, data in yamls:
        if not data or not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            entry_level = str(entry.get("evidence_level", ""))
            for kp in entry.get("key_points", []):
                if not isinstance(kp, str):
                    continue
                m = INLINE_GRADE_RE.search(kp)
                if m:
                    conflicts.append((
                        path.relative_to(ROOT),
                        entry.get("id", "?"),
                        entry_level,
                        m.group(),
                        kp[:90],
                    ))
                    break  # 每条 entry 只报首个命中
    return conflicts


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="audit_grounding: 证据接地可靠性审计")
    ap.add_argument("--only", choices=["g1", "g2", "g3"])
    args = ap.parse_args()

    yamls, parse_errors = load_yamls()
    n_files = len(yamls)
    exit_code = 0

    if parse_errors:
        exit_code = 1
        print(f"[!] {len(parse_errors)} 个 YAML 解析失败（视为 FAIL）：")
        for rel, msg in parse_errors:
            print(f"  ✗ FAIL  {rel}  {msg}")
        print()

    if args.only in (None, "g1"):
        print(f"[G1] 词汇合法性检查（{n_files} 个 YAML）…")
        noise, total = audit_g1(yamls)
        for rel, eid, lv in noise:
            print(f"  ✗ FAIL  {rel}  entry={eid}  evidence_level={lv!r}  （疑似提取噪声）")
        if noise:
            exit_code = 1
        status = f"{len(noise)} 个非法取值 ✗" if noise else "通过 ✓"
        print(f"[G1] {status}  （共扫描 {total} 条 evidence_level）\n")

    if args.only in (None, "g2"):
        print("[G2] 映射覆盖检查…")
        missing_marker, uncovered, actual_vocab = audit_g2(yamls)
        valid_found = sorted(actual_vocab & VALID_VOCAB)
        noise_found = sorted(actual_vocab - VALID_VOCAB)
        print(f"  实际词汇：合法 {valid_found}  |  噪声 {noise_found}")
        if missing_marker:
            exit_code = 1
            print(f"  ✗ FAIL  {SCHEMA_DOCTOR.relative_to(ROOT)} 缺少「{MAPPING_MARKER}」节")
            print("          → Part-C 未完成：output_schema_doctor.md 须加源→模板映射表")
        elif uncovered:
            exit_code = 1
            for v in sorted(uncovered):
                print(f"  ✗ FAIL  映射表未覆盖源取值 {v!r}")
        else:
            print("  ✓ 映射表存在，全部合法取值已覆盖")
        status = "通过 ✓" if (not missing_marker and not uncovered) else "FAIL ✗"
        print(f"[G2] {status}\n")

    if args.only in (None, "g3"):
        print("[G3] 内嵌等级冲突检查（WARN，不影响退出码）…")
        conflicts = audit_g3(yamls)
        for rel, eid, entry_lv, tok, snippet in conflicts:
            print(f"  ⚠ WARN  {rel}  entry={eid}  entry_level={entry_lv!r}  内嵌词={tok!r}")
            print(f"          {snippet!r}")
        status = f"{len(conflicts)} 个待和解 ⚠" if conflicts else "通过 ✓"
        print(f"[G3] {status}\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
