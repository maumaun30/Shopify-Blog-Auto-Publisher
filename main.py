"""
Shopify Blog Auto-Publisher
FastAPI app — POST /publish-blog with a .docx file upload.
"""

import os
import uuid
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from app.parser import parse_docx
from app.ai import (
    generate_pexels_query,
    generate_alt_text,
    generate_seo,
    generate_featured_image_query,
)
from app.images import fetch_and_process_image
from app.shopify_client import get_blog_id, upload_image, create_article
from app.builder import build_body_html

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory job store (swap for Redis/DB in production)
jobs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate required env vars on startup
    missing = [
        k for k in [
            "ANTHROPIC_API_KEY", "PEXELS_API_KEY",
            "SHOPIFY_STORE_DOMAIN", "SHOPIFY_ACCESS_TOKEN"
        ]
        if not os.getenv(k)
    ]
    if missing:
        print(f"⚠️  Missing env vars: {', '.join(missing)}")
    yield


app = FastAPI(
    title="Shopify Blog Auto-Publisher",
    description="Upload a .docx blog post and auto-publish it to Shopify with AI-sourced images and SEO.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/publish-blog", summary="Upload a .docx and publish to Shopify")
async def publish_blog(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Blog post .docx file"),
    author: str = Form(default="Admin", description="Article author name"),
    published: bool = Form(default=False, description="Publish immediately or save as draft"),
    blog_handle: str = Form(default=None, description="Shopify blog handle (overrides .env)"),
):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are accepted.")

    # Save uploaded file
    job_id = str(uuid.uuid4())
    tmp_path = UPLOAD_DIR / f"{job_id}.docx"
    contents = await file.read()
    tmp_path.write_bytes(contents)

    # Register job
    jobs[job_id] = {"status": "queued", "file": file.filename}

    # Process in background
    background_tasks.add_task(
        process_and_publish,
        job_id=job_id,
        file_path=str(tmp_path),
        author=author,
        published=published,
        blog_handle=blog_handle,
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "message": "Processing started. Poll /job/{job_id} for status.",
        },
    )


@app.get("/job/{job_id}", summary="Poll the status of a publish job")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# ---------------------------------------------------------------------------
# Core processing pipeline
# ---------------------------------------------------------------------------

async def process_and_publish(
    job_id: str,
    file_path: str,
    author: str,
    published: bool,
    blog_handle: str | None,
):
    def update(status: str, **kwargs):
        jobs[job_id] = {"status": status, **kwargs}

    try:
        # ── 1. Parse docx ────────────────────────────────────────────────
        update("parsing", message="Reading document structure…")
        parsed = await asyncio.to_thread(parse_docx, file_path)
        title = parsed["title"]
        sections = parsed["sections"]

        if not title:
            update("error", message="No H1 heading found in document.")
            return

        update("ai", message="Generating SEO metadata and image queries…")

        # Full plain text for SEO generation
        full_text = title + "\n" + "\n".join(
            sec["heading"] + "\n" + sec["content"] for sec in sections
        )

        # ── 2. SEO metadata ───────────────────────────────────────────────
        seo = await asyncio.to_thread(generate_seo, title, full_text)

        # ── 3. Featured image ─────────────────────────────────────────────
        intro_text = sections[0]["content"] if sections else ""
        featured_query = await asyncio.to_thread(
            generate_featured_image_query, title, intro_text
        )
        featured_alt = await asyncio.to_thread(generate_alt_text, title, featured_query)

        update("images", message=f"Fetching featured image ({featured_query})…")
        featured_bytes, featured_filename = await asyncio.to_thread(
            fetch_and_process_image, featured_query
        )

        featured_cdn_url = None
        if featured_bytes:
            update("images", message="Uploading featured image to Shopify…")
            featured_cdn_url = await asyncio.to_thread(
                upload_image, featured_bytes, featured_filename or "featured.webp", featured_alt
            )

        # ── 4. Per-section images ─────────────────────────────────────────
        enriched_sections = []
        for i, sec in enumerate(sections):
            heading = sec["heading"]
            if not heading:
                # Intro section — no image
                enriched_sections.append({**sec, "image_url": None, "image_alt": None})
                continue

            update(
                "images",
                message=f"Processing image for section {i+1}/{len(sections)}: '{heading}'…",
            )

            query = await asyncio.to_thread(
                generate_pexels_query, heading, sec["content"]
            )
            alt_text = await asyncio.to_thread(generate_alt_text, heading, query)

            img_bytes, img_filename = await asyncio.to_thread(
                fetch_and_process_image, query
            )

            cdn_url = None
            if img_bytes:
                cdn_url = await asyncio.to_thread(
                    upload_image, img_bytes, img_filename or f"section-{i+1}.webp", alt_text
                )

            enriched_sections.append(
                {
                    **sec,
                    "image_url": cdn_url,
                    "image_alt": alt_text,
                    "pexels_query": query,
                }
            )

        # ── 5. Build body HTML ────────────────────────────────────────────
        body_html = build_body_html(enriched_sections)

        # ── 6. Resolve blog ID ────────────────────────────────────────────
        update("publishing", message="Resolving Shopify blog…")
        blog_id = await asyncio.to_thread(get_blog_id, blog_handle)
        if not blog_id:
            update("error", message="Could not resolve Shopify blog. Check SHOPIFY_BLOG_HANDLE.")
            return

        # ── 7. Create article ─────────────────────────────────────────────
        update("publishing", message="Creating Shopify article…")
        article = await asyncio.to_thread(
            create_article,
            blog_id=blog_id,
            title=title,
            body_html=body_html,
            featured_image_url=featured_cdn_url,
            featured_image_alt=featured_alt,
            meta_title=seo["meta_title"],
            meta_description=seo["meta_description"],
            author=author,
            published=published,
        )

        if not article:
            update("error", message="Shopify article creation failed. Check server logs.")
            return

        # ── 8. Done ───────────────────────────────────────────────────────
        update(
            "done",
            article_id=article.get("id"),
            article_url=f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/blogs/{blog_handle or os.getenv('SHOPIFY_BLOG_HANDLE', 'blog')}/{article.get('handle')}",
            title=title,
            meta_title=seo["meta_title"],
            meta_description=seo["meta_description"],
            featured_image_url=featured_cdn_url,
            sections_processed=len(enriched_sections),
            published=published,
        )

    except Exception as e:
        import traceback
        update("error", message=str(e), traceback=traceback.format_exc())
    finally:
        # Clean up temp file
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass