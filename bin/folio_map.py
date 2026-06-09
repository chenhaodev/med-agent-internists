#!/usr/bin/env python3
"""
folio_map.py — 印刷页码(folio)提取工具

从 source/chapters/*.md 文件的 [p.N] 块中提取印刷页码。
每个 [p.N] 块头部几行含运行页眉，页眉下方紧跟独立的 2-4 位数字即为 folio。

API:
    extract_folio_from_page_text(text) -> int | None  — 从 PDF 原始文字提取 folio（供 ingest.py）
    build_folio_map(md_path) -> {physical_page: folio}  — 从源 md 建映射（供迁移/审计）

CLI:
    python3 bin/folio_map.py source/chapters/neurology/dementia.md  — 打印映射
    python3 bin/folio_map.py --check  — 验证已知 ground-truth
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 匹配 [p.N] 和 [p.N | 页码 F]（新格式）
PAGE_RE = re.compile(r"^\[p\.(\d+)(?:\s*\|\s*页码\s*(\d+))?\]\s*$")
FOLIO_RE = re.compile(r"^\d{2,4}$")
FOLIO_MIN, FOLIO_MAX = 10, 1600  # 本书印刷页范围


def extract_folio_from_page_text(text: str) -> "int | None":
    """从 PDF 物理页的原始文字中提取印刷 folio（供 ingest.py 直接调用）。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for l in lines[:5]:
        if FOLIO_RE.match(l):
            v = int(l)
            if FOLIO_MIN <= v <= FOLIO_MAX:
                return v
    return None


def _folio_from_block_lines(lines: list) -> "int | None":
    """从 [p.N] 块内容行中提取 folio。"""
    for l in lines[:6]:
        l = l.strip()
        if FOLIO_RE.match(l):
            v = int(l)
            if FOLIO_MIN <= v <= FOLIO_MAX:
                return v
    return None


def build_folio_map(md_path: Path) -> "dict[int, int]":
    """
    返回 {物理页: 印刷folio}。
    - 优先从页标记 [p.N | 页码 F] 读取（新格式）
    - 回退到从块内容行提取（旧格式）
    - 对提取失败的页做线性插值
    """
    mapping: "dict[int, int]" = {}
    cur_phys: "int | None" = None
    cur_folio_from_marker: "int | None" = None
    buf: list = []

    for line in md_path.read_text(encoding="utf-8").splitlines():
        m = PAGE_RE.match(line.strip())
        if m:
            if cur_phys is not None:
                folio = cur_folio_from_marker if cur_folio_from_marker else _folio_from_block_lines(buf)
                if folio is not None:
                    mapping[cur_phys] = folio
            cur_phys = int(m.group(1))
            cur_folio_from_marker = int(m.group(2)) if m.group(2) else None
            buf = []
        else:
            buf.append(line.strip())

    if cur_phys is not None:
        folio = cur_folio_from_marker if cur_folio_from_marker else _folio_from_block_lines(buf)
        if folio is not None:
            mapping[cur_phys] = folio

    # 线性插值补全缺失页（包括范围外的前后各 5 页）
    if len(mapping) >= 2:
        sorted_phys = sorted(mapping)
        p_min, p_max = sorted_phys[0], sorted_phys[-1]
        # extend range outward by 5 to cover chapter-start title pages
        full_range = range(max(1, p_min - 5), p_max + 6)
        for p in full_range:
            if p not in mapping:
                before = max((pp for pp in sorted_phys if pp < p), default=None)
                after = min((pp for pp in sorted_phys if pp > p), default=None)
                if before is not None and after is not None:
                    span = after - before
                    fspan = mapping[after] - mapping[before]
                    mapping[p] = mapping[before] + round(fspan * (p - before) / span)
                elif before is not None:
                    mapping[p] = mapping[before] + (p - before)
                elif after is not None:
                    mapping[p] = mapping[after] - (after - p)

    return mapping


def main() -> None:
    if "--check" in sys.argv:
        _run_checks()
        return

    if len(sys.argv) < 2:
        print("用法: python3 bin/folio_map.py <source_md_path>")
        print("      python3 bin/folio_map.py --check")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"错误: {path} 不存在", file=sys.stderr)
        sys.exit(1)

    mapping = build_folio_map(path)
    print(f"{path.name}: {len(mapping)} 页映射")
    for phys, folio in sorted(mapping.items()):
        print(f"  phys {phys:4d} → 页码 {folio}")


def _run_checks() -> None:
    KNOWN = [
        ("neurology/dementia.md",    459, 1071),
        ("neurology/dementia.md",    456, 1068),
        ("endocrine/diabetes_t2.md",  99,  709),
    ]
    src_root = ROOT / "source" / "chapters"
    all_ok = True
    for rel, phys, expected in KNOWN:
        path = src_root / rel
        if not path.exists():
            print(f"  SKIP {rel} (not found)")
            continue
        m = build_folio_map(path)
        got = m.get(phys)
        status = "✓" if got == expected else f"✗ (expected {expected}, got {got})"
        print(f"  {rel}  phys {phys} → {got}  {status}")
        if got != expected:
            all_ok = False
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
