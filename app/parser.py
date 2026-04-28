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
from docx.text.run import Run
import html


def _run_element_to_html(r_el, paragraph) -> str:
    """Convert a <w:r> element to inline HTML with bold/italic/underline formatting."""
    run = Run(r_el, paragraph)
    text = html.escape(run.text)
    if not text:
        return ""
    if run.underline:
        text = f"<u>{text}</u>"
    if run.italic:
        text = f"<em>{text}</em>"
    if run.bold:
        text = f"<strong>{text}</strong>"
    return text


def _runs_to_html(paragraph) -> str:
    """Convert paragraph children to inline HTML, preserving bold/italic/links."""
    parts = []
    rels = paragraph.part.rels
    for child in paragraph._p.iterchildren():
        tag = child.tag
        if tag == qn("w:r"):
            parts.append(_run_element_to_html(child, paragraph))
        elif tag == qn("w:hyperlink"):
            inner = "".join(
                _run_element_to_html(r, paragraph)
                for r in child.findall(qn("w:r"))
            )
            if not inner:
                continue
            r_id = child.get(qn("r:id"))
            anchor = child.get(qn("w:anchor"))
            href = None
            if r_id and r_id in rels:
                href = rels[r_id].target_ref
            elif anchor:
                href = f"#{anchor}"
            if href:
                parts.append(f'<a href="{html.escape(href, quote=True)}">{inner}</a>')
            else:
                parts.append(inner)
    return "".join(parts)


def _list_format_for(paragraph) -> str | None:
    """Return 'ol' or 'ul' if paragraph is a list item, else None.

    Resolves the actual numFmt from the document's numbering part instead of
    guessing from the paragraph style name.
    """
    pPr = paragraph._p.find(qn("w:pPr"))
    if pPr is None:
        return None
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return None

    numId_el = numPr.find(qn("w:numId"))
    ilvl_el = numPr.find(qn("w:ilvl"))
    if numId_el is None:
        return "ul"
    numId = numId_el.get(qn("w:val"))
    ilvl = ilvl_el.get(qn("w:val")) if ilvl_el is not None else "0"

    try:
        numbering = paragraph.part.numbering_part.element
    except (AttributeError, NotImplementedError, KeyError):
        return "ul"

    num = numbering.find(f"{qn('w:num')}[@{qn('w:numId')}='{numId}']")
    if num is None:
        return "ul"
    abstract_id_el = num.find(qn("w:abstractNumId"))
    if abstract_id_el is None:
        return "ul"
    abstract_id = abstract_id_el.get(qn("w:val"))

    abstract = numbering.find(
        f"{qn('w:abstractNum')}[@{qn('w:abstractNumId')}='{abstract_id}']"
    )
    if abstract is None:
        return "ul"
    lvl = abstract.find(f"{qn('w:lvl')}[@{qn('w:ilvl')}='{ilvl}']")
    if lvl is None:
        return "ul"
    numFmt = lvl.find(qn("w:numFmt"))
    fmt = numFmt.get(qn("w:val")) if numFmt is not None else "bullet"
    return "ul" if fmt in ("bullet", "none") else "ol"


def _para_to_html(paragraph) -> str | None:
    """Convert a single paragraph to its HTML representation."""
    style = paragraph.style.name if paragraph.style else ""
    text = _runs_to_html(paragraph)
    raw_text = paragraph.text.strip()

    if not raw_text:
        return None

    list_tag = _list_format_for(paragraph)
    if list_tag:
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
