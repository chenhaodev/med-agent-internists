#!/usr/bin/env python3
"""
extract.py — 章节 Markdown → 疾病 YAML（通过 DeepSeek 提取结构化知识）

用法：
  python3 bin/extract.py source/chapters/cardiology/hypertension.md
  python3 bin/extract.py source/chapters/cardiology/hypertension.md --out knowledge/cardiology/hypertension.yaml
  python3 bin/extract.py --specialty cardiology  # 批量处理一个专科

依赖：DEEPSEEK_API_KEY 已在 .env 中配置
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent

try:
    import yaml
except ImportError:
    print("错误：需要 pyyaml。运行 pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ─── 专科/疾病元数据（用于 YAML header）─────────────────────────────────────
SPECIALTY_META = {
    "cardiology": {
        "specialty_zh": "心血管",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "hypertension":  "高血压",
            "heart_failure": "心力衰竭",
            "cad":           "冠心病",
            "arrhythmia":    "心律失常",
        }
    },
    "endocrine": {
        "specialty_zh": "内分泌代谢",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "diabetes_t2":  "2型糖尿病",
            "thyroid":      "甲状腺疾病",
            "dyslipidemia": "血脂异常",
            "gout":         "痛风与高尿酸血症",
            "obesity":      "肥胖症",
        }
    },
    "respiratory": {
        "specialty_zh": "呼吸",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "copd":      "慢性阻塞性肺疾病",
            "asthma":    "支气管哮喘",
            "pneumonia": "肺炎",
        }
    },
    "digestive": {
        "specialty_zh": "消化（含肝）",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "liver": "病毒性肝炎与肝硬化",
            "gi":    "胃肠疾病",
            "ibd":   "炎症性肠病",
        }
    },
    "renal": {
        "specialty_zh": "肾",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "ckd":       "慢性肾脏病",
            "nephritis": "肾小球肾炎",
        }
    },
    "hematology": {
        "specialty_zh": "血液",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "anemia": "贫血",
        }
    },
    "infectious": {
        "specialty_zh": "感染",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "general": "感染性疾病",
        }
    },
    "rheumatology": {
        "specialty_zh": "风湿骨",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "ra":           "类风湿关节炎",
            "sle":          "系统性红斑狼疮",
            "osteoporosis": "骨质疏松症",
        }
    },
}

EXTRACT_SYSTEM_PROMPT = """你是医学知识结构化提取专家。
从输入的教材章节文本中，提取适合患者家属查询的核心知识条目。

输出严格为 JSON 格式（不要 markdown 代码块），结构如下：
{
  "entries": [
    {
      "id": "简短英文ID如 HTN_DIET",
      "title": "条目标题（中文，20字以内）",
      "source_page": 印刷页码整数,
      "evidence_level": "高/中/低",
      "recommendation": "强推荐/推荐/可考虑",
      "key_points": [
        "具体、可操作的家属建议（每条 ≤50 字）",
        "..."
      ]
    }
  ]
}

要求：
- 每章提取 5-15 个条目，覆盖：病因/危险因素、诊断标准（家属可理解）、生活方式管理、药物管理注意事项（不写剂量）、并发症预防、何时就医
- **source_page 填印刷页码**：文本中每个 [p.N | 页码 F] 标记里，F 就是印刷页码；只有旧格式 [p.N] 时，从块内容第 2-3 行的独立数字（如 "1071"）读取印刷页码。**绝不使用 [p.N] 里的 N（那是 PDF 物理页，不是印刷页码）**
- 若同一知识点跨多页，填内容最多的那页的印刷页码
- **patient_facing 章节必须提取生活方式条目**：若原文涉及运动/锻炼/饮食/体力活动/戒烟/戒酒/康复/居家护理，必须各自单独成条，不得合并到诊断或治疗条目中
- key_points 用家属能理解的语言，避免专业缩写堆砌
- evidence_level 和 recommendation 如原文有明确说明则引用，否则填"未注明"
- 不包含任何具体药物剂量"""


def load_env() -> str:
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("错误：未设置 DEEPSEEK_API_KEY。请配置 .env 文件。", file=sys.stderr)
        sys.exit(1)
    return key


def call_deepseek(api_key: str, model: str, system: str, user: str, max_retries: int = 3) -> str:
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": model,
        "temperature": 0,
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ]
    }).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < max_retries:
                wait = attempt * 3
                print(f"  HTTP {e.code}，{wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except (OSError, ConnectionError) as e:
            if attempt < max_retries:
                wait = attempt * 5
                print(f"  网络错误 ({e})，{wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    return ""


def extract_chapter(md_file: Path, out_yaml: Path, api_key: str, model: str) -> None:
    specialty = md_file.parent.name
    disease = md_file.stem

    meta = SPECIALTY_META.get(specialty, {})
    disease_zh = meta.get("diseases", {}).get(disease, disease)
    specialty_zh = meta.get("specialty_zh", specialty)
    source = meta.get("source", "《西氏内科学精要》（中文版）")

    text = md_file.read_text(encoding="utf-8")
    # 截断到约 8000 字（避免超出 context）
    if len(text) > 8000:
        text = text[:8000] + "\n\n[文本已截断]"

    user_prompt = f"专科：{specialty_zh}  疾病：{disease_zh}\n\n以下是教材章节原文：\n\n{text}"

    print(f"  调用 DeepSeek 提取 {specialty}/{disease} ...")
    raw = call_deepseek(api_key, model, EXTRACT_SYSTEM_PROMPT, user_prompt)

    # 解析 JSON
    raw_clean = raw.strip()
    if raw_clean.startswith("```"):
        raw_clean = "\n".join(raw_clean.split("\n")[1:])
        if raw_clean.endswith("```"):
            raw_clean = raw_clean[:-3]

    try:
        extracted = json.loads(raw_clean)
    except json.JSONDecodeError as e:
        print(f"  警告：JSON 解析失败（{e}），写入原始输出到 {out_yaml}.raw", file=sys.stderr)
        out_yaml.with_suffix(".raw").write_text(raw, encoding="utf-8")
        return

    # 组装 YAML
    output = {
        "specialty": specialty,
        "specialty_zh": specialty_zh,
        "disease": disease,
        "disease_zh": disease_zh,
        "source": source,
        "entries": extracted.get("entries", [])
    }

    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    with out_yaml.open("w", encoding="utf-8") as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"  → {out_yaml}  ({len(output['entries'])} 条目)")


def main() -> None:
    parser = argparse.ArgumentParser(description="章节 Markdown → 疾病 YAML 提取工具")
    parser.add_argument("input", nargs="?", help="输入 Markdown 文件路径")
    parser.add_argument("--out", help="输出 YAML 路径（默认 knowledge/{specialty}/{disease}.yaml）")
    parser.add_argument("--specialty", help="批量处理指定专科下所有章节")
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    )
    args = parser.parse_args()

    api_key = load_env()

    if args.specialty:
        chapters_dir = ROOT_DIR / "source" / "chapters" / args.specialty
        if not chapters_dir.exists():
            print(f"错误：{chapters_dir} 不存在，请先运行 ingest.py。", file=sys.stderr)
            sys.exit(1)
        md_files = sorted(chapters_dir.glob("*.md"))
        if not md_files:
            print(f"错误：{chapters_dir} 中没有 .md 文件。", file=sys.stderr)
            sys.exit(1)
        for md in md_files:
            out = ROOT_DIR / "knowledge" / args.specialty / f"{md.stem}.yaml"
            extract_chapter(md, out, api_key, args.model)
            time.sleep(2)
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    md_file = Path(args.input)
    if not md_file.exists():
        print(f"错误：文件不存在：{md_file}", file=sys.stderr)
        sys.exit(1)

    if args.out:
        out_yaml = Path(args.out)
    else:
        specialty = md_file.parent.name
        disease = md_file.stem
        out_yaml = ROOT_DIR / "knowledge" / specialty / f"{disease}.yaml"

    extract_chapter(md_file, out_yaml, api_key, args.model)


if __name__ == "__main__":
    main()
