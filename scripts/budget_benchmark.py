"""Budget classification benchmark — tests accuracy against known-correct mappings.

Run:  python scripts/budget_benchmark.py
      python scripts/budget_benchmark.py --save baseline
      python scripts/budget_benchmark.py --compare baseline
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

DATA_DIR = PROJECT_DIR / "data"
BENCHMARK_DIR = DATA_DIR / "benchmarks"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)


# 40 test transactions with known-correct classifications
TEST_TRANSACTIONS = [
    # (description, amount, txn_type, expected_section, expected_category)

    # ── Obvious cases (should always work) ──
    ("lunch at pret", 8.50, "expense", "variable", "Eating out"),
    ("groceries at tesco", 45.00, "expense", "variable", "Groceries"),
    ("uber to work", 12.00, "expense", "variable", "Transport"),
    ("netflix subscription", 15.99, "expense", "fixed", "Subscriptions"),
    ("rent", 1200.00, "expense", "fixed", "Rent / Mortgage"),
    ("salary", 2500.00, "income", "income", "Salary"),
    ("freelance project", 500.00, "income", "income", "Freelance"),
    ("saved 200", 200.00, "savings", "savings", "Savings"),
    ("cinema tickets", 25.00, "expense", "variable", "Entertainment"),
    ("pharmacy", 6.50, "expense", "variable", "Health"),

    # ── Store-based inference (store should hint category) ──
    ("weekly shop at sainsbury", 62.00, "expense", "variable", "Groceries"),
    ("stuff at aldi", 30.00, "expense", "variable", "Groceries"),
    ("bits from lidl", 18.00, "expense", "variable", "Groceries"),
    ("coffee at costa", 4.50, "expense", "variable", "Eating out"),
    ("meal at wagamama", 22.00, "expense", "variable", "Eating out"),

    # ── Substring false positive traps ──
    ("business lunch", 15.00, "expense", "variable", "Eating out"),  # NOT Transport (bus)
    ("paypal refund", 25.00, "income", "income", "Other"),  # NOT Salary (pay)
    ("apple watch", 399.00, "expense", "variable", "Shopping"),  # NOT Subscriptions (apple)
    ("game night dinner", 35.00, "expense", "variable", "Eating out"),  # NOT Entertainment (game)
    ("barber haircut", 20.00, "expense", "variable", "Health"),  # NOT Entertainment (bar)

    # ── Multi-word priority ──
    ("amazon fresh groceries", 28.00, "expense", "variable", "Groceries"),  # NOT Shopping (amazon)
    ("amazon prime renewal", 8.99, "expense", "fixed", "Subscriptions"),  # NOT Shopping (amazon)
    ("uber eats delivery", 18.00, "expense", "variable", "Eating out"),  # NOT Transport (uber)
    ("gym membership monthly", 35.00, "expense", "fixed", "Subscriptions"),  # NOT Health (gym)
    ("food shopping at tesco", 55.00, "expense", "variable", "Groceries"),  # NOT Eating out (food)

    # ── Context-dependent ──
    ("drinks at pub", 20.00, "expense", "variable", "Entertainment"),
    ("drinks from tesco", 8.00, "expense", "variable", "Groceries"),  # store context
    ("protein shake at holland and barrett", 12.00, "expense", "variable", "Health"),
    ("train to london", 45.00, "expense", "variable", "Transport"),
    ("gas bill", 80.00, "expense", "fixed", "Utilities"),

    # ── Income variations ──
    ("got paid 2000", 2000.00, "income", "income", "Salary"),
    ("refund from amazon", 15.00, "income", "income", "Other"),
    ("cashback reward", 5.00, "income", "income", "Other"),

    # ── Savings variations ──
    ("invest in stocks", 500.00, "savings", "savings", "Investments"),
    ("credit card payment", 200.00, "savings", "savings", "Credit card / Loan payment"),
    ("isa deposit", 100.00, "savings", "savings", "Investments"),

    # ── Edge cases ──
    ("spotify premium", 10.99, "expense", "fixed", "Subscriptions"),
    ("dentist checkup", 60.00, "expense", "variable", "Health"),
    ("parking at hospital", 5.00, "expense", "variable", "Transport"),
    ("shoes from jd sports", 85.00, "expense", "variable", "Shopping"),
]


def run_benchmark() -> dict:
    """Run all test transactions through the classifier."""
    from jobpulse.budget_agent import classify_transaction

    results = {
        "total": len(TEST_TRANSACTIONS),
        "correct": 0,
        "wrong": 0,
        "failures": [],
        "by_issue_type": {
            "substring_false_positive": 0,
            "priority_ordering": 0,
            "missing_context": 0,
            "other": 0,
        },
    }

    print(f"{'Description':<40} {'Expected':<20} {'Got':<20} {'Result'}")
    print("=" * 100)

    for desc, amount, txn_type, exp_section, exp_category in TEST_TRANSACTIONS:
        section, category = classify_transaction(desc, amount, txn_type)

        correct = (section == exp_section and category == exp_category)
        if correct:
            results["correct"] += 1
            status = "OK"
        else:
            results["wrong"] += 1
            status = "WRONG"

            # Classify the type of failure
            failure = {
                "description": desc,
                "expected": f"{exp_section}|{exp_category}",
                "actual": f"{section}|{category}",
            }

            desc_lower = desc.lower()
            if any(k in desc_lower for k in ["bus", "pay", "apple", "game", "bar", "food"]):
                failure["issue_type"] = "substring_false_positive"
                results["by_issue_type"]["substring_false_positive"] += 1
            elif "amazon" in desc_lower or "uber" in desc_lower or "gym" in desc_lower:
                failure["issue_type"] = "priority_ordering"
                results["by_issue_type"]["priority_ordering"] += 1
            elif "at " in desc_lower or "from " in desc_lower:
                failure["issue_type"] = "missing_context"
                results["by_issue_type"]["missing_context"] += 1
            else:
                failure["issue_type"] = "other"
                results["by_issue_type"]["other"] += 1

            results["failures"].append(failure)

        print(f"{desc:<40} {exp_category:<20} {category:<20} {status}")

    accuracy = results["correct"] / results["total"] * 100
    results["accuracy"] = round(accuracy, 1)
    results["score"] = round(accuracy / 10, 1)

    print(f"\n{'=' * 100}")
    print(f"ACCURACY: {results['correct']}/{results['total']} ({accuracy:.1f}%)")
    print(f"SCORE: {results['score']}/10")

    if results["failures"]:
        print(f"\nFailure breakdown:")
        for issue_type, count in results["by_issue_type"].items():
            if count > 0:
                print(f"  {issue_type}: {count}")

    results["timestamp"] = datetime.now().isoformat()
    return results


def save_results(results: dict, label: str):
    path = BENCHMARK_DIR / f"budget_{label}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to {path}")


def compare_results(label: str, current: dict):
    path = BENCHMARK_DIR / f"budget_{label}.json"
    if not path.exists():
        print(f"\nNo baseline at {path}")
        return

    with open(path) as f:
        baseline = json.load(f)

    print(f"\n{'=' * 60}")
    print(f"COMPARISON: {label} vs current")
    print(f"{'=' * 60}")
    print(f"  Accuracy:    {baseline['accuracy']}% -> {current['accuracy']}%  "
          f"[{'improved' if current['accuracy'] > baseline['accuracy'] else 'same/worse'}]")
    print(f"  Correct:     {baseline['correct']}/{baseline['total']} -> "
          f"{current['correct']}/{current['total']}")
    print(f"  Wrong:       {baseline['wrong']} -> {current['wrong']}")

    if baseline.get("by_issue_type") and current.get("by_issue_type"):
        print(f"\n  Issue breakdown:")
        for issue_type in baseline["by_issue_type"]:
            base = baseline["by_issue_type"].get(issue_type, 0)
            curr = current["by_issue_type"].get(issue_type, 0)
            delta = curr - base
            print(f"    {issue_type:<30} {base} -> {curr}  [{'+' if delta >= 0 else ''}{delta}]")


if __name__ == "__main__":
    results = run_benchmark()

    if "--save" in sys.argv:
        idx = sys.argv.index("--save")
        label = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "baseline"
        save_results(results, label)

    if "--compare" in sys.argv:
        idx = sys.argv.index("--compare")
        label = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "baseline"
        compare_results(label, results)
