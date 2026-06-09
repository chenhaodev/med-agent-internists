# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A greenfield rebuild of a Chinese-language **internal-medicine (西式内科学) psychoeducation/Q&A agent**,
grounded in **《西氏内科学精要》(Cecil Essentials of Medicine)** — a two-volume PDF (`上卷` + `下卷`).

The repo is **currently empty except `TASK.md`**. Per `TASK.md`, the predecessor `../med-agent-inner-all/`
("inner-all") works but its architecture has drifted and become tangled after repeated restructuring. This
repo restarts from scratch, **porting the cleaner, proven architecture of the sibling project
`../med-agent-psy/` (a DSM-5 psychiatry agent)** rather than continuing inner-all's lineage. Treat
`../med-agent-psy/CLAUDE.md` as the canonical architectural reference and `../med-agent-inner-all/` as the
source of internal-medicine domain content (chapter manifest, specialty list, OOB keyword tables, evidence
handling).

**No RAG / no vector store.** The design is **structured knowledge injection + deterministic keyword
routing**, with every fact traceable to a printed page (`source_page`) in the Cecil book. Answers are in
Chinese; the source is Chinese (西氏内科学精要), so extraction does not translate.

## Architecture to build (mirrors med-agent-psy)

Two pipelines: an **offline build pipeline** (book → knowledge) and an **online ask pipeline**
(question → grounded answer). Port file-for-file from `../med-agent-psy/bin/`, adapting for the domain
differences below.

### Offline build pipeline (PDF → structured knowledge)

1. **`bin/ingest.py`** — manifest-driven PDF → chapter Markdown. Reads `knowledge/chapters.yaml`
   (per-chapter `pdf_page_start/end`, `volume` 上/下, `part`, `specialty`, `slug`, `patient_facing`).
   Emits `source/chapters/{specialty}/{slug}.md`. **Two-volume gotcha:** unlike psy's single PDF, ingest
   must select the right volume PDF per chapter (`volume: 上|下`) and map physical→printed page numbers
   per volume.
2. **`bin/extract.py`** — chapter Markdown → per-disorder/-disease YAML via DeepSeek. Each entry carries
   `{id,title,source_page,pdf_page,evidence_level,recommendation,key_points[],must_warn[]}`.

Knowledge is organized by **specialty** (see inner-all's set, ~17–18): `cardiology`, `respiratory`,
`digestive`, `renal`, `endocrine`, `hematology`, `oncology`, `infectious`, `neurology`, `rheumatology`,
`bone_mineral`, `geriatrics`, `palliative`, `perioperative`, `substance_use`, `mens_health`,
`womens_health`, `molecular`.

### Online ask pipeline (`bin/ask.sh` orchestrates)

1. **OOB / scope gate** (`bin/oob_check.sh`, deterministic, no API, <10ms). Port inner-all's
   action-based model (NOT psy's suicide-crisis-first model — different domain): `surgery` (手术/介入决策)
   → `chemo` (化疗具体方案/剂量) → `diagnosis` (要求确诊/看化验单) → `dosing_change` (自行调药) →
   `unrelated`. Each returns `out_of_scope:<type>` → `ask.sh` prints the matching OOB template and exits.
2. **Routing** (`bin/router.sh`) — keyword table → `specialty:disease` tags; DeepSeek fallback then a
   default. `--domain` forces a domain.
3. **Prompt build** (`bin/build_prompt.sh`) — system prompt + output schema (mode-specific) + per-specialty
   section prompt (`prompts/sections/*.md`) + disease YAML knowledge.
4. **Generation** (`bin/call_deepseek.sh`) — sha256 payload cache under `.cache/deepseek/`.
5. **Verify + reroll** (`--accurate` only, `bin/verify_claims.py`) — grep-checks atomic claims, rerolls once.
6. **Postprocess** (`bin/postprocess.sh`) — validates section structure against `schema/sections.yaml`.

### Two orthogonal axes (same as psy)

- **Audience** (`--mode patient|doctor`, default patient): selects `system_*.md` / `output_schema_*.md`
  and required section headings.
- **Execution** (`--fast` default vs `--accurate`/`--deep`): adds verify+reroll (~2-3× calls).

## Key divergence from med-agent-psy: internal medicine HAS treatment + evidence grades

This is the single most important adaptation. psy is built on the DSM-5, which has **no treatment content
and no evidence grades**, so its prompts forbid drugs/doses and its doctor schema omits evidence summaries.
**Internal medicine is the opposite** (follow inner-all here, not psy):

- `extract.py` populates real `evidence_level` (高/中/低) and `recommendation` (强推荐/推荐/可考虑) when the
  book states them; `未注明` only when absent — do **not** hard-code them to `未注明` the way psy does.
- The doctor schema **includes** an evidence-grade summary, and `audit_grounding.py` keeps inner-all's G2
  **evidence-mapping** check (psy replaced this with a source_page check).
- Prompts may surface internal-medicine management/treatment (it's in the book) — but surgical indications,
  interventional timing, and chemo regimens/doses stay OOB (the `surgery`/`chemo` gate above).

## Commands (target — port from med-agent-psy)

```bash
cp .env.example .env                 # set DEEPSEEK_API_KEY (only external dep)

# Build
python3 bin/ingest.py --list
python3 bin/ingest.py --all
python3 bin/extract.py --specialty cardiology     # or a single MD file path

# Ask
./bin/ask.sh "我爸有高血压，平时饮食要注意什么？"
./bin/ask.sh --mode doctor "高血压血压控制目标？"
./bin/ask.sh --accurate "肺栓塞抗凝怎么做？"
./bin/ask.sh --domain cardiology:heart_failure --debug "..."

# Gates — ALL must exit 0 before eval (port bin/check.sh + audit_*.py + check_crisis-equivalent)
./bin/check.sh

# Eval
./bin/eval.sh --mode both --concurrency 8   # fast baseline over eval/gold.yaml
./bin/eval_deep.sh                          # accurate (verify+reroll)
./bin/eval_oob.sh                           # OOB interception
```

## Conventions & gotchas

- **Determinism first**: the OOB gate and router never call the API on the happy path — keep them pure
  bash/regex.
- **Traceability is the core contract**: every entry carries `source_page` (printed folio); `audit_grounding`
  enforces it's an int within the disease's printed-page range. Answers cite per `schema/sections.yaml`
  `citation_regex`.
- **`schema/sections.yaml` is the single source of truth** for section names; `output_schema*.md`,
  `judge_prompt*.md`, and `postprocess.sh` all derive from / are checked against it by `audit_schema.py`.
- **Adding a disease**: add a row to `knowledge/chapters.yaml` (with correct `volume`) → `ingest.py` →
  `extract.py` → router keywords + a `prompts/sections/{specialty}.md` for a new specialty → gold cases.
- **Stack is minimal**: Python deps `pymupdf==1.24.11`, `pyyaml==6.0.2`; lint via `ruff.toml`
  (line-length 99, `select = ["E","F","I"]`). Pipeline orchestration is bash; only ingest/extract/audits
  are Python.
- **DeepSeek config** lives in `.env` (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_TIMEOUT`,
  `DEEPSEEK_MAX_RETRIES`). `.cache/`, `source/`, `*.pdf`, `.env`, and `eval/results/*.json` are git-ignored.

## Bootstrapping note

When starting implementation, the fastest correct path is to copy the structure of `../med-agent-psy/`
(`bin/`, `prompts/`, `schema/`, `knowledge/chapters.yaml` shape) and the domain content of
`../med-agent-inner-all/` (specialty list, `chapters.yaml` rows with `volume`, OOB keyword tables,
evidence-aware extract prompt), then reconcile the two per the "Key divergence" section above.
