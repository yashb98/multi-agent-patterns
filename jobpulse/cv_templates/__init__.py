"""CV and Cover Letter PDF generators matching Yash's template style."""

from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pymupdf


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
    """Load the applicant identity from ProfileStore (config fallback)."""
    try:
        from shared.profile_store import get_profile_store
        ident = get_profile_store().identity()
        return {
            "name": ident.full_name,
            "phone": ident.phone,
            "email": ident.email,
            "linkedin": normalize_linkedin_url(ident.linkedin),
            "github": ident.github,
            "portfolio": ident.portfolio,
        }
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).debug("ProfileStore unavailable: %s", _exc)
        from jobpulse.config import APPLICANT_PROFILE as profile
        return {
            "name": f"{profile['first_name']} {profile['last_name']}".strip(),
            "phone": profile["phone"],
            "email": profile["email"],
            "linkedin": normalize_linkedin_url(profile["linkedin"]),
            "github": profile["github"],
            "portfolio": profile["portfolio"],
        }


def _get_author_name() -> str:
    try:
        from shared.profile_store import get_profile_store
        return get_profile_store().identity().full_name or "Yash Bishnoi"
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).debug("ProfileStore unavailable for author name: %s", _exc)
        return "Yash Bishnoi"


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
