#!/usr/bin/env python3
"""
build_chapter_map.py — 从 PDF 自动构建章节→页码映射表

上卷：TOC 书签完好，直接读取。
下卷：书签全坏（均指向 p.20/21），改为全文扫描 `第N章` 标题行。

用法:
  python3 bin/build_chapter_map.py
  python3 bin/build_chapter_map.py --upper-vol pdfs/西氏内科学精要-上卷.pdf \\
      --lower-vol pdfs/西氏内科学精要-下卷.pdf --output knowledge/chapters.yaml
"""

import argparse
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent

try:
    import fitz
except ImportError:
    print("错误：需要 pymupdf。运行 pip install pymupdf", file=sys.stderr)
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("错误：需要 pyyaml。运行 pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ZH_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}

# No space required between 章 and title (TOC entries have no separator)
CH_PAT = re.compile(r"第\s*(\d+)\s*章[\s　]*(.*)")
PART_PAT = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*部分[\s　]*(.*)")


def _zh_or_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        return ZH_NUM.get(raw.strip(), 0)


def _build_part_map(toc: list) -> list[tuple[int, int]]:
    """从 TOC 提取 [(part_no, start_page), ...] 按页升序。"""
    parts = []
    for _, title, page in toc:
        m = PART_PAT.match(title.strip())
        if m:
            pno = _zh_or_int(m.group(1))
            if pno:
                parts.append((pno, page))
    return sorted(parts, key=lambda x: x[1])


def _find_part(page: int, part_map: list[tuple[int, int]]) -> int:
    current = 0
    for pno, pstart in part_map:
        if page >= pstart:
            current = pno
        else:
            break
    return current


def extract_upper_vol(pdf_path: Path) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()
    total = len(doc)
    doc.close()

    part_map = _build_part_map(toc)

    raw = []
    for _, title, page in toc:
        m = CH_PAT.match(title.strip())
        if m:
            ch_no = int(m.group(1))
            # Chapters 53-126 appear in the upper-vol TOC as broken entries (all pointing
            # to p.20 or p.21 — the TOC pages). Skip them; lower vol scan handles ch53-126.
            if page <= 21:
                continue
            raw.append({"chapter_no": ch_no, "title": m.group(2).strip(), "pdf_page_start": page})

    raw.sort(key=lambda x: x["chapter_no"])
    for i, ch in enumerate(raw):
        ch["pdf_page_end"] = raw[i + 1]["pdf_page_start"] - 1 if i + 1 < len(raw) else total
        ch["volume"] = "上"
        ch["part"] = _find_part(ch["pdf_page_start"], part_map)
        ch["specialty"] = ""
        ch["slug"] = ""
        ch["patient_facing"] = True
    return raw


def extract_lower_vol(pdf_path: Path) -> list[dict]:
    """全文扫描每页，检测 `第N章` 首次出现作为章节起始页。
    跳过含 \\x08 的行（这些是 TOC 印刷目录条目，带书页码）。
    """
    doc = fitz.open(str(pdf_path))
    total = len(doc)

    part_starts: list[tuple[int, int]] = []  # (part_no, 1-based page)
    ch_starts: list[tuple[int, int, str]] = []  # (ch_no, 1-based page, title)
    seen_ch: set[int] = set()

    for pn in range(total):
        text = doc[pn].get_text("text")
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        for idx, line in enumerate(lines[:40]):
            # 跳过 TOC 印刷目录条目（含 \x08 回退符 或 \x07 BEL 符）
            if "\x08" in line or "\x07" in line:
                continue

            # 部分标题
            pm = PART_PAT.match(line)
            if pm:
                pno = _zh_or_int(pm.group(1))
                if pno and pno >= 9:
                    if not part_starts or part_starts[-1][0] != pno:
                        part_starts.append((pno, pn + 1))

            # 章标题
            cm = CH_PAT.match(line)
            if cm:
                ch_no = int(cm.group(1))
                if 53 <= ch_no <= 130 and ch_no not in seen_ch:
                    title = cm.group(2).strip()
                    # 标题可能在下一行（如 `第54 章` / `肿瘤流行病学`）
                    if not title and idx + 1 < len(lines):
                        title = lines[idx + 1].strip()
                    seen_ch.add(ch_no)
                    ch_starts.append((ch_no, pn + 1, title))
                    break  # 一页只取第一个章标题

    doc.close()

    ch_starts.sort(key=lambda x: x[0])

    # Validate monotonicity: if a chapter's start page < previous chapter's,
    # it was detected on an overview/preview page and needs manual review.
    validated: list[tuple[int, int, str]] = []
    skipped: list[tuple[int, int, str]] = []
    for entry in ch_starts:
        if validated and entry[1] <= validated[-1][1]:
            skipped.append(entry)
        else:
            validated.append(entry)
    if skipped:
        print(f"  ⚠ 以下章节起始页不单调，已标记需人工校验：", file=sys.stderr)
        for ch_no, page, title in skipped:
            print(f"    ch{ch_no} detected at p.{page} ('{title[:30]}')", file=sys.stderr)
    ch_starts = validated

    chapters = []
    for i, (ch_no, start, title) in enumerate(ch_starts):
        end = ch_starts[i + 1][1] - 1 if i + 1 < len(ch_starts) else total
        chapters.append({
            "chapter_no": ch_no,
            "title": title,
            "part": _find_part(start, part_starts),
            "volume": "下",
            "pdf_page_start": start,
            "pdf_page_end": end,
            "specialty": "",
            "slug": "",
            "patient_facing": True,
        })
    return chapters


def write_yaml(chapters: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 手工可读格式：每条记录用 block style，字段顺序固定
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(
            chapters,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upper-vol", default="pdfs/西氏内科学精要-上卷.pdf")
    parser.add_argument("--lower-vol", default="pdfs/西氏内科学精要-下卷.pdf")
    parser.add_argument("--output", default="knowledge/chapters.yaml")
    args = parser.parse_args()

    def resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else ROOT_DIR / path

    upper_path = resolve(args.upper_vol)
    lower_path = resolve(args.lower_vol)
    out_path = resolve(args.output)

    if not upper_path.exists():
        print(f"错误：上卷 PDF 不存在：{upper_path}", file=sys.stderr)
        sys.exit(1)
    if not lower_path.exists():
        print(f"错误：下卷 PDF 不存在：{lower_path}", file=sys.stderr)
        sys.exit(1)

    print("提取上卷章节（via TOC）…")
    upper = extract_upper_vol(upper_path)
    print(f"  → {len(upper)} 章")

    print("扫描下卷章节（full-text scan）…")
    lower = extract_lower_vol(lower_path)
    print(f"  → {len(lower)} 章")

    all_chs = sorted(upper + lower, key=lambda x: x["chapter_no"])
    print(f"合计：{len(all_chs)} 章")

    write_yaml(all_chs, out_path)
    print(f"\n已写入：{out_path}")
    print("\n后续步骤：")
    print("  1. 人工抽查下卷 ~10 章的页码范围（对照印刷版目录）")
    print("  2. 为每条记录手工填写 specialty / slug 字段")
    print("  3. 将纯方法/基础章节（分子医学、实验室诊断等）设 patient_facing: false")


if __name__ == "__main__":
    main()
