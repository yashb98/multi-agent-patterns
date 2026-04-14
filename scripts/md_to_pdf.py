"""Convert markdown spec to PDF using ReportLab."""
import re
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_JUSTIFY


def md_to_pdf(input_path: str, output_path: str):
    with open(input_path) as f:
        lines = f.readlines()

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=25*mm, rightMargin=25*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()
    teal = HexColor("#1a5276")
    styles.add(ParagraphStyle("H1C", parent=styles["Heading1"], textColor=teal, fontSize=18, spaceAfter=10))
    styles.add(ParagraphStyle("H2C", parent=styles["Heading2"], textColor=teal, fontSize=14, spaceAfter=8, spaceBefore=16))
    styles.add(ParagraphStyle("H3C", parent=styles["Heading3"], textColor=HexColor("#2c3e50"), fontSize=11, spaceAfter=6, spaceBefore=12))
    styles.add(ParagraphStyle("H4C", parent=styles["Normal"], fontSize=10, leading=13, spaceAfter=4, textColor=HexColor("#34495e")))
    styles.add(ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=13, spaceAfter=4, alignment=TA_JUSTIFY))
    styles.add(ParagraphStyle("CodeBlock", parent=styles["Code"], fontSize=8, leading=10, backColor=HexColor("#f4f4f4"), spaceAfter=6))

    story = []
    in_code_block = False

    for line in lines:
        line = line.rstrip()

        # Code block toggle
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, styles["CodeBlock"]))
            continue

        if not line:
            story.append(Spacer(1, 4))
            continue

        if line.strip() == "---":
            story.append(Spacer(1, 8))
            continue

        # Headers
        if line.startswith("# ") and not line.startswith("## "):
            story.append(Paragraph(line[2:], styles["H1C"]))
            continue
        if line.startswith("## "):
            story.append(Paragraph(line[3:], styles["H2C"]))
            continue
        if line.startswith("### "):
            story.append(Paragraph(line[4:], styles["H3C"]))
            continue
        if line.startswith("#### "):
            story.append(Paragraph("<b>" + _escape(line[5:]) + "</b>", styles["H4C"]))
            continue

        # Table separator rows
        if re.match(r"^\|[\s\-|]+\|$", line):
            continue

        # Process inline markdown
        text = _escape(line)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8">\1</font>', text)

        if text.startswith("- "):
            text = "\u2022 " + text[2:]

        # Table rows
        if text.startswith("|"):
            text = text.strip("|").strip()
            text = " | ".join(cell.strip() for cell in text.split("|"))

        try:
            story.append(Paragraph(text, styles["Body"]))
        except Exception:
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, styles["CodeBlock"]))

    doc.build(story)
    print(f"PDF saved to {output_path}")


def _escape(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


if __name__ == "__main__":
    inp = sys.argv[1] if len(sys.argv) > 1 else "docs/superpowers/specs/2026-04-14-ultraplan-design.md"
    out = inp.replace(".md", ".pdf")
    md_to_pdf(inp, out)
