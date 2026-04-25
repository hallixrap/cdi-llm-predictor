#!/bin/bash
# Example script showing how to run the hill-climbing evaluator
# This is NOT meant to be run without a valid API key

# Example 1: Quick test run (10 cases, 5 iterations)
# Estimated runtime: 15-20 minutes with gpt-4.1-mini
echo "Example 1: Quick test run"
echo "python scripts/hill_climb_eval.py \\"
echo "  --api-key YOUR_STANFORD_API_KEY \\"
echo "  --model gpt-4.1-mini \\"
echo "  --sample-size 10 \\"
echo "  --max-iterations 5"
echo ""

# Example 2: Standard run (30 cases, 20 iterations)
# Estimated runtime: 30-45 minutes with gpt-5
echo "Example 2: Standard run (recommended for first time)"
echo "python scripts/hill_climb_eval.py \\"
echo "  --api-key YOUR_STANFORD_API_KEY \\"
echo "  --model gpt-5 \\"
echo "  --sample-size 30 \\"
echo "  --max-iterations 20"
echo ""

# Example 3: Full production run (50 cases, 30 iterations)
# Estimated runtime: 1.5-2 hours with gpt-5
echo "Example 3: Production run (longer, more comprehensive)"
echo "python scripts/hill_climb_eval.py \\"
echo "  --api-key YOUR_STANFORD_API_KEY \\"
echo "  --model gpt-5 \\"
echo "  --sample-size 50 \\"
echo "  --max-iterations 30 \\"
echo "  --results-dir results/hill_climb_prod"
echo ""

# Example 4: Background run with output redirection
echo "Example 4: Run in background with logging"
echo "nohup python scripts/hill_climb_eval.py \\"
echo "  --api-key YOUR_STANFORD_API_KEY \\"
echo "  --model gpt-5 \\"
echo "  --sample-size 50 \\"
echo "  --max-iterations 30 \\"
echo "  > hill_climb.log 2>&1 &"
echo ""
echo "# Then monitor with:"
echo "tail -f hill_climb.log"
echo "# or check results:"
echo "tail -f results/hill_climb_results.tsv"
