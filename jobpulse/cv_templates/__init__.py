"""CV and Cover Letter PDF generators.

All applicant data (identity, experience, projects, skills) is read from
`data/user_profile.db` at render time via `shared.profile_store.ProfileStore`.
No personal information is hardcoded in this package — see pii-policy.md.
"""

import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pymupdf

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATS_PATH = _PROJECT_ROOT / "data" / "project_stats.json"

_DEFAULT_STATS = {
    "loc_display": "142,500+",
    "tests_display": "3,350+",
    "databases": 57,
}

_stats_cache: dict | None = None


def get_project_stats() -> dict:
    """Load project stats from data/project_stats.json (written by update_stats.py)."""
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    try:
        _stats_cache = json.loads(_STATS_PATH.read_text())
    except Exception:
        _stats_cache = _DEFAULT_STATS
    return _stats_cache


_XMP_TEMPLATE = (
    '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:pdf="http://ns.adobe.com/pdf/1.3/"'
    ' xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
    "<dc:title><rdf:Alt><rdf:li xml:lang=\"x-default\">{title}</rdf:li>"
    "</rdf:Alt></dc:title>"
    "<dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>"
    "<pdf:Producer>ReportLab</pdf:Producer>"
    "<xmp:CreatorTool>JobPulse CV Generator</xmp:CreatorTool>"
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
    '<?xpacket end="w"?>'
)


def normalize_linkedin_url(url: str) -> str:
    """Return a canonical HTTPS LinkedIn profile URL."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"

    parsed = urlparse(raw)
    if "linkedin.com" not in parsed.netloc.lower():
        return raw.rstrip("/")

    return urlunparse(
        (
            "https",
            "www.linkedin.com",
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )


def build_applicant_identity() -> dict[str, str]:
    """Load the applicant identity from ProfileStore, with config backfill for empty fields.

    ProfileStore can return rows with blank columns (DB partially populated).
    Config (.env) is the source of truth for identity fields, so any blank
    coming back from ProfileStore is filled from `APPLICANT_PROFILE` rather
    than rendered as an empty string in CVs / forms.
    """
    from jobpulse.config import APPLICANT_PROFILE as cfg
    cfg_name = f"{cfg['first_name']} {cfg['last_name']}".strip()
    cfg_ident = {
        "name": cfg_name,
        "phone": cfg["phone"],
        "email": cfg["email"],
        "linkedin": normalize_linkedin_url(cfg["linkedin"]),
        "github": cfg["github"],
        "portfolio": cfg["portfolio"],
    }
    try:
        from shared.profile_store import get_profile_store
        ident = get_profile_store().identity()
        store_ident = {
            "name": ident.full_name,
            "phone": ident.phone,
            "email": ident.email,
            "linkedin": normalize_linkedin_url(ident.linkedin),
            "github": ident.github,
            "portfolio": ident.portfolio,
        }
        return {k: (store_ident.get(k) or cfg_ident.get(k, "")) for k in cfg_ident}
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).debug("ProfileStore unavailable: %s", _exc)
        return cfg_ident


def _get_author_name() -> str:
    """Read the applicant's full name from ProfileStore. Falls back to env-var
    config (`config.APPLICANT_FIRST_NAME`/`APPLICANT_LAST_NAME`) so PII never
    needs to be hardcoded. Returns an empty string as a last resort — callers
    treat that as "no author metadata" and proceed without crashing.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from shared.profile_store import get_profile_store
        name = (get_profile_store().identity().full_name or "").strip()
        if name:
            return name
    except Exception as _exc:
        log.debug("ProfileStore unavailable for author name: %s", _exc)
    try:
        from jobpulse.config import APPLICANT_FIRST_NAME, APPLICANT_LAST_NAME
        fallback = f"{APPLICANT_FIRST_NAME} {APPLICANT_LAST_NAME}".strip()
        if fallback:
            return fallback
    except Exception:
        pass
    log.warning(
        "_get_author_name: no name in ProfileStore or config — "
        "PDF metadata 'author' will be blank"
    )
    return ""


def sanitize_pdf(pdf_path: Path) -> None:
    """Re-save PDF via PyMuPDF for maximum ATS/upload compatibility."""
    author = _get_author_name()
    tmp = pdf_path.with_suffix(".tmp.pdf")
    doc = pymupdf.open(str(pdf_path))
    doc.set_metadata({
        "producer": "ReportLab",
        "creator": "JobPulse CV Generator",
        "title": pdf_path.name,
        "author": author,
    })
    doc.set_xml_metadata(_XMP_TEMPLATE.format(title=pdf_path.name, author=author))
    doc.save(str(tmp), garbage=4, deflate=True, clean=True)
    doc.close()
    tmp.replace(pdf_path)
