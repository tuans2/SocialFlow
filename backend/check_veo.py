#!/usr/bin/env python3
"""
Veo preflight — answer "will this actually work" before spending anything.

    cd backend && python3 check_veo.py            # free: key + model availability
    cd backend && python3 check_veo.py --generate # ~one clip, costs real money

The listing step costs nothing. The generation step is opt-in, prints the
estimated cost and asks for confirmation before it runs.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx  # noqa: E402

# Đợt 3 / E4: `store_bytes` ghi asset theo tổ chức. Script kiểm tra tay này cần
# một tổ chức THẬT để asset nó tạo ra nhìn thấy được trong giao diện — lấy từ
# biến môi trường thay vì gắn cứng, vì mã tổ chức đổi giữa các lần dựng dữ liệu.
TO_CHUC_KIEM_TRA = os.getenv("SOCIALFLOW_ORG_ID") or ""
if not TO_CHUC_KIEM_TRA:
    raise SystemExit(
        "Thieu SOCIALFLOW_ORG_ID — dat bang organization_id that cua to chuc "
        "muon gan asset kiem tra vao."
    )

BASE = "https://generativelanguage.googleapis.com/v1beta"

AVAILABLE_VEO = []


# Cheapest first. The ordering is what matters and is consistent across
# sources; the absolute prices are not — some quote per second, some per
# video — so nothing here states a number. Check Google's pricing page.
PREFERENCE = [
    "veo-3.1-lite-generate-preview",    # cheapest, "functional" quality
    "veo-3.1-fast-generate-preview",    # mid, aimed at social
    "veo-3.1-generate-preview",         # highest quality, priciest
]


def cheapest(available):
    for name in PREFERENCE:
        if name in available:
            return name
    return available[0] if available else None


def listing(key: str) -> int:
    print("→ Checking the key and which models it can see…\n")
    try:
        resp = httpx.get(f"{BASE}/models", params={"pageSize": "200"},
                         headers={"x-goog-api-key": key}, timeout=30.0)
    except Exception as e:
        print(f"  ✗ Could not reach Google: {type(e).__name__}: {e}")
        return 1

    if resp.status_code != 200:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:400]}")
        if resp.status_code in (401, 403):
            print("\n  The key was rejected. Check GEMINI_API_KEY in .env.")
        return 1

    names = [m["name"].replace("models/", "") for m in resp.json().get("models", [])]
    veo = sorted(n for n in names if "veo" in n.lower())
    AVAILABLE_VEO[:] = veo

    print(f"  ✓ Key works — {len(names)} models visible.")
    if veo:
        print("  ✓ Veo models the API lists:")
        for v in veo:
            print(f"      {v}")
        print("\n  ! Listed is not the same as usable. This endpoint reports what the\n"
              "    API knows about, not what your key is entitled to generate with.\n"
              "    Veo needs billing enabled; on a free-tier key the models still\n"
              "    appear here and the first generation fails with a quota error.\n"
              "    Only --generate settles it.")
    else:
        print("  ✗ No Veo model is visible to this key.")
        print("    Video generation needs Veo enabled on a paid Gemini API project.")
        print("    Text and image generation are unaffected.")
        return 2

    cheap = cheapest(veo)
    if cheap and len(veo) > 1:
        print(f"\n  Cheapest available: {cheap}")
        print("  For vertical social clips the lite/fast tiers are usually enough;")
        print("  the standard tier costs several times more per clip.")

    from video_providers.veo import VeoProvider
    wanted = os.getenv("VEO_MODEL") or VeoProvider.DEFAULT_MODEL
    if wanted in veo:
        print(f"\n  ✓ Configured VEO_MODEL '{wanted}' is available.")
        if cheap and wanted != cheap:
            print(f"    (a cheaper tier exists: set VEO_MODEL={cheap} in .env)")
    else:
        print(f"\n  ! Configured VEO_MODEL '{wanted}' is NOT in the list above.")
        print(f"    Set VEO_MODEL in .env to one of them, e.g. {veo[0]}")
        return 2
    return 0


async def generate(key: str, model: str = None) -> int:
    import video_providers
    from video_providers import ProviderError
    from video_providers.veo import VeoProvider

    provider = VeoProvider(model=model) if model else video_providers.get("veo")
    duration = provider.normalise_duration(int(os.getenv("VEO_TEST_DURATION", "4")))
    cost = provider.estimated_cost(duration)

    print(f"\n→ About to generate a real {duration}s clip with {provider.model}.")
    if cost:
        print(f"  Estimated cost: ${cost:.2f}")
    else:
        print("  Estimated cost: not calculated — VEO_COST_PER_SECOND is unset in .env.")
        print("  This is the cheapest tier and a short clip, so the charge is small,")
        print("  but the exact figure comes from Google's pricing page, not from here.")
    if input("  Proceed? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("  Cancelled. Nothing was charged.")
        return 0

    try:
        job = await provider.submit(
            "A slow push-in on a cup of coffee on a wooden desk, morning light",
            duration=duration, aspect_ratio="9:16",
        )
        print(f"  submitted: {job.provider_job_id}")

        for attempt in range(60):
            await asyncio.sleep(10)
            job = await provider.poll(job.provider_job_id)
            print(f"  [{attempt + 1:>2}] {job.status}")
            if job.done:
                break

        if not job.ok:
            print(f"\n  ✗ Generation failed: {job.error}")
            return 1

        data = await provider.download(job)

    except ProviderError as e:
        reason = e.reason.lower()
        print(f"\n  ✗ {e.reason}")
        if "quota" in reason or "rate limit" in reason:
            print("\n  This is an account limit, not a code problem — the request was")
            print("  well-formed and rejected at billing. Veo is a paid-tier feature:")
            print("  the models appear in the listing on a free key but generation")
            print("  fails exactly like this.")
            print("\n  Check https://ai.dev/rate-limit and whether billing is enabled")
            print("  on the Google Cloud project behind this key.")
            return 3
        return 1

    import media_store
    asset = media_store.store_bytes(
        data, declared_name="veo-preflight.mp4", origin="generated",
        provider=f"veo:{provider.model}", title="Veo preflight test",
        tags=["test", "veo"], cost_usd=cost,
    organization_id=TO_CHUC_KIEM_TRA)
    print(f"\n  ✓ Video stored: {asset['path']}")
    print(f"    Served at:    {asset['url']}")
    print(f"    Asset id:     {asset['asset_id']}")
    print(f"    Size:         {asset['bytes']:,} bytes")
    print("\n  End to end works: submit → poll → download → media library.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true",
                    help="also generate one real clip (costs money, asks first)")
    ap.add_argument("--model", default=None,
                    help="model for the test clip; defaults to the cheapest available")
    args = ap.parse_args()

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        print("✗ GEMINI_API_KEY is not set in .env")
        return 1
    print(f"GEMINI_API_KEY: present ({len(key)} chars)\n")

    code = listing(key)
    if code or not args.generate:
        if not args.generate and code == 0:
            print("\nAll good. Re-run with --generate to make one real clip.")
        return code
    model = args.model or cheapest(AVAILABLE_VEO) or os.getenv("VEO_MODEL")
    return asyncio.run(generate(key, model))


if __name__ == "__main__":
    sys.exit(main())
