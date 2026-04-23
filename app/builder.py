"""
Assembles the final Shopify body_html from parsed sections + uploaded image URLs.

Structure per section:
  <h2>Heading</h2>
  <img src="..." alt="..." width="1280" style="max-width:100%;height:auto;" />
  <p>Content...</p>
  ...
"""


def build_body_html(sections: list[dict]) -> str:
    """
    Each section dict:
        {
            "heading": str,
            "content": str,         # inner HTML paragraphs/lists
            "image_url": str|None,
            "image_alt": str|None,
        }
    """
    parts = []

    for sec in sections:
        heading = sec.get("heading", "")
        content = sec.get("content", "")
        image_url = sec.get("image_url")
        image_alt = sec.get("image_alt", heading)

        if heading:
            parts.append(f'<h2>{heading}</h2>')

        if image_url:
            parts.append(
                f'<img src="{image_url}" alt="{_escape_attr(image_alt)}" '
                f'width="1280" style="max-width:100%;height:auto;display:block;margin:1.5em 0;" />'
            )

        if content:
            parts.append(content)

    return "\n".join(parts)


def _escape_attr(text: str) -> str:
    return text.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
