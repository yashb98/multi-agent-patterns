"""
Unified Runner: Compare All 3 Orchestration Patterns
=====================================================

Run this to see all three patterns process the same topic,
then compare execution traces, scores, and outputs.

Usage:
    python run_all.py                    # Uses default topic
    python run_all.py "Your topic here"  # Custom topic

Requires OPENAI_API_KEY environment variable.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patterns.hierarchical import run_hierarchical
from patterns.peer_debate import run_debate
from patterns.dynamic_swarm import run_swarm


def compare_patterns(topic: str):
    """
    Runs all three patterns on the same topic and compares results.
    """
    print("\n" + "█" * 60)
    print("  MULTI-AGENT ORCHESTRATION COMPARISON")
    print(f"  Topic: {topic}")
    print("█" * 60)
    
    results = {}
    
    # ── Pattern 1: Hierarchical ──
    print("\n\n" + "▓" * 60)
    print("  PATTERN 1: HIERARCHICAL SUPERVISOR")
    print("▓" * 60)
    t1 = time.time()
    try:
        results["hierarchical"] = run_hierarchical(topic)
        results["hierarchical"]["_time"] = time.time() - t1
    except Exception as e:
        print(f"  ❌ Error: {e}")
        results["hierarchical"] = {"_error": str(e), "_time": time.time() - t1}
    
    # ── Pattern 2: Peer Debate ──
    print("\n\n" + "▓" * 60)
    print("  PATTERN 2: PEER DEBATE")
    print("▓" * 60)
    t2 = time.time()
    try:
        results["debate"] = run_debate(topic)
        results["debate"]["_time"] = time.time() - t2
    except Exception as e:
        print(f"  ❌ Error: {e}")
        results["debate"] = {"_error": str(e), "_time": time.time() - t2}
    
    # ── Pattern 3: Dynamic Swarm ──
    print("\n\n" + "▓" * 60)
    print("  PATTERN 3: DYNAMIC SWARM")
    print("▓" * 60)
    t3 = time.time()
    try:
        results["swarm"] = run_swarm(topic)
        results["swarm"]["_time"] = time.time() - t3
    except Exception as e:
        print(f"  ❌ Error: {e}")
        results["swarm"] = {"_error": str(e), "_time": time.time() - t3}
    
    # ── Comparison Summary ──
    print("\n\n" + "█" * 60)
    print("  COMPARISON RESULTS")
    print("█" * 60)
    
    print(f"\n{'Pattern':<20} {'Score':>8} {'Words':>8} {'Iters':>8} {'Time':>10}")
    print("-" * 56)
    
    for name, result in results.items():
        if "_error" in result:
            print(f"{name:<20} {'ERROR':>8} {'-':>8} {'-':>8} {result['_time']:>8.1f}s")
        else:
            score = result.get("review_score", 0)
            words = len(result.get("final_output", "").split())
            iters = result.get("iteration", 0)
            elapsed = result.get("_time", 0)
            print(f"{name:<20} {score:>7.1f} {words:>7} {iters:>7} {elapsed:>8.1f}s")
    
    # Save outputs
    _project_root = os.path.dirname(os.path.abspath(__file__))
    _output_dir = os.path.join(_project_root, "outputs")
    os.makedirs(_output_dir, exist_ok=True)
    for name, result in results.items():
        if "_error" not in result:
            path = os.path.join(_output_dir, f"{name}_output.md")
            with open(path, "w") as f:
                f.write(result.get("final_output", "No output"))
    
    print(f"\n💾 Outputs saved to /outputs/")
    
    return results


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "How AI Agents Are Changing Software Development in 2026"
    compare_patterns(topic)
