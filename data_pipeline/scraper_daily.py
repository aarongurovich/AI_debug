"""
RAG Debugging Assistant - DAILY incremental scraper.
Runs on schedule via GitHub Actions. Only fetches content from the last 24 hours.
Existing rows are skipped (no Gemini quota wasted).
"""
import os
import time
import html
import re
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client
from markdownify import markdownify as md

# --- Load credentials (from .env locally, GitHub Secrets in Actions) ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
SO_KEY = os.getenv("STACK_EXCHANGE_KEY") or None
GH_TOKEN = os.getenv("GITHUB_TOKEN")

# --- Init clients ---
gemini_client = genai.Client(api_key=GEMINI_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Config: DAILY (small, fast) ---
LANGUAGES = ["python", "java", "javascript", "c"]
SO_PAGES_PER_LANG = 1
SO_SEARCH_TERMS = ["error", "exception"]
GH_REPOS_PER_LANG = 10
GH_ISSUES_PER_REPO = 30
EMBED_DIMS = 768

# --- 24-hour recency cutoff ---
CUTOFF_DT = datetime.now(timezone.utc) - timedelta(hours=24)
CUTOFF_UNIX = int(CUTOFF_DT.timestamp())
CUTOFF_ISO = CUTOFF_DT.strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"[Daily incremental] Fetching content since {CUTOFF_ISO}")


def clean_html(text):
    if not text:
        return ""
    converted = md(text, heading_style="ATX", code_language="", bullets="-")
    converted = re.sub(r"\n{3,}", "\n\n", converted)
    return converted.strip()


ERROR_PATTERNS = {
    "python": [
        r"\b[A-Z][a-zA-Z]*(?:Error|Exception|Warning):\s*[^\n.]+",
        r"(?:fatal\s+)?[Ee]rror:\s*[^\n.]+",
    ],
    "java": [
        r"(?:java|javax|org)\.[a-zA-Z0-9.$]+(?:Exception|Error)(?::\s*[^\n]+)?",
        r"Exception\s+in\s+thread\s+\"[^\"]+\"\s+[^\n]+",
        r"\b[A-Z][a-zA-Z]*(?:Exception|Error)(?::\s*[^\n]+)?",
    ],
    "javascript": [
        r"\b(?:Type|Reference|Syntax|Range|Eval|URI)Error:\s*[^\n]+",
        r"\bUnhandled[A-Z][a-zA-Z]*:\s*[^\n]+",
        r"\b[A-Z][a-zA-Z]*Error:\s*[^\n]+",
    ],
    "c": [
        r"[\w./-]+\.[ch]:\d+:\d+:\s*(?:fatal\s+)?error:\s*[^\n]+",
        r"undefined reference to\s+[`'\"][^'\"\n]+[`'\"]",
        r"[Ss]egmentation fault(?:\s*\(core dumped\))?",
        r"(?:fatal\s+)?error:\s*[^\n]+",
        r"warning:\s*[^\n]+",
    ],
}


def extract_error_only(text, language, fallback_title=""):
    if not text:
        return fallback_title.strip()
    patterns = ERROR_PATTERNS.get(language, [])
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            error = match.group(0).strip()
            error = re.sub(r"\s+", " ", error)
            stripped = re.sub(r"[`\s\-_=*#:]+", "", error)
            if len(stripped) < 10:
                continue
            if error.count("`") >= 3:
                continue
            if "![" in error or "](http" in error:
                continue
            alpha_chars = sum(1 for c in error if c.isalpha())
            if alpha_chars < 8:
                continue
            tail = error.split(":", 1)[-1].strip()
            if len(tail) < 5:
                continue
            return error[:500]
    return fallback_title.strip()[:500]


CROSS_LANGUAGE_SIGNALS = {
    "python": ["Python.h:", "gcc ", "g++ ", "clang ", "x86_64-linux-gnu-gcc",
               "NullPointerException", "ClassCastException", ".java:",
               "npm install", "console.log("],
    "java": ["Traceback (most recent call last)", "pip install", "ImportError:",
             "npm install", "console.log(", ".py:", "def __init__"],
    "javascript": ["Traceback (most recent call last)", "pip install", "ImportError:",
                   "NullPointerException", "ClassCastException", ".java:",
                   "def __init__", "print("],
    "c": ["Traceback (most recent call last)", "pip install",
          "NullPointerException", "ClassCastException", ".java:",
          "def __init__", "console.log(", "npm install",
          "TypeError:", "ReferenceError:"],
}


def is_language_match(text, language):
    lower = text.lower()
    for signal in CROSS_LANGUAGE_SIGNALS.get(language, []):
        if signal.lower() in lower:
            return False
    return True


def embed_text(text, retries=3):
    for attempt in range(retries):
        try:
            result = gemini_client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBED_DIMS,
                ),
            )
            return result.embeddings[0].values
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 30 * (attempt + 1)
                print(f"  ! Gemini rate limit, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise Exception("Embed failed after retries")


def insert_solution(source_type, source_url, language, error_message, solution_text):
    try:
        existing = supabase.table("solutions").select("id").eq("source_url", source_url).limit(1).execute()
        if existing.data:
            return False  # already in DB

        combined = error_message + " " + solution_text
        if not is_language_match(combined, language):
            return False

        text_to_embed = f"Programming language: {language}. Error: {error_message}"
        embedding = embed_text(text_to_embed)

        supabase.table("solutions").upsert({
            "source_type": source_type,
            "source_url": source_url,
            "language": language,
            "error_message": error_message[:2000],
            "solution_text": solution_text[:5000],
            "embedding": embedding,
        }, on_conflict="source_url").execute()
        return True
    except Exception as e:
        print(f"  x Insert failed: {str(e)[:150]}")
        return False


def scrape_stackoverflow(language):
    print(f"\n[SO] {language}...")
    count = 0
    for search_term in SO_SEARCH_TERMS:
        for page in range(1, SO_PAGES_PER_LANG + 1):
            params = {
                "page": page, "pagesize": 20, "order": "desc", "sort": "creation",
                "tagged": language, "title": search_term, "site": "stackoverflow",
                "filter": "withbody", "fromdate": CUTOFF_UNIX,
            }
            if SO_KEY:
                params["key"] = SO_KEY
            try:
                r = requests.get("https://api.stackexchange.com/2.3/search/advanced",
                                 params=params, timeout=15)
                r.raise_for_status()
                questions = r.json().get("items", [])
            except Exception as e:
                print(f"  x SO list failed: {e}")
                continue

            for q in questions:
                if not q.get("is_answered") or q.get("answer_count", 0) == 0:
                    continue
                if q.get("creation_date", 0) < CUTOFF_UNIX:
                    continue
                q_id = q["question_id"]
                ans_params = {"order": "desc", "sort": "votes", "site": "stackoverflow",
                              "filter": "withbody", "pagesize": 1}
                if SO_KEY:
                    ans_params["key"] = SO_KEY
                try:
                    ar = requests.get(f"https://api.stackexchange.com/2.3/questions/{q_id}/answers",
                                      params=ans_params, timeout=15)
                    ar.raise_for_status()
                    answers = ar.json().get("items", [])
                except Exception:
                    continue
                if not answers:
                    continue
                full_body = clean_html(q["title"] + ". " + q.get("body", ""))
                error_msg = extract_error_only(full_body, language, fallback_title=q["title"])
                solution = clean_html(answers[0].get("body", ""))[:5000]
                if len(solution) < 50:
                    continue
                if insert_solution("stackoverflow", q["link"], language, error_msg, solution):
                    count += 1
                    print(f"  + {error_msg[:70]}")
                time.sleep(0.5)
    print(f"[SO] {language}: +{count} new")
    return count


GH_REPOS = {
    "python": [
        ("psf/requests", "Bug"), ("pandas-dev/pandas", "Bug"),
        ("scipy/scipy", "defect"), ("scrapy/scrapy", "bug"),
        ("matplotlib/matplotlib", "status: confirmed bug"),
        ("ansible/ansible", "bug"), ("scikit-learn/scikit-learn", "Bug"),
        ("python/cpython", "type-bug"), ("apache/airflow", "kind:bug"),
        ("huggingface/transformers", "bug"),
    ],
    "java": [
        ("spring-projects/spring-boot", "type: bug"), ("square/okhttp", "bug"),
        ("apache/maven", "bug"), ("google/guava", "type=defect"),
        ("netty/netty", "defect"), ("apache/dubbo", "type/bug"),
        ("alibaba/arthas", "bug"), ("quarkusio/quarkus", "kind/bug"),
        ("google/gson", "bug"), ("eclipse-vertx/vert.x", "bug"),
    ],
    "javascript": [
        ("facebook/react", "Type: Bug"), ("expressjs/express", "bug"),
        ("nodejs/node", "confirmed-bug"), ("microsoft/TypeScript", "Bug"),
        ("denoland/deno", "bug"), ("vercel/next.js", "bug"),
        ("webpack/webpack", "bug"), ("eslint/eslint", "bug"),
        ("storybookjs/storybook", "bug"), ("nuxt/nuxt", "bug"),
    ],
    "c": [
        ("ggerganov/llama.cpp", "bug"), ("netdata/netdata", "bug"),
        ("php/php-src", "Bug"), ("openzfs/zfs", "Type: Defect"),
        ("wazuh/wazuh", "type/bug"), ("nginx/nginx", "bug"),
        ("haproxy/haproxy", "type: bug"), ("ImageMagick/ImageMagick", "bug"),
        ("audacity/audacity", "bug"), ("gpac/gpac", "bug"),
    ],
}


def scrape_github(language):
    print(f"\n[GH] {language}...")
    count = 0
    repos = GH_REPOS.get(language, [])[:GH_REPOS_PER_LANG]
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    for repo, bug_label in repos:
        params = {"state": "closed", "sort": "updated", "direction": "desc",
                  "per_page": GH_ISSUES_PER_REPO, "labels": bug_label, "since": CUTOFF_ISO}
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/issues",
                             headers=headers, params=params, timeout=15)
            r.raise_for_status()
            issues = r.json()
        except Exception as e:
            print(f"  x {repo}: {e}")
            continue

        for issue in issues:
            if "pull_request" in issue:
                continue
            issue_num = issue["number"]
            try:
                cr = requests.get(issue["comments_url"], headers=headers, timeout=15)
                cr.raise_for_status()
                comments = cr.json()
            except Exception:
                continue
            if not comments:
                continue
            full_body = (issue.get("title", "") + "\n\n" + (issue.get("body") or ""))
            error_msg = extract_error_only(full_body, language, fallback_title=issue.get("title", ""))
            solution = max((c.get("body") or "" for c in comments), key=len)[:5000]
            if len(solution) < 50:
                continue
            if insert_solution("github", issue["html_url"], language, error_msg, solution):
                count += 1
                print(f"  + {repo}#{issue_num}: {error_msg[:60]}")
            time.sleep(0.3)

    print(f"[GH] {language}: +{count} new")
    return count


if __name__ == "__main__":
    print("=" * 60)
    print("Daily Incremental Scraper")
    print(f"Cutoff: {CUTOFF_ISO}")
    print("=" * 60)
    total = 0
    for lang in LANGUAGES:
        total += scrape_stackoverflow(lang)
        total += scrape_github(lang)
    print("\n" + "=" * 60)
    print(f"DONE. New rows added: {total}")
    print("=" * 60)