"""
Shopify Admin REST API client.

Handles:
- Uploading images as Shopify files (returns CDN URL)
- Resolving blog ID from handle
- Creating blog articles with body_html, featured image, SEO fields
"""

import os
import time
import base64
import httpx

SHOPIFY_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_BLOG_HANDLE = os.getenv("SHOPIFY_BLOG_HANDLE", "blog")
API_VERSION = "2024-10"


def _base_url() -> str:
    return f"https://{SHOPIFY_DOMAIN}/admin/api/{API_VERSION}"


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json",
    }


def upload_image(image_bytes: bytes, filename: str, alt_text: str) -> str | None:
    """
    Upload an image to Shopify Files and return the CDN URL.
    Uses the Files API (REST) via base64 attachment.
    Returns the public CDN URL or None on failure.
    """
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "image": {
            "attachment": b64,
            "filename": filename,
            "alt": alt_text,
        }
    }

    # We upload to a neutral product image endpoint then use the URL.
    # Actually use Shopify Files API which accepts base64 via GraphQL.
    # Fallback: use the older Assets approach via blogs.
    # Best supported REST approach: upload as a theme asset or use storefront.
    # We'll use the Shopify Files GraphQL API (supported on all plans).
    return _upload_via_graphql(image_bytes, filename, alt_text)


def _upload_via_graphql(image_bytes: bytes, filename: str, alt_text: str) -> str | None:
    """Upload image using Shopify Files GraphQL API (stagedUploadsCreate → fileCreate)."""
    graphql_url = f"https://{SHOPIFY_DOMAIN}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json",
    }

    # Step 1: Create a staged upload target
    stage_query = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets {
          url
          resourceUrl
          parameters { name value }
        }
        userErrors { field message }
      }
    }
    """
    variables = {
        "input": [
            {
                "filename": filename,
                "mimeType": "image/webp",
                "resource": "FILE",
                "fileSize": str(len(image_bytes)),
                "httpMethod": "POST",
            }
        ]
    }

    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                graphql_url,
                headers=headers,
                json={"query": stage_query, "variables": variables},
            )
            r.raise_for_status()
            data = r.json()

            targets = (
                data.get("data", {})
                .get("stagedUploadsCreate", {})
                .get("stagedTargets", [])
            )
            if not targets:
                errors = data.get("data", {}).get("stagedUploadsCreate", {}).get("userErrors", [])
                print(f"[shopify] stagedUploadsCreate errors: {errors}")
                return None

            target = targets[0]
            upload_url = target["url"]
            resource_url = target["resourceUrl"]
            params = {p["name"]: p["value"] for p in target["parameters"]}

            # Step 2: Upload binary to the staged URL (multipart POST to GCS/S3)
            files = {"file": (filename, image_bytes, "image/webp")}
            upload_r = client.post(upload_url, data=params, files=files)
            upload_r.raise_for_status()

            # Step 3: Create the file in Shopify Files
            create_query = """
            mutation fileCreate($files: [FileCreateInput!]!) {
              fileCreate(files: $files) {
                files {
                  id
                  fileStatus
                  ... on MediaImage {
                    image { url }
                    alt
                  }
                  ... on GenericFile {
                    url
                  }
                }
                userErrors { field message }
              }
            }
            """
            create_vars = {
                "files": [
                    {
                        "originalSource": resource_url,
                        "alt": alt_text,
                        "contentType": "IMAGE",
                    }
                ]
            }
            cr = client.post(
                graphql_url,
                headers=headers,
                json={"query": create_query, "variables": create_vars},
            )
            cr.raise_for_status()
            create_data = cr.json()

            files_created = (
                create_data.get("data", {}).get("fileCreate", {}).get("files", [])
            )
            errors = create_data.get("data", {}).get("fileCreate", {}).get("userErrors", [])

            if not files_created:
                print(f"[shopify] fileCreate returned no files. userErrors: {errors}")
                return None

            f = files_created[0]
            file_id = f.get("id")

            # If already processed, return immediately
            if f.get("image") and f["image"].get("url"):
                return f["image"]["url"]
            if f.get("url"):
                return f["url"]

            # Otherwise poll until READY (Shopify processes asynchronously)
            if file_id:
                return _poll_file_url(client, graphql_url, headers, file_id)

            print(f"[shopify] fileCreate: no id returned. response: {create_data}")
            return None

    except Exception as e:
        print(f"[shopify] Image upload failed: {e}")
        return None


def _poll_file_url(
    client: httpx.Client,
    graphql_url: str,
    headers: dict,
    file_id: str,
    max_attempts: int = 15,
    interval: float = 1.0,
) -> str | None:
    """Poll a Shopify file by ID until fileStatus == READY, then return the CDN URL."""
    node_query = """
    query getFile($id: ID!) {
      node(id: $id) {
        ... on MediaImage {
          id
          fileStatus
          image { url }
        }
        ... on GenericFile {
          id
          fileStatus
          url
        }
      }
    }
    """
    for attempt in range(max_attempts):
        try:
            r = client.post(
                graphql_url,
                headers=headers,
                json={"query": node_query, "variables": {"id": file_id}},
            )
            r.raise_for_status()
            node = r.json().get("data", {}).get("node") or {}
            status = node.get("fileStatus")
            if status == "READY":
                if node.get("image") and node["image"].get("url"):
                    return node["image"]["url"]
                if node.get("url"):
                    return node["url"]
                print(f"[shopify] file READY but no URL: {node}")
                return None
            if status == "FAILED":
                print(f"[shopify] file processing FAILED: {node}")
                return None
        except Exception as e:
            print(f"[shopify] poll error (attempt {attempt+1}): {e}")
        time.sleep(interval)

    print(f"[shopify] file {file_id} did not reach READY after {max_attempts} attempts")
    return None


def get_blog_id(handle: str = None) -> int | None:
    """Resolve a blog handle to its numeric ID."""
    handle = handle or SHOPIFY_BLOG_HANDLE
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{_base_url()}/blogs.json",
                headers=_headers(),
            )
            r.raise_for_status()
            blogs = r.json().get("blogs", [])
            for blog in blogs:
                if blog.get("handle") == handle:
                    return blog["id"]
            # Fallback: return first blog if handle not matched
            if blogs:
                print(f"[shopify] Blog handle '{handle}' not found, using first blog: {blogs[0]['handle']}")
                return blogs[0]["id"]
            return None
    except Exception as e:
        print(f"[shopify] get_blog_id failed: {e}")
        return None


def create_article(
    blog_id: int,
    title: str,
    body_html: str,
    featured_image_url: str | None,
    featured_image_alt: str | None,
    meta_title: str,
    meta_description: str,
    author: str = "Admin",
    published: bool = False,
) -> dict | None:
    """
    Create a Shopify blog article.
    Returns the created article dict or None on failure.
    """
    article_payload: dict = {
        "title": title,
        "body_html": body_html,
        "author": author,
        "published": published,
        "metafields": [
            {
                "key": "title_tag",
                "value": meta_title,
                "type": "single_line_text_field",
                "namespace": "global",
            },
            {
                "key": "description_tag",
                "value": meta_description,
                "type": "single_line_text_field",
                "namespace": "global",
            },
        ],
    }

    if featured_image_url:
        article_payload["image"] = {
            "src": featured_image_url,
            "alt": featured_image_alt or title,
        }

    payload = {"article": article_payload}

    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{_base_url()}/blogs/{blog_id}/articles.json",
                headers=_headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json().get("article")
    except httpx.HTTPStatusError as e:
        print(f"[shopify] create_article HTTP error: {e.response.status_code} {e.response.text}")
        return None
    except Exception as e:
        print(f"[shopify] create_article failed: {e}")
        return None
