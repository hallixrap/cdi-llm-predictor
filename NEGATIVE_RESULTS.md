# Negative Results — Things We've Tested That Don't Work

Single source of truth for failed experiments, so we don't repeat them. Each entry: what we tried, what the data said, why it failed, and what would be worth revisiting under different conditions.

Locked production baseline as of 11 May 2026:
**gpt-5-4 + v15_cdi_agent_style prompt + balanced (3-vote) voting + Jaccard already-documented filter.**
- Recall: 53.1% on 30-case paired sample
- LEGITIMATE_CDI: 53.3% (gpt-5 judge) / 30.0% (opus judge)
- Composite (Recall × LEG): 28.3% (gpt-5) / 15.9% (opus)
- Data: 1086-case `cdi_expanded_notes_eval.csv` with 11 note types per case

Every experiment below was paired against this baseline on the same 30 cases (seed=42).

---

## 1. v13 — Category-expanded prompt (single-pass) — LOSE

**What:** Same user-prefix design as v15 but explicitly listing "less common but high-value" categories (pressure injuries, encephalopathy, functional quadriplegia, surgical complications, drug reactions). Hypothesis: expanded coverage hits the "other" bucket harder.

**Data (paired 30-case):**
- Recall: 46.9% (−6.2pp vs v15)
- LEG (gpt-5 judge): 36.7% (vs v15 53.3%)
- LEG (opus judge): 30.0% (flat vs v15)
- Composite worse under both judges.

**Why it failed:** v13 has no system prompt, only a user prefix. Under voting (3 samples), without a system-prompt anchor, samples diverge more and the consensus filter rejects more candidates. The expanded category list also surfaced more "near miss" diagnoses that the matching judge tagged ALREADY_CODED or PARTIALLY_VALID.

**Worth revisiting if:**
- Voting is replaced with something that handles low-anchor prompts better
- Pair v13's user prefix with v15's system prompt (composition not tested)

## 2. v17 — Recall-max prompt — LOSE

**What:** "Flag every potentially missed diagnosis. Better to over-include than miss." Aggressive recall posture.

**Data (paired 30-case):**
- Recall: 65.6% (+12.5pp vs v15)
- Discovery rate: 19.6/case (vs v15 5.5/case)
- LEG (gpt-5): 33.3% (vs v15 53.3%)
- LEG (opus): 20.0% (vs v15 30.0%)
- Composite: 21.8% (gpt-5) vs 28.3% baseline; 13.1% (opus) vs 15.9% baseline.

**Why it failed:** The recall lift was bought with hallucination. HALLUCINATION rate climbed from 13-17% (baseline) to 20-27% (v17). Voting filters inconsistent output, not speculative output — so noise from "cast a wide net" passes consensus.

**Worth revisiting if:**
- An aggressive *downstream* filter (e.g., per-prediction LLM verification against chart evidence) can strip the noise. The two-pass-verify approach (Phase 3 below) was meant to test this and also failed.

## 3. v18 — Two-pass verify (IEEE 2025 paradigm) — LOSE

**What:** Pass 1 generates broadly (similar to v17). Pass 2 verifies each candidate against the chart, keeps only CONFIRMED. Hypothesis: verification step strips Pass 1's hallucinations structurally.

**Data (paired 30-case):**
- Recall: 56.2% (+3.1pp vs v15)
- Discovery rate: 23.2/case (higher than v17 — worst of any config tested)
- LEG (gpt-5): 40.0% (vs v15 53.3%)
- LEG (opus): **6.7%** (vs v15 30.0% — worst result of any test)
- Composite: 22.5% (gpt-5) vs 28.3% baseline; **3.8%** (opus) vs 15.9% baseline.

**Why it failed:** Pass 2 verified "evidence exists in chart" — but didn't check "is this missing from the discharge dx". So Pass 2 confirmed many findings that ARE in the chart but are ALREADY in the discharge dx. Result: ALREADY_CODED climbed from 13% (baseline) to 37% (under Opus judge) — a 24pp jump. Voting+filter didn't help because the v18 path bypasses voting (two-pass IS the discipline).

**Worth revisiting if:**
- Pass 2 prompt explicitly asks "is this missing from the discharge diagnoses" not just "is there evidence". The wording would need:
  *"REJECT if (a) no clinical evidence, OR (b) condition is already in the formal discharge diagnoses (any wording)."*
- Combine v18 with the LLM already-documented filter (#5 below) — but that filter also failed, so this is double-uncertain.
- Use claude-opus-4-7 for Pass 2 — Opus is stricter at "already coded" detection.

## 4. CDIAgentRunner (Claude Agent SDK shape, opus-4-7) — LOSE

**What:** Replicate the Anthropic-built `cdi-agent` architecture on Stanford's Bedrock gateway. Multi-turn tool-use loop, structured-output `report_diagnoses` tool, ICD-10 format-validation hook. SKILL.md content (11 CDI categories with quantitative criteria) folded into the system prompt.

**Data (paired 30-case):**
- Recall: 59.4% (+6.3pp vs v15)
- Discovery rate: 15.2/case
- LEG (gpt-5): 16.7%
- LEG (opus): 16.7% (self-eval)
- HALL: **30.0% under both judges** (judge-independent — real architectural weakness)
- Composite: 9.9% (gpt-5) vs 28.3% baseline.

**Why it failed:** Three stacking issues —
1. SKILL.md primer is exhaustive (~200 lines of CDI rules across 11 categories), priming Opus to find something in every category.
2. `report_diagnoses` tool said "all clinically-significant missed diagnoses" — no top-N cap. Original Anthropic agent capped at top 5.
3. Validation hook only checks ICD-10 *format*, not grounding. Format passes easily; speculative findings dump.

**Worth revisiting if all three fix at once:**
- Restore top-N cap in `report_diagnoses` ("Report the top 5 highest-yield missed diagnoses").
- Tighten validation hook to require evidence quoting (regex for quoted chart text in `evidence` field).
- Trim SKILL.md primer to the top 5 categories' criteria; let the model rely on training for the rest.

Recorded as "Agent track v2" in the May 2026 plan.

## 5. LLM-based already-documented filter (gpt-5-nano) — LOSE

**What:** A second pass after the Jaccard already-documented filter. For each surviving prediction, ask gpt-5-nano: "is this semantically equivalent to anything in the documented-diagnosis list?" Filter out duplicates. Hypothesis: catches paraphrased duplicates the Jaccard misses ("AKI" ≡ "acute renal failure").

**Data (paired 30-case, three configs tested):**

| Config | Recall (no filter / +filter) | LEG opus (no filter / +filter) | Composite opus |
|---|---|---|---|
| v15 + filter | 53.1 → 40.6% | 30.0 → 30.0% | 15.9 → 12.2% ❌ |
| v17 + filter | 65.6 → 56.2% | 20.0 → 16.7% | 13.1 → 9.4% ❌ |
| v18 + filter | 56.2 → 43.8% | 6.7 → 10.0% | 3.8 → 4.4% marginal |

**Why it failed:** gpt-5-nano can't distinguish specificity upgrades. It sees "severe sepsis with organ dysfunction" and "sepsis" as equivalent — filters the clinically-valuable specific version. The 9-12pp recall drop wasn't paraphrased duplicates being removed; it was real specificity-upgrade queries being killed.

**Worth revisiting if:**
- Use `claude-opus-4-7` as the filter model (Opus knows specificity nuance better). Cost ~10x on filter calls but if it works, that's the production setting. We did NOT test this — code is wired and ready: `--llm-filter --filter-model claude-opus-4-7`.
- Use a hybrid approach: gpt-5-nano for obvious matches, Opus only for borderline cases.
- Add a specificity-upgrade exemption list to the filter prompt (don't filter if candidate contains "severe", "acute on chronic", "with organ dysfunction", etc).

The wiring is in place (`scripts/cdi_engine.py` `_llm_already_documented_filter`) — disabled by default. See top-of-function comment.

---

## Cross-cutting lessons

1. **LLM judges disagree by 2-3× on ALREADY_CODED.** gpt-5 is lenient; opus is strict. Until there's clinical reviewer calibration, neither is ground truth. Optimisation against either alone oscillates.

2. **The precision wall is upstream of prompt engineering.** Every recall-boost attempt has hit ALREADY_CODED inflation under the strict (opus) judge. The bottleneck appears to be the boundary between "missed diagnosis worth querying" and "technically findable but already coded" — which is a structural problem, not a prompt problem.

3. **We have one data type (notes); SmarterDx has nine.** The CDI rules require structured lab values, MAR data, pathology reports. We've been asking the model to extract these from narrative text. **More data is likely the highest-EV next bet** — see `PHASE_E_PATHOLOGY_DESIGN.md` for the structural pipeline approach.
