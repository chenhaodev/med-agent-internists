#!/usr/bin/env python3
"""
verify_claims.py — 原子声明 grep 核验
用法：
  python3 bin/verify_claims.py --chapter <path> --yaml <path> --answer "..."
  # 或从 stdin 读取答案：
  echo "answer" | python3 bin/verify_claims.py --chapter <path> --yaml <path>

选项：
  --mode patient|doctor   patient（默认）：严格 grep 所有声明
                          doctor：跳过含 (临床常用)/(教材未明示) 标签行的声明核验

输出：JSON（stdout）
  {claims: [...], has_fail: bool, fail_count: int, folio_range: [min, max]}
退出码：0 = 全 ✓/⚠（无需 reroll），1 = 有 ✗（触发 reroll）
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── Patterns ─────────────────────────────────────────────────────────────────

PAGE_RE = re.compile(r'第\s*(\d{1,4})\s*页')

NUM_UNITS = (
    r'(?:mg/(?:kg·d|d|L)|mg|g(?!/)|ml|L(?!\w)|mmHg|mmol/L|μmol/L|nmol/L'
    r'|IU(?:/(?:L|ml))?|%|天|周|月|年|岁|kcal|kJ)'
)
NUM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(' + NUM_UNITS + ')')

# Common pharmaceutical suffixes in Chinese drug names
DRUG_SUFFIX = (
    r'(?:沙坦|普利|洛尔|地平|他汀|格列|拉嗪|噻嗪|噻酮|替丁'
    r'|替尼|磺酰|甲酸|硝酸|磷酸|盐酸|碳酸|枸橼酸|葡萄糖酸'
    r'|素|胺|酚|嗪|酮|啶|坦)'
)
DRUG_RE = re.compile(r'[一-龥]{2,6}' + DRUG_SUFFIX)

# Words that match DRUG_RE but are clearly not drug names — skip to avoid false ✗
NON_DRUG_EXACT = frozenset({
    # 素-ending non-drug words
    '维生素', '激素', '因素', '元素', '色素', '毒素', '遗传因素',
    '危险因素', '风险因素', '诱发因素', '可控因素', '环境因素',
    '生物因素', '心理因素', '多种因素', '保护因素',
    # 胺-ending non-drug phrases (multi-char prefix that includes a verb/connector)
    '刺激多巴胺', '释放多巴胺',
})

# If the match ends with these sequences, it is almost certainly not a drug name
NON_DRUG_SUFFIXES = ('因素', '因子', '维生素', '抗生素')

# Non-drug single-char vocabulary that shouldn't appear as the sole prefix before a drug suffix
NON_DRUG_VERBS = frozenset('是的和或等也向对为从在与及使用服用注射口服通过减少增加促进刺激释放抑制')


def _is_likely_drug(word: str) -> bool:
    """Return False for common words that match DRUG_RE but are not drug names."""
    if word in NON_DRUG_EXACT:
        return False
    for sfx in NON_DRUG_SUFFIXES:
        if word.endswith(sfx):
            return False
    # If every char in the 2-char prefix is a known non-drug verb/connector, skip
    if len(word) >= 3:
        prefix = word[:-1]  # everything except the last char (the suffix char itself)
        if all(c in NON_DRUG_VERBS for c in prefix):
            return False
    return True


# ── Chapter parsing ───────────────────────────────────────────────────────────

def extract_folios(chapter_text: str) -> set:
    """Extract all printed folio numbers from ingested chapter markdown.

    Format after ingest.py: [p.N] marker → (blank) → header line → folio number line.
    """
    folios = set()
    lines = chapter_text.split('\n')
    for i, line in enumerate(lines):
        if re.match(r'\[p\.\d+\]', line.strip()):
            for j in range(i + 1, min(i + 8, len(lines))):
                m = re.match(r'^\s*(\d{1,4})\s*$', lines[j])
                if m:
                    folios.add(int(m.group(1)))
                    break
    return folios


# ── YAML parsing ──────────────────────────────────────────────────────────────

def collect_yaml_drugs(yaml_data: dict) -> set:
    """Collect drug name patterns from YAML key_points and must_warn fields."""
    drugs = set()
    for entry in yaml_data.get('entries', []):
        for text in entry.get('key_points', []) + entry.get('must_warn', []):
            for m in DRUG_RE.finditer(text):
                word = m.group(0)
                if _is_likely_drug(word):
                    drugs.add(word)
    return drugs


# ── Verifiers ─────────────────────────────────────────────────────────────────

def verify_page_claims(answer: str, folios: set) -> list:
    results = []
    seen = set()
    for m in PAGE_RE.finditer(answer):
        page_num = int(m.group(1))
        if page_num in seen:
            continue
        seen.add(page_num)

        if not folios:
            status = '⚠'
            evidence = '章节文件无法提取 folio，跳过核验'
        elif page_num in folios:
            status = '✓'
            evidence = f'folio {page_num} 在本章节范围内'
        else:
            lo, hi = min(folios), max(folios)
            status = '✗'
            evidence = f'folio {page_num} 不在本章节范围 {lo}–{hi}'

        results.append({'claim': m.group(0), 'kind': 'page',
                        'status': status, 'evidence': evidence})
    return results


def verify_numeric_claims(answer: str, chapter_text: str) -> list:
    results = []
    seen = set()
    for m in NUM_RE.finditer(answer):
        claim = m.group(0)
        if claim in seen:
            continue
        seen.add(claim)

        value_str, unit = m.group(1), m.group(2)
        val = float(value_str)

        # Exact match
        exact_re = re.compile(re.escape(value_str) + r'\s*' + re.escape(unit))
        if exact_re.search(chapter_text):
            status = '✓'
            evidence = f'章节正文精确命中 "{claim}"'
        else:
            # Approximate match ±5%
            approx_found = None
            unit_re = re.compile(r'(\d+(?:\.\d+)?)\s*' + re.escape(unit))
            for m2 in unit_re.finditer(chapter_text):
                found = float(m2.group(1))
                denom = max(abs(found), abs(val), 1e-9)
                if abs(found - val) / denom <= 0.05:
                    approx_found = m2.group(0)
                    break

            if approx_found:
                status = '⚠'
                evidence = f'章节仅找到近似值 "{approx_found}"'
            else:
                status = '✗'
                evidence = f'章节正文未找到 "{claim}"'

        results.append({'claim': claim, 'kind': 'numeric',
                        'status': status, 'evidence': evidence})
    return results


def verify_drug_claims(answer: str, chapter_text: str, yaml_drugs: set) -> list:
    results = []
    seen = set()
    for m in DRUG_RE.finditer(answer):
        drug = m.group(0)
        if drug in seen:
            continue
        seen.add(drug)
        if not _is_likely_drug(drug):
            continue

        if drug in yaml_drugs:
            status = '✓'
            evidence = 'YAML 知识库已收录'
        elif drug in chapter_text:
            status = '✓'
            evidence = '章节正文已收录'
        else:
            status = '✗'
            evidence = f'"{drug}" 在知识库和章节正文中均未找到'

        results.append({'claim': drug, 'kind': 'drug',
                        'status': status, 'evidence': evidence})
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description='Atomic claim verification')
    parser.add_argument('--chapter', help='Path to source/chapters/<sp>/<disease>.md')
    parser.add_argument('--yaml', help='Path to knowledge/<sp>/<disease>.yaml')
    parser.add_argument('--answer', help='Answer text (default: read stdin)')
    parser.add_argument('--mode', choices=['patient', 'doctor'], default='patient',
                        help='patient: strict grep; doctor: skip tagged clinical-common lines')
    args = parser.parse_args()

    answer = args.answer if args.answer else sys.stdin.read()

    # Load chapter text
    chapter_text = ''
    folios: set = set()
    if args.chapter:
        p = Path(args.chapter)
        if p.exists():
            chapter_text = p.read_text(encoding='utf-8')
            folios = extract_folios(chapter_text)

    # Load YAML
    yaml_drugs: set = set()
    if args.yaml:
        p = Path(args.yaml)
        if p.exists():
            import yaml
            with open(p, encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f)
            yaml_drugs = collect_yaml_drugs(yaml_data or {})

    # If no source files at all, skip silently
    if not chapter_text and not yaml_drugs:
        result = {
            'claims': [],
            'has_fail': False,
            'fail_count': 0,
            'folio_range': [],
            'warn': '未找到章节文件，跳过核验',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Doctor mode: strip lines tagged as clinical-common from numeric/drug verification
    # (page claims are still checked on all lines)
    DOCTOR_EXEMPT_TAGS = re.compile(r'\(临床常用\)|\(教材未明示\)|\(教材.*未.*明示\)')
    if args.mode == 'doctor':
        answer_for_numeric = '\n'.join(
            line for line in answer.split('\n')
            if not DOCTOR_EXEMPT_TAGS.search(line)
        )
    else:
        answer_for_numeric = answer

    # Run verifications
    claims = []
    claims.extend(verify_page_claims(answer, folios))
    claims.extend(verify_numeric_claims(answer_for_numeric, chapter_text))
    claims.extend(verify_drug_claims(answer_for_numeric, chapter_text, yaml_drugs))

    has_fail = any(c['status'] == '✗' for c in claims)
    fail_count = sum(1 for c in claims if c['status'] == '✗')

    result = {
        'claims': claims,
        'has_fail': has_fail,
        'fail_count': fail_count,
        'folio_range': sorted(folios)[:2] + [sorted(folios)[-1]] if folios else [],
    }
    # Compact folio_range: just [min, max]
    result['folio_range'] = [min(folios), max(folios)] if folios else []

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if has_fail else 0


if __name__ == '__main__':
    sys.exit(main())
