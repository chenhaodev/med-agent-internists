#!/usr/bin/env python3
"""审计 knowledge YAML 的 source_page（印刷页码）是否与 source/chapters 内容一致。

方法：
  1. 从 source md 建立 {物理页: folio} 映射（via folio_map）
  2. 对每条 entry，从 title+key_points 抽取锚点词，在各页文本中匹配
  3. 比较 claimed folio 是否与内容最匹配页的 folio 一致
  4. 检查 COVERAGE_GAP：源文出现的患者关键词在 YAML 中有无对应条目

用法：
  python3 bin/audit_pages.py                                # 审计默认列表
  python3 bin/audit_pages.py knowledge/neurology/dementia.yaml   # 指定文件
  python3 bin/audit_pages.py --specialty neurology          # 指定专科
"""
import sys, re, yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KN = ROOT / "knowledge"
SRC = ROOT / "source" / "chapters"

sys.path.insert(0, str(ROOT / "bin"))
try:
    from folio_map import build_folio_map
    _HAS_FOLIO_MAP = True
except ImportError:
    _HAS_FOLIO_MAP = False

# 匹配旧格式 [p.N] 和新格式 [p.N | 页码 F]
PAGE_RE = re.compile(r"^\[p\.(\d+)(?:\s*\|\s*页码\s*(\d+))?\]\s*$")

CJK = r"一-鿿"
TOKEN_RE = re.compile(rf"[{CJK}]{{2,8}}|[A-Za-z][A-Za-z0-9\-]{{2,}}|\d+(?:\.\d+)?")
STOP = set("患者疾病治疗症状建议医生就医注意可能没有需要进行通过包括以及如果出现这是什么日常常见误区依据管理控制增加降低正常异常情况问题方法检查评估".split())

# 患者向高价值关键词（检查 COVERAGE_GAP 用）
PATIENT_KEYWORDS = ["运动", "锻炼", "体力活动", "饮食", "生活方式", "戒烟", "戒酒", "康复", "居家护理", "家庭护理"]


def tokens(text):
    out = set()
    for m in TOKEN_RE.findall(text):
        if m not in STOP:
            out.add(m)
    return out


def load_pages(md_path):
    """返回 [(phys_page, folio_or_None, text_block)]。"""
    pages = []
    cur_phys, cur_folio, buf = None, None, []
    for line in md_path.read_text(encoding="utf-8").splitlines():
        m = PAGE_RE.match(line.strip())
        if m:
            if cur_phys is not None:
                pages.append((cur_phys, cur_folio, "\n".join(buf)))
            cur_phys = int(m.group(1))
            cur_folio = int(m.group(2)) if m.group(2) else None
            buf = []
        else:
            buf.append(line)
    if cur_phys is not None:
        pages.append((cur_phys, cur_folio, "\n".join(buf)))
    return pages


def best_pages_by_anchor(anchor, pages_with_folio):
    """pages_with_folio: [(phys, folio, text)]. Returns sorted [(hits, phys, folio)]."""
    scored = []
    for phys, folio, txt in pages_with_folio:
        hits = sum(1 for a in anchor if a in txt)
        scored.append((hits, phys, folio))
    scored.sort(reverse=True)
    return scored


def audit_disease(spec, disease):
    yaml_path = KN / spec / f"{disease}.yaml"
    md_path = SRC / spec / f"{disease}.md"
    if not yaml_path.exists() or not md_path.exists():
        return

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    raw_pages = load_pages(md_path)

    # Build folio map for this chapter
    folio_map: "dict[int, int]" = {}
    folio_to_phys: "dict[int, int]" = {}
    if _HAS_FOLIO_MAP:
        folio_map = build_folio_map(md_path)
        folio_to_phys = {v: k for k, v in folio_map.items()}

    # Enrich pages with folio if missing (from folio_map)
    pages_enriched = []
    for phys, folio, txt in raw_pages:
        if folio is None and phys in folio_map:
            folio = folio_map[phys]
        pages_enriched.append((phys, folio, txt))

    # Build lookup dicts
    phys_nums = [p for p, _, _ in pages_enriched]
    folio_nums = [f for _, f, _ in pages_enriched if f]
    phys_to_text = {p: t for p, _, t in pages_enriched}
    folio_to_text = {f: t for _, f, t in pages_enriched if f}

    pmin_folio = min(folio_nums) if folio_nums else min(phys_nums)
    pmax_folio = max(folio_nums) if folio_nums else max(phys_nums)
    print(f"\n===== {spec}/{disease}  (folio {pmin_folio}-{pmax_folio}, {len(pages_enriched)}页) =====")

    # ── 1. Per-entry folio check ─────────────────────────────────
    for e in data.get("entries", []):
        claimed = e.get("source_page")
        anchor = tokens(e.get("title", ""))
        for kp in e.get("key_points", []):
            anchor |= tokens(kp)
        anchor = {a for a in anchor if len(a) >= 2}

        scored = best_pages_by_anchor(anchor, pages_enriched)
        best_hits, best_phys, best_folio = scored[0] if scored else (0, None, None)

        # claimed may be folio (after migration) or physical (before migration)
        # resolve to text block
        if claimed in folio_to_text:
            claimed_text = folio_to_text[claimed]
            claimed_is_folio = True
        elif claimed in phys_to_text:
            claimed_text = phys_to_text[claimed]
            claimed_is_folio = False
        else:
            claimed_text = ""
            claimed_is_folio = True  # assume post-migration

        claimed_hits = sum(1 for a in anchor if a in claimed_text)
        # also check folio ±1
        neigh_hits = claimed_hits
        for delta in (-1, 1):
            nb_folio = claimed + delta if claimed_is_folio else None
            nb_phys = claimed + delta if not claimed_is_folio else None
            t = folio_to_text.get(nb_folio) if nb_folio else phys_to_text.get(nb_phys)
            if t:
                neigh_hits = max(neigh_hits, sum(1 for a in anchor if a in t))

        flag = ""
        if claimed_is_folio and claimed not in folio_to_text and claimed not in folio_nums:
            flag = "‼ folio 不在本章范围"
        elif not claimed_is_folio and claimed not in phys_nums:
            flag = "‼ 物理页不在本章范围"
        elif max(claimed_hits, neigh_hits) == 0:
            flag = "‼ claimed页±1 零命中"
        elif best_hits >= claimed_hits + 3 and best_folio and abs(best_folio - claimed) > 2:
            flag = f"⚠ best=folio {best_folio}(命中{best_hits}) 远高于 claimed"

        top3 = " ".join(
            f"folio {f}:{h}" if f else f"phys {p}:{h}"
            for h, p, f in scored[:3]
        )
        eid = e.get("id", "?")
        if flag:
            print(f"  [{eid:24}] claimed folio {claimed}(命中{claimed_hits},邻{neigh_hits}) | top: {top3}  {flag}")
        else:
            print(f"  [{eid:24}] folio {claimed}(命中{claimed_hits}) ✓ | top: {top3}")

    # ── 2. COVERAGE_GAP check ─────────────────────────────────────
    all_text = " ".join(t for _, _, t in pages_enriched)
    all_kp_text = " ".join(
        kp for e in data.get("entries", []) for kp in e.get("key_points", [])
    ) + " ".join(
        e.get("title", "") for e in data.get("entries", [])
    )

    gaps = []
    for kw in PATIENT_KEYWORDS:
        if kw in all_text and kw not in all_kp_text:
            gaps.append(kw)
    if gaps:
        print(f"  COVERAGE_GAP: 源文有但未抽取成条目的患者关键词 → {gaps}")


DEFAULT_TARGETS = [
    ("bone_mineral", ["bone_physiology", "metabolic_bone", "mineral_disorders", "osteoporosis"]),
    ("cardiology", ["arrhythmia", "cad", "heart_failure", "hypertension"]),
    ("digestive",  ["gi", "ibd", "liver"]),
    ("endocrine",  ["diabetes_t2", "dyslipidemia", "gout", "obesity", "thyroid"]),
    ("geriatrics", ["elderly_care"]),
    ("hematology", ["anemia"]),
    ("infectious", ["general"]),
    ("mens_health", ["mens_health"]),
    ("neurology",  ["dementia", "epilepsy", "headache_pain", "movement_disorders", "stroke"]),
    ("oncology",   ["breast_cancer", "gi_cancer", "hematologic_cancer", "lung_cancer"]),
    ("palliative", ["palliative_care"]),
    ("perioperative", ["periop_management"]),
    ("renal",      ["ckd", "nephritis"]),
    ("respiratory", ["asthma", "copd", "pneumonia"]),
    ("rheumatology", ["osteoporosis", "ra", "sle"]),
    ("substance_use", ["alcohol_drugs"]),
    ("womens_health", ["womens_health"]),
]


def main():
    # Parse args
    dry = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    specialty_filter = None
    if "--specialty" in sys.argv:
        idx = sys.argv.index("--specialty")
        if idx + 1 < len(sys.argv):
            specialty_filter = sys.argv[idx + 1]

    if args:
        # Positional yaml paths
        for path_str in args:
            p = Path(path_str)
            if not p.exists():
                print(f"SKIP {p}: not found")
                continue
            try:
                spec = p.parent.name
                disease = p.stem
                audit_disease(spec, disease)
            except Exception as e:
                print(f"ERROR {p}: {e}")
    elif specialty_filter:
        for spec, diseases in DEFAULT_TARGETS:
            if spec == specialty_filter:
                for d in diseases:
                    audit_disease(spec, d)
    else:
        for spec, diseases in DEFAULT_TARGETS:
            for d in diseases:
                audit_disease(spec, d)


if __name__ == "__main__":
    main()
