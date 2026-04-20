"""CV and Cover Letter PDF generators matching Yash's template style."""

from pathlib import Path

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
    "<dc:creator><rdf:Seq><rdf:li>Yash Bishnoi</rdf:li></rdf:Seq></dc:creator>"
    "<pdf:Producer>ReportLab</pdf:Producer>"
    "<xmp:CreatorTool>JobPulse CV Generator</xmp:CreatorTool>"
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
    '<?xpacket end="w"?>'
)


def sanitize_pdf(pdf_path: Path) -> None:
    """Re-save PDF via PyMuPDF for maximum ATS/upload compatibility."""
    tmp = pdf_path.with_suffix(".tmp.pdf")
    doc = pymupdf.open(str(pdf_path))
    doc.set_metadata({
        "producer": "ReportLab",
        "creator": "JobPulse CV Generator",
        "title": pdf_path.name,
        "author": "Yash Bishnoi",
    })
    doc.set_xml_metadata(_XMP_TEMPLATE.format(title=pdf_path.name))
    doc.save(str(tmp), garbage=4, deflate=True, clean=True)
    doc.close()
    tmp.replace(pdf_path)
