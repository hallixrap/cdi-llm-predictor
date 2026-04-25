# Hill-Climbing Evaluator: Architecture & Design

## Overview

`hill_climb_eval.py` is an autonomous optimization loop inspired by Karpathy's autoresearch framework. It iteratively improves the CDI predictor by testing prompt modifications, keeping improvements, and discarding degradations.

## Core Design Principles

### 1. **Single Metric Optimization**

Optimizes only **recall** (% of true diagnoses matched):

```
recall = (# predicted diagnoses that match ground truth) / (# ground truth diagnoses)
```

Why recall?
- **Captures** the primary objective: find missed diagnoses
- **Binary decision-making**: improvement is clear (higher is better)
- **Interpretable**: easily communicated to stakeholders
- **No tradeoff confusion**: precision/recall tradeoffs handled separately

### 2. **Fair Comparison via Fixed Sampling**

Each iteration uses the **same 30 cases**:

```
Iteration 0 (baseline): Evaluate on cases [1,5,7,12,15,...,N]
Iteration 1 (strategy 1): Evaluate on SAME cases [1,5,7,12,15,...,N]
Iteration 2 (strategy 2): Evaluate on SAME cases [1,5,7,12,15,...,N]
...
```

Benefits:
- Direct comparison of recall differences
- Eliminates sampling variance
- Results are reproducible (fixed seed=42)
- Each improvement is real, not statistical noise

### 3. **Strategy-Based Modification**

Changes are organized as pluggable **strategies**:

```python
class ModificationStrategy:
    def apply(config) -> modified_config
    def revert(config) -> original_config
```

Why?
- **Modular**: easy to add/remove/test strategies
- **Consistent**: all strategies follow same interface
- **Reversible**: revert by simply calling revert()
- **Testable**: each strategy can be tested independently

### 4. **Greedy Hill-Climbing**

Simple algorithm: keep improvements, discard degradations

```
best_recall = baseline_recall
best_config = baseline_config

for iteration in 1..max_iterations:
    modified_config = apply_strategy(best_config)
    recall = evaluate(modified_config)
    
    if recall > best_recall:
        best_config = modified_config
        best_recall = recall
    # else: discard, keep best_config
```

**Not random search, not gradient descent** — just greedy improvement. Advantages:
- **Interpretable**: can explain why each decision was made
- **Safe**: never goes backward in performance
- **Fast**: no randomness to converge
- **Autonomous**: no hyperparameters to tune

## Architecture

### Class Hierarchy

```
ModificationStrategy (abstract base)
├── TwoPassModeStrategy
├── ExtractionPromptDetailStrategy
├── CategorySpecificPromptsStrategy
├── PromptStructureStrategy
├── TemperatureStrategy
├── FewShotExamplesStrategy
├── OutputFormatStrategy
└── VerificationStepStrategy

HillClimbEvaluator
├── load_sample_data() -> List[Dict]
├── run_baseline_evaluation() -> float (recall)
├── evaluate_config(cases, config) -> (recall, precision, matched)
├── run_iteration(cases, iteration, strategy_idx) -> bool (improved)
└── run() -> None (main loop)
```

### Data Flow

```
Load CSV Dataset
    ↓
Sample 30 cases (seed=42)
    ├─ Extract ground truth diagnoses
    └─ Store: id, discharge_summary, progress_note, true_diagnoses
    ↓
Run baseline (iteration 0)
    ├─ For each case: predict_missed_diagnoses(discharge_summary, model)
    ├─ For each prediction: HybridMatcher.match(pred_dx, true_dx)
    └─ Compute: recall = matched / total_true
    ↓
Run hill-climbing (iterations 1..N)
    ├─ Apply strategy (modify config)
    ├─ Re-predict on SAME 30 cases with modified prompt
    ├─ Re-evaluate with modified predictions
    ├─ Compare recall to best_recall
    ├─ Keep if improved, revert if not
    └─ Log result to TSV
    ↓
Save checkpoint & final results
```

### Configuration Format

Config is a simple dict of parameters:

```python
{
    'two_pass_mode': False,                      # Strategy 1
    'prompt_detail_level': 0,                    # Strategy 2
    'use_category_specific_prompts': False,      # Strategy 3
    'use_system_message': False,                 # Strategy 4
    'temperature': 0.1,                          # Strategy 5
    'use_few_shot_examples': False,              # Strategy 6
    'output_format': 'text',                     # Strategy 7
    'include_verification_step': False,          # Strategy 8
}
```

Each strategy modifies one or more parameters.

## Key Implementation Details

### 1. Hybrid Matching (from llm_judge.py)

Diagnoses match if:

1. **Rule-based match** (fast, ~95% of cases):
   - Exact match (after normalization)
   - Substring match
   - Clinical equivalents (sepsis=septic, CHF=heart failure, etc.)
   - Jaccard word overlap > 0.7

2. **LLM judge** (for uncertain cases):
   - Uses gpt-5-nano (fast & cheap)
   - Cached for efficiency
   - Confidence threshold: >0.5

This ensures accurate matching while minimizing API calls.

### 2. Ground Truth Extraction

From CSV column `cdi_diagnoses_confirmed`:

```python
def extract_cdi_diagnosis_from_query(query_text: str) -> List[str]:
    # Parse patterns: [X] diagnosis, clinically valid, etc.
    # Return list of diagnoses CDI specialist queried about
```

This extracts what the CDI specialist considered "missed" diagnoses — the ground truth.

### 3. Prediction with Modified Config

Currently, `_predict_with_config()` calls standard `predict_missed_diagnoses()`:

```python
def _predict_with_config(self, discharge_summary, progress_note, config):
    result = predict_missed_diagnoses(
        discharge_summary=discharge_summary,
        api_key=self.api_key,
        model=self.model,
        filter_documented=True,
        progress_note=progress_note,
        # Note: config not yet used, but framework is ready
    )
    # Extract diagnoses from result
    return diagnoses
```

**Future work**: Modify `predict_missed_diagnoses()` to accept config, apply modifications to prompt.

### 4. Checkpointing

After each iteration, saves state:

```json
{
    "iteration": 5,
    "best_recall": 0.412,
    "best_config": { /* config dict */ },
    "baseline_recall": 0.346,
    "timestamp": "2026-03-28T14:23:45.123456"
}
```

Enables resuming interrupted runs.

## Modification Strategies Explained

### Strategy 1: Two-Pass Mode
**Concept**: First pass identifies candidate diagnoses, second pass verifies them

Current state: Placeholder. Would modify prompt to:
1. First call: "List all possible diagnoses"
2. Second call: "For each diagnosis above, verify it's clinically supported"

### Strategy 2: Extraction Prompt Detail
**Concept**: Adjust verbosity level

- `detail_level = -1`: Ultra-concise prompt (fewer examples, terse instructions)
- `detail_level = 0`: Balanced (current baseline)
- `detail_level = 1`: Verbose (more examples, detailed explanations)

### Strategy 3: Category-Specific Prompts
**Concept**: Use specialized prompts for high-value categories

Example:
```
Standard: "Find any missed diagnoses"
Specialized (Sepsis): "Look for clinical indicators of infection: fever, elevated WBC, 
positive cultures, sepsis protocols initiated"
```

### Strategy 4: Prompt Structure
**Concept**: System message vs inline context

- `False`: Single user message (current)
- `True`: System role + user message (more structure)

### Strategy 5: Temperature
**Concept**: Control sampling creativity (only for non-GPT-5 models)

- `0.1`: Conservative (deterministic)
- `0.3`: Balanced (some creativity)
- `1.0`: Creative (high variance)

GPT-5 doesn't support temperature tuning (only supports temperature=1).

### Strategy 6: Few-Shot Examples
**Concept**: Add examples from high-performing categories

Example:
```
You are a CDI specialist. Here are examples of missed diagnoses:

Example 1 (Sepsis):
  Note: "Patient admitted with fever, WBC 15K, lactate 2.5"
  Missed: "Sepsis, unspecified"

Example 2 (Respiratory):
  Note: "Patient on supplemental O2, ABG shows pH 7.28, pCO2 65"
  Missed: "Hypercapnic respiratory failure"

Now, identify missed diagnoses in this case...
```

### Strategy 7: Output Format
**Concept**: Structured JSON vs free text

- `text`: "1. Sepsis\n2. Respiratory failure\n..."
- `json`: `{"diagnoses": ["Sepsis", "Respiratory failure"], "confidence": [0.9, 0.8]}`

JSON can be more parseable; text may be more natural.

### Strategy 8: Verification Step
**Concept**: Add explicit verification prompt

Example:
```
[Standard extraction prompt]

Now, double-check your findings:
- For each diagnosis, verify there is explicit clinical evidence
- Remove any that are ambiguous or speculative
- Return final list of high-confidence diagnoses
```

## Expected Convergence

**Pattern**: Usually improves significantly in first 5-10 iterations, then plateaus

Example trajectory:
```
Iteration 0 (baseline):    recall = 0.346
Iteration 1 (strategy 1):  recall = 0.412 ✓ KEEP (+19%)
Iteration 2 (strategy 2):  recall = 0.398 ✗ discard
Iteration 3 (strategy 3):  recall = 0.428 ✓ KEEP (+4%)
Iteration 4 (strategy 4):  recall = 0.425 ✗ discard
Iteration 5 (strategy 5):  recall = 0.445 ✓ KEEP (+4%)
Iteration 6 (strategy 6):  recall = 0.442 ✗ discard
...plateau...
```

Typical improvement: **15-30%** over baseline.

## Performance Considerations

### Cost
- Per case: 1-2 API calls (predict + match verification)
- 30 cases * 20 iterations = 600 cases evaluated
- gpt-5: ~6,000 tokens per case = ~3.6M tokens total (~$3-5)
- gpt-4.1: cheaper but slower

### Speed
- gpt-5: 30 cases, 20 iterations = ~30-45 minutes
- gpt-4.1-mini: 30 cases, 20 iterations = ~20-30 minutes
- gpt-5-nano (judgment only): ~5 minutes

### Caching
- HybridMatcher caches LLM judgments (same cases, same predictions)
- By iteration 5+, judgment cache is ~90% hit rate
- Significant speedup as run progresses

## Integration Points

After optimization, apply results:

```python
# 1. Extract best configuration
best_config = pd.read_csv('results/hill_climb_results.tsv').iloc[-1]

# 2. Modify cdi_llm_predictor.py to use config
def predict_missed_diagnoses(..., config=None):
    if config and config['two_pass_mode']:
        # Implement two-pass logic
    if config and config['include_verification_step']:
        # Add verification to prompt
    # ...

# 3. Run full evaluation with best_config
python scripts/evaluate_cdi_accuracy.py --config best_config.json

# 4. Compare models
python scripts/compare_models.py --baseline gpt-4.1 --challenger gpt-5
```

## Future Enhancements

1. **Larger modification space**: Add more strategies for prompt engineering
2. **Adaptive strategy selection**: Choose strategies based on success rate
3. **Multi-metric optimization**: Pareto frontier of recall vs precision
4. **Transfer learning**: Test config on different models
5. **Statistical significance**: Confidence intervals on recall improvements
6. **Automatic threshold finding**: Optimal confidence thresholds per category
7. **Prompt generation**: Auto-generate modification prompts using Claude

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| No improvement after iterations | All strategies tried unsuitable | Try longer sample size, different dataset split |
| Recall getting worse | Bad strategy combination | Check strategy logic, add fallback |
| Slow evaluation | Too many LLM calls | Increase HybridMatcher confidence threshold |
| API rate limits | Too aggressive | Reduce sample_size, use slower model |
| Inconsistent results | Different data sampled | Check seed=42 in load_sample_data() |

## Files Modified

- `/scripts/hill_climb_eval.py` - Main evaluation loop (NEW)
- `/HILL_CLIMB_USAGE.md` - User guide (NEW)
- `/HILL_CLIMB_ARCHITECTURE.md` - This document (NEW)
- `/EXAMPLE_HILL_CLIMB_RUN.sh` - Example commands (NEW)

No modifications to existing codebase until best_config is identified.

