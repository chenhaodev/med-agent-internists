#!/usr/bin/env python3
"""
audit_routing.py — gold.yaml 路由与标签契约审计（无 LLM 调用，<5s 跑完 147 题）

两类确定性检查，覆盖最易静默失败的两个环节：

  [ROUTE] 每题过 router.sh，断言输出 ∩ expected_domain ≠ ∅
          → 捕获"关键词漏配 → 路由到 *:general → YAML 静默跳过"类缺陷
            （如 肺栓塞 误入 respiratory:general 而非 hematology:thrombosis）

  [TAG]   doctor_must_have_tags 每个标签须能在该题命中 domain 的 YAML
          key_points 中作为子串找到 → 捕获"标签是元描述而非模型会写的具体词"
            （如 药物选择依据 / 具体阈值 这类抽象短语，模型永远不会逐字输出）

  [MUSTWARN] gold 每条 must_warn 须在其 expected_domain 的层1（病种 YAML）+
          层3（安全底线 YAML）语料中找到接地（3-字 CJK 片段或字母数字 token 重叠）
          → shift-left 捕获"gold 强求书外警告"这一会诱发幻觉的失败类
            （如 NEURO_DEM 的「甲状腺功能减退」、GERI_POLY「不可自行停药」、HIV 接触血液防护）。
          WARN 而非 ERROR：候选 B2（书中有→按页回填 YAML）或 B3（书外→doctor 放宽 /
          patient 落入安全底线），最终须对 PDF 人工裁定。

用法：
  python3 bin/audit_routing.py                    # 全量
  python3 bin/audit_routing.py --only route       # 仅路由
  python3 bin/audit_routing.py --only tag          # 仅标签
  python3 bin/audit_routing.py --only mustwarn     # 仅 must_warn 接地
  退出码 1 = 有 FAIL（可挂 CI / pre-commit）；MUSTWARN 仅 WARN，不改退出码
"""

import argparse
import re
import subprocess
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_GOLD = ROOT / "eval" / "gold.yaml"
KN = ROOT / "knowledge"
ROUTER = ROOT / "bin" / "router.sh"

# doctor 模板固定段落/证据分级词，非知识词，豁免接地检查
TEMPLATE_TOKENS = {
    "证据等级", "高级别证据", "中级别证据", "低级别证据", "临床常用", "指南推荐",
}


def load_gold():
    with open(EVAL_GOLD) as f:
        return yaml.safe_load(f)["questions"]


def route(question: str) -> list[str]:
    """跑 router.sh，返回 specialty:disease 标签列表。"""
    out = subprocess.run(
        ["bash", str(ROUTER), question],
        capture_output=True, text=True, timeout=30,
    )
    return out.stdout.strip().split()


def yaml_text_for(domain: str) -> str:
    """读 knowledge/<specialty>/<disease>.yaml 的全文（用于子串匹配）。"""
    try:
        specialty, disease = domain.split(":", 1)
    except ValueError:
        return ""
    path = KN / specialty / f"{disease}.yaml"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def warn_corpus_for(domain: str) -> str:
    """must_warn 接地语料：病种 YAML（层1）+ 同病种安全底线 YAML（层3，若有）。
    层3 是 patient 专属、可审计的书外宽免——已落入安全底线的警告不应再被判为「书外」。"""
    try:
        specialty, disease = domain.split(":", 1)
    except ValueError:
        return ""
    text = yaml_text_for(domain)
    floor = KN / specialty / "safety_floor" / f"{disease}.yaml"
    if floor.exists():
        text += floor.read_text(encoding="utf-8")
    return text


# CJK 连续字符段（≥2 字）与字母数字 token（如 B12 / NSAIDs）
_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_ALNUM_TOK = re.compile(r"[A-Za-z0-9]{2,}")

# 通用照护/告诫连接词（动词·严重度·就医动作·连词）——这些 2-字片段几乎在每个病种
# YAML 都出现，不能作为「书中有该警告」的证据，否则一切 must_warn 都会被它们接地。
# 刻意只收**非疾病特异**的泛词，不含任何临床名词（脓胸/晕厥/胸痛/引流 等保留为有效接地词）。
_STOP_BIGRAMS = {
    "立即", "及时", "尽快", "就医", "送医", "就诊", "急诊", "转诊", "出现", "发生",
    "严重", "异常", "持续", "突然", "需要", "必须", "应当", "建议", "注意", "提前",
    "防范", "警惕", "医生", "医师", "专科", "自行", "停药", "调整", "用药", "服药",
    "不可", "不要", "禁止", "可能", "导致", "引起", "危及", "生命", "风险", "方案",
    "确认", "决定", "告知", "处理", "评估", "检查", "治疗", "症状", "患者", "家属",
    "隐患", "受伤", "之前", "如不", "若不", "药物", "药品", "剂量",
}


def mustwarn_grounded(warning: str, corpus: str) -> bool:
    """粗粒度接地判定：must_warn 是否与语料共享任一**疾病特异**的 2-字 CJK 片段或字母数字 token。
    命中即视为「书中有迹可循」（B1，表达漏说）；全不命中 = 候选 B2/B3（书外）。
    通用照护连接词（见 _STOP_BIGRAMS）不计为接地，避免「立即就医」这类泛词把一切警告都判为接地。
    刻意宽松（只要任一疾病特异片段重叠就放过）以**只**捕获整条书外的 must_warn。"""
    corpus_low = corpus.lower()
    for tok in _ALNUM_TOK.findall(warning):
        if tok.lower() in corpus_low:
            return True
    for run in _CJK_RUN.findall(warning):
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram in _STOP_BIGRAMS:
                continue
            if bigram in corpus:
                return True
    return False


def audit_mustwarn(questions):
    """返回 warns：gold must_warn 在**实际路由 ∪ expected_domain** 的层1+层3 语料中**完全**找不到接地。
    输出 WARN（非 ERROR）——可能是 B2（书中有但 YAML 漏录，应按页码回填）或
    B3-doctor（书中也无，doctor 应拆分/放宽，否则强求书外患教 = 诱发幻觉且违反 doctor schema）。

    **只查 doctor 可达题**（mode∈both/doctor）：patient 模式的书外安全底线警告（如「不可自行停药」
    「接触血液戴手套」）是照护安全网的**刻意设计**（层3 safety_floor 的存在前提），并非违约——故
    `mode: patient` 的题豁免本检查。真正的契约风险只在 doctor 输出里出现书外患教。
    语料取实际路由域与 expected_domain 的并集，避免 gold 标注陈旧（如癌痛已改投 palliative）误报。"""
    warns = []
    for q in questions:
        if q.get("mode") == "patient":   # patient 安全网书外警告属设计内豁免
            continue
        warnings = q.get("must_warn", []) or []
        if not warnings:
            continue
        doms = sorted(set(q.get("expected_domain", [])) | set(route(q["question"])))
        corpus = "".join(warn_corpus_for(d) for d in doms)
        for w in warnings:
            if not mustwarn_grounded(w, corpus):
                warns.append((q["id"], w, doms))
    return warns


def has_yaml(domain: str) -> bool:
    try:
        specialty, disease = domain.split(":", 1)
    except ValueError:
        return False
    return (KN / specialty / f"{disease}.yaml").exists()


def audit_route(questions):
    """
    返回 (errors, warns)。
      ERROR = 实际路由的标签全都没有 YAML → 知识静默跳过、回退参数记忆（PE_ANTICOAG 类真 bug）
      WARN  = 路由命中了某个有 YAML 的 disease，但与 gold expected_domain 不交集
              （多为跨专科共置，如 gout 在 endocrine 而 gold 写 rheumatology；或 gold 已过时）
    真正会拉低 grounding 的只有 ERROR；WARN 供人工复核 gold 标注是否陈旧。
    """
    errors, warns = [], []
    for q in questions:
        expected = set(q.get("expected_domain", []))
        if not expected:
            continue
        actual = route(q["question"])
        if expected & set(actual):
            continue
        if not any(has_yaml(t) for t in actual):
            errors.append((q["id"], sorted(expected), actual))
        else:
            warns.append((q["id"], sorted(expected), actual))
    return errors, warns


def audit_tags(questions):
    fails = []
    for q in questions:
        tags = q.get("doctor_must_have_tags", [])
        if not tags:
            continue
        # 标签须在 expected_domain 任一 YAML 中找到（证据等级是模板通用词，豁免）
        corpus = "".join(yaml_text_for(d) for d in q.get("expected_domain", []))
        for tag in tags:
            if tag in TEMPLATE_TOKENS:   # 模板/证据分级固定词，非知识词，豁免
                continue
            if tag not in corpus:
                fails.append((q["id"], tag, q.get("expected_domain", [])))
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["route", "tag", "mustwarn"])
    args = ap.parse_args()

    questions = load_gold()
    exit_code = 0

    if args.only in (None, "route"):
        print(f"[ROUTE] 检查 {len(questions)} 题路由契约 …")
        errors, warns = audit_route(questions)
        for qid, exp, act in errors:
            print(f"  ✗ ERROR {qid}: 实际 {act} 均无 YAML → 知识静默跳过（expected {exp}）")
        for qid, exp, act in warns:
            print(f"  ⚠ WARN  {qid}: 路由 {act} 有 YAML 但 ≠ gold {exp}（复核 gold 是否陈旧）")
        if errors:
            exit_code = 1
        print(f"[ROUTE] {len(errors)} ERROR / {len(warns)} WARN"
              f" {'✓' if not errors else '✗'}\n")

    if args.only in (None, "tag"):
        print("[TAG] 检查 doctor_must_have_tags 接地 …")
        fails = audit_tags(questions)
        if fails:
            exit_code = 1
            for qid, tag, doms in fails:
                print(f"  ✗ {qid}: 标签「{tag}」未出现在 {doms} 的 YAML 中（疑为元描述）")
        print(f"[TAG] {'通过 ✓' if not fails else f'{len(fails)} 个标签可疑 ✗'}")

    if args.only in (None, "mustwarn"):
        print("\n[MUSTWARN] 检查 must_warn 接地（层1 病种 YAML + 层3 安全底线）…")
        mw = audit_mustwarn(questions)
        for qid, w, doms in mw:
            print(f"  ⚠ WARN  {qid}: must_warn「{w}」在 {doms} 的层1/层3 语料中无接地"
                  f"（候选 B2 回填 / B3 放宽或落底线，须对 PDF 终判）")
        print(f"[MUSTWARN] {len(mw)} WARN"
              f" {'✓' if not mw else '（仅提示，不改退出码）'}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
