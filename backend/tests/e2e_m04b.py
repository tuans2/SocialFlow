"""M04b E2E verification — verifies the full pipeline without core:
1. Backend starts with M04b route registered
2. Auth (401 without JWT)
3. Route accessible in OpenAPI schema
4. Frontend page loads

Core integration E2E requires running floraos-core with a product
that has an APPROVED master image.

Run: cd backend && python3 tests/e2e_m04b.py
"""
import os
import sys
import time
import json
import hmac
import hashlib
import base64
import signal
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

SECRET = "khoa-thu-du-dai-cho-bo-test-nay"
os.environ["SSO_SESSION_SECRET"] = SECRET

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


def make_token(org: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": "user-1", "org": org, "email": "test@example.com",
        "iat": int(time.time()), "exp": int(time.time()) + 3600,
    }).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{header}.{payload}.{sig}"


backend_proc = None
try:
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8100", "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://localhost:8100/api/health", timeout=2) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(1)
    else:
        check("server starts", False, True)
        out, err = backend_proc.communicate(timeout=5)
        print("STDOUT:", out.decode()[:2000])
        print("STDERR:", err.decode()[:2000])
        raise SystemExit(1)

    check("server starts", True, True)

    token = make_token("e2e-org")
    headers = {"X-FloraOS-SSO": token}

    # ── 1. OpenAPI: route registered ──────────────
    with urllib.request.urlopen("http://localhost:8100/openapi.json", timeout=5) as resp:
        schema = json.loads(resp.read())
    m04b_paths = [p for p in schema.get("paths", {}) if "m04b" in p]
    check("route registered in OpenAPI", len(m04b_paths) > 0, True)
    for p in m04b_paths:
        check(f"route {p} has background-removal or download",
              "background-removal" in p or "download" in p, True)

    # ── 2. Auth required ────────────────────────
    try:
        req = urllib.request.Request(
            "http://localhost:8100/api/m04b/background-removal",
            data=json.dumps({"product_id": "test"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        check("no auth → 401", False, True)
    except urllib.error.HTTPError as e:
        check("no auth → 401", e.code, 401)
    except Exception as e:
        check("no auth → 401", f"other: {type(e).__name__}", "401")

    # ── 3. Authenticated request (will fail at core, not auth) ──
    try:
        req = urllib.request.Request(
            "http://localhost:8100/api/m04b/background-removal",
            data=json.dumps({"product_id": "test"}).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        check("authed request → 200", resp.status, 200)
        if resp.status == 200:
            check("has asset_id", bool(body.get("asset_id")), True)
            check("status PENDING", body.get("status"), "PENDING")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        # Expected: core not running → 502 or connection error
        check("authed request (core unavailable)", e.code in (502, 503, 500), True)
        print(f"  (core unavailable, got {e.code}: {body})")
    except Exception as e:
        check("authed request", False, True)
        print(f"  authed request error: {e}")

    # ── 4. Frontend loads ────────────────────────
    try:
        req = urllib.request.urlopen("http://localhost:8100/", timeout=5)
        html = req.read().decode()
        check("frontend loads", req.status, 200)
        check("frontend has Marketing Creative", "Marketing Creative" in html, True)
        check("frontend has React", "React" in html, True)
    except Exception as e:
        check("frontend loads", False, True)
        FAIL.append(("frontend loads", str(e), "200"))

    # ── 5. Download endpoint reachable ───────────
    try:
        req = urllib.request.Request(
            f"http://localhost:8100/api/m04b/assets/test-asset/download",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            check("download endpoint reachable", resp.status, 200)
    except urllib.error.HTTPError as e:
        check("download endpoint reachable", e.code in (200, 404), True)
    except Exception as e:
        check("download request", False, True)
        FAIL.append(("download request", str(e), "200/404"))

finally:
    if backend_proc:
        backend_proc.send_signal(signal.SIGTERM)
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()

print(f"\n{'='*50}")
print(f"E2E Results")
print(f"{'='*50}")
print(f"PASS: {len(PASS)}/{len(PASS) + len(FAIL)}")
for name, got, want in FAIL:
    print(f"  FAIL: {name} → got {got!r}, want {want!r}")
if not FAIL:
    print("ALL E2E TESTS PASSED")
sys.exit(0 if not FAIL else 1)
