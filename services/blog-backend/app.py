#!/usr/bin/env python3
"""
Flask blog backend: reads .md articles, serves JSON API with search, related
articles, reading time, and JSON-LD structured data.
"""
import json
import os
import re
import random
from datetime import datetime

from flask import Flask, jsonify, request, abort
import frontmatter
import markdown

# ── config ──────────────────────────────────────────────────────────────
ARTICLES_DIR = os.environ.get("ARTICLES_DIR", "/app/articles")
CHINESE_CHARS_PER_MINUTE = 400
ENGLISH_WORDS_PER_MINUTE = 200
CODE_LINE_WEIGHT = 0.3
SITE_URL = os.environ.get("SITE_URL", "https://example.com")

app = Flask(__name__)

_all_articles = None
_slug_index = None


# ── markdown → HTML ──────────────────────────────────────────────────────

md_converter = markdown.Markdown(
    extensions=["fenced_code", "codehilite", "tables", "toc"],
    extension_configs={
        "codehilite": {"css_class": "highlight"},
    },
)


def md_to_html(text: str) -> str:
    return md_converter.reset().convert(text)


# ── reading time ────────────────────────────────────────────────────────

def calculate_reading_time(body: str) -> int:
    parts = re.split(r"```[\s\S]*?```", body)
    code_blocks = re.findall(r"```[\s\S]*?```", body)

    non_code = " ".join(parts)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", non_code))
    english_words = len(re.findall(r"[a-zA-Z]+", non_code))

    non_code_minutes = chinese / CHINESE_CHARS_PER_MINUTE + english_words / ENGLISH_WORDS_PER_MINUTE

    code_lines = 0
    for cb in code_blocks:
        code_lines += len(cb.strip().split("\n")) - 2
    code_minutes = max(0, code_lines) * CODE_LINE_WEIGHT / 20

    return max(1, round(non_code_minutes + code_minutes))


# ── article loading ─────────────────────────────────────────────────────

def load_articles() -> list[dict]:
    articles = []
    if not os.path.isdir(ARTICLES_DIR):
        return articles

    for fname in sorted(os.listdir(ARTICLES_DIR)):
        if not fname.endswith(".md"):
            continue
        slug = fname[:-3]
        fpath = os.path.join(ARTICLES_DIR, fname)

        with open(fpath, "r", encoding="utf-8") as fh:
            post = frontmatter.load(fh)

        body = post.content
        meta = post.metadata

        title = meta.get("title", slug)
        date = meta.get("date", "")
        tags = meta.get("tags", [])
        summary = meta.get("summary", "")

        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        reading_time = calculate_reading_time(body)

        articles.append({
            "slug": slug,
            "title": title,
            "date": str(date),
            "tags": tags,
            "summary": summary,
            "reading_time": reading_time,
            "body_md": body,
        })

    articles.sort(key=lambda a: a["date"], reverse=True)
    return articles


def get_articles():
    global _all_articles, _slug_index
    if _all_articles is None:
        _all_articles = load_articles()
        _slug_index = {a["slug"]: a for a in _all_articles}
    return _all_articles, _slug_index


# ── JSON-LD helper ──────────────────────────────────────────────────────

def make_jsonld(article: dict) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "datePublished": article["date"],
        "description": article["summary"],
        "author": {
            "@type": "Person",
            "name": "Blog Author",
            "url": SITE_URL,
        },
        "publisher": {
            "@type": "Person",
            "name": "Blog Author",
            "url": SITE_URL,
        },
        "url": f"{SITE_URL}/blog/{article['slug']}",
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"{SITE_URL}/blog/{article['slug']}",
        },
        "keywords": ", ".join(article.get("tags", [])),
        "wordCount": len(article.get("body_md", "")),
    }


# ── routes ──────────────────────────────────────────────────────────────

@app.route("/")
def article_list():
    articles, _ = get_articles()
    result = []
    for a in articles:
        result.append({
            "slug": a["slug"],
            "title": a["title"],
            "date": a["date"],
            "tags": a["tags"],
            "summary": a["summary"],
            "reading_time": a["reading_time"],
        })
    return jsonify(result)


@app.route("/<slug>")
def article_detail(slug: str):
    _, idx = get_articles()
    article = idx.get(slug)
    if not article:
        abort(404, description=f"Article '{slug}' not found")

    html_body = md_to_html(article["body_md"])

    return jsonify({
        "slug": article["slug"],
        "title": article["title"],
        "date": article["date"],
        "tags": article["tags"],
        "summary": article["summary"],
        "reading_time": article["reading_time"],
        "html": html_body,
        "jsonld": make_jsonld(article),
    })


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    articles, _ = get_articles()
    results = []
    q_lower = q.lower()

    for a in articles:
        score = 0
        if q_lower in a["title"].lower():
            score += 10
        for tag in a["tags"]:
            if q_lower in tag.lower():
                score += 5
        body_lower = a["body_md"].lower()
        count = body_lower.count(q_lower)
        score += count * 2

        if score > 0:
            results.append({
                "slug": a["slug"],
                "title": a["title"],
                "date": a["date"],
                "tags": a["tags"],
                "summary": a["summary"],
                "reading_time": a["reading_time"],
                "score": score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return jsonify(results[:20])


@app.route("/related/<slug>")
def related_articles(slug: str):
    articles, idx = get_articles()
    current = idx.get(slug)
    if not current:
        abort(404, description=f"Article '{slug}' not found")

    current_tags = set(current["tags"])
    candidates = []

    for a in articles:
        if a["slug"] == slug:
            continue
        overlap = len(current_tags & set(a["tags"]))
        if overlap > 0:
            candidates.append((overlap, a))

    candidates.sort(key=lambda x: (-x[0], random.random()))

    result = []
    for _, a in candidates[:3]:
        result.append({
            "slug": a["slug"],
            "title": a["title"],
            "date": a["date"],
            "tags": a["tags"],
            "summary": a["summary"],
            "reading_time": a["reading_time"],
        })

    return jsonify(result)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(404)
def not_found(err):
    return jsonify({"error": str(err.description)}), 404


@app.errorhandler(500)
def server_error(_err):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    articles, _ = get_articles()
    print(f"Blog backend ready — {len(articles)} articles loaded")
    app.run(host="0.0.0.0", port=5000, debug=False)
