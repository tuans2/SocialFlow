from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from org_context import require_org

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"

router = APIRouter(
    prefix="/api/m04b",
    tags=["M04b Marketing Creative"],
    dependencies=[Depends(require_org)],
)

# Nợ #80 (floraos-core/docs/dac-ta/TECHNICAL_DEBT.md) — rà 17/09 phát hiện route
# này chưa từng gọi POST /integration/usage (hàm ghi_usage() có sẵn trong
# floraos_core.py nhưng không nơi nào gọi tới) và không có bước kiểm năng lực
# (capabilities/check) trước khi chạy — bất kỳ phiên đăng nhập hợp lệ nào của
# một tổ chức đều chạy được vô hạn lần, không tính hạn mức/chi phí. Quyết định:
# khai tử, không vá — M04b thật (tách nền + ghép bối cảnh + watermark + 4 tỷ lệ,
# có cổng Subject Integrity đo lõi chủ thể) đã dựng xong trong floraos-core từ
# P24 (`workers/media_ai/jobs/variant_worker.py`, endpoint
# `POST /api/v1/media/variants` — năng lực I4/I5, có capability + credit + audit
# đầy đủ qua RBAC của chính floraos-core). Route cũ ở đây không còn lý do tồn tại
# song song. Endpoint tải ảnh cũ (`/assets/{asset_id}/download`) GIỮ LẠI, vì các
# asset đã tạo trước đây vẫn cần tải được — chỉ đường TẠO MỚI bị khoá.


@router.post("/background-removal")
async def api_background_removal():
    raise HTTPException(
        status_code=410,
        detail=(
            "Route này đã khai tử (nợ #80, 17/09) — dùng M04b thật trong "
            "floraos-core: POST /api/v1/media/variants (yêu cầu Master Image đã "
            "duyệt, năng lực I4)."
        ),
    )


@router.get("/assets/{asset_id}/download")
async def api_download_asset(
    asset_id: str,
    organization_id: str = Depends(require_org),
):
    matches = list(UPLOADS_DIR.glob(f"org/{organization_id}/*/{asset_id}.png"))
    if not matches:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(matches[0]), media_type="image/png")
