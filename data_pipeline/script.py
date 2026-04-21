"""
RAG Debugging Assistant - Knowledge Base Scraper
Scrapes Stack Overflow + GitHub solutions for Python/Java/JavaScript errors,
embeds them with Gemini gemini-embedding-001 (768 dims), stores in Supabase pgvector.
"""
import os
import time
import html
import re
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client

# --- Load credentials ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
SO_KEY = os.getenv("STACK_EXCHANGE_KEY") or None
GH_TOKEN = os.getenv("GITHUB_TOKEN")

# --- Init clients ---
gemini_client = genai.Client(api_key=GEMINI_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Config ---
LANGUAGES = ["python", "java", "javascript", "c"]
SO_PAGES_PER_LANG = 3
GH_REPOS_PER_LANG = 5
GH_ISSUES_PER_REPO = 10
EMBED_DIMS = 768


# ========== Helpers ==========

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Regex patterns for extracting error lines per language
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
        # Compiler errors: file.c:42:1: error: expected ';'
        r"[\w./-]+\.[ch]:\d+:\d+:\s*(?:fatal\s+)?error:\s*[^\n]+",
        # Linker: undefined reference to `foo'
        r"undefined reference to\s+[`'\"][^'\"\n]+[`'\"]",
        # Segfault / runtime
        r"[Ss]egmentation fault(?:\s*\(core dumped\))?",
        # Generic "fatal error:" or "error:" lines
        r"(?:fatal\s+)?error:\s*[^\n]+",
        # Warnings
        r"warning:\s*[^\n]+",
    ],
}


def extract_error_only(text, language, fallback_title=""):
    """
    Extract just the error line from a full Stack Overflow question body.
    Falls back to the question title if no pattern matches.
    """
    if not text:
        return fallback_title.strip()

    patterns = ERROR_PATTERNS.get(language, [])
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            # Clean up the match - trim whitespace, cap length
            error = match.group(0).strip()
            # Remove trailing junk after the error message
            error = re.sub(r"\s+", " ", error)
            return error[:500]

    # No pattern matched — fall back to just the question title
    return fallback_title.strip()[:500]


# Cross-language pollution: content that should NOT be embedded as [language]
CROSS_LANGUAGE_SIGNALS = {
    "python": [
        "Python.h:", "gcc ", "g++ ", "clang ", "x86_64-linux-gnu-gcc",
        "NullPointerException", "ClassCastException", ".java:",
        "npm install", "console.log(",
    ],
    "java": [
        "Traceback (most recent call last)", "pip install", "ImportError:",
        "npm install", "console.log(", ".py:", "def __init__",
    ],
    "javascript": [
        "Traceback (most recent call last)", "pip install", "ImportError:",
        "NullPointerException", "ClassCastException", ".java:",
        "def __init__", "print(",
    ],
    "c": [
        "Traceback (most recent call last)", "pip install",
        "NullPointerException", "ClassCastException", ".java:",
        "def __init__", "console.log(", "npm install",
        "TypeError:", "ReferenceError:",
    ],
}


def is_language_match(text, language):
    """Reject content that's obviously from the wrong language."""
    lower = text.lower()
    for signal in CROSS_LANGUAGE_SIGNALS.get(language, []):
        if signal.lower() in lower:
            return False
    return True


def embed_text(text, retries=3):
    """Generate a 768-dim embedding using Gemini. Retries on rate limits."""
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
        # Filter out cross-language pollution before embedding
        combined = error_message + " " + solution_text
        if not is_language_match(combined, language):
            print(f"  ~ Skipped (wrong language content): {source_url[:70]}")
            return False

        # Embed only the error (what users will search with),
        # not the solution (what they haven't seen yet).
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


# ========== Stack Overflow Scraper ==========

def scrape_stackoverflow(language):
    """Top-voted questions in [language] with 'error' or 'exception' in title."""
    print(f"\n[Stack Overflow] Scraping {language}...")
    count = 0

    for search_term in ["error", "exception"]:
        for page in range(1, SO_PAGES_PER_LANG + 1):
            url = "https://api.stackexchange.com/2.3/search/advanced"
            params = {
                "page": page,
                "pagesize": 10,
                "order": "desc",
                "sort": "votes",
                "tagged": language,
                "title": search_term,
                "site": "stackoverflow",
                "filter": "withbody",
                "accepted": "True",
            }
            if SO_KEY:
                params["key"] = SO_KEY

            try:
                r = requests.get(url, params=params, timeout=15)
                r.raise_for_status()
                questions = r.json().get("items", [])
            except Exception as e:
                print(f"  x Page {page} failed: {e}")
                continue

            for q in questions:
                if not q.get("is_answered") or q.get("answer_count", 0) == 0:
                    continue

                q_id = q["question_id"]
                ans_url = f"https://api.stackexchange.com/2.3/questions/{q_id}/answers"
                ans_params = {
                    "order": "desc",
                    "sort": "votes",
                    "site": "stackoverflow",
                    "filter": "withbody",
                    "pagesize": 1,
                }
                if SO_KEY:
                    ans_params["key"] = SO_KEY

                try:
                    ar = requests.get(ans_url, params=ans_params, timeout=15)
                    ar.raise_for_status()
                    answers = ar.json().get("items", [])
                except Exception as e:
                    print(f"  x Answers fetch failed Q{q_id}: {e}")
                    continue

                if not answers:
                    continue

                # Extract just the error, not the whole question
                full_body = clean_html(q["title"] + ". " + q.get("body", ""))
                error_msg = extract_error_only(full_body, language, fallback_title=q["title"])
                solution = clean_html(answers[0].get("body", ""))[:3000]
                source_url = q["link"]

                if len(solution) < 50:
                    continue

                if insert_solution("stackoverflow", source_url, language, error_msg, solution):
                    count += 1
                    print(f"  + [{count}] {error_msg[:70]}")

                time.sleep(1.0)

            time.sleep(2)

    print(f"[Stack Overflow] {language}: inserted {count}")
    return count


# ========== GitHub Scraper ==========

GH_REPOS = {
    "python": [
        "psf/requests", "pallets/flask", "django/django",
        "pandas-dev/pandas", "numpy/numpy",
    ],
    "java": [
        "spring-projects/spring-boot", "google/guava", "apache/maven",
        "elastic/elasticsearch", "square/okhttp",
    ],
    "javascript": [
        "facebook/react", "vuejs/vue", "expressjs/express",
        "nodejs/node", "axios/axios",
    ],
    "c": [
        "curl/curl", "git/git", "torvalds/linux",
        "postgres/postgres", "redis/redis",
    ],
}


def scrape_github(language):
    print(f"\n[GitHub] Scraping {language}...")
    count = 0
    repos = GH_REPOS.get(language, [])[:GH_REPOS_PER_LANG]
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    for repo in repos:
        url = f"https://api.github.com/repos/{repo}/issues"
        params = {
            "state": "closed",
            "sort": "comments",
            "direction": "desc",
            "per_page": GH_ISSUES_PER_REPO,
            "labels": "bug",
        }

        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            r.raise_for_status()
            issues = r.json()
        except Exception as e:
            print(f"  x Repo {repo} failed: {e}")
            continue

        for issue in issues:
            if "pull_request" in issue:
                continue

            issue_num = issue["number"]
            comments_url = issue["comments_url"]

            try:
                cr = requests.get(comments_url, headers=headers, timeout=15)
                cr.raise_for_status()
                comments = cr.json()
            except Exception as e:
                print(f"  x Comments failed {repo}#{issue_num}: {e}")
                continue

            if not comments:
                continue

            full_body = (issue.get("title", "") + "\n\n" + (issue.get("body") or ""))
            error_msg = extract_error_only(full_body, language, fallback_title=issue.get("title", ""))
            solution = max((c.get("body") or "" for c in comments), key=len)[:3000]

            if len(solution) < 50:
                continue

            if insert_solution("github", issue["html_url"], language, error_msg, solution):
                count += 1
                print(f"  + [{count}] {repo}#{issue_num}: {error_msg[:60]}")

            time.sleep(1.0)

        time.sleep(2)

    print(f"[GitHub] {language}: inserted {count}")
    return count


# ========== Main ==========

if __name__ == "__main__":
    print("=" * 60)
    print("RAG Debugging Assistant - Knowledge Base Builder")
    print("=" * 60)

    total = 0
    for lang in LANGUAGES:
        total += scrape_stackoverflow(lang)
        total += scrape_github(lang)

    print("\n" + "=" * 60)
    print(f"DONE. Total solutions inserted: {total}")
    print("=" * 60)