#!/usr/bin/env python3
"""
ingest.py — PDF → 章节 Markdown（manifest-driven，依据 knowledge/chapters.yaml）

用法：
  python3 bin/ingest.py --specialty cardiology
  python3 bin/ingest.py --chapter 16
  python3 bin/ingest.py --all
  python3 bin/ingest.py --list

输出：source/chapters/{specialty}/{slug}.md
      每段文字前标注 [p.{页码}]

YAML 字段说明：
  chapter_no      章节号（1-126）
  pdf_page_start  PDF 物理页起始（1-based）
  pdf_page_end    PDF 物理页结束（含）
  volume          上/下（决定读哪个 PDF）
  specialty       专科名（目录路径）
  slug            病种 slug（文件名）
  sub_slugs       可选，同一章生成多个 slug 文件（内容相同，extract.py 再细分）
  patient_facing  false = 基础/方法章节，可跳过提取
"""

import argparse
import re
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).parent))
    from folio_map import extract_folio_from_page_text as _extract_folio
except ImportError:
    _extract_folio = None

ROOT_DIR = Path(__file__).parent.parent
CHAPTERS_YAML = ROOT_DIR / "knowledge" / "chapters.yaml"

PDF_UPPER = ROOT_DIR / "pdfs" / "西氏内科学精要-上卷.pdf"
PDF_LOWER = ROOT_DIR / "pdfs" / "西氏内科学精要-下卷.pdf"

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


def load_chapters() -> list[dict]:
    if not CHAPTERS_YAML.exists():
        print(f"错误：{CHAPTERS_YAML} 不存在。先运行 bin/build_chapter_map.py", file=sys.stderr)
        sys.exit(1)
    with open(CHAPTERS_YAML, encoding="utf-8") as f:
        chapters = yaml.safe_load(f)
    return [c for c in chapters if c.get("specialty") and c.get("slug")]


def get_pdf_path(volume: str) -> Path:
    path = PDF_UPPER if volume == "上" else PDF_LOWER
    if not path.exists():
        print(f"错误：PDF 不存在：{path}", file=sys.stderr)
        print("请将 PDF 放入 pdfs/ 目录（已 git-ignored）", file=sys.stderr)
        sys.exit(1)
    return path


def extract_pages(pdf_path: Path, start: int, end: int) -> list[tuple[int, str, "int | None"]]:
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    result = []
    for pn in range(start - 1, min(end, total)):
        text = doc[pn].get_text("text")
        if text.strip():
            folio = _extract_folio(text) if _extract_folio else None
            result.append((pn + 1, text, folio))
    doc.close()
    return result


def pages_to_markdown(pages: list[tuple[int, str, "int | None"]], title: str) -> str:
    lines = [f"# {title}\n"]
    for page_num, text, folio in pages:
        if folio is not None:
            lines.append(f"\n[p.{page_num} | 页码 {folio}]\n")
        else:
            lines.append(f"\n[p.{page_num}]\n")
        cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
        lines.append(cleaned)
    return "\n".join(lines)


def ingest_chapter(ch: dict) -> None:
    specialty = ch["specialty"]
    slug = ch["slug"]
    title = ch.get("title", slug)
    volume = ch["volume"]
    start = ch["pdf_page_start"]
    end = ch["pdf_page_end"]

    pdf_path = get_pdf_path(volume)
    out_dir = ROOT_DIR / "source" / "chapters" / specialty
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [ch{ch['chapter_no']} {specialty}/{slug}] 提取 p.{start}-{end} ({volume}卷)…")
    pages = extract_pages(pdf_path, start, end)
    if not pages:
        print(f"    警告：未提取到文本，跳过")
        return

    md = pages_to_markdown(pages, title)
    slugs = [slug] + list(ch.get("sub_slugs") or [])
    for s in slugs:
        out_file = out_dir / f"{s}.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"    → {out_file}  ({len(pages)} 页, {len(md)} 字符)")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF → 章节 Markdown（manifest-driven）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--specialty", help="处理指定专科的所有章节")
    group.add_argument("--chapter", type=int, help="处理指定章节号")
    group.add_argument("--all", action="store_true", help="处理所有已标注章节")
    group.add_argument("--list", action="store_true", help="列出所有已标注章节")
    args = parser.parse_args()

    chapters = load_chapters()
    if not chapters:
        print("chapters.yaml 中还没有填写 specialty/slug 的记录。", file=sys.stderr)
        print("请先为每章填写 specialty 和 slug 字段，再运行 ingest。", file=sys.stderr)
        sys.exit(1)

    if args.list:
        for c in sorted(chapters, key=lambda x: x["chapter_no"]):
            subs = f" [sub_slugs: {c['sub_slugs']}]" if c.get("sub_slugs") else ""
            pf = "" if c.get("patient_facing", True) else " [非患者向]"
            print(f"  ch{c['chapter_no']:3d} {c['specialty']:18s}/{c['slug']}{subs}{pf}")
        return

    if args.chapter:
        targets = [c for c in chapters if c["chapter_no"] == args.chapter]
        if not targets:
            print(f"错误：章节 {args.chapter} 未在 chapters.yaml 中找到（或 specialty/slug 未填写）",
                  file=sys.stderr)
            sys.exit(1)
    elif args.specialty:
        targets = [c for c in chapters if c["specialty"] == args.specialty]
        if not targets:
            print(f"错误：专科 '{args.specialty}' 无已标注章节", file=sys.stderr)
            sys.exit(1)
    else:
        targets = chapters  # --all

    print(f"开始 ingest：{len(targets)} 个章节")
    for ch in sorted(targets, key=lambda x: x["chapter_no"]):
        ingest_chapter(ch)

    print("\n完成。请检查 source/chapters/ 目录。")


if __name__ == "__main__":
    main()
