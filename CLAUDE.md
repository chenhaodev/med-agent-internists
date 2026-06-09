# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Chinese-language **internal-medicine (西式内科学) psychoeducation/Q&A agent**, grounded in
**《西氏内科学精要》(Cecil Essentials of Medicine)** — a two-volume Chinese PDF (`上卷` + `下卷` under
`pdfs/`, git-ignored). It serves two audiences (patient/家属 and clinician) over 18 specialties.

**No RAG / no vector store.** It uses **structured knowledge injection + deterministic keyword routing**,
with every fact traceable to a printed page (`source_page`) in the Cecil book. Both questions and source
are Chinese, so there is no translation step.

This repo is a clean **rebuild on the architecture of the sibling project `../med-agent-psy/`** (a DSM-5
psychiatry agent), reusing the internal-medicine domain data of the older `../med-agent-inner-all/`. The
core pipeline scripts are nearly identical across all three projects (common ancestor); the divergences are
listed under "Internal-medicine specifics" below.

Coverage: **full-book — all 126 chapters extracted** (`knowledge/{specialty}/{disease}.yaml`), across
**18 specialties** (`molecular` was added as the home of ch1 「人类疾病的分子基础」). This includes every
patient-facing clinical chapter plus the 17 background chapters (normal structure/function, patient
assessment, imaging/endoscopy, lab diagnostics, host defense, etc.). Background chapters are extracted with
`extract.py --background` (a clinical-reference system prompt that drops the lifestyle-mandate of the
patient-facing prompt) and routed with deliberately **narrow** keywords that sit before the `*:general`
fallback but after disease routes, so they never poach disease-level traffic.

Two chapters are marked `patient_facing` in `chapters.yaml` but are intentionally **not** re-extracted
because their content already lives under a sibling slug — these are sibling-mapped, not gaps:
`rheumatology/gout` → `endocrine/gout.yaml`, and `bone_mineral/osteoporosis` → `rheumatology/osteoporosis.yaml`.

To add a *new* chapter (none remain in this book): the chapter MD exists under `source/chapters/`, so run
`extract.py` (add `--background` for non-patient-facing chapters) then wire router keywords + gold (see
"Adding a disease").

## Architecture

Two pipelines: an **offline build pipeline** (book → knowledge) and an **online ask pipeline**
(question → grounded answer).

### Offline build pipeline (PDF → structured knowledge)

1. **`bin/ingest.py`** — manifest-driven PDF → chapter Markdown. Reads `knowledge/chapters.yaml`
   (per-chapter `pdf_page_start/end`, **`volume: 上|下`**, `specialty`, `slug`, `patient_facing`). Emits
   `source/chapters/{specialty}/{slug}.md`. **Two-volume**: it selects the right volume PDF per chapter and
   interpolates a printed folio (`页码`) for every physical page, tagging each block `[p.{physical} | 页码 {folio}]`.
   Run `python3 bin/ingest.py --all` (API-free, ~10 min for all 126 chapters).
2. **`bin/extract.py`** — chapter Markdown → per-disease YAML via DeepSeek. **Evidence-aware**: emits real
   `evidence_level` (高/中/低) and `recommendation` (强推荐/推荐/可考虑) when the book states them, `未注明`
   otherwise.

Each disease YAML: top-level `specialty`/`disease`/`disease_zh`/`source` + `entries[]`, each entry
`{id,title,source_page,pdf_page,evidence_level,recommendation,key_points[],must_warn[]}`.
**Two non-disease YAML kinds** also live under `knowledge/`: `{specialty}/guidelines/*.yaml` (guideline
injection, keyed `guideline_name`) and `{specialty}/safety_floor/*.yaml` (layer-3 safety knowledge, keyed
`safety_floor_name`). These have no `specialty`/`disease` keys — audits that iterate diseases must skip them.

### Online ask pipeline (`bin/ask.sh` orchestrates)

1. **OOB / scope gate** (`bin/oob_check.sh`, deterministic, no API, <10ms). Action-based model, priority
   order: `surgery` (手术/介入决策) → `chemo` (化疗具体方案/剂量) → `diagnosis` (要求确诊/看化验单) →
   `dosing_change` (自行调药) → `unrelated`. Each returns `out_of_scope:<type>` → `ask.sh` prints the
   matching template (`prompts/oob_templates*.md`) and exits without entering the pipeline.
2. **Routing** (`bin/router.sh`) — keyword table → up to 2 `specialty:disease` tags; DeepSeek fallback,
   then `cardiology:general` default. `--domain` forces a domain.
3. **Prompt build** (`bin/build_prompt.sh`) — stacks system prompt + output schema (mode-specific) +
   per-specialty section prompt (`prompts/sections/*.md`) + disease YAML knowledge (+ guideline/safety-floor
   injection where applicable).
4. **Generation** (`bin/call_deepseek.sh`, or `_stream.sh`) — sha256 payload cache under `.cache/deepseek/`.
5. **Verify + reroll** (`--accurate` only, `bin/verify_claims.py`) — grep-checks atomic claims, rerolls once.
6. **Postprocess** (`bin/postprocess.sh`) — validates section structure against `schema/sections.yaml`.

### Two orthogonal axes

- **Audience** (`--mode patient|doctor`, default patient): selects `system_*.md` / `output_schema_*.md`
  and the required section headings (`schema/sections.yaml`):
  - patient: 【这是什么】【日常该怎么做】【什么情况要就医】【常见误区】【依据】
  - doctor: 【定义与流行病学】【循证管理】【红旗症状/转诊指征】【证据等级汇总】【参考】
- **Execution** (`--fast` default vs `--accurate`/`--deep`): adds verify+reroll (~2-3× calls).

## Internal-medicine specifics (the divergences from med-agent-psy)

1. **Evidence grades are real.** Unlike the DSM (which has none, so psy hard-codes `未注明`), the Cecil book
   has evidence levels and recommendation strengths. The **doctor schema includes 【证据等级汇总】** (an
   evidence-grade summary table), gold cases assert `doctor_must_have_tags: 证据等级`, and `doctor_checks.py`
   analyzes the doctor answer for evidence-grade presence/homogeneity.
2. **Section schema is internal-medicine-shaped** (see headings above), not DSM-shaped.
3. **OOB gate is action-based** (surgery/chemo/diagnosis/dosing), not psy's suicide-crisis-first model. There
   is **no `check_crisis.py`**; OOB recall is measured by `eval_oob.sh` against `eval/oob_gold.yaml`.
4. **Two-volume ingest** (`volume: 上|下`).

## Common commands

```bash
cp .env.example .env                 # set DEEPSEEK_API_KEY (only external dep; .env present locally)

# Build (API-free for ingest; extract needs DeepSeek)
python3 bin/ingest.py --list
python3 bin/ingest.py --all          # PDF → source/chapters/*.md (needed by page-grounding gates)
python3 bin/extract.py --specialty cardiology

# Ask
./bin/ask.sh "我爸有高血压，平时饮食要注意什么？"
./bin/ask.sh --mode doctor "高血压血压控制目标？"
./bin/ask.sh --accurate "肺栓塞抗凝怎么做？"
./bin/ask.sh --domain cardiology:hypertension --debug "..."

# Gates — ALL must exit 0 before eval. One command:
./bin/check.sh                   # audit_routing · audit_grounding · audit_grounding_sample · audit_schema · smoke
./bin/check.sh --with-oob        # also runs eval_oob (consumes API)

# Eval
./bin/eval.sh --mode both --specialty cardiology,respiratory,digestive --concurrency 8   # slice
./bin/eval.sh --mode both --concurrency 8        # full gold.yaml (150 q)
./bin/eval_deep.sh                               # accurate (verify+reroll)
./bin/eval_oob.sh                                # OOB interception
```

## Conventions & gotchas

- **Determinism first**: the OOB gate and router never call the API on the happy path — keep them pure
  bash/regex.
- **Traceability is the core contract**: every entry carries `source_page` (printed folio);
  `audit_grounding_sample.py` enforces each `source_page` falls within its disease's chapter folio range
  (built from `source/chapters/*.md`, tolerance ±2). **This gate needs `source/chapters/` to exist — run
  `bin/ingest.py --all` first** (reuse-of-data mode does not generate it automatically).
- **Folio floor is 2, not 10**: `bin/folio_map.py` detects printed folios with `FOLIO_MIN=2` because the
  book's front chapter (ch1 分子医学概论) starts at printed page 2. An earlier `FOLIO_MIN=10` silently
  dropped the folio tags on ch1's first pages, which made `molecular_basis` entries (folios 2–9) fail
  page-grounding. If you re-tune this floor, re-ingest **ch1** and re-run `audit_grounding_sample.py`.
- **`schema/sections.yaml` is the single source of truth** for section names; `output_schema*.md`,
  `judge_prompt*.md`, and `postprocess.sh` all derive from / are checked against it by `audit_schema.py`.
- **Disease ↔ chapter is 1:1** for grounding. When a disease spans multiple book chapters (e.g. the 肝炎
  chapter vs the 肝硬化 chapter), keep its entries within the chapter its `source_page`s point to, or the
  page-grounding gate fails. (This is why `liver.yaml` = 肝硬化 and `hepatitis.yaml` = the 肝炎 chapter.)
- **Adding a disease**: add a row to `knowledge/chapters.yaml` (with `volume`) → `ingest.py` → `extract.py`
  → router keywords + a `prompts/sections/{specialty}.md` for a new specialty → gold cases → `check.sh`.
- **Stack is minimal**: Python deps `pymupdf==1.24.11`, `pyyaml==6.0.2`; lint via `ruff.toml` (line-length
  99, `select=["E","F","I"]`). Pipeline orchestration is bash; only ingest/extract/audits are Python.
- **DeepSeek config** in `.env` (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_TIMEOUT`,
  `DEEPSEEK_MAX_RETRIES`). `.cache/`, `source/`, `*.pdf`, `.env`, `eval/results/*.json` are git-ignored.
