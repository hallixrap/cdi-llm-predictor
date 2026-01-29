#!/usr/bin/env python3
"""
LLM-as-Judge for CDI Diagnosis Matching

Uses a fast LLM (gpt-5-nano) to determine if a predicted diagnosis
semantically matches a CDI query diagnosis. This provides better
matching than rule-based fuzzy matching for clinical equivalents.

Usage:
    from llm_judge import HybridMatcher, diagnoses_match_llm

    # Simple LLM matching
    is_match, confidence, reasoning = diagnoses_match_llm(
        "Sepsis due to pneumonia",
        "Sepsis, clinically valid",
        api_key
    )

    # Hybrid matcher (rules first, LLM for uncertain cases)
    matcher = HybridMatcher(api_key)
    is_match, confidence = matcher.match(pred_dx, true_dx)
"""

import os
import sys
import json
import re
from typing import Tuple, Dict, Optional
from functools import lru_cache

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdi_llm_predictor import call_stanford_llm


# Prompt template for LLM judge
JUDGE_PROMPT = """You are a Clinical Documentation Integrity (CDI) specialist evaluating if two diagnoses match semantically.

CDI Query Diagnosis (Ground Truth): {true_diagnosis}
LLM Predicted Diagnosis: {pred_diagnosis}

Determine if these represent the SAME clinical condition, considering:

1. **Clinical Equivalence**: Different terms for the same condition
   - "sepsis" = "sepsis, clinically valid" = "sepsis present on admission"
   - "pressure ulcer" = "pressure injury" = "decubitus ulcer"
   - "CHF" = "heart failure" = "congestive heart failure"

2. **Specificity Relationships**: More specific matches general
   - "Sepsis due to pneumonia" MATCHES "Sepsis"
   - "Stage 3 pressure ulcer of sacrum" MATCHES "Pressure ulcer"
   - "Acute on chronic diastolic heart failure" MATCHES "CHF"

3. **Category Matching**: Same diagnosis category
   - "Type 2 MI due to demand ischemia" MATCHES "Demand ischemia"
   - "Cardiogenic shock" and "Septic shock" do NOT match (different etiology)

4. **Ruled Out / Confirmed Status**:
   - "Sepsis, confirmed" MATCHES "Sepsis"
   - "Sepsis, ruled out" does NOT match "Sepsis" (opposite meaning!)

Return a JSON response:
{{
    "match": true/false,
    "confidence": "high/medium/low",
    "reasoning": "Brief explanation (1-2 sentences)"
}}

Important: Be GENEROUS with matching for clinical equivalents, but STRICT about opposite meanings (ruled out vs confirmed).
"""


def diagnoses_match_llm(
    pred_dx: str,
    true_dx: str,
    api_key: str,
    model: str = "gpt-5-nano",
    verbose: bool = False
) -> Tuple[bool, float, str]:
    """
    Use LLM to determine if diagnoses match semantically.

    Args:
        pred_dx: Predicted diagnosis from LLM
        true_dx: True diagnosis from CDI query
        api_key: Stanford API key
        model: Model to use for judging (default: gpt-5-nano for speed/cost)
        verbose: Print debug info

    Returns:
        Tuple of (is_match, confidence_score, reasoning)
        - is_match: True if diagnoses match
        - confidence_score: 0.0-1.0 confidence in the match decision
        - reasoning: Brief explanation
    """
    prompt = JUDGE_PROMPT.format(
        true_diagnosis=true_dx,
        pred_diagnosis=pred_dx
    )

    try:
        response = call_stanford_llm(prompt, api_key, model=model)

        # Parse JSON response
        # Try to extract JSON from response (may be wrapped in markdown)
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
        else:
            result = json.loads(response)

        is_match = result.get("match", False)
        confidence_str = result.get("confidence", "low")
        reasoning = result.get("reasoning", "No reasoning provided")

        # Convert confidence string to score
        confidence_map = {
            "high": 0.95,
            "medium": 0.75,
            "low": 0.55
        }
        confidence_score = confidence_map.get(confidence_str.lower(), 0.5)

        if verbose:
            print(f"  LLM Judge: {pred_dx[:50]} vs {true_dx[:50]}")
            print(f"    Match: {is_match}, Confidence: {confidence_str}")
            print(f"    Reason: {reasoning}")

        return (is_match, confidence_score, reasoning)

    except json.JSONDecodeError as e:
        if verbose:
            print(f"  LLM Judge JSON error: {e}")
        # Fallback to conservative no-match
        return (False, 0.3, f"JSON parse error: {str(e)}")

    except Exception as e:
        if verbose:
            print(f"  LLM Judge error: {e}")
        # Fallback to rule-based matching
        return (False, 0.3, f"LLM error, fallback: {str(e)}")


class HybridMatcher:
    """
    Hybrid matcher that combines rule-based and LLM-based matching.

    First tries fast rule-based matching. If uncertain (partial overlap),
    uses LLM to make the final decision.
    """

    def __init__(
        self,
        api_key: str,
        llm_model: str = "gpt-5-nano",
        uncertainty_threshold: float = 0.3,
        cache_enabled: bool = True
    ):
        """
        Initialize hybrid matcher.

        Args:
            api_key: Stanford API key for LLM calls
            llm_model: Model to use for LLM judging
            uncertainty_threshold: Word overlap threshold to trigger LLM
            cache_enabled: Whether to cache LLM judgments
        """
        self.api_key = api_key
        self.llm_model = llm_model
        self.uncertainty_threshold = uncertainty_threshold
        self.cache_enabled = cache_enabled
        self._cache: Dict[str, Tuple[bool, float, str]] = {}
        self._stats = {
            "rule_matches": 0,
            "rule_non_matches": 0,
            "llm_calls": 0,
            "cache_hits": 0
        }

    def _normalize(self, dx: str) -> str:
        """Normalize diagnosis for comparison"""
        dx = dx.lower().strip()
        dx = re.sub(r'\s+', ' ', dx)
        dx = re.sub(r'[^\w\s]', '', dx)
        return dx

    def _rule_based_match(self, pred_dx: str, true_dx: str) -> Tuple[Optional[bool], float]:
        """
        Try rule-based matching first.

        Returns:
            (match_result, confidence)
            - match_result: True/False if confident, None if uncertain
            - confidence: Confidence in the result
        """
        pred_norm = self._normalize(pred_dx)
        true_norm = self._normalize(true_dx)

        # Exact match
        if pred_norm == true_norm:
            return (True, 0.99)

        # Substring match
        if pred_norm in true_norm or true_norm in pred_norm:
            return (True, 0.9)

        # Clinical equivalents (high confidence matches)
        clinical_equivalents = {
            'sepsis': ['sepsis', 'septic', 'urosepsis', 'septicemia'],
            'pressure ulcer': ['pressure ulcer', 'pressure injury', 'decubitus', 'pressure sore', 'bed sore'],
            'malnutrition': ['malnutrition', 'protein calorie malnutrition', 'cachexia', 'underweight'],
            'heart failure': ['heart failure', 'chf', 'congestive heart failure', 'hfref', 'hfpef'],
            'respiratory failure': ['respiratory failure', 'hypoxic respiratory failure', 'hypoxia'],
            'anemia': ['anemia', 'blood loss anemia', 'iron deficiency anemia'],
            'acute kidney injury': ['acute kidney injury', 'aki', 'acute renal failure', 'acute renal insufficiency'],
            'demand ischemia': ['demand ischemia', 'type 2 mi', 'type 2 myocardial infarction', 'nstemi type 2'],
            'encephalopathy': ['encephalopathy', 'metabolic encephalopathy', 'hepatic encephalopathy', 'delirium'],
            'pulmonary edema': ['pulmonary edema', 'flash pulmonary edema', 'cardiogenic pulmonary edema'],
            'thrombocytopenia': ['thrombocytopenia', 'low platelets', 'pancytopenia'],
            'hypoalbuminemia': ['hypoalbuminemia', 'low albumin'],
        }

        for base_term, equivalents in clinical_equivalents.items():
            pred_has = any(eq in pred_norm for eq in equivalents)
            true_has = any(eq in true_norm for eq in equivalents)
            if pred_has and true_has:
                return (True, 0.85)

        # Check for "ruled out" - this is a definite NON-match
        if 'ruled out' in true_norm and 'ruled out' not in pred_norm:
            return (False, 0.95)
        if 'ruled out' in pred_norm and 'ruled out' not in true_norm:
            return (False, 0.95)

        # Word overlap analysis
        stop_words = {'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to', 'of', 'in', 'on',
                      'confirmed', 'present', 'admission', 'poa', 'acute', 'chronic', 'clinically', 'valid'}
        pred_words = set(pred_norm.split()) - stop_words
        true_words = set(true_norm.split()) - stop_words

        if not pred_words or not true_words:
            return (False, 0.8)

        overlap = len(pred_words & true_words)
        union = len(pred_words | true_words)
        jaccard = overlap / union if union > 0 else 0

        # High overlap = likely match
        if jaccard >= 0.7:
            return (True, 0.75)

        # For CDI matching, we want to be generous - let LLM judge most cases
        # Only skip LLM if there's truly zero semantic overlap
        # (The rules above already caught exact matches and clinical equivalents)

        # If there's ANY word overlap, let LLM decide
        if overlap > 0:
            return (None, jaccard)

        # Zero word overlap - but diagnoses might still be related
        # (e.g., different terminology for same condition)
        # Only return False if diagnoses are from clearly different categories
        # For now, let LLM judge even zero-overlap cases to be thorough
        return (None, 0.1)  # Low confidence, but let LLM decide

    def match(
        self,
        pred_dx: str,
        true_dx: str,
        verbose: bool = False
    ) -> Tuple[bool, float]:
        """
        Check if predicted diagnosis matches true diagnosis.

        Args:
            pred_dx: Predicted diagnosis
            true_dx: True diagnosis from CDI query
            verbose: Print debug info

        Returns:
            (is_match, confidence_score)
        """
        # Try rule-based first
        rule_result, rule_confidence = self._rule_based_match(pred_dx, true_dx)

        if rule_result is not None:
            if rule_result:
                self._stats["rule_matches"] += 1
            else:
                self._stats["rule_non_matches"] += 1
            return (rule_result, rule_confidence)

        # Need LLM for uncertain cases
        cache_key = f"{self._normalize(pred_dx)}|{self._normalize(true_dx)}"

        if self.cache_enabled and cache_key in self._cache:
            self._stats["cache_hits"] += 1
            is_match, confidence, _ = self._cache[cache_key]
            return (is_match, confidence)

        # Call LLM
        self._stats["llm_calls"] += 1
        is_match, confidence, reasoning = diagnoses_match_llm(
            pred_dx, true_dx, self.api_key,
            model=self.llm_model, verbose=verbose
        )

        # Cache result
        if self.cache_enabled:
            self._cache[cache_key] = (is_match, confidence, reasoning)

        return (is_match, confidence)

    def get_stats(self) -> Dict:
        """Get matching statistics"""
        total = (self._stats["rule_matches"] + self._stats["rule_non_matches"] +
                 self._stats["llm_calls"])
        return {
            **self._stats,
            "total_comparisons": total,
            "llm_call_rate": self._stats["llm_calls"] / total if total > 0 else 0,
            "cache_hit_rate": self._stats["cache_hits"] / self._stats["llm_calls"]
                if self._stats["llm_calls"] > 0 else 0
        }

    def clear_cache(self):
        """Clear the judgment cache"""
        self._cache.clear()


def test_matcher():
    """Test the hybrid matcher with example cases"""
    print("="*60)
    print("LLM Judge Test Cases")
    print("="*60)

    test_cases = [
        # Should match
        ("Sepsis due to pneumonia", "Sepsis, clinically valid", True),
        ("Pressure ulcer, Stage 3, Sacral", "Pressure injury", True),
        ("Acute on Chronic Diastolic Heart Failure", "CHF", True),
        ("Type 2 NSTEMI due to demand ischemia", "Demand Ischemia", True),
        ("Severe Protein-Calorie Malnutrition", "Malnutrition, Severe", True),

        # Should NOT match
        ("Sepsis", "Sepsis, ruled out", False),
        ("Cardiogenic Shock", "Septic Shock", False),
        ("Respiratory Failure", "Heart Failure", False),
        ("Acute Blood Loss Anemia", "Iron Deficiency Anemia", False),  # Different types
    ]

    for pred, true, expected in test_cases:
        # Use rule-based only for test (no API key needed)
        matcher = HybridMatcher(api_key="", cache_enabled=False)
        result, confidence = matcher._rule_based_match(pred, true)

        if result is None:
            status = "UNCERTAIN (needs LLM)"
        elif result == expected:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"

        print(f"\n{status}")
        print(f"  Pred: {pred}")
        print(f"  True: {true}")
        print(f"  Expected: {expected}, Got: {result}, Conf: {confidence:.2f}")

    print("\n" + "="*60)


if __name__ == "__main__":
    test_matcher()
