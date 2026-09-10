"""Một chỗ duy nhất để nói "tổ chức là bắt buộc" — Đợt 3 / E7.

Thay cho `organization_id or DEFAULT_ORGANIZATION_ID`, khuôn mẫu đã nằm ở 13
tệp trước lượt này. Khuôn ấy có hai vấn đề, và cái thứ hai mới là cái đắt:

1. Nó biến một lỗi lập trình (quên truyền tổ chức) thành một truy vấn CHẠY
   ĐƯỢC trên dữ liệu của một tổ chức khác.
2. Đi kèm nó là `COALESCE(organization_id, ?) = ?` trong SQL, khiến MỌI hàng
   `organization_id IS NULL` khớp với BẤT KỲ tổ chức nào hỏi. Với một tổ chức
   thì không ai thấy gì sai; với hai tổ chức thì đó là toàn bộ dữ liệu cũ lộ
   cho cả hai.

Nay tổ chức luôn đến từ một nguồn đã xác minh (JWT ở tầng HTTP, hoặc chính
bản ghi đang xử lý ở tầng chạy nền), và thiếu nó là hỏng to, hỏng sớm.
"""

from typing import Optional


class ThieuToChuc(ValueError):
    """Một đường mã chạy mà không biết mình thuộc tổ chức nào."""


def bat_buoc(organization_id: Optional[str]) -> str:
    if not organization_id:
        raise ThieuToChuc(
            "Thiếu organization_id. Tổ chức phải đến từ JWT floraos_sso đã xác "
            "minh (tầng HTTP) hoặc từ chính bản ghi đang xử lý (tầng chạy nền) "
            "— không có giá trị mặc định nào."
        )
    return organization_id
