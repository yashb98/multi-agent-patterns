"""A/B Testing — compare old vs new persona prompts with statistical tracking."""
import sqlite3
import json
import random
from datetime import datetime
from shared.logging_config import get_logger
from shared.db import get_db_conn
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

AB_DB = DATA_DIR / "swarm_experience.db"


def _get_conn():
    return get_db_conn(AB_DB, mkdir=False)


def _init_ab_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ab_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            variant_a_prompt TEXT NOT NULL,
            variant_b_prompt TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ab_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL,
            variant TEXT NOT NULL,
            score REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ab_test ON ab_results(test_name, variant);
    """)
    conn.commit()
    conn.close()


_init_ab_db()


def create_test(test_name: str, agent_name: str, variant_a: str, variant_b: str) -> int:
    """Create a new A/B test comparing two prompts."""
    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO ab_tests (test_name, agent_name, variant_a_prompt, variant_b_prompt, created_at) VALUES (?,?,?,?,?)",
        (test_name, agent_name, variant_a, variant_b, datetime.now().isoformat())
    )
    test_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info("Created A/B test '%s' for %s (id=%d)", test_name, agent_name, test_id)
    return test_id


def get_variant(test_name: str) -> tuple[str, str]:
    """Get a random variant for a test. Returns (variant_label, prompt_text)."""
    conn = _get_conn()
    test = conn.execute(
        "SELECT * FROM ab_tests WHERE test_name=? AND status='active' ORDER BY id DESC LIMIT 1",
        (test_name,)
    ).fetchone()
    conn.close()

    if not test:
        return ("", "")

    if random.random() < 0.5:
        return ("A", test["variant_a_prompt"])
    else:
        return ("B", test["variant_b_prompt"])


def record_result(test_name: str, variant: str, score: float, metadata: dict = None):
    """Record the result of one A/B test trial."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO ab_results (test_name, variant, score, metadata, created_at) VALUES (?,?,?,?,?)",
        (test_name, variant, score, json.dumps(metadata or {}), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="score_change",
            source_loop="ab_testing",
            domain=test_name,
            agent_name=test_name,
            payload={"variant": variant, "score": score},
            session_id=f"ab_{test_name}",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Optimization signal failed: %s", e)


def get_results(test_name: str) -> dict:
    """Get A/B test comparison stats."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT variant, COUNT(*) as count, AVG(score) as avg_score, "
        "MIN(score) as min_score, MAX(score) as max_score "
        "FROM ab_results WHERE test_name=? GROUP BY variant",
        (test_name,)
    ).fetchall()
    conn.close()

    results = {}
    for r in rows:
        d = dict(r)
        results[d["variant"]] = d

    # Determine winner
    winner = None
    if "A" in results and "B" in results:
        min_samples = 10
        if results["A"]["count"] >= min_samples and results["B"]["count"] >= min_samples:
            if results["A"]["avg_score"] > results["B"]["avg_score"]:
                winner = "A"
            elif results["B"]["avg_score"] > results["A"]["avg_score"]:
                winner = "B"
            else:
                winner = "tie"

    return {"test_name": test_name, "variants": results, "winner": winner}


def promote_winner(test_name: str) -> bool:
    """Promote the winning variant to the active persona prompt."""
    result = get_results(test_name)
    if not result["winner"] or result["winner"] == "tie":
        logger.info("No clear winner for '%s', not promoting", test_name)
        return False

    conn = _get_conn()
    test = conn.execute(
        "SELECT * FROM ab_tests WHERE test_name=? AND status='active' ORDER BY id DESC LIMIT 1",
        (test_name,)
    ).fetchone()

    if not test:
        conn.close()
        return False

    winning_prompt = test["variant_a_prompt"] if result["winner"] == "A" else test["variant_b_prompt"]
    agent_name = test["agent_name"]

    # Update persona
    from jobpulse.swarm_dispatcher import store_persona, get_persona
    current = get_persona(agent_name)
    gen = (current["generation"] + 1) if current else 1
    avg = result["variants"][result["winner"]]["avg_score"]
    store_persona(agent_name, winning_prompt, gen, avg)

    # Mark test as complete
    conn.execute("UPDATE ab_tests SET status='completed' WHERE test_name=? AND status='active'", (test_name,))
    conn.commit()
    conn.close()

    logger.info("Promoted variant %s for '%s' (agent=%s, gen=%d, score=%.2f)",
                result["winner"], test_name, agent_name, gen, avg)
    return True


def get_all_tests() -> list[dict]:
    """Get all A/B tests with their current results."""
    conn = _get_conn()
    tests = conn.execute("SELECT * FROM ab_tests ORDER BY created_at DESC").fetchall()
    conn.close()

    results = []
    for t in tests:
        d = dict(t)
        d["results"] = get_results(d["test_name"])
        results.append(d)
    return results
