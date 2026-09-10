"""
Scout Agent — Intelligence Gathering

Responsibilities:
  - Fetch AI news from Hacker News API + RSS feeds
  - Scan InBharat GitHub repos for new releases
  - Identify trending topics
  - Store signals in SQLite for Planner agent

Runs: 8 AM, 2 PM, 8 PM daily (via orchestrator)
"""

import os
import re
import json
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import httpx

from to_chuc import bat_buoc

# Cung hang so voi migrations.py/main.py/orchestrator.py.

# AI relevance keywords
AI_KEYWORDS = [
    r'\bAI\b', r'\bartificial intelligence\b', r'\bLLM\b', r'\bGPT\b',
    r'\bClaude\b', r'\bAnthropic\b', r'\bOpenAI\b', r'\bGemini\b',
    r'\bmachine learning\b', r'\bdeep learning\b', r'\bneural net',
    r'\bNLP\b', r'\bRAG\b', r'\btransformer\b', r'\bfine-tun',
    r'\bprompt\b', r'\bchatbot\b', r'\bautonomous agent',
    r'\bcomputer vision\b', r'\bMLOps\b', r'\bvector',
    r'\bembedding\b', r'\bOllama\b', r'\bHugging\s*Face\b',
    r'\bMistral\b', r'\bLlama\b', r'\bStable\s*Diffusion\b',
    r'\bMidjourney\b', r'\bCopilot\b', r'\bCodex\b',
]
AI_PATTERN = re.compile('|'.join(AI_KEYWORDS), re.IGNORECASE)

DB_PATH = Path(__file__).parent.parent / "socialflow.db"


def init_signals_table():
    """Create signals table if not exists."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT,
        summary TEXT,
        score INTEGER DEFAULT 0,
        relevance_score REAL DEFAULT 0.0,
        source_date TEXT,
        fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        processed INTEGER DEFAULT 0,
        signal_type TEXT DEFAULT 'news'
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_signals_url ON signals(url)''')
    conn.commit()
    conn.close()


def signal_exists(url: str) -> bool:
    """Check if a signal with this URL already exists."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT 1 FROM signals WHERE url = ?", (url,)).fetchone()
    conn.close()
    return row is not None


def save_signal(source: str, title: str, url: str, summary: str = "",
                score: int = 0, signal_type: str = "news"):
    """Save a signal to the database."""
    if signal_exists(url):
        return False
    relevance = _compute_relevance(title + " " + summary)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO signals (source, title, url, summary, score, relevance_score, source_date, signal_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source, title, url, summary, score, relevance, datetime.now().isoformat(), signal_type)
    )
    conn.commit()
    conn.close()
    return True


def _compute_relevance(text: str) -> float:
    """Compute AI relevance score 0-1 based on keyword density."""
    matches = AI_PATTERN.findall(text)
    words = len(text.split())
    if words == 0:
        return 0.0
    return min(1.0, len(matches) / max(words * 0.05, 1))


async def fetch_hackernews(max_stories: int = 15) -> List[Dict]:
    """Fetch top stories from Hacker News API, filtered for AI relevance."""
    signals = []
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=15.0
            )
            story_ids = resp.json()[:50]  # Check top 50, keep max_stories
        except Exception as e:
            print(f"Scout: HN API error: {e}")
            return signals

        for story_id in story_ids:
            if len(signals) >= max_stories:
                break
            try:
                resp = await client.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                    timeout=10.0
                )
                item = resp.json()
                if not item or item.get("type") != "story":
                    continue

                title = item.get("title", "")
                url = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                score = item.get("score", 0)

                # Filter: AI-relevant OR high score
                if AI_PATTERN.search(title) or score > 200:
                    saved = save_signal(
                        source="HackerNews",
                        title=title,
                        url=url,
                        summary=title,
                        score=score,
                        signal_type="news"
                    )
                    if saved:
                        signals.append({"title": title, "url": url, "score": score})
            except Exception:
                continue

    return signals


async def fetch_github_repos(username: str = "inbharatai") -> List[Dict]:
    """Scan GitHub user for new repos and releases."""
    signals = []
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"https://api.github.com/users/{username}/repos?sort=created&per_page=10",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=15.0
            )
            repos = resp.json()
        except Exception as e:
            print(f"Scout: GitHub API error: {e}")
            return signals

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        for repo in repos:
            if repo.get("fork"):
                continue
            created = repo.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt > cutoff:
                    url = repo["html_url"]
                    saved = save_signal(
                        source="GitHub",
                        title=f"New repo: {repo['name']}",
                        url=url,
                        summary=repo.get("description", "") or "",
                        score=repo.get("stargazers_count", 0),
                        signal_type="repo"
                    )
                    if saved:
                        signals.append({"title": repo["name"], "url": url, "type": "repo"})
            except (ValueError, KeyError):
                continue

        # Check latest releases for each repo
        for repo in repos[:5]:
            try:
                resp = await client.get(
                    f"https://api.github.com/repos/{username}/{repo['name']}/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=10.0
                )
                if resp.status_code != 200:
                    continue
                release = resp.json()
                published = release.get("published_at", "")
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if dt > datetime.now(timezone.utc) - timedelta(hours=24):
                    tag = release.get("tag_name", "")
                    url = release.get("html_url", "")
                    saved = save_signal(
                        source="GitHub",
                        title=f"Release: {repo['name']} {tag}",
                        url=url,
                        summary=release.get("body", "")[:300],
                        score=0,
                        signal_type="release"
                    )
                    if saved:
                        signals.append({"title": f"{repo['name']} {tag}", "url": url, "type": "release"})
            except Exception:
                continue

    return signals


async def run(organization_id: Optional[str] = None):
    """Main scout run — fetch all signal sources.

    organization_id is accepted only so the orchestrator can call every agent
    with the same signature — Scout itself ignores it: `signals` is one
    shared table, not organization-owned (see migrations.py, D1 A5).
    """
    init_signals_table()
    print(f"[Scout] Starting at {datetime.now().strftime('%H:%M:%S')}")

    hn_signals = await fetch_hackernews(max_stories=10)
    print(f"[Scout] HackerNews: {len(hn_signals)} new signals")

    gh_signals = await fetch_github_repos()
    print(f"[Scout] GitHub: {len(gh_signals)} new signals")

    total = len(hn_signals) + len(gh_signals)
    print(f"[Scout] Done — {total} new signals stored")
    return total


SQL_UNPROCESSED_SIGNALS = """SELECT s.* FROM signals s
           WHERE NOT EXISTS (
               SELECT 1 FROM signal_org_processed sop
               WHERE sop.signal_id = s.id AND sop.organization_id = ?
           )
           ORDER BY s.relevance_score DESC, s.score DESC
           LIMIT ?"""


def get_unprocessed_signals(organization_id: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Get signals this organization's Planner has not turned into a plan yet.

    `signals` is one shared table (Scout runs once for everybody, not once per
    organization -- see migrations.py). "Unprocessed" is therefore per
    organization, tracked in signal_org_processed, not a single shared
    `processed` flag on the signal itself (D1, A5).
    """
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        SQL_UNPROCESSED_SIGNALS,
        (org_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_processed(signal_ids: List[int], organization_id: Optional[str] = None):
    """Mark signals as processed BY THIS ORGANIZATION's Planner.

    Does not touch the shared `signals` row at all -- another organization's
    Planner must still see the same signal as unprocessed for itself.
    """
    if not signal_ids:
        return
    org_id = bat_buoc(organization_id)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executemany(
        "INSERT OR IGNORE INTO signal_org_processed (organization_id, signal_id) VALUES (?, ?)",
        [(org_id, sid) for sid in signal_ids]
    )
    conn.commit()
    conn.close()
