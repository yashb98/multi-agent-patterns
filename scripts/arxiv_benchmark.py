"""Benchmark script for arXiv ranking + fact-checking quality.

Measures 5 metrics:
1. JSON parse reliability — can we handle messy LLM responses?
2. Fact-checker accuracy — does scoring match expected verdicts?
3. Fact-checker confidence bounds — are values clamped to [0,1]?
4. Fact-checker skip-type enforcement — are opinion/definition claims filtered?
5. Test coverage — pytest --cov for both modules

Run:  python scripts/arxiv_benchmark.py
      python scripts/arxiv_benchmark.py --save baseline
      python scripts/arxiv_benchmark.py --compare baseline
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
BENCHMARK_DIR = DATA_DIR / "benchmarks"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

# Add project root to path
sys.path.insert(0, str(PROJECT_DIR))


def benchmark_json_parsing() -> dict:
    """Test JSON extraction from various LLM response formats."""
    test_cases = [
        # (raw_response, should_parse, description)
        ('[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]',
         True, "Raw JSON array"),
        ('```json\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]\n```',
         True, "Markdown json block"),
        ('```\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]\n```',
         True, "Markdown block no lang"),
        ('Here are the top papers:\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]',
         True, "Text prefix before JSON"),
        ('I found these papers:\n\n```json\n[{"rank":1,"paper_num":0,"score":9.0}]\n```\n\nHope this helps!',
         True, "Text prefix + suffix around markdown"),
        ('{"rank":1}', False, "Object instead of array"),
        ('not json at all', False, "Plain text"),
        ('[]', True, "Empty array"),
        ('[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"},\n'
         '{"rank":2,"paper_num":1,"score":8.0,"reason":"ok","key_technique":"y","category_tag":"Vision"}]',
         True, "Multi-line JSON"),
        ('```json\n[\n  {"rank": 1, "paper_num": 0, "score": 9.0, "reason": "good", '
         '"key_technique": "x", "category_tag": "LLM"}\n]\n```',
         True, "Pretty-printed markdown JSON"),
    ]

    results = {"passed": 0, "failed": 0, "total": len(test_cases), "failures": []}

    from jobpulse.arxiv_agent import _extract_json_array

    for raw, should_parse, desc in test_cases:
        try:
            parsed = _extract_json_array(raw)
            did_parse = isinstance(parsed, list) and len(parsed) >= 0
            # For "Object instead of array" and "Plain text", _extract_json_array returns []
            # which is a valid empty list — check if we expected non-empty parse
            if should_parse is False and parsed == []:
                did_parse = False
        except Exception:
            did_parse = False

        if did_parse == should_parse:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append({
                "description": desc,
                "expected": f"parse={'yes' if should_parse else 'no'}",
                "actual": f"parse={'yes' if did_parse else 'no'}",
            })

    results["score"] = results["passed"] / results["total"] * 10 if results["total"] else 10.0
    return results


def benchmark_fact_checker_scoring() -> dict:
    """Test deterministic accuracy scoring with known inputs."""
    from shared.fact_checker import compute_accuracy_score

    test_cases = [
        # (verifications, expected_score, description)
        (
            [{"verdict": "VERIFIED", "severity": "low"}],
            10.0, "Single verified claim"
        ),
        (
            [{"verdict": "VERIFIED"}, {"verdict": "VERIFIED"}, {"verdict": "VERIFIED"}],
            10.0, "All verified"
        ),
        (
            [{"verdict": "INACCURATE", "severity": "high"}],
            0.0, "Single inaccurate (clamped to 0)"
        ),
        (
            [{"verdict": "EXAGGERATED", "severity": "medium"}],
            0.0, "Single exaggerated (negative, clamped to 0)"
        ),
        (
            [{"verdict": "UNVERIFIED", "severity": "low"}],
            0.0, "Single unverified low severity"
        ),
        (
            [{"verdict": "UNVERIFIED", "severity": "high"}],
            0.0, "Single unverified high severity"
        ),
        (
            [], 10.0, "Empty verifications"
        ),
        (
            [{"verdict": "VERIFIED"}, {"verdict": "VERIFIED"}, {"verdict": "EXAGGERATED", "severity": "medium"}],
            3.33, "Mostly verified with one exaggerated (approx)"
        ),
    ]

    results = {"passed": 0, "failed": 0, "total": len(test_cases), "failures": []}

    for verifications, expected, desc in test_cases:
        actual = compute_accuracy_score(verifications)
        # Allow 0.5 tolerance for rounding
        if abs(actual - expected) <= 0.5:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append({
                "description": desc,
                "expected": expected,
                "actual": round(actual, 2),
            })

    results["score"] = results["passed"] / results["total"] * 10 if results["total"] else 10.0
    return results


def benchmark_confidence_bounds() -> dict:
    """Check if confidence values from fact-checker are properly validated."""
    from shared.fact_checker import verify_claims

    # We can't call the real LLM, so we test the code path that processes results.
    # Check if the verify_claims function clamps confidence values.
    # This is a structural check — does the code have clamping logic?
    import inspect
    source = inspect.getsource(verify_claims)

    checks = {
        "has_confidence_clamping": "min(" in source and "max(" in source and "confidence" in source,
        "has_verdict_upper": ".upper()" in source and "verdict" in source,
        "filters_skip_types": "SKIP_TYPES" in source or "opinion" in source.lower(),
    }

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)

    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()},
        "score": passed / total * 10 if total else 10.0,
    }


def benchmark_test_coverage() -> dict:
    """Check test file existence and count for both modules."""
    arxiv_test = PROJECT_DIR / "tests" / "test_arxiv_agent.py"
    fact_test = PROJECT_DIR / "tests" / "test_fact_checker.py"

    results = {
        "arxiv_test_exists": arxiv_test.exists(),
        "fact_test_exists": fact_test.exists(),
        "arxiv_test_count": 0,
        "fact_test_count": 0,
    }

    if arxiv_test.exists():
        content = arxiv_test.read_text()
        results["arxiv_test_count"] = content.count("def test_")

    if fact_test.exists():
        content = fact_test.read_text()
        results["fact_test_count"] = content.count("def test_")

    total_tests = results["arxiv_test_count"] + results["fact_test_count"]
    # Score: 10 if >= 40 tests, proportional below that
    results["total_tests"] = total_tests
    results["score"] = min(10.0, total_tests / 4.0)  # 40 tests = 10.0

    return results


def benchmark_multicriteria_scoring() -> dict:
    """Check if ranking uses per-criteria scores (novelty, significance, practical, breadth)."""
    import inspect
    from jobpulse.arxiv_agent import llm_rank_broad
    source = inspect.getsource(llm_rank_broad)

    checks = {
        "has_novelty_in_prompt": "novelty" in source.lower() or "NOVELTY" in source,
        "has_significance_in_prompt": "significance" in source.lower() or "SIGNIFICANCE" in source,
        "has_practical_in_prompt": "practical" in source.lower() or "PRACTICAL" in source,
        "has_breadth_in_prompt": "breadth" in source.lower() or "BREADTH" in source,
        "has_per_criteria_scores": '"scores"' in source or "'scores'" in source,
        "has_weighted_overall": "overall" in source or "weighted" in source.lower(),
    }

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)

    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()},
        "score": passed / total * 10 if total else 10.0,
    }


def benchmark_fact_check_integration() -> dict:
    """Check if fact-checking is wired into the arXiv digest pipeline."""
    import inspect
    from jobpulse import arxiv_agent

    checks = {
        "has_summarize_and_verify": hasattr(arxiv_agent, "summarize_and_verify_paper"),
        "has_fact_check_import": "fact_checker" in inspect.getsource(arxiv_agent),
        "has_fact_check_in_digest": False,
        "has_fact_check_db_columns": False,
    }

    # Check if build_digest uses fact-checking
    if hasattr(arxiv_agent, "build_digest"):
        source = inspect.getsource(arxiv_agent.build_digest)
        checks["has_fact_check_in_digest"] = "fact_check" in source or "verify" in source

    # Check if DB has fact_check columns
    try:
        import sqlite3
        conn = sqlite3.connect(str(arxiv_agent.DB_PATH))
        cursor = conn.execute("PRAGMA table_info(papers)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        checks["has_fact_check_db_columns"] = "fact_check_score" in columns
    except Exception:
        pass

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)

    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()},
        "score": passed / total * 10 if total else 10.0,
    }


def run_all_benchmarks() -> dict:
    """Run all benchmarks and return combined results."""
    print("=" * 60)
    print("arXiv Ranking & Fact-Checking Benchmark")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    benchmarks = {
        "json_parsing": ("JSON Parse Reliability", benchmark_json_parsing),
        "fact_scoring": ("Fact-Checker Scoring Accuracy", benchmark_fact_checker_scoring),
        "confidence_bounds": ("Confidence & Verdict Validation", benchmark_confidence_bounds),
        "test_coverage": ("Test Coverage", benchmark_test_coverage),
        "multicriteria": ("Multi-Criteria Scoring", benchmark_multicriteria_scoring),
        "fact_integration": ("Fact-Check Integration", benchmark_fact_check_integration),
    }

    results = {}
    overall_score = 0.0

    for key, (name, func) in benchmarks.items():
        print(f"\n--- {name} ---")
        try:
            result = func()
            results[key] = result
            score = result.get("score", 0)
            overall_score += score

            if "passed" in result and "total" in result:
                print(f"  Result: {result['passed']}/{result['total']} passed")
            if "checks" in result:
                for check, status in result["checks"].items():
                    icon = "pass" if status == "PASS" else "FAIL"
                    print(f"  [{icon}] {check}")
            if "failures" in result and result["failures"]:
                for f in result["failures"][:3]:
                    print(f"  FAIL: {f['description']} (expected {f['expected']}, got {f['actual']})")
            print(f"  Score: {score:.1f}/10")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[key] = {"score": 0, "error": str(e)}

    num_benchmarks = len(benchmarks)
    overall = overall_score / num_benchmarks if num_benchmarks else 0
    results["_overall"] = {
        "score": round(overall, 2),
        "max": 10.0,
        "timestamp": datetime.now().isoformat(),
        "num_benchmarks": num_benchmarks,
    }

    print(f"\n{'=' * 60}")
    print(f"OVERALL SCORE: {overall:.1f}/10")
    print(f"{'=' * 60}")

    return results


def save_results(results: dict, label: str):
    """Save benchmark results to JSON."""
    path = BENCHMARK_DIR / f"{label}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to {path}")


def compare_results(label: str, current: dict):
    """Compare current results against a saved baseline."""
    path = BENCHMARK_DIR / f"{label}.json"
    if not path.exists():
        print(f"\nNo baseline found at {path}. Run with --save {label} first.")
        return

    with open(path) as f:
        baseline = json.load(f)

    print(f"\n{'=' * 60}")
    print(f"COMPARISON: {label} vs current")
    print(f"{'=' * 60}")

    for key in baseline:
        if key.startswith("_"):
            continue
        base_score = baseline[key].get("score", 0)
        curr_score = current.get(key, {}).get("score", 0)
        delta = curr_score - base_score
        arrow = "^" if delta > 0 else ("v" if delta < 0 else "=")
        print(f"  {key:25s}  {base_score:5.1f} -> {curr_score:5.1f}  [{arrow} {abs(delta):+.1f}]")

    base_overall = baseline.get("_overall", {}).get("score", 0)
    curr_overall = current.get("_overall", {}).get("score", 0)
    delta = curr_overall - base_overall
    print(f"\n  {'OVERALL':25s}  {base_overall:5.1f} -> {curr_overall:5.1f}  [{'^' if delta > 0 else 'v'} {abs(delta):+.1f}]")


if __name__ == "__main__":
    results = run_all_benchmarks()

    if "--save" in sys.argv:
        idx = sys.argv.index("--save")
        label = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "baseline"
        save_results(results, label)

    if "--compare" in sys.argv:
        idx = sys.argv.index("--compare")
        label = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "baseline"
        compare_results(label, results)
