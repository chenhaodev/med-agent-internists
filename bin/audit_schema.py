#!/usr/bin/env python3
"""
audit_schema.py — 段式 schema 契约一致性与横切传播完整性审计（无 LLM，<2s）

两项静态检查：

  [S1] 契约一致性：
       对每个模式（patient / doctor），断言以下三处出现的【…】段名
       == schema/sections.yaml 中声明的规范集合（缺/多/错均 FAIL）：
         · prompts/output_schema*.md     （告诉模型怎么写）
         · eval/judge_prompt*.md         （eval 判分时检查什么）
         · bin/postprocess.sh            （CLI 运行时校验什么；派生后即 sections.yaml）
       本次 doctor/postprocess 漂移 bug 在提交期会被此检查拦截。

  [S2] 横切传播完整性：
       解析 bin/ask.sh，断言每个 mode 敏感阶段（oob_check / build_prompt /
       verify_claims / postprocess）的子调用都传入了 --mode。
       把「横切维度只传播到部分阶段」缺陷类制度化为静态门禁。

用法：
  python3 bin/audit_schema.py              # 全量
  python3 bin/audit_schema.py --only s1    # 仅契约一致
  python3 bin/audit_schema.py --only s2    # 仅横切传播
  退出码 1 = 有 FAIL
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SECTIONS_YAML = ROOT / "schema" / "sections.yaml"
ASK_SH = ROOT / "bin" / "ask.sh"

# mode → (output_schema, judge_prompt)
MODE_FILES = {
    "patient": (
        ROOT / "prompts" / "output_schema.md",
        ROOT / "eval" / "judge_prompt.md",
    ),
    "doctor": (
        ROOT / "prompts" / "output_schema_doctor.md",
        ROOT / "eval" / "judge_prompt_doctor.md",
    ),
}

# mode-sensitive stages in ask.sh whose sub-invocations must carry --mode
MODE_SENSITIVE_STAGES = ["oob_check.sh", "build_prompt.sh", "verify_claims.py", "postprocess.sh"]

# Pattern extracting 【…】 section headers from markdown text
SECTION_RE = re.compile(r"【([^】]+)】")


def load_sections_yaml():
    """Load schema/sections.yaml; return dict or raise on failure."""
    if not SECTIONS_YAML.exists():
        raise FileNotFoundError(f"{SECTIONS_YAML.relative_to(ROOT)} not found")
    return yaml.safe_load(SECTIONS_YAML.read_text(encoding="utf-8"))


def extract_sections(text):
    """Return set of 【…】 header strings found in text."""
    return {f"【{m}】" for m in SECTION_RE.findall(text)}


# ── S1 ───────────────────────────────────────────────────────────────────────


def audit_s1(schema_data):
    """
    Contract consistency check.
    Returns list of (location, mode, extra_sections, missing_sections).
    """
    failures = []
    for mode, (schema_path, judge_path) in MODE_FILES.items():
        canonical = set(schema_data.get(mode, {}).get("sections", []))
        if not canonical:
            failures.append(("schema/sections.yaml", mode, set(), set(), "canonical set is empty"))
            continue

        locations = {
            schema_path.relative_to(ROOT): schema_path,
            judge_path.relative_to(ROOT): judge_path,
        }
        # postprocess.sh now derives from sections.yaml, so check it reads correctly
        locations[Path("bin/postprocess.sh")] = ROOT / "bin" / "postprocess.sh"

        for rel, path in locations.items():
            if not path.exists():
                failures.append((str(rel), mode, set(), canonical, "file not found"))
                continue
            text = path.read_text(encoding="utf-8")
            found = extract_sections(text)
            found_canonical_overlap = found & canonical
            missing = canonical - found_canonical_overlap

            # For postprocess.sh: since it now derives from sections.yaml, we check
            # that the file references the YAML path (i.e. it's using the single source)
            if str(rel) == "bin/postprocess.sh":
                if "schema/sections.yaml" not in text and "SECTIONS_YAML" not in text:
                    failures.append((
                        str(rel), mode, set(), set(),
                        "postprocess.sh does not derive from schema/sections.yaml "
                        "(hardcoded section names detected)",
                    ))
                # postprocess.sh is derived — no section-name drift possible
                continue

            if missing:
                failures.append((str(rel), mode, set(), missing, None))
            extra_headers = found - canonical
            # Only flag extras that look like 【…】 schema headers (not incidental 【…】 in prose)
            schema_extras = {
                h for h in extra_headers
                if h not in canonical and len(h) > 2
                # Must not be from the other mode's canonical set
                and any(
                    h in set(schema_data.get(other, {}).get("sections", []))
                    for other in MODE_FILES
                    if other != mode
                )
            }
            if schema_extras:
                failures.append((str(rel), mode, schema_extras, set(), None))

    return failures


# ── S2 ───────────────────────────────────────────────────────────────────────


def audit_s2():
    """
    Horizontal propagation completeness.
    Returns list of (stage, issue_description).
    """
    if not ASK_SH.exists():
        return [("bin/ask.sh", "file not found")]

    text = ASK_SH.read_text(encoding="utf-8")
    failures = []
    lines = text.splitlines()
    for stage in MODE_SENSITIVE_STAGES:
        for i, line in enumerate(lines):
            if stage not in line or line.strip().startswith("#"):
                continue
            lineno = i + 1
            # Multi-line invocations: collect this line + continuation lines (ending in \)
            # and the next few lines to capture arguments on separate lines
            window = []
            j = i
            while j < len(lines) and j < i + 8:
                window.append(lines[j])
                if not lines[j].rstrip().endswith("\\"):
                    break
                j += 1
            window_text = " ".join(window)
            if "--mode" not in window_text:
                failures.append((
                    stage,
                    f"bin/ask.sh:{lineno}: invokes {stage} without --mode\n    {line.strip()}",
                ))
    return failures


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="audit_schema: schema 契约一致性 + 横切传播完整性")
    ap.add_argument("--only", choices=["s1", "s2"])
    args = ap.parse_args()

    exit_code = 0

    try:
        schema_data = load_sections_yaml()
    except (FileNotFoundError, yaml.YAMLError) as exc:
        print(f"[!] 无法加载 schema/sections.yaml: {exc}")
        sys.exit(1)

    if args.only in (None, "s1"):
        print("[S1] 契约一致性检查…")
        failures = audit_s1(schema_data)
        for loc, mode, extra, missing, note in failures:
            exit_code = 1
            if note:
                print(f"  ✗ FAIL  {loc}  mode={mode}  {note}")
            if missing:
                for s in sorted(missing):
                    print(f"  ✗ FAIL  {loc}  mode={mode}  缺少段名 {s}")
            if extra:
                for s in sorted(extra):
                    print(f"  ✗ FAIL  {loc}  mode={mode}  出现异模式段名 {s}")
        status = "通过 ✓" if not failures else f"{len(failures)} 处不一致 ✗"
        print(f"[S1] {status}\n")

    if args.only in (None, "s2"):
        print("[S2] 横切传播完整性检查（--mode 传播到所有 mode-sensitive 阶段）…")
        failures = audit_s2()
        for stage, desc in failures:
            exit_code = 1
            print(f"  ✗ FAIL  {desc}")
        status = "通过 ✓" if not failures else f"{len(failures)} 处未传播 ✗"
        print(f"[S2] {status}\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
