# Hill-Climbing Evaluation Loop for CDI Predictor

## Overview

`hill_climb_eval.py` implements a Karpathy autoresearch-inspired hill-climbing optimization loop that autonomously iterates on CDI predictor configurations, keeping improvements and discarding degradations.

## Key Concept

The script optimizes a single metric: **recall** (% of true diagnoses matched). For each iteration:

1. **Apply** a modification strategy to the current best configuration
2. **Evaluate** the modified config on a fixed sample (ensures fair comparison)
3. **Keep** if recall improves; **Discard** if it degrades
4. **Move** to next strategy and repeat

This binary decision-making (improve = keep, degrade = discard) ensures steady progress toward better performance.

## Usage

### Basic Usage (with default settings)

```bash
python scripts/hill_climb_eval.py --api-key YOUR_STANFORD_API_KEY
```

### With Custom Parameters

```bash
# Test run with smaller sample
python scripts/hill_climb_eval.py \
  --api-key YOUR_KEY \
  --model gpt-4.1 \
  --sample-size 10 \
  --max-iterations 5

# Production run with larger sample
python scripts/hill_climb_eval.py \
  --api-key YOUR_KEY \
  --model gpt-5 \
  --sample-size 50 \
  --max-iterations 30
```

### Command-Line Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--api-key` | (required) | Stanford PHI-safe API key |
| `--model` | `gpt-5` | Model: `gpt-5`, `gpt-4.1`, `gpt-5-nano`, `gpt-4.1-mini` |
| `--data` | `data/cdi_3notes_cleaned_confirmed_only.csv` | Path to evaluation dataset |
| `--sample-size` | 30 | Number of cases to sample for evaluation |
| `--max-iterations` | 20 | Maximum hill-climbing iterations |
| `--results-dir` | `results` | Directory to save results |

## Modification Strategies

The script cycles through 8 optimization strategies:

1. **Two-Pass Mode** - Enable/disable two-pass extraction (identify, then verify)
2. **Extraction Prompt Detail** - Adjust prompt verbosity (concise vs detailed)
3. **Category-Specific Prompts** - Add/remove specialized prompts for high-value diagnoses
4. **Prompt Structure** - Change between system message vs inline context
5. **Temperature** - Adjust sampling temperature (0.1=conservative, 0.3=creative)
6. **Few-Shot Examples** - Add examples from high-performing categories (sepsis, respiratory)
7. **Output Format** - Change output format (JSON vs free text)
8. **Verification Step** - Add double-checking step to prompt

Each strategy is applied, evaluated, and kept or discarded based on recall improvement.

## Output Files

### Results TSV (`results/hill_climb_results.tsv`)

Tab-separated values with columns:

- `iteration` - Iteration number (0=baseline)
- `recall` - Recall score (0.0-1.0)
- `precision` - Precision score (0.0-1.0)
- `matched` - Number of matched diagnoses
- `status` - `baseline`, `keep`, or `discard`
- `description` - Strategy name/description
- `timestamp` - ISO 8601 timestamp

Example:
```
iteration	recall		precision	matched	status		description	timestamp
0		0.346		0.0		0	baseline	Baseline v1 monolithic prompt	2026-03-28T...
1		0.412		0.0		0	keep		Two-pass extraction enabled	2026-03-28T...
2		0.398		0.0		0	discard		Added few-shot examples	2026-03-28T...
```

### Checkpoint JSON (`results/hill_climb_checkpoint.json`)

Resumable checkpoint with:

- `iteration` - Current iteration number
- `best_recall` - Best recall achieved so far
- `best_config` - Best configuration found
- `baseline_recall` - Baseline recall for comparison
- `timestamp` - When checkpoint was saved

## How It Works

### Evaluation Loop

```
Load dataset (CSV)
  ↓
Sample 30 cases (fixed seed for reproducibility)
  ↓
Extract ground truth CDI diagnoses
  ↓
Run baseline evaluation → log results
  ↓
For each iteration (1 to max_iterations):
  ├─ Apply modification strategy
  ├─ Run evaluation on SAME sample
  ├─ If recall improved:
  │  ├─ Keep modification (new best_config)
  │  └─ Log "keep"
  └─ Else:
     ├─ Discard modification
     └─ Log "discard"
     
Log final best config
```

### Matching Logic

Uses hybrid matching from `llm_judge.py`:

1. **Rule-based first** (fast)
   - Exact match
   - Substring match
   - Clinical equivalents (sepsis = septic, CHF = heart failure, etc.)
   - Jaccard word overlap

2. **LLM judge for uncertain cases** (accurate)
   - Uses gpt-5-nano for semantic matching
   - Cached for efficiency

### Fair Comparison

**Critical**: The script uses the **SAME 30 cases** across all iterations. This ensures:

- Metrics are directly comparable
- Improvements are real, not due to different samples
- Random variation is controlled

Sample is selected with `random.seed(42)` for reproducibility.

## Tips for Running

### Quick Testing
```bash
# Test on 10 cases, 5 iterations (takes ~30 minutes)
python scripts/hill_climb_eval.py \
  --api-key YOUR_KEY \
  --model gpt-4.1-mini \
  --sample-size 10 \
  --max-iterations 5
```

### Production Run
```bash
# Full evaluation on 50 cases, 30 iterations (takes ~8 hours)
python scripts/hill_climb_eval.py \
  --api-key YOUR_KEY \
  --model gpt-5 \
  --sample-size 50 \
  --max-iterations 30 &

# Monitor progress in another terminal
tail -f results/hill_climb_results.tsv
```

### Analyzing Results

After run completes:

```python
import pandas as pd

results = pd.read_csv('results/hill_climb_results.tsv', sep='\t')

# Best configuration
best = results[results['status'].isin(['baseline', 'keep'])].iloc[-1]
print(f"Best recall: {best['recall']:.3f}")

# Performance trend
print(results[['iteration', 'recall', 'status']])

# Improvement from baseline
baseline = results[results['status'] == 'baseline'].iloc[0]
improvement = (best['recall'] - baseline['recall']) / baseline['recall'] * 100
print(f"Improvement: {improvement:+.1f}%")
```

## Error Handling

The script handles failures gracefully:

- **API errors**: Retries with exponential backoff
- **Prediction errors**: Logs error and continues to next case
- **Evaluation errors**: Continues to next iteration
- **Checkpointing**: Saves state after each iteration (resumable)

Check error output in terminal; most are expected (rate limits, API key issues).

## Customization

To add new modification strategies, subclass `ModificationStrategy`:

```python
class MyStrategy(ModificationStrategy):
    def __init__(self):
        super().__init__(
            "my_strategy",
            "Description of what this does"
        )
    
    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        # Apply modifications to config
        config['my_parameter'] = True
        return config
    
    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['my_parameter'] = False
        return config

# Add to evaluator.strategies in HillClimbEvaluator.__init__()
```

Then modify `HillClimbEvaluator._predict_with_config()` to actually use the config parameters.

## Expected Performance

- **Baseline recall**: ~35-40% (from CLAUDE.md: current 58.6% overall, but dataset may differ)
- **Target improvement**: +20-30% over baseline with good optimizations
- **Typical runtime**: 20-30 minutes for 30 cases, 20 iterations (with gpt-5)

## Next Steps

After hill-climbing converges:

1. **Analyze results** - Which strategies worked best?
2. **Apply best config** - Update `cdi_llm_predictor.py` with winning modifications
3. **Full evaluation** - Run `evaluate_cdi_accuracy.py` on full dataset with best config
4. **Compare models** - Run against gpt-4.1 baseline with `compare_models.py`
5. **Production** - Deploy improved predictor to web demo

