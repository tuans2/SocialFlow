"""M04b background_removal — backend test.

Run: cd backend && python3 tests/test_m04b.py
"""
import importlib
import io
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import org_context
import m04b.infra.core_client as core_client_mod
import m04b.use_cases.background_removal as br_module
import m04b.routes
from fastapi import Request
from m04b.adapters.image_processor import remove_background
from m04b.domain.models import BackgroundRemovalRequest
from PIL import Image

_TEST_ORG = str(uuid.uuid4())
_TEST_PRODUCT = str(uuid.uuid4())
_TEST_ASSET = str(uuid.uuid4())

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


class MockCoreClient:
    def __init__(self, *, sso_token=None, bearer=None):
        pass

    @classmethod
    def default(cls):
        return cls()

    def get_master_image(self, product_id, sso_token=None):
        if product_id != _TEST_PRODUCT:
            raise RuntimeError("404")
        return {
            "master": {
                "id": _TEST_ASSET,
                "storage_key": f"org/{_TEST_ORG}/{_TEST_PRODUCT}/{_TEST_ASSET}.png",
                "url": f"http://localhost:3100/api/v1/storage/{_TEST_ASSET}",
                "mime_type": "image/png",
                "width": 200,
                "height": 200,
                "aspect_ratio": "1:1",
            },
            "ratios": [],
        }

    def download_image(self, url):
        img = Image.new("RGB", (100, 100), color=(240, 240, 240))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def register_asset(self, organization_id, sso_token=None, asset=None):
        return {**asset, "asset_id": asset["asset_id"], "state": "PENDING"}


core_client_mod.CoreClientAdapter = MockCoreClient
br_module.CoreClientAdapter = MockCoreClient


async def patched_require_org(request: Request):
    return _TEST_ORG


async def patched_sso_token_tho(request: Request):
    return "test-token"


org_context.require_org = patched_require_org
org_context.sso_token_tho = patched_sso_token_tho
importlib.reload(m04b.routes)

from m04b.routes import router as m04b_router  # noqa: E402
from m04b.use_cases.background_removal import (  # noqa: E402
    request_background_removal,
    request_background_removal_from_bytes,
)


def make_request(product_id):
    return request_background_removal(
        organization_id=_TEST_ORG,
        request=BackgroundRemovalRequest(product_id=product_id),
    )


# ── Use-case tests ──────────────────────────────────

result = make_request(_TEST_PRODUCT)
check("removal returns asset_id", bool(result.asset_id), True)
check("removal parent_asset_id", result.parent_asset_id, _TEST_ASSET)
check("removal product_id", result.product_id, _TEST_PRODUCT)
check("removal mime_type", result.mime_type, "image/png")
check("removal width > 0", result.width > 0, True)
check("removal height > 0", result.height > 0, True)
check("removal storage_key has org", _TEST_ORG in result.storage_key, True)
check("removal storage_key has product", _TEST_PRODUCT in result.storage_key, True)
check("removal storage_key has png", result.storage_key.endswith(".png"), True)

try:
    make_request("non-existent-product")
    check("non-existent product raises", False, True)
except RuntimeError:
    check("non-existent product raises", True, True)

_test_buf = io.BytesIO()
Image.new("RGB", (50, 50), color=(200, 200, 200)).save(_test_buf, format="PNG")
result2 = request_background_removal_from_bytes(
    organization_id=_TEST_ORG,
    product_id=_TEST_PRODUCT,
    image_bytes=_test_buf.getvalue(),
)
check("from_bytes returns asset_id", bool(result2.asset_id), True)
check("from_bytes storage_key", "png" in result2.storage_key, True)

# ── Image processor quality test ────────────────────

test_img = Image.new("RGB", (80, 80), color=(240, 240, 240))
buf = io.BytesIO()
test_img.save(buf, format="PNG")
image_bytes = buf.getvalue()

processed, w, h = remove_background(image_bytes)
result_img = Image.open(io.BytesIO(processed))
alpha = result_img.split()[-1] if result_img.mode == "RGBA" else None
if alpha:
    transparent = sum(1 for p in alpha.getdata() if p < 128)
    opaque = sum(1 for p in alpha.getdata() if p >= 128)
    check("removal has transparent", transparent > 0, True)
    check("removal has content", opaque > 0 or transparent > 4000, True)
else:
    check("removal has alpha", True, False)
check("removal returns correct size", (w, h), (80, 80))
check("removal output is PNG", processed[:4] == b"\x89PNG", True)

# ── Route tests ─────────────────────────────────────

from fastapi import FastAPI
from fastapi.testclient import TestClient  # noqa: E402

test_app = FastAPI()
test_app.include_router(m04b_router)
test_client = TestClient(test_app)

routes = [r.path for r in test_app.routes if hasattr(r, "path")]
check("route background-removal", "/api/m04b/background-removal" in routes, True)
check("route download", "/api/m04b/assets/{asset_id}/download" in routes, True)

# Nợ #80 — route khai tử 17/09 (xem m04b/routes.py): POST trả 410 cho MỌI lời
# gọi, kể cả sản phẩm hợp lệ. Dùng use-case trực tiếp ở trên (không qua HTTP)
# để vẫn kiểm được `background_removal.py`/`image_processor.py` còn đúng —
# hai module đó KHÔNG bị xoá, chỉ mất đường vào qua route.
res = test_client.post(
    "/api/m04b/background-removal",
    json={"product_id": _TEST_PRODUCT},
    headers={"X-FloraOS-SSO": "test-token"},
)
check("API status → 410 (route đã khai tử)", res.status_code, 410)
check("API detail nhắc floraos-core", "floraos-core" in res.json().get("detail", ""), True)

res_bad = test_client.post(
    "/api/m04b/background-removal",
    json={"product_id": "non-existent"},
    headers={"X-FloraOS-SSO": "test-token"},
)
check("API bad product → vẫn 410 (không chạm use-case nữa)", res_bad.status_code, 410)

res_download = test_client.get(
    f"/api/m04b/assets/{result.asset_id}/download",
    headers={"X-FloraOS-SSO": "test-token"},
)
check("download 200", res_download.status_code, 200)
check("download content-type", res_download.headers.get("content-type"), "image/png")

res_download_missing = test_client.get(
    f"/api/m04b/assets/{uuid.uuid4()}/download",
    headers={"X-FloraOS-SSO": "test-token"},
)
check("download missing → 404", res_download_missing.status_code, 404)

# ── Result ──────────────────────────────────────────

print(f"\n{'='*50}")
print(f"M04b Test Results")
print(f"{'='*50}")
print(f"PASS: {len(PASS)}/{len(PASS) + len(FAIL)}")
for name, got, want in FAIL:
    print(f"  FAIL: {name} → got {got!r}, want {want!r}")
if not FAIL:
    print("ALL TESTS PASSED")
sys.exit(0 if not FAIL else 1)
