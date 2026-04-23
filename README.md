# Shopify Blog Auto-Publisher

A FastAPI service that takes a `.docx` blog post and fully auto-publishes it to Shopify — complete with AI-sourced landscape images per section, WebP conversion, and AI-generated SEO metadata.

## What it does

1. **Parses** your `.docx` — extracts the H1 as the blog title and each H2 as a section
2. **Generates SEO** — Claude writes a meta title (≤60 chars) and meta description (≤155 chars) for the post
3. **Finds images** — Claude generates a smart Pexels query per H2 (and for the featured image), fetches a landscape photo
4. **Processes images** — Pillow resizes each photo to 1280px width (locked aspect ratio) and converts to WebP
5. **Generates alt text** — Claude writes descriptive alt text combining the H2 context and photo subject
6. **Uploads to Shopify** — images go to Shopify Files via GraphQL staged uploads; article is created via REST
7. **Creates the article** — body HTML has `<h2>` → image → content per section; featured image and SEO fields are set

Jobs run in the background — you get a `job_id` immediately and poll for status.

---

## Setup

### 1. Clone and install

This project uses a plain `venv` — no `pip` alias needed, just the `python3` you have from Homebrew.

```bash
git clone <repo>
cd shopify-blog-publisher

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
python3 -m pip install -r requirements.txt
```

To activate the venv in future sessions:
```bash
source .venv/bin/activate
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
PEXELS_API_KEY=...
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_...
SHOPIFY_BLOG_HANDLE=blog
```

**Getting your Shopify access token:**
- Go to Shopify Admin → Settings → Apps and sales channels → Develop apps
- Create a private app, grant these scopes: `write_content`, `read_content`, `write_files`, `read_files`
- Copy the Admin API access token

**Getting your Pexels API key:**
- Sign up at [pexels.com/api](https://www.pexels.com/api/)

### 3. Run the server

```bash
source .venv/bin/activate   # if not already active
uvicorn main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs

---

## Usage

### Publish a blog post

```bash
curl -X POST http://localhost:8000/publish-blog \
  -F "file=@my-blog-post.docx" \
  -F "author=Mau" \
  -F "published=false"
```

Response:
```json
{
  "job_id": "a1b2c3d4-...",
  "message": "Processing started. Poll /job/{job_id} for status."
}
```

### Poll for status

```bash
curl http://localhost:8000/job/a1b2c3d4-...
```

Status progression: `queued` → `parsing` → `ai` → `images` → `publishing` → `done` (or `error`)

Done response:
```json
{
  "status": "done",
  "article_id": 123456789,
  "article_url": "https://your-store.myshopify.com/blogs/blog/how-to-grow-rice",
  "title": "How to Grow Rice in the Philippines",
  "meta_title": "How to Grow Rice in the Philippines | Expert Guide",
  "meta_description": "Learn the best techniques for rice farming...",
  "featured_image_url": "https://cdn.shopify.com/...",
  "sections_processed": 5,
  "published": false
}
```

### Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `file` | `.docx` | required | Blog post document |
| `author` | string | `"Admin"` | Article author displayed on Shopify |
| `published` | bool | `false` | `true` = publish immediately, `false` = save as draft |
| `blog_handle` | string | from `.env` | Override the target blog (e.g. `news`, `blog`) |

---

## Document Format

Your `.docx` must follow this structure:

```
# Blog Post Title          ← H1 (becomes article title)

Intro paragraph here.      ← Regular text (optional intro before first H2)

## First Section Heading   ← H2 (image inserted below this)
Section content goes here.

## Second Section Heading  ← H2
More content...
```

- **H1** → article title (only first H1 is used)
- **H2** → section headings; a Pexels image is fetched and placed directly below each
- **H3/H4** → sub-headings, passed through as-is
- **Bold / italic** → preserved in body HTML
- **Bullet/numbered lists** → converted to `<ul>`/`<ol>`
- **Embedded images** → ignored (replaced by Pexels photos)

---

## Architecture

```
main.py                  FastAPI app + background job runner
app/
  parser.py              .docx → structured sections (title, H2s, content HTML)
  ai.py                  Claude API: Pexels queries, alt text, SEO metadata
  images.py              Pexels search + Pillow resize/WebP conversion
  shopify_client.py      Shopify GraphQL (file upload) + REST (article create)
  builder.py             Assembles final body_html with h2 + img + content
```

---

## Notes

- Articles are created as **drafts** by default (`published=false`). Review in Shopify admin before publishing.
- If a Pexels image can't be found for a section, that section is published without an image (no crash).
- The Shopify Files GraphQL upload uses **staged uploads** (works on all plan tiers).
- Job state is in-memory — restart the server and in-flight jobs are lost. For production, replace the `jobs` dict with Redis or a database.
