"""CLI entry point: python -m shared.adversarial"""

import sys
from shared.adversarial._eval_runner import EvalRunner


def main():
    quick = "--quick" in sys.argv
    runner = EvalRunner()
    report = runner.run(quick=quick)
    print(f"\nAdversarial Evaluation Report")
    print(f"{'=' * 40}")
    print(f"Total: {report.total}  Passed: {report.passed}  Failed: {report.failed}")
    print(f"Duration: {report.duration_s:.2f}s")
    if report.regressions:
        print(f"\nRegressions detected:")
        for r in report.regressions:
            print(f"  {r.metric}: {r.baseline_value:.3f} → {r.current_value:.3f} ({r.drop_pct:.1%} drop)")
    if report.failed:
        print(f"\nFailed cases:")
        for d in report.details:
            if not d.passed:
                print(f"  {d.case_id}: {d.notes}")
        sys.exit(1)
    print("\nAll cases passed.")


if __name__ == "__main__":
    main()
