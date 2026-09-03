#!/usr/bin/env python3
"""
HeyGen preflight — what only a real key can answer.

    python3 check_heygen.py              free: key valid, avatars, voices
    python3 check_heygen.py --generate   one real avatar video (costs credits)

Listing avatars is a better proof than Veo's model listing turned out to be:
it is an authenticated read of account data, so a 200 means real credentials,
not just a public catalogue. Generation credits are still a separate question,
which is what --generate settles.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from video_providers import ProviderError  # noqa: E402
from video_providers.heygen import HeyGenProvider  # noqa: E402


async def inspect(provider) -> int:
    print("→ Checking the key and what this account can use…\n")
    try:
        avatars = await provider.list_avatars()
    except ProviderError as e:
        print(f"  ✗ {e.reason}")
        return 1
    except Exception as e:
        print(f"  ✗ Could not reach HeyGen: {type(e).__name__}: {e}")
        return 1

    print(f"  ✓ Key works — {len(avatars)} avatar(s) available.")
    for a in avatars[:8]:
        aid = a.get("avatar_id") or a.get("talking_photo_id")
        print(f"      {aid}  {a.get('avatar_name') or a.get('talking_photo_name') or ''}")
    if len(avatars) > 8:
        print(f"      … and {len(avatars) - 8} more")

    try:
        voices = await provider.list_voices()
    except ProviderError as e:
        print(f"\n  ! Could not list voices: {e.reason}")
        return 0

    print(f"\n  ✓ {len(voices)} voice(s) available.")
    vi = [v for v in voices if (v.get("language") or "").lower().startswith("viet")]
    shown = (vi or voices)[:8]
    if vi:
        print("    Vietnamese voices:")
    for v in shown:
        print(f"      {v.get('voice_id')}  {v.get('name')}  [{v.get('language')}]")

    configured = os.getenv("HEYGEN_VOICE_ID", "")
    if configured:
        ok = any(v.get("voice_id") == configured for v in voices)
        print(f"\n  {'✓' if ok else '!'} HEYGEN_VOICE_ID '{configured}' "
              f"{'is available' if ok else 'is NOT in the list above'}")
    elif shown:
        print(f"\n  ! HEYGEN_VOICE_ID is not set in .env.")
        print(f"    A spoken script needs one, e.g. HEYGEN_VOICE_ID={shown[0].get('voice_id')}")
    return 0


async def generate(provider) -> int:
    avatars = await provider.list_avatars()
    voices = await provider.list_voices()
    if not avatars or not voices:
        print("  ✗ Need at least one avatar and one voice to test.")
        return 1

    avatar_id = avatars[0].get("avatar_id") or avatars[0].get("talking_photo_id")
    voice_id = os.getenv("HEYGEN_VOICE_ID") or voices[0].get("voice_id")
    script = "Hello from SocialFlow. This is a short test of avatar video generation."

    print(f"\n→ About to generate a real avatar video.")
    print(f"  avatar: {avatar_id}")
    print(f"  voice:  {voice_id}")
    cost = provider.estimated_cost(10)
    print(f"  Estimated cost: {('$%.2f' % cost) if cost else 'not calculated — HEYGEN_COST_PER_SECOND is unset'}")
    print("  This consumes HeyGen credits.")
    if input("  Proceed? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("  Cancelled. Nothing was charged.")
        return 0

    try:
        job = await provider.submit(script, avatar_id=avatar_id, voice_id=voice_id,
                                    aspect_ratio="9:16")
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
        print(f"\n  ✗ {e.reason}")
        if "credit" in e.reason.lower() or "rate limit" in e.reason.lower():
            print("\n  This is an account limit, not a code problem — the request")
            print("  was accepted and refused at billing. HeyGen's API is prepaid.")
            return 3
        return 1

    import media_store
    asset = media_store.store_bytes(
        data, declared_name="heygen-preflight.mp4", origin="generated",
        provider="heygen", title="HeyGen preflight test",
        tags=["test", "heygen", "avatar"], cost_usd=cost,
    )
    print(f"\n  ✓ Video stored: {asset['path']}")
    print(f"    Served at:    {asset['url']}")
    print(f"    Asset id:     {asset['asset_id']}")
    print("\n  End to end works: submit → poll → download → media library.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true",
                    help="also generate one real video (consumes credits, asks first)")
    args = ap.parse_args()

    provider = HeyGenProvider()
    if not provider.is_configured():
        print("✗ HEYGEN_API_KEY is not set in .env.\n")
        print("  The HEYGEN_EMAIL and HEYGEN_PASSWORD already in .env are the web")
        print("  login — the REST API needs a separate key from HeyGen's API")
        print("  settings page. The API is prepaid, from $5, with no subscription.")
        return 1

    print(f"HEYGEN_API_KEY: present ({len(provider.api_key)} chars)\n")
    code = asyncio.run(inspect(provider))
    if code or not args.generate:
        if not args.generate and code == 0:
            print("\nAll good. Re-run with --generate to make one real video.")
        return code
    return asyncio.run(generate(provider))


if __name__ == "__main__":
    sys.exit(main())
