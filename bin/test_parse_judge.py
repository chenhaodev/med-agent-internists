#!/usr/bin/env python3
"""test_parse_judge.py — parse_judge.py 的零依赖自测（python3 bin/test_parse_judge.py）。

聚焦回归：ILD_BREATHLESS_01 类「严格 JSON 合法但某维 score 取不到 → 静默填 0、
ok=True、不重跑」的假失败。核心不变量：
  - 提取失败（null/占位符/同义键失配/嵌套异常）→ ok=False（触发重跑），绝不静默吐 0。
  - 判官**真实**给 0（"score":0）→ ok=True，分数=0。
退出码 0=全过，1=有失败。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_judge import parse  # noqa: E402

CASES = [
    # name, raw, expect_ok, expected_scores(None=不校验该维)
    ("well-formed",
     '{"coverage":{"score":8},"accuracy":{"score":9},"safety":{"score":10},"grounding":{"score":7},"flags":["x"]}',
     True, (8, 9, 10, 7)),
    ("genuine zero accuracy (must stay 0, ok)",
     '{"coverage":{"score":6},"accuracy":{"score":0},"safety":{"score":10},"grounding":{"score":9}}',
     True, (6, 0, 10, 9)),
    ("null score → not silent 0, must rerun (ok=False)",
     '{"coverage":{"score":6},"accuracy":{"score":null},"safety":{"score":10},"grounding":{"score":10}}',
     False, None),
    ("echoed placeholder 分数 (invalid JSON) → ok=False",
     '{"coverage":{"score":6},"accuracy":{"score":分数},"safety":{"score":10},"grounding":{"score":10}}',
     False, None),
    ("synonym key 评分 → recovered, no rerun",
     '{"coverage":{"score":6},"accuracy":{"评分":9},"safety":{"score":10},"grounding":{"score":10}}',
     True, (6, 9, 10, 10)),
    ("score as string '10' → recovered",
     '{"coverage":{"score":6},"accuracy":"10","safety":{"score":10},"grounding":{"score":10}}',
     True, (6, 10, 10, 10)),
    ("unescaped quote in rationale, score before → regex recovers",
     '{"coverage":{"score":6,"r":"x"},"accuracy":{"score":9,"r":"病人说"好了"实误导"},"safety":{"score":10},"grounding":{"score":8}}',
     True, (6, 9, 10, 8)),
    ("flat ints",
     '{"coverage":7,"accuracy":8,"safety":9,"grounding":10}',
     True, (7, 8, 9, 10)),
    ("total garbage → ok=False",
     'the judge had an error and returned prose only',
     False, None),
    ("missing whole dimension → ok=False",
     '{"coverage":{"score":6},"safety":{"score":10},"grounding":{"score":10}}',
     False, None),
]


def main():
    passed = failed = 0
    for name, raw, exp_ok, exp_scores in CASES:
        res, ok = parse(raw)
        errs = []
        if ok != exp_ok:
            errs.append(f"ok={ok} expected {exp_ok}")
        if exp_scores is not None:
            got = tuple(res[d] for d in ("coverage", "accuracy", "safety", "grounding"))
            if got != exp_scores:
                errs.append(f"scores={got} expected {exp_scores}")
        if errs:
            failed += 1
            print(f"  ✗ {name}: {'; '.join(errs)}")
        else:
            passed += 1
            print(f"  ✓ {name}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
