"""Export/Backup — one-click export of all databases, experiences, and personas."""
import shutil
import json
import hashlib
import tarfile
from datetime import datetime
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR, PROJECT_DIR

logger = get_logger(__name__)

EXPORT_DIR = PROJECT_DIR / "exports"


def export_all(archive: bool = True) -> str:
    """Export all databases and learned data to a timestamped directory.

    Returns path to the export directory (or .tar.gz if archive=True).
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = EXPORT_DIR / f"backup_{timestamp}"
    export_path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "timestamp": datetime.now().isoformat(),
        "files": [],
    }

    # 1. Copy all SQLite databases
    db_files = [
        "mindgraph.db",
        "jobpulse.db",
        "budget.db",
        "swarm_experience.db",
    ]
    for db_name in db_files:
        src = DATA_DIR / db_name
        if src.exists():
            dst = export_path / db_name
            shutil.copy2(str(src), str(dst))
            manifest["files"].append({
                "name": db_name,
                "size": dst.stat().st_size,
                "checksum": _md5(dst),
            })
            logger.info("Exported %s (%d bytes)", db_name, dst.stat().st_size)

    # 2. Export persona prompts as JSON
    try:
        from jobpulse.swarm_dispatcher import _get_exp_conn
        conn = _get_exp_conn()
        personas = conn.execute("SELECT * FROM persona_prompts").fetchall()
        conn.close()
        persona_data = [dict(r) for r in personas]
        persona_path = export_path / "persona_prompts.json"
        persona_path.write_text(json.dumps(persona_data, indent=2))
        manifest["files"].append({"name": "persona_prompts.json", "count": len(persona_data)})
    except Exception as e:
        logger.debug("Persona export skipped: %s", e)

    # 3. Export experiences as JSON
    try:
        from jobpulse.swarm_dispatcher import _get_exp_conn
        conn = _get_exp_conn()
        exps = conn.execute("SELECT * FROM experiences ORDER BY created_at DESC LIMIT 500").fetchall()
        conn.close()
        exp_data = [dict(r) for r in exps]
        exp_path = export_path / "experiences.json"
        exp_path.write_text(json.dumps(exp_data, indent=2))
        manifest["files"].append({"name": "experiences.json", "count": len(exp_data)})
    except Exception as e:
        logger.debug("Experience export skipped: %s", e)

    # 4. Export A/B test results
    try:
        from jobpulse.ab_testing import get_all_tests
        tests = get_all_tests()
        ab_path = export_path / "ab_tests.json"
        ab_path.write_text(json.dumps(tests, indent=2, default=str))
        manifest["files"].append({"name": "ab_tests.json", "count": len(tests)})
    except Exception as e:
        logger.debug("A/B test export skipped: %s", e)

    # 5. Export rate limit history
    try:
        from shared.rate_monitor import get_current_limits
        limits = get_current_limits()
        limits_path = export_path / "rate_limits.json"
        limits_path.write_text(json.dumps(limits, indent=2))
        manifest["files"].append({"name": "rate_limits.json", "count": len(limits)})
    except Exception as e:
        logger.debug("Rate limit export skipped: %s", e)

    # Write manifest
    manifest_path = export_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Optionally create tar.gz archive
    if archive:
        archive_path = EXPORT_DIR / f"backup_{timestamp}.tar.gz"
        with tarfile.open(str(archive_path), "w:gz") as tar:
            tar.add(str(export_path), arcname=f"backup_{timestamp}")
        # Clean up the directory, keep only the archive
        shutil.rmtree(str(export_path))
        logger.info("Export archive created: %s", archive_path)
        return str(archive_path)

    logger.info("Export directory created: %s", export_path)
    return str(export_path)


def _md5(path: Path) -> str:
    """Compute MD5 checksum of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
