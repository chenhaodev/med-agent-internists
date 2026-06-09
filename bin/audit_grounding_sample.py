#!/usr/bin/env python3
"""
audit_grounding_sample.py — 核验「来源页」落在该障碍真实印刷页范围内（无 LLM）

知识库为英译中，无法用中文词去英文原文里匹配；故改为校验**引用页码是否落在该障碍
章节的真实印刷页范围内**：每个障碍 MD 的 [p.N | 页码 F] 标记给出该障碍覆盖的印刷
页区间 [min,max]，断言其每个 entry 的 source_page ∈ [min-tol, max+tol]。
这能捕获「引用页指向了别的障碍 / 越界 / 插值大幅偏移」这类真实接地缺陷。

用法：
  python3 bin/audit_grounding_sample.py [--tol 2]
  退出码 1 = 有越界引用
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KN = ROOT / "knowledge"
SRC = ROOT / "source" / "chapters"
PAGE_RE = re.compile(r"^\[p\.\d+(?:\s*\|\s*页码\s*(\d+))?\]\s*$")


def folio_range(md_path: Path):
    folios = []
    for line in md_path.read_text(encoding="utf-8").splitlines():
        m = PAGE_RE.match(line.strip())
        if m and m.group(1):
            folios.append(int(m.group(1)))
    return (min(folios), max(folios)) if folios else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=int, default=2, help="允许超出范围的印刷页容差")
    args = ap.parse_args()

    total = ok = oob = skipped = 0
    bad = []
    for p in sorted(KN.rglob("*.yaml")):
        if p.name == "chapters.yaml":
            continue
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        # 跳过非病种 YAML（guidelines/ 指南注入、safety_floor/ 层3 安全底线，
        # 无 specialty/disease 顶层键、不对应单一章节 MD）
        if "specialty" not in data or "disease" not in data:
            continue
        md = SRC / data["specialty"] / f"{data['disease']}.md"
        rng = folio_range(md) if md.exists() else None
        if not rng:
            skipped += 1
            continue
        lo, hi = rng[0] - args.tol, rng[1] + args.tol
        for e in data.get("entries", []):
            sp = e.get("source_page")
            if not isinstance(sp, int):
                continue
            total += 1
            if lo <= sp <= hi:
                ok += 1
            else:
                oob += 1
                bad.append(f"{data['specialty']}/{data['disease']} {e.get('id')} "
                           f"source_page={sp} 不在范围 [{rng[0]},{rng[1]}]")

    rate = ok / total if total else 0.0
    print(f"校验 {total} 条 entry（{skipped} 个障碍无源MD跳过）")
    print(f"在范围内 {ok}  越界 {oob}  →  页码接地率 {rate:.1%}  (容差 ±{args.tol} 印刷页)")
    for b in bad[:20]:
        print(f"  ✗ {b}")
    sys.exit(0 if oob == 0 else 1)


if __name__ == "__main__":
    main()
