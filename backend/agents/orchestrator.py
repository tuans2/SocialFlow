"""
Agent Orchestrator — Coordinates the 6-agent pipeline

Pipeline: Scout → Planner → Creator → Reviewer → Publisher → Analyst

Each agent runs sequentially. The orchestrator is triggered by:
  - APScheduler (automatic daily schedule)
  - Manual API call (POST /api/pipeline/run)
  - Dashboard "Run Pipeline" button
"""

import asyncio
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from agents import scout, planner, creator, reviewer, publisher, analyst

# Cung hang so voi migrations.py, analytics_store.py, main.py -- to chuc
# khong truyen organization_id thi coi la to chuc mac dinh (tuong thich
# nguoc voi du lieu truoc D1).
DEFAULT_ORGANIZATION_ID = os.getenv("SOCIALFLOW_DEFAULT_ORGANIZATION_ID", "default")

# A6 (Unified Shell Sec 7): so to chuc chay cung mot luc trong lich tu dong.
# A2 chot "lo song song co gioi han" (vd 5-10) thay vi tuan tu tung to chuc --
# day la con so do thanh hien thuc.
BATCH_SIZE = int(os.getenv("SOCIALFLOW_PIPELINE_BATCH_SIZE", "8"))

DB_PATH = Path(__file__).parent.parent / "socialflow.db"


class PipelineStatus:
    """Track current pipeline state for the dashboard.

    A3 (Unified Shell Sec 7, D1 Phase 2): truoc ban sua nay, trang thai la mot
    bien toan cuc duy nhat khong phan biet to chuc nao dang chay -- to chuc
    thu hai goi run_full_pipeline() trong luc to chuc thu nhat dang chay se
    am tham nhan lai last_result CU cua to chuc thu nhat, tuong do la ket qua
    cua chinh minh. current_organization_id ghi lai to chuc nao dang giu luot
    chay, de tu choi ro rang thay vi tra nham du lieu.

    Day CHUA phai chay song song that theo to chuc (do la A2 -- lo song song
    co gioi han, con cho A4/A5 dua organization_id vao bang du lieu va cac
    agent truoc khi bat). Pipeline van chi chay mot luot tai mot thoi diem
    (single-flight) -- ban sua nay chi khien viec tu choi luot chay thu hai
    tro nen tuong minh va dung to chuc, thay vi tra nham ket qua.
    """
    def __init__(self):
        self.running = False
        self.current_agent = None
        self.current_organization_id = None
        self.last_run = None
        self.last_result = {}
        self.error = None
        # Đo thời gian thật mỗi bước (A2b, Unified Shell §7) — số đo này
        # quyết định giới hạn song song cho A2 (lô song song có giới hạn),
        # không phải đoán.
        self.last_stage_seconds = {}

    def to_dict(self):
        return {
            "running": self.running,
            "current_agent": self.current_agent,
            "current_organization_id": self.current_organization_id,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "error": self.error,
            "last_stage_seconds": self.last_stage_seconds,
        }


# A6: MOT PipelineStatus RIENG cho moi to chuc, khong con mot bien toan cuc
# duy nhat dung chung (do la ly do A3 phai TU CHOI luot chay thu hai thay vi
# cho chay that su song song). Voi du lieu da duoc cach ly theo to chuc o
# A4/A5, hai to chuc khac nhau chay dong thoi la an toan; chi mot to chuc
# CHAY HAI LAN CUNG LUC voi chinh no moi can chan (van single-flight, nhung
# gio la single-flight THEO TUNG TO CHUC, khong phai toan tien trinh).
_status_by_org: Dict[str, "PipelineStatus"] = {}


def get_status(organization_id: Optional[str] = None) -> "PipelineStatus":
    """Return (creating if needed) the PipelineStatus for one organization."""
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    if org_id not in _status_by_org:
        _status_by_org[org_id] = PipelineStatus()
    return _status_by_org[org_id]


def list_known_organizations() -> List[str]:
    """Organizations the automatic schedule should run for.

    SocialFlow has no organizations table of its own (organizations live in
    floraos-core). `accounts` is the one table every real, onboarded
    organization MUST have at least one row in to ever publish anything
    (Phase 1 rebuilt it as UNIQUE(organization_id, platform)) -- so it
    doubles as the roster of "organizations actually using this instance."
    Falls back to [DEFAULT_ORGANIZATION_ID] so a fresh install with no
    accounts yet still runs the historical single-organization behavior.
    """
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(organization_id, ?) FROM accounts",
            (DEFAULT_ORGANIZATION_ID,)
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    orgs = sorted({r[0] for r in rows if r[0]})
    return orgs or [DEFAULT_ORGANIZATION_ID]


async def run_full_pipeline(ai_generate_fn: Callable = None,
                            automation_getter: Callable = None,
                            skip_publish: bool = False,
                            organization_id: Optional[str] = None,
                            run_scout: bool = True):
    """Run the complete pipeline: [Scout →] Planner → Creator → Reviewer → [Publisher] → Analyst,
    for ONE organization.

    Args:
        ai_generate_fn: Async function for content generation (from main.py)
        automation_getter: Function(platform, organization_id) -> automation instance
        skip_publish: If True, skip Publisher agent (useful for manual review first)
        organization_id: Which organization to run this for. Every agent call
            below is scoped to it (D1 A4/A5).
        run_scout: If False, skip the Scout stage entirely -- used by
            run_scheduled_cycle() (A6), which runs Scout ONCE for everybody
            (signals is one shared table, D1 A5) and then calls this
            function per organization for the rest of the pipeline. Manual/
            on-demand single-organization runs (the dashboard button,
            POST /api/pipeline/run) keep the default True.

    Busy-checking and the returned status object are now PER ORGANIZATION
    (get_status(organization_id), A6) -- two different organizations calling
    this concurrently no longer block or interfere with each other at all;
    only the SAME organization calling it twice while its own run is still
    in flight gets the busy-rejection A3 added.
    """
    org_id = organization_id or DEFAULT_ORGANIZATION_ID
    status = get_status(org_id)

    if status.running:
        print(
            f"[Orchestrator] Pipeline dang chay cho to chuc '{org_id}' "
            f"-- tu choi yeu cau moi tu chinh to chuc do (khong tra ket qua cu)"
        )
        return {
            "error": "pipeline_busy",
            "message": (
                f"Pipeline dang chay cho to chuc '{org_id}'. "
                f"Vui long thu lai sau khi luot chay do hoan tat."
            ),
            "running_organization_id": org_id,
            "requested_organization_id": org_id,
        }

    status.running = True
    status.current_organization_id = org_id
    status.error = None
    result = {}
    stage_seconds = {}
    pipeline_started = time.monotonic()

    async def _timed_stage(name: str, label: str, coro):
        status.current_agent = name
        print(f"\n[Orchestrator][{org_id}] ═══ {label} ═══")
        started = time.monotonic()
        value = await coro
        elapsed = time.monotonic() - started
        stage_seconds[name] = round(elapsed, 1)
        print(f"[Orchestrator][{org_id}] {name} took {elapsed:.1f}s")
        return value

    try:
        # Agent 1: Scout — shared across every organization (D1 A5). Skipped
        # when run_scheduled_cycle() already ran it once for this cycle.
        if run_scout:
            result["scout"] = await _timed_stage("scout", "STAGE 1: SCOUT", scout.run(organization_id=org_id))
        else:
            result["scout"] = "skipped-shared"
            stage_seconds["scout"] = 0.0

        # Agent 2: Planner
        result["planner"] = await _timed_stage(
            "planner", "STAGE 2: PLANNER", planner.run(organization_id=org_id)
        )

        # Agent 3: Creator
        result["creator"] = await _timed_stage(
            "creator", "STAGE 3: CREATOR",
            creator.run(ai_generate_fn=ai_generate_fn, organization_id=org_id)
        )

        # Agent 4: Reviewer
        result["reviewer"] = await _timed_stage(
            "reviewer", "STAGE 4: REVIEWER", reviewer.run(organization_id=org_id)
        )

        # Agent 5: Publisher (optional — skip if manual review preferred)
        if not skip_publish:
            result["publisher"] = await _timed_stage(
                "publisher",
                "STAGE 5: PUBLISHER",
                publisher.run(automation_getter=automation_getter, organization_id=org_id),
            )
        else:
            result["publisher"] = "skipped"
            stage_seconds["publisher"] = 0.0
            print("\n[Orchestrator] ═══ STAGE 5: PUBLISHER (skipped — manual review mode) ═══")

        # Agent 6: Analyst
        result["analyst"] = await _timed_stage(
            "analyst", "STAGE 6: ANALYST", analyst.run(organization_id=org_id)
        )

        stage_seconds["total"] = round(time.monotonic() - pipeline_started, 1)
        status.last_result = result
        status.last_stage_seconds = stage_seconds
        status.last_run = datetime.now().isoformat()
        print(f"\n[Orchestrator] ═══ PIPELINE COMPLETE — {stage_seconds['total']:.1f}s total ═══")
        print(f"[Orchestrator] Stage breakdown: {stage_seconds}")

    except Exception as e:
        status.error = str(e)
        stage_seconds["total"] = round(time.monotonic() - pipeline_started, 1)
        status.last_stage_seconds = stage_seconds
        print(f"\n[Orchestrator] PIPELINE ERROR at {status.current_agent}: {e}")
        result["error"] = str(e)

    finally:
        status.running = False
        status.current_agent = None
        status.current_organization_id = None

    return result


async def run_scout_only(organization_id: Optional[str] = None):
    """Run just the Scout agent (news fetch). organization_id accepted for a
    uniform signature only -- Scout ignores it (signals is one shared table)."""
    return await scout.run(organization_id=organization_id)


async def run_publish_only(automation_getter=None, organization_id: Optional[str] = None):
    """Run just the Publisher agent for one organization (for posting window triggers)."""
    return await publisher.run(automation_getter=automation_getter, organization_id=organization_id)


async def run_analyst_only(organization_id: Optional[str] = None):
    """Run just the Analyst agent (daily report) for one organization."""
    return await analyst.run(organization_id=organization_id)


async def run_for_organizations(async_fn: Callable, organization_ids: Optional[List[str]] = None,
                                 batch_size: int = None, **kwargs) -> Dict[str, object]:
    """A6: A2's design actually executed -- bounded-parallel batches, not one
    organization at a time (too slow at real scale) and not unlimited
    concurrency either (thundering herd against the AI provider, the social
    platforms' automations, and GitHub/HN if Scout were looped here too --
    it never is, see run_scheduled_cycle).

    async_fn must accept organization_id as a keyword arg (run_full_pipeline,
    run_publish_only, run_analyst_only all do). Returns {organization_id: result}.
    """
    orgs = organization_ids if organization_ids is not None else list_known_organizations()
    size = batch_size or BATCH_SIZE
    results: Dict[str, object] = {}
    sem = asyncio.Semaphore(max(1, size))

    async def _one(org_id: str):
        async with sem:
            results[org_id] = await async_fn(organization_id=org_id, **kwargs)

    print(f"[Orchestrator] Running {len(orgs)} organization(s) in batches of {size}: {orgs}")
    await asyncio.gather(*(_one(oid) for oid in orgs))
    return results


async def run_scheduled_cycle(ai_generate_fn: Callable = None, automation_getter: Callable = None,
                               skip_publish: bool = True, batch_size: int = None,
                               organization_ids: Optional[List[str]] = None) -> Dict[str, object]:
    """The real entry point for the automatic (APScheduler) daily pipeline (A6).

    Scout runs exactly ONCE for the whole cycle -- signals is one shared
    table (D1 A5); looping Scout per organization here would multiply
    external HackerNews/GitHub calls for the identical result and risk
    exhausting GitHub's public 60-requests/hour limit for EVERY organization
    at once, the exact failure mode the "signals stays shared" decision was
    made to avoid. Every organization's own Planner → Creator → Reviewer →
    [Publisher] → Analyst then runs via run_for_organizations() -- bounded
    parallel batches (A2), each organization's own PipelineStatus
    (get_status) tracking it independently.
    """
    print("[Orchestrator] Scheduled cycle starting — Scout (shared, once)")
    scout_result = await scout.run()

    org_results = await run_for_organizations(
        run_full_pipeline, organization_ids, batch_size,
        ai_generate_fn=ai_generate_fn, automation_getter=automation_getter,
        skip_publish=skip_publish, run_scout=False,
    )
    return {"scout": scout_result, "organizations": org_results}
