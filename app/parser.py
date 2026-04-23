"""
Parses a .docx file into structured sections.

Returns:
    {
        "title": str,           # From H1
        "sections": [
            {
                "heading": str, # H2 text
                "content": str  # HTML of paragraphs/lists under this H2
            }
        ]
    }
"""

from docx import Document
from docx.oxml.ns import qn
import html


def _runs_to_html(paragraph) -> str:
    """Convert paragraph runs to inline HTML, preserving bold/italic/links."""
    parts = []
    for run in paragraph.runs:
        text = html.escape(run.text)
        if not text:
            continue
        if run.bold and run.italic:
            text = f"<strong><em>{text}</em></strong>"
        elif run.bold:
            text = f"<strong>{text}</strong>"
        elif run.italic:
            text = f"<em>{text}</em>"
        parts.append(text)
    return "".join(parts)


def _para_to_html(paragraph) -> str | None:
    """Convert a single paragraph to its HTML representation."""
    style = paragraph.style.name if paragraph.style else ""
    text = _runs_to_html(paragraph)
    raw_text = paragraph.text.strip()

    if not raw_text:
        return None

    # List items
    numPr = paragraph._p.find(qn("w:pPr"))
    is_list = False
    list_tag = "ul"
    if numPr is not None:
        numEl = numPr.find(qn("w:numPr"))
        if numEl is not None:
            is_list = True
            ilvl = numEl.find(qn("w:ilvl"))
            # Check number format — heuristic: if style has "List Number", use ol
            if "Number" in style:
                list_tag = "ol"

    if is_list:
        return f"<li data-list='{list_tag}'>{text}</li>"

    if style.startswith("Heading 3"):
        return f"<h3>{text}</h3>"
    if style.startswith("Heading 4"):
        return f"<h4>{text}</h4>"

    return f"<p>{text}</p>"


def _wrap_list_items(html_parts: list[str]) -> list[str]:
    """Group consecutive <li> elements into <ul> or <ol> wrappers."""
    result = []
    i = 0
    while i < len(html_parts):
        part = html_parts[i]
        if part.startswith("<li "):
            # Determine list type
            import re
            m = re.search(r"data-list='(ul|ol)'", part)
            tag = m.group(1) if m else "ul"
            # Strip the data attribute before output
            clean = re.sub(r" data-list='[^']*'", "", part)
            items = [clean]
            i += 1
            while i < len(html_parts) and html_parts[i].startswith("<li "):
                m2 = re.search(r"data-list='(ul|ol)'", html_parts[i])
                clean2 = re.sub(r" data-list='[^']*'", "", html_parts[i])
                items.append(clean2)
                i += 1
            result.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            result.append(part)
            i += 1
    return result


def parse_docx(file_path: str) -> dict:
    doc = Document(file_path)

    title = ""
    sections = []
    current_section = None

    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        raw_text = para.text.strip()

        # H1 → blog title (first one wins)
        if style.startswith("Heading 1"):
            if not title:
                title = raw_text
            continue

        # H2 → new section
        if style.startswith("Heading 2"):
            if current_section is not None:
                sections.append(current_section)
            current_section = {"heading": raw_text, "html_parts": []}
            continue

        # Everything else → content under current section (or pre-section intro)
        html_chunk = _para_to_html(para)
        if html_chunk:
            if current_section is None:
                # Content before first H2: treat as an intro section with no heading
                current_section = {"heading": "", "html_parts": []}
            current_section["html_parts"].append(html_chunk)

    # Flush last section
    if current_section is not None:
        sections.append(current_section)

    # Wrap list items and join HTML
    final_sections = []
    for sec in sections:
        wrapped = _wrap_list_items(sec["html_parts"])
        final_sections.append(
            {
                "heading": sec["heading"],
                "content": "\n".join(wrapped),
            }
        )

    return {"title": title, "sections": final_sections}
