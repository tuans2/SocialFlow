"""
Reviewer Agent — Quality + Compliance

Responsibilities:
  - Validate claims (no fabricated stats)
  - Check brand voice alignment
  - Scan for credential/PII leaks
  - Enforce rate-limits and posting windows
  - Auto-approve safe content, flag risky content for manual review

Runs: After Creator completes
"""

import os
import re
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DB_PATH = Path(__file__).parent.parent / "socialflow.db"

# Cung hang so voi migrations.py/main.py/orchestrator.py.
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")

# Credential patterns to block
CREDENTIAL_PATTERNS = [
    r'(?:api[_-]?key|secret|token|password|passwd|pwd)\s*[=:]\s*\S+',
    r'sk-[a-zA-Z0-9]{20,}',           # OpenAI keys
    r'ghp_[a-zA-Z0-9]{36}',           # GitHub tokens
    r'xoxb-[a-zA-Z0-9\-]+',           # Slack tokens
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b.*password',  # Email + password
]

# Fabricated claim patterns
FABRICATION_PATTERNS = [
    r'\d{2,3}%\s+(?:increase|decrease|growth|improvement|reduction)',  # "95% improvement"
    r'(?:millions?|billions?)\s+(?:users?|downloads?|installs?)',       # "millions of users"
    r'(?:first|only|#1)\s+(?:in the world|globally|ever)',             # Superlatives
    r'studies?\s+(?:show|prove|confirm)',                               # Unattributed studies
]

# Brand voice negative signals
BRAND_VIOLATIONS = [
    r'game[\s-]?changer',
    r'revolutionary',
    r'disrupt(?:ive|ing|or)',
    r'paradigm\s+shift',
    r'synergy',
    r'leverage',
    r'circle\s+back',
    r'move\s+the\s+needle',
    r'at\s+the\s+end\s+of\s+the\s+day',
    r'low[\s-]?hanging\s+fruit',
]

# Internal metadata that should never appear in public posts
METADATA_PATTERNS = [
    r'approval_level\s*:', r'risk_score\s*:', r'content_id\s*:',
    r'source_file\s*:', r'webhook_ready\s*:', r'status\s*:.*pending',
    r'\{["\'](?:platform_content|image_brief|cover_path)',
    r'```(?:json|yaml)',
]


def review_post(post: Dict) -> Tuple[str, List[str]]:
    """Review a post and return (status, issues).

    Returns:
        status: 'approved', 'review', or 'blocked'
        issues: List of issue descriptions
    """
    content = post.get("content", "")
    issues = []

    # Gate 1: Credential Safety
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(f"CREDENTIAL LEAK: matches pattern '{pattern[:30]}...'")
            return "blocked", issues

    # Gate 2: Metadata leakage
    for pattern in METADATA_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(f"METADATA LEAK: internal field detected")
            return "blocked", issues

    # Gate 3: Fabricated claims (warning, not block)
    for pattern in FABRICATION_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(f"UNVERIFIED CLAIM: '{re.search(pattern, content, re.IGNORECASE).group()}'")

    # Gate 4: Brand voice
    for pattern in BRAND_VIOLATIONS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(f"BRAND VOICE: '{re.search(pattern, content, re.IGNORECASE).group()}'")

    # Gate 5: Length check
    platform = post.get("platform", "")
    max_chars = {"x": 280, "discord": 2000, "linkedin": 3000, "instagram": 2200}.get(platform, 5000)
    if len(content) > max_chars:
        issues.append(f"TOO LONG: {len(content)} chars (max {max_chars} for {platform})")

    # Gate 6: Empty or too short
    if len(content.strip()) < 20:
        issues.append("TOO SHORT: Post has less than 20 characters")
        return "blocked", issues

    # Decision
    if not issues:
        return "approved", []
    elif any("CREDENTIAL" in i or "METADATA" in i for i in issues):
        return "blocked", issues
    elif len(issues) >= 3:
        return "review", issues  # Too many warnings → manual review
    else:
        return "approved", issues  # Minor issues → auto-approve with warnings


async def run(organization_id: Optional[str] = None):
    """Main reviewer run — review this organization's draft posts."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    print(f"[Reviewer] Starting at {datetime.now().strftime('%H:%M:%S')} for org '{org_id}'")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    drafts = conn.execute(
        "SELECT * FROM posts WHERE status = 'draft' AND COALESCE(organization_id, ?) = ? "
        "ORDER BY created_at ASC",
        (DEFAULT_ORGANIZATION_ID, org_id)
    ).fetchall()

    approved = 0
    review = 0
    blocked = 0

    for draft in drafts:
        post = dict(draft)
        status, issues = review_post(post)

        conn.execute(
            "UPDATE posts SET status = ? WHERE id = ?",
            (status, post["id"])
        )

        if status == "approved":
            approved += 1
            print(f"[Reviewer] #{post['id']} {post['platform']}: APPROVED")
        elif status == "review":
            review += 1
            print(f"[Reviewer] #{post['id']} {post['platform']}: NEEDS REVIEW — {'; '.join(issues)}")
        else:
            blocked += 1
            print(f"[Reviewer] #{post['id']} {post['platform']}: BLOCKED — {'; '.join(issues)}")

        if issues:
            # Store review notes
            conn.execute(
                "UPDATE posts SET error_message = ? WHERE id = ?",
                (json.dumps(issues) if len(issues) > 1 else issues[0], post["id"])
            )

    conn.commit()
    conn.close()

    print(f"[Reviewer] Done — approved: {approved}, review: {review}, blocked: {blocked}")
    return {"approved": approved, "review": review, "blocked": blocked}


import json
