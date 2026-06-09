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
            "general":             "感染性疾病",
            "head_neck_infection": "头颈部感染",
            "cv_infection":        "心血管感染（感染性心内膜炎）",
            "abdominal_infection": "腹腔感染",
            "infectious_diarrhea": "感染性腹泻",
            "bone_joint_infection": "骨和关节感染",
            "hospital_infection":  "医院相关性感染",
            "sti":                 "性传播感染",
            "immunocompromised":   "免疫缺陷宿主中的感染",
            "travel_infection":    "旅行者感染（原虫和蠕虫感染）",
        }
    },
    "rheumatology": {
        "specialty_zh": "风湿骨",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "ra":           "类风湿关节炎",
            "sle":          "系统性红斑狼疮",
            "osteoporosis": "骨质疏松症",
            "rheum_assessment": "风湿病患者的处理",
            "soft_tissue":  "非关节性软组织疾病",
            "sjogrens":     "干燥综合征与系统性疾病的风湿样表现",
        }
    },
    "molecular": {
        "specialty_zh": "分子医学",
        "source": "《西氏内科学精要》（中文版）",
        "diseases": {
            "molecular_basis": "人类疾病的分子基础",
        }
    },
}

# ─── 全书全覆盖新增章节的中文名（背景章 + 余下患者向缺口）──────────────────
# 这些 slug 不在上方按疾病组织的 SPECIALTY_META.diseases 里，单独登记中文名以美化
# YAML header（缺省会回落到 slug，不影响门禁，仅影响可读性）。
EXTRA_DISEASE_ZH = {
    "cardiology": {
        "cv_structure":   "心脏和血管的正常结构与功能",
        "cv_assessment":  "心血管疾病患者的评估",
        "cv_diagnostics": "心血管疾病的辅助检查",
    },
    "respiratory": {
        "lung_basics":    "健康肺与病肺",
        "resp_assessment": "呼吸疾病患者的诊治思路",
        "lung_function":  "肺结构与功能的评估",
    },
    "renal": {
        "renal_structure": "肾脏的结构与功能",
        "renal_patient":   "了解肾脏病患者",
        "non_glomerular":  "常见非肾小球疾病",
    },
    "digestive": {
        "gi_symptoms":     "胃肠道疾病的常见临床表现",
        "gi_diagnostics":  "内镜及影像学检查",
        "liver_labs":      "肝脏疾病的实验室检查",
        "acute_liver_failure": "急性肝衰竭",
    },
    "hematology": {
        "hematopoiesis":   "造血与造血衰竭",
        "neutrophil":      "中性粒细胞相关临床疾病",
        "coagulation_basics": "生理止血",
    },
    "endocrine": {
        "male_repro_endo": "男性生殖内分泌学",
    },
    "infectious": {
        "host_defense":         "宿主如何防御感染",
        "infect_lab_diagnosis": "感染性疾病的实验室诊断",
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


# 背景章（解剖/生理/患者评估/辅助检查/检验/宿主防御等 patient_facing:false 章节）专用提示。
# 与患者向提示的唯一差异：**去掉"必须提取生活方式条目"的强制项**（这些章无生活方式内容），
# 改为面向临床参考/科普的条目要求；页码规则与 evidence_level 规则、JSON 结构均保持不变。
BACKGROUND_SYSTEM_PROMPT = """你是医学知识结构化提取专家。
从输入的教材背景章节（正常结构与功能、患者评估思路、辅助检查/影像内镜、实验室检查、宿主防御等）中，
提取适合临床参考与患者科普的核心知识条目。

输出严格为 JSON 格式（不要 markdown 代码块），结构如下：
{
  "entries": [
    {
      "id": "简短英文ID如 ECG_BASICS",
      "title": "条目标题（中文，20字以内）",
      "source_page": 印刷页码整数,
      "evidence_level": "高/中/低",
      "recommendation": "强推荐/推荐/可考虑",
      "key_points": [
        "具体、可理解的知识点（每条 ≤50 字）",
        "..."
      ]
    }
  ]
}

要求：
- 每章提取 5-15 个条目，覆盖：基本概念/正常值与意义、各项检查能查出什么/适用场景、结果如何解读（不做诊断）、检查注意事项与风险、何时需进一步评估或就医
- **source_page 填印刷页码**：文本中每个 [p.N | 页码 F] 标记里，F 就是印刷页码；只有旧格式 [p.N] 时，从块内容第 2-3 行的独立数字（如 "1071"）读取印刷页码。**绝不使用 [p.N] 里的 N（那是 PDF 物理页，不是印刷页码）**
- 若同一知识点跨多页，填内容最多的那页的印刷页码
- key_points 用患者/家属能理解的语言，避免专业缩写堆砌；解释类条目可适当展开但勿冗长
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
        "max_tokens": 8000,
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


def extract_chapter(md_file: Path, out_yaml: Path, api_key: str, model: str,
                    background: bool = False) -> None:
    specialty = md_file.parent.name
    disease = md_file.stem

    meta = SPECIALTY_META.get(specialty, {})
    disease_zh = (
        meta.get("diseases", {}).get(disease)
        or EXTRA_DISEASE_ZH.get(specialty, {}).get(disease)
        or disease
    )
    specialty_zh = meta.get("specialty_zh", specialty)
    source = meta.get("source", "《西氏内科学精要》（中文版）")
    system_prompt = BACKGROUND_SYSTEM_PROMPT if background else EXTRACT_SYSTEM_PROMPT

    text = md_file.read_text(encoding="utf-8")
    # 截断到约 8000 字（避免超出 context）
    if len(text) > 8000:
        text = text[:8000] + "\n\n[文本已截断]"

    user_prompt = f"专科：{specialty_zh}  疾病：{disease_zh}\n\n以下是教材章节原文：\n\n{text}"

    tag = "背景章" if background else "患者向"
    print(f"  调用 DeepSeek 提取 {specialty}/{disease}（{tag}）...")
    raw = call_deepseek(api_key, model, system_prompt, user_prompt)

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
    parser.add_argument(
        "--background", action="store_true",
        help="背景章模式（解剖/生理/检查/检验类，去掉生活方式强制项）",
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
            extract_chapter(md, out, api_key, args.model, background=args.background)
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

    extract_chapter(md_file, out_yaml, api_key, args.model, background=args.background)


if __name__ == "__main__":
    main()
