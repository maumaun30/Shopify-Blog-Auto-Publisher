"""
Uses Claude to:
- Generate a Pexels search query for each H2 heading + surrounding content
- Generate meta title and meta description for the whole post
- Generate alt text for each image (heading + AI description)
"""

import os
import json
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-5"


def _call(prompt: str, max_tokens: int = 300) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def generate_pexels_query(heading: str, content_snippet: str) -> str:
    """Return a concise Pexels search query for a landscape photo matching this section."""
    prompt = f"""You are helping find a landscape stock photo for a blog section.

Section heading: {heading}
Content preview: {content_snippet[:300]}

Return ONLY a short Pexels search query (3-6 words) that will find a relevant, 
high-quality landscape-oriented photo. No quotes, no explanation, just the query."""
    return _call(prompt, max_tokens=30)


def generate_alt_text(heading: str, pexels_query: str) -> str:
    """Generate descriptive alt text combining heading context and photo subject."""
    prompt = f"""Write a concise, descriptive image alt text (max 125 characters) for a landscape photo 
used in a blog section titled "{heading}". The photo was found with query: "{pexels_query}".
Return ONLY the alt text, no quotes, no explanation."""
    return _call(prompt, max_tokens=60)


def generate_seo(title: str, full_text: str) -> dict:
    """Generate meta title and meta description for the blog post."""
    prompt = f"""Generate SEO metadata for this blog post.

Title: {title}
Content excerpt: {full_text[:800]}

Return a JSON object with exactly these two keys:
- "meta_title": SEO-optimized title, max 60 characters
- "meta_description": compelling description, max 155 characters

Return ONLY valid JSON, no markdown, no explanation."""

    raw = _call(prompt, max_tokens=200)
    try:
        # Strip accidental markdown fences
        clean = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return {
            "meta_title": data.get("meta_title", title)[:60],
            "meta_description": data.get("meta_description", "")[:155],
        }
    except Exception:
        return {"meta_title": title[:60], "meta_description": ""}


def generate_featured_image_query(title: str, intro_content: str) -> str:
    """Return a Pexels search query for the featured (hero) image."""
    prompt = f"""You are helping find a featured hero image for a blog post.

Blog title: {title}
Intro: {intro_content[:300]}

Return ONLY a short Pexels search query (3-6 words) for a beautiful landscape-oriented hero image. 
No quotes, no explanation."""
    return _call(prompt, max_tokens=30)
