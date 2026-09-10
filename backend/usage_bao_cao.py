"""Báo chi phí phát sinh ở SocialFlow về floraos-core — Đợt 3 / G2.

Chi phí đã được TÍNH SẴN ở repo này từ trước (`assets.cost_usd`,
`video_jobs.cost_usd`, `IMAGE_COST_USD`, `provider.estimated_cost()`), chỉ là
không có đường nào chảy về `usage` của core. Đây là việc nối dây, không phải
việc đo đạc.

**Ghi nhận SAU, không phải cổng chặn.** Khác hẳn cách `LocalBudd` dùng
`POST /integration/jobs` làm cổng hạn mức: ở đó thứ bị chặn là một job chưa
chạy, chặn được thì tiết kiệm được. Ở đây thứ đã tốn tiền là một lượt gọi nhà
cung cấp ĐÃ HOÀN TẤT — chặn lúc này không lấy lại được gì, chỉ mất luôn cả
việc ghi nhận. Nên hàm dưới đây KHÔNG BAO GIỜ ném; hỏng thì ghi log.
"""

from typing import Optional

# Mã tính năng gửi sang core. Bảng giá của core
# (`src/modules/usage/domain/pricing.ts`) là hằng số, mã lạ rơi vào giá mặc
# định — nên hai mã này phải được đăng ký bên đó khi có bảng giá thật.
TINH_NANG_ANH = "social.image"
TINH_NANG_VIDEO = "social.video"


def bao_usage(organization_id: Optional[str], *, feature: str,
              cost_usd: Optional[float] = None, quantity: int = 1,
              job_id: Optional[str] = None,
              sso_token: Optional[str] = None) -> bool:
    """Ghi một dòng usage bên core. `True` nếu ghi được.

    Nuốt mọi lỗi có chủ đích — xem doc-comment của module. Log đủ để đối chiếu
    sau bằng `scripts/doi_chieu_usage.py` (G3).
    """
    if not organization_id:
        print("[usage] bỏ qua: không có organization_id", flush=True)
        return False
    try:
        from floraos_core import client_chay_nen, client_thay_mat_nguoi_dung

        client = (client_thay_mat_nguoi_dung(sso_token) if sso_token
                  else client_chay_nen(organization_id))
        client.ghi_usage(feature=feature, quantity=quantity,
                         cost_usd=cost_usd, status="COMPLETED", job_id=job_id)
        return True
    except Exception as e:  # noqa: BLE001 — xem doc-comment module
        print(
            f"[usage] KHÔNG báo được về core: org={organization_id} "
            f"feature={feature} cost_usd={cost_usd} job_id={job_id} — {e}",
            flush=True,
        )
        return False
