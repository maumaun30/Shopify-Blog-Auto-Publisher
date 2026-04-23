"""
Fetches landscape photos from Pexels and processes them:
- Search by query, prefer landscape orientation
- Download the image
- Resize to 1280px width (locked aspect ratio)
- Convert to WebP
Returns raw bytes + suggested filename.
"""

import os
import io
import httpx
from PIL import Image

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
TARGET_WIDTH = 1280


def fetch_and_process_image(query: str) -> tuple[bytes, str] | tuple[None, None]:
    """
    Returns (webp_bytes, filename) or (None, None) on failure.
    filename is derived from the query, e.g. 'rice-paddy-field.webp'
    """
    photo = _search_pexels(query)
    if not photo:
        return None, None

    # Prefer large landscape src
    src = photo.get("src", {})
    url = src.get("large2x") or src.get("large") or src.get("original")
    if not url:
        return None, None

    image_bytes = _download(url)
    if not image_bytes:
        return None, None

    processed = _resize_and_convert(image_bytes)
    slug = query.lower().strip().replace(" ", "-")[:50]
    filename = f"{slug}.webp"
    return processed, filename


def _search_pexels(query: str) -> dict | None:
    if not PEXELS_API_KEY:
        raise ValueError("PEXELS_API_KEY is not set")

    params = {
        "query": query,
        "orientation": "landscape",
        "size": "large",
        "per_page": 5,
        "page": 1,
    }
    headers = {"Authorization": PEXELS_API_KEY}

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(PEXELS_SEARCH_URL, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            photos = data.get("photos", [])
            if not photos:
                return None
            # Pick first result (already sorted by relevance)
            return photos[0]
    except Exception as e:
        print(f"[pexels] Search failed for '{query}': {e}")
        return None


def _download(url: str) -> bytes | None:
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        print(f"[pexels] Download failed: {e}")
        return None


def _resize_and_convert(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Resize to TARGET_WIDTH, lock aspect ratio
    orig_w, orig_h = img.size
    if orig_w != TARGET_WIDTH:
        ratio = TARGET_WIDTH / orig_w
        new_h = int(orig_h * ratio)
        img = img.resize((TARGET_WIDTH, new_h), Image.LANCZOS)

    # Convert to WebP
    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=85, method=6)
    return buffer.getvalue()
