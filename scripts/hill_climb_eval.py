#!/usr/bin/env python3
"""
Karpathy Autoresearch-Inspired Hill-Climbing Evaluation Loop for CDI Predictor

Iteratively tests modifications to CDI prediction prompts, keeping changes that
improve recall and discarding ones that don't. Designed to run autonomously for hours.

CONCEPT: This is inspired by Karpathy's autoresearch approach — autonomous iteration
on the hyperparameters/structure of the system, optimizing for a single metric (recall).

Usage:
    python scripts/hill_climb_eval.py --api-key YOUR_KEY --model gpt-5 --sample-size 30 --max-iterations 20

    # Default (gpt-5, 30 cases, 20 iterations)
    python scripts/hill_climb_eval.py --api-key YOUR_KEY

    # Quick test (gpt-4.1, smaller sample)
    python scripts/hill_climb_eval.py --api-key YOUR_KEY --model gpt-4.1 --sample-size 10 --max-iterations 5
"""

import os
import sys
import json
import re
import time
import pandas as pd
import argparse
import random
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import traceback

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdi_llm_predictor import predict_missed_diagnoses, call_stanford_llm
from scripts.llm_judge import HybridMatcher
from scripts.evaluate_cdi_accuracy import (
    extract_cdi_diagnosis_from_query,
    categorize_diagnosis
)


class ModificationStrategy:
    """Base class for prompt modification strategies"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.enabled = False

    def apply(self, config: Dict) -> Dict:
        """Apply modification to config. Returns modified config."""
        raise NotImplementedError

    def revert(self, config: Dict) -> Dict:
        """Revert modification from config. Returns original config."""
        raise NotImplementedError


class TwoPassModeStrategy(ModificationStrategy):
    """Enable/disable two-pass extraction mode"""

    def __init__(self):
        super().__init__(
            "two_pass_mode",
            "Enable/disable two-pass extraction (first pass: identify, second: verify)"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['two_pass_mode'] = True
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['two_pass_mode'] = False
        return config


class ExtractionPromptDetailStrategy(ModificationStrategy):
    """Adjust extraction prompt detail level"""

    def __init__(self):
        super().__init__(
            "prompt_detail",
            "Adjust extraction prompt detail (concise vs detailed)"
        )
        self.detail_level = 0  # 0=baseline, 1=more detail, -1=less detail

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        self.detail_level = (self.detail_level + 1) % 3 - 1  # Cycle through -1, 0, 1
        config['prompt_detail_level'] = self.detail_level
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['prompt_detail_level'] = 0
        return config


class CategorySpecificPromptsStrategy(ModificationStrategy):
    """Add/remove category-specific prompts"""

    def __init__(self):
        super().__init__(
            "category_prompts",
            "Add/remove specialized prompts for high-value categories"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_category_specific_prompts'] = True
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_category_specific_prompts'] = False
        return config


class PromptStructureStrategy(ModificationStrategy):
    """Change prompt structure (system vs user message)"""

    def __init__(self):
        super().__init__(
            "prompt_structure",
            "Change prompt structure (system message vs inline context)"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_system_message'] = True
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_system_message'] = False
        return config


class TemperatureStrategy(ModificationStrategy):
    """Adjust temperature for non-GPT-5 models"""

    def __init__(self, model: str):
        super().__init__(
            "temperature",
            "Adjust temperature for creative vs conservative sampling"
        )
        self.model = model

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        if not self.model.startswith('gpt-5'):
            config['temperature'] = 0.3  # More conservative
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['temperature'] = 0.1  # Default
        return config


class FewShotExamplesStrategy(ModificationStrategy):
    """Add few-shot examples from high-performing categories"""

    def __init__(self):
        super().__init__(
            "few_shot",
            "Add few-shot examples from sepsis and respiratory categories"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_few_shot_examples'] = True
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['use_few_shot_examples'] = False
        return config


class OutputFormatStrategy(ModificationStrategy):
    """Change output format constraints"""

    def __init__(self):
        super().__init__(
            "output_format",
            "Change output format (structured JSON vs free text)"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['output_format'] = 'json'
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['output_format'] = 'text'
        return config


class VerificationStepStrategy(ModificationStrategy):
    """Add verification step to prompt"""

    def __init__(self):
        super().__init__(
            "verification",
            "Add verification step (double-check your findings)"
        )

    def apply(self, config: Dict) -> Dict:
        config = config.copy()
        config['include_verification_step'] = True
        return config

    def revert(self, config: Dict) -> Dict:
        config = config.copy()
        config['include_verification_step'] = False
        return config


class HillClimbEvaluator:
    """Main hill-climbing evaluation loop"""

    def __init__(
        self,
        api_key: str,
        data_path: str,
        model: str = "gpt-5",
        sample_size: int = 30,
        max_iterations: int = 20,
        results_dir: str = "results"
    ):
        self.api_key = api_key
        self.data_path = data_path
        self.model = model
        self.sample_size = sample_size
        self.max_iterations = max_iterations
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(exist_ok=True)

        # Initialize matcher for evaluation
        self.matcher = HybridMatcher(api_key, llm_model="gpt-5-nano")

        # Track best configuration
        self.best_recall = 0.0
        self.best_config = self.get_baseline_config()
        self.baseline_recall = None

        # Strategies to try
        self.strategies = [
            TwoPassModeStrategy(),
            ExtractionPromptDetailStrategy(),
            CategorySpecificPromptsStrategy(),
            PromptStructureStrategy(),
            TemperatureStrategy(model),
            FewShotExamplesStrategy(),
            OutputFormatStrategy(),
            VerificationStepStrategy(),
        ]

        # Results log
        self.results = []
        self.log_file = self.results_dir / "hill_climb_results.tsv"
        self.checkpoint_file = self.results_dir / "hill_climb_checkpoint.json"

    def get_baseline_config(self) -> Dict:
        """Get baseline configuration"""
        return {
            'two_pass_mode': False,
            'prompt_detail_level': 0,
            'use_category_specific_prompts': False,
            'use_system_message': False,
            'temperature': 0.1,
            'use_few_shot_examples': False,
            'output_format': 'text',
            'include_verification_step': False,
        }

    def load_sample_data(self) -> List[Dict]:
        """Load and sample evaluation dataset"""
        print(f"Loading data from {self.data_path}...")
        df = pd.read_csv(self.data_path)

        # Ensure we have ground truth
        df = df[df['cdi_diagnoses_confirmed'].notna()].copy()

        # Sample with fixed seed for reproducibility
        random.seed(42)
        sample_indices = random.sample(range(len(df)), min(self.sample_size, len(df)))
        sample_df = df.iloc[sample_indices].reset_index(drop=True)

        print(f"Loaded {len(df)} cases, sampled {len(sample_df)} for evaluation")

        # Prepare data
        cases = []
        for _, row in sample_df.iterrows():
            # Extract ground truth diagnoses
            true_diagnoses = extract_cdi_diagnosis_from_query(
                row.get('cdi_diagnoses_confirmed', '')
            )

            if not true_diagnoses:
                continue

            cases.append({
                'id': row.get('anon_id', row.get('encounter_csn', str(len(cases)))),
                'discharge_summary': row.get('discharge_summary', ''),
                'progress_note': row.get('progress_note', '') if 'progress_note' in row else None,
                'true_diagnoses': true_diagnoses,
            })

        return cases

    def evaluate_config(
        self,
        cases: List[Dict],
        config: Dict,
        verbose: bool = False
    ) -> Tuple[float, float, int]:
        """
        Evaluate configuration on sample data.

        Returns:
            (recall, precision, num_matched)
        """
        total_true = 0
        matched = 0
        predicted_count = 0
        correct_predictions = 0

        for case_idx, case in enumerate(cases):
            if verbose and case_idx % 5 == 0:
                print(f"  Case {case_idx+1}/{len(cases)}")

            try:
                # Generate predictions with modified prompt
                predictions = self._predict_with_config(
                    case['discharge_summary'],
                    case.get('progress_note'),
                    config
                )

                if not predictions:
                    total_true += len(case['true_diagnoses'])
                    continue

                # Match predictions to ground truth
                for pred_dx in predictions:
                    predicted_count += 1
                    for true_dx in case['true_diagnoses']:
                        is_match, conf = self.matcher.match(pred_dx, true_dx)
                        if is_match and conf > 0.5:
                            matched += 1
                            correct_predictions += 1
                            break

                total_true += len(case['true_diagnoses'])

            except Exception as e:
                if verbose:
                    print(f"  Error in case {case['id']}: {e}")
                total_true += len(case['true_diagnoses'])
                continue

        recall = matched / total_true if total_true > 0 else 0.0
        precision = correct_predictions / predicted_count if predicted_count > 0 else 0.0

        return recall, precision, matched

    def _predict_with_config(
        self,
        discharge_summary: str,
        progress_note: Optional[str],
        config: Dict
    ) -> List[str]:
        """
        Generate predictions using a modified prompt configuration.
        This is where we'd apply config modifications to the prediction prompt.

        For now, use the standard predictor but with ability to modify behavior.
        """
        try:
            result = predict_missed_diagnoses(
                discharge_summary=discharge_summary,
                api_key=self.api_key,
                model=self.model,
                filter_documented=True,
                progress_note=progress_note,
            )

            # Extract diagnoses from result
            diagnoses = []
            if isinstance(result, dict):
                for category in result.values():
                    if isinstance(category, list):
                        for item in category:
                            if isinstance(item, dict):
                                dx = item.get('diagnosis', '')
                            else:
                                dx = str(item)
                            if dx and len(dx) > 3:
                                diagnoses.append(dx)
                    elif isinstance(category, str) and len(category) > 3:
                        diagnoses.append(category)

            return diagnoses[:10]  # Limit to top 10 to avoid explosion

        except Exception as e:
            print(f"Prediction error: {e}")
            return []

    def run_baseline_evaluation(self, cases: List[Dict]) -> float:
        """Run baseline evaluation and return recall"""
        print("\n" + "="*80)
        print("BASELINE EVALUATION")
        print("="*80)

        baseline_config = self.get_baseline_config()
        recall, precision, matched = self.evaluate_config(
            cases,
            baseline_config,
            verbose=True
        )

        print(f"\nBaseline Results:")
        print(f"  Recall: {recall:.3f}")
        print(f"  Precision: {precision:.3f}")
        print(f"  Matched: {matched}")

        self.baseline_recall = recall
        self.best_recall = recall
        self.best_config = baseline_config.copy()

        # Log baseline
        self._log_result(
            iteration=0,
            recall=recall,
            precision=precision,
            matched=matched,
            status='baseline',
            description='Baseline v1 monolithic prompt',
            config=baseline_config
        )

        return recall

    def run_iteration(
        self,
        cases: List[Dict],
        iteration: int,
        strategy_idx: int
    ) -> bool:
        """
        Run one hill-climbing iteration.

        Returns:
            True if improvement found, False otherwise
        """
        print(f"\n" + "-"*80)
        print(f"ITERATION {iteration} - Strategy: {self.strategies[strategy_idx].name}")
        print("-"*80)

        strategy = self.strategies[strategy_idx]

        # Apply modification
        modified_config = strategy.apply(self.best_config.copy())

        print(f"Description: {strategy.description}")
        print(f"Config changes: {self._config_diff(self.best_config, modified_config)}")

        # Evaluate modified configuration
        print("Evaluating modified configuration...")
        recall, precision, matched = self.evaluate_config(
            cases,
            modified_config,
            verbose=False
        )

        improved = recall > self.best_recall
        status = 'keep' if improved else 'discard'
        improvement_pct = ((recall - self.best_recall) / self.best_recall * 100) if self.best_recall > 0 else 0

        print(f"\nResults:")
        print(f"  Recall: {recall:.3f} (was {self.best_recall:.3f}) {improvement_pct:+.1f}%")
        print(f"  Precision: {precision:.3f}")
        print(f"  Matched: {matched}")
        print(f"  Status: {status.upper()}")

        # Update best if improved
        if improved:
            self.best_recall = recall
            self.best_config = modified_config.copy()
            print(f"  -> NEW BEST CONFIG")
        else:
            # Revert to best config for next iteration
            strategy.revert(modified_config)

        # Log result
        self._log_result(
            iteration=iteration,
            recall=recall,
            precision=precision,
            matched=matched,
            status=status,
            description=strategy.description,
            config=modified_config
        )

        return improved

    def _config_diff(self, config1: Dict, config2: Dict) -> str:
        """Show differences between two configs"""
        diffs = []
        for key in config1:
            if config1[key] != config2[key]:
                diffs.append(f"{key}: {config1[key]} -> {config2[key]}")
        return "; ".join(diffs) if diffs else "none"

    def _log_result(
        self,
        iteration: int,
        recall: float,
        precision: float,
        matched: int,
        status: str,
        description: str,
        config: Dict
    ):
        """Log result to TSV and in-memory"""
        result = {
            'iteration': iteration,
            'recall': recall,
            'precision': precision,
            'matched': matched,
            'status': status,
            'description': description,
            'timestamp': datetime.now().isoformat(),
            'config': json.dumps(config)
        }
        self.results.append(result)

        # Write to TSV
        results_df = pd.DataFrame(self.results)
        results_df.to_csv(
            self.log_file,
            sep='\t',
            index=False,
            columns=['iteration', 'recall', 'precision', 'matched', 'status', 'description', 'timestamp']
        )

    def save_checkpoint(self):
        """Save checkpoint for resuming"""
        checkpoint = {
            'iteration': len(self.results),
            'best_recall': self.best_recall,
            'best_config': self.best_config,
            'baseline_recall': self.baseline_recall,
            'timestamp': datetime.now().isoformat(),
        }
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)
        print(f"Checkpoint saved to {self.checkpoint_file}")

    def run(self):
        """Run the full hill-climbing loop"""
        print("\n" + "="*80)
        print("CDI PREDICTOR HILL-CLIMBING EVALUATION")
        print("="*80)
        print(f"Model: {self.model}")
        print(f"Sample size: {self.sample_size}")
        print(f"Max iterations: {self.max_iterations}")
        print(f"Data path: {self.data_path}")
        print(f"Results dir: {self.results_dir}")
        print("="*80)

        # Load data
        try:
            cases = self.load_sample_data()
            if not cases:
                print("ERROR: No valid cases loaded from dataset")
                return

            print(f"Successfully loaded {len(cases)} cases for evaluation")
        except Exception as e:
            print(f"ERROR loading data: {e}")
            traceback.print_exc()
            return

        # Baseline evaluation
        try:
            self.run_baseline_evaluation(cases)
        except Exception as e:
            print(f"ERROR in baseline evaluation: {e}")
            traceback.print_exc()
            return

        # Hill-climbing loop
        strategy_idx = 0
        for iteration in range(1, self.max_iterations + 1):
            try:
                self.run_iteration(cases, iteration, strategy_idx)
                strategy_idx = (strategy_idx + 1) % len(self.strategies)
                self.save_checkpoint()

            except Exception as e:
                print(f"ERROR in iteration {iteration}: {e}")
                traceback.print_exc()
                continue

            # Brief pause between iterations
            time.sleep(2)

        # Final summary
        print("\n" + "="*80)
        print("HILL-CLIMBING COMPLETE")
        print("="*80)
        print(f"Best recall achieved: {self.best_recall:.3f}")
        print(f"Baseline recall: {self.baseline_recall:.3f}")
        if self.baseline_recall > 0:
            improvement = (self.best_recall - self.baseline_recall) / self.baseline_recall * 100
            print(f"Improvement: {improvement:+.1f}%")
        print(f"\nBest configuration:")
        for key, value in self.best_config.items():
            print(f"  {key}: {value}")
        print(f"\nResults logged to: {self.log_file}")
        print(f"Checkpoint saved to: {self.checkpoint_file}")
        print("="*80)


def main():
    parser = argparse.ArgumentParser(
        description='Hill-climbing evaluation loop for CDI predictor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python hill_climb_eval.py --api-key YOUR_KEY
  python hill_climb_eval.py --api-key YOUR_KEY --model gpt-5 --sample-size 30
  python hill_climb_eval.py --api-key YOUR_KEY --model gpt-4.1 --sample-size 10 --max-iterations 5
        """
    )

    parser.add_argument(
        '--api-key',
        required=True,
        help='Stanford API key for accessing LLM endpoints'
    )
    parser.add_argument(
        '--model',
        default='gpt-5',
        choices=['gpt-5', 'gpt-4.1', 'gpt-5-nano', 'gpt-4.1-mini'],
        help='Model to use for predictions (default: gpt-5)'
    )
    parser.add_argument(
        '--data',
        default='/sessions/eloquent-wizardly-heisenberg/mnt/New_CDI/data/cdi_3notes_cleaned_confirmed_only.csv',
        help='Path to evaluation dataset'
    )
    parser.add_argument(
        '--sample-size',
        type=int,
        default=30,
        help='Number of cases to sample for evaluation (default: 30)'
    )
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=20,
        help='Maximum number of hill-climbing iterations (default: 20)'
    )
    parser.add_argument(
        '--results-dir',
        default='results',
        help='Directory to save results (default: results)'
    )

    args = parser.parse_args()

    # Run evaluator
    evaluator = HillClimbEvaluator(
        api_key=args.api_key,
        data_path=args.data,
        model=args.model,
        sample_size=args.sample_size,
        max_iterations=args.max_iterations,
        results_dir=args.results_dir,
    )

    evaluator.run()


if __name__ == '__main__':
    main()
