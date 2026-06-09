#!/usr/bin/env python3
"""parse_judge.py — 健壮解析判官（judge）响应里的四维打分。

输入：判官原始响应文本（stdin）。
输出：规范化 JSON（stdout）：
    {"coverage":N,"accuracy":N,"safety":N,"grounding":N,
     "flags":[...],"ok":bool,"error":str|null}

退出码：
    0  成功提取四维分数（严格解析或正则兜底全部命中）。
    3  无法提取分数（无 JSON 对象 / 正则零命中）→ 调用方可据此用 --no-cache 重跑判官一次。

设计动机：判官有时在字符串值里写入未转义的中文引号或嵌套结构，
导致 json.loads 在前几十字符就报 "Expecting ',' delimiter"，
旧逻辑直接判 0/40（如 ONCO_LIFESTYLE_01）。本工具改为：
严格解析 → 轻量修复 → 逐维正则兜底，确保分数不因格式噪声丢失。
"""
import json
import re
import sys

DIMS = ("coverage", "accuracy", "safety", "grounding")
# 判官 score 的常见键名（schema 规定为 "score"，此处容忍同义/本地化变体）
_SCORE_KEYS = ("score", "得分", "分数", "评分", "rating", "value")


def _extract_balanced(text):
    """从首个 '{' 起按括号配平截取 JSON 对象子串（尊重字符串与转义）。

    对良构字符串可精确定位对象边界，避免贪婪 `\\{.*\\}` 把尾随散文一并吞入。
    若字符串内有未转义引号导致配平错乱，返回值仍会让 json.loads 失败，
    从而落到正则兜底——是安全的。
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]  # 未配平（疑似截断）→ 返回剩余部分交给后续兜底


def _strict_parse(obj_str):
    """严格 json.loads + 一次轻量修复（去尾随逗号）。成功返回 dict，否则 None。"""
    candidates = [obj_str, re.sub(r",(\s*[}\]])", r"\1", obj_str)]
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _coerce_score(v, depth=2):
    """把一个值强制解析为整数分数。**取不到时返回 None（区别于真实的 0）**。

    容忍：裸数字、数字字符串（"10"/"10/10"）、嵌套 dict（按 _SCORE_KEYS 找，或下钻一层）。
    刻意只经 score 键名下钻，不抓 dict 里任意数字，避免把 rationale 里的数字/权重误当分数。
    """
    if isinstance(v, bool):           # True/False 不是分数
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        return int(m.group()) if m else None
    if isinstance(v, dict) and depth > 0:
        for k in _SCORE_KEYS:
            if k in v:
                s = _coerce_score(v[k], depth - 1)
                if s is not None:
                    return s
        for val in v.values():        # 仅下钻嵌套 dict 找 score 键
            if isinstance(val, dict):
                s = _coerce_score(val, depth - 1)
                if s is not None:
                    return s
    return None


def _score_of(data, dim):
    """从结构化 dict 取某维 score；**取不到返回 None**（None=提取失败→应兜底/重跑，
    与判官真实给 0 严格区分）。兼容 {"score":N}、裸数字、数字字符串、同义键、单层嵌套。"""
    if not isinstance(data, dict) or dim not in data:
        return None
    return _coerce_score(data[dim])


def _regex_scores(raw):
    """逐维正则兜底：从原始文本直接抓 "<dim>": {... "score": N ...}。

    操作原始文本而非配平子串，因而不受未转义引号 / 配平错乱影响。
    返回 (scores_dict, hit_count)。
    """
    scores = {}
    hits = 0
    for dim in DIMS:
        m = re.search(
            rf'"{dim}"\s*:\s*\{{[^{{}}]*?"score"\s*:\s*(\d+)', raw, re.DOTALL
        )
        if not m:
            # 退一步：兼容 "coverage": 8 这类扁平写法
            m = re.search(rf'"{dim}"\s*:\s*(\d+)', raw)
        if m:
            scores[dim] = int(m.group(1))
            hits += 1
        else:
            scores[dim] = 0
    return scores, hits


def _regex_flags(raw):
    """尽力提取 flags 数组中的引号字符串（兜底用）。"""
    m = re.search(r'"flags"\s*:\s*\[(.*?)\]', raw, re.DOTALL)
    if not m:
        return []
    return re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))


def parse(raw):
    """返回 (result_dict, ok)。ok=False 表示分数不可信，建议重跑判官。"""
    raw = (raw or "").strip()
    obj_str = _extract_balanced(raw)

    if obj_str:
        normalized = obj_str.replace("\n", " ").replace("\r", " ")
        data = _strict_parse(normalized)
        if data is not None:
            strict = {d: _score_of(data, d) for d in DIMS}
            # 仅当四维分数**全部成功提取**才采信严格解析结果。
            # 任一维取不到（None：score 为 null/占位符/同义键失配/嵌套异常）→ 不静默填 0，
            # 落正则兜底；兜底仍凑不齐则 ok=False，由调用方重跑判官（修 ILD_BREATHLESS 类 A=0 假失败）。
            if all(s is not None for s in strict.values()):
                return {
                    **strict,
                    "flags": list(data.get("flags", []) or []),
                    "ok": True,
                    "error": None,
                }, True

    # 严格解析失败 / 有维度取不到分 → 逐维正则兜底（作用于原始文本）
    scores, hits = _regex_scores(raw)
    if hits >= len(DIMS):  # 四维全部命中 → 视为已恢复，可信
        return {
            **scores,
            "flags": _regex_flags(raw),
            "ok": True,
            "error": None,
        }, True

    # 兜底也无法凑齐四维 → 标记不可信，交由调用方决定是否重跑
    return {
        **scores,
        "flags": _regex_flags(raw) or [f"判官响应无法解析（仅命中 {hits}/4 维分数）"],
        "ok": False,
        "error": (obj_str or raw)[:200],
    }, False


def main():
    raw = sys.stdin.read()
    result, ok = parse(raw)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
