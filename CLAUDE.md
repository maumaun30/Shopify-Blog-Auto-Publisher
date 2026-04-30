# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in API keys

# Run the development server
uvicorn main:app --reload --port 8000

# API docs
open http://localhost:8000/docs

# Test a publish job (requires a .docx file)
curl -X POST http://localhost:8000/publish-blog \
  -F "file=@your-file.docx"
# Then poll: curl http://localhost:8000/job/{job_id}
```

No test suite exists. There is no Makefile, Docker setup, or CI/CD.

## Architecture

**Entry point**: `main.py` — FastAPI app with two routes:
- `POST /publish-blog` — accepts `.docx` upload, saves to `uploads/{job_id}.docx`, registers an in-memory job, fires a background task, returns `job_id` immediately
- `GET /job/{job_id}` — returns current job status and result

**Pipeline** (runs asynchronously via `asyncio.to_thread` for all blocking I/O):

```
parser.py → ai.py → images.py → shopify_client.py → builder.py
```

1. **`app/parser.py`** — Parses `.docx` using `python-docx`: H1 → title, H2 → section boundaries, inline formatting and lists → HTML
2. **`app/ai.py`** — Three Claude API calls: SEO metadata, featured image query, per-section Pexels queries, alt text generation
3. **`app/images.py`** — Fetches landscape photos from Pexels via `httpx`, processes with Pillow (resize to 1280px wide, WebP at quality=85)
4. **`app/shopify_client.py`** — 3-step GraphQL staged upload for images, then REST API article creation with SEO metafields
5. **`app/builder.py`** — Assembles final `body_html` from enriched sections

## Key Constraints and Patterns

- **In-memory job state**: `jobs` dict in `main.py` is ephemeral — lost on restart. README notes Redis/DB needed for production.
- **Async bridging**: All I/O uses `asyncio.to_thread()`. Underlying libs (`httpx`, `anthropic`, `python-docx`, Pillow) are used synchronously inside threads.
- **Shopify API version**: Pinned to `2024-10` in `shopify_client.py`.
- **Image failures are non-fatal**: Missing Pexels results or failed uploads skip that section silently — no abort.
- **`.docx` requirement**: Must use Word heading styles (Heading 1, Heading 2), not manual font sizing.
- **No auth layer**: Service is intended for local/private use only.
- **Claude model**: Currently uses `claude-sonnet-4-5` in `app/ai.py`.

## Required Environment Variables

See `.env.example`:
- `ANTHROPIC_API_KEY`
- `PEXELS_API_KEY`
- `SHOPIFY_STORE_DOMAIN` (e.g. `your-store.myshopify.com`)
- `SHOPIFY_ACCESS_TOKEN` (needs `write_content`, `read_content`, `write_files`, `read_files` scopes)
- `SHOPIFY_BLOG_HANDLE` (default: `blog`)
