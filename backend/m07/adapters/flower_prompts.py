"""
Flower Prompts & Banned Words Adapter for M07 Content Engine.
Chuyên biệt hóa văn phong tiếp thị hoa tươi, loại bỏ đề tài công nghệ di sản.
"""
from typing import Dict, List, Optional

# Danh sách từ cấm đồng bộ từ TU_DIEN_TU_CAM_CONTENT_NGANH_HOA.md
FLOWER_BANNED_PHRASES = [
    # Cam kết sai lệch về hoa
    "hoa vĩnh cửu", "không bao giờ tàn", "hoa tự nhiên 100%", "giống hệt 100% hình mẫu",
    "hoa nở mãi mãi", "cam kết nở đúng từng phút",
    # Từ sáo rỗng AI
    "tóm lại", "kết luận là", "chúng tôi vô cùng hào hứng", "vui mừng chia sẻ",
    "bước ngoặt mang tính cách mạng", "là một mô hình ai", "đắm chìm vào",
    "tận dụng tối đa", "phát huy tiềm năng", "vô song", "đỉnh chóp vũ trụ",
    # Giật gân chợ búa
    "xả kho lỗ vốn", "cứu chủ shop", "rẻ như cho", "rẻ tụt quần", "hoa ế thanh lý", "hàng dạt",
    # Vi phạm chính sách
    "chữa lành tâm linh hoàn toàn", "click ngay kẻo lỡ"
]

ANGLE_DESCRIPTIONS = {
    "emotional": "Góc tiếp cận CẢM XÚC: Ngôn từ ấm áp, thơ mộng, chạm đến trái tim người nhận, tôn vinh tình yêu, lòng biết ơn hoặc sự gắn kết.",
    "technical": "Góc tiếp cận KỸ THUẬT & TAY NGHỀ: Tôn vinh nghệ thuật cắm hoa của florist, độ tươi nhập khẩu, cách phối màu tinh tế và quy trình dưỡng hoa kỹ lưỡng.",
    "promotional": "Góc tiếp cận ƯU ĐÃI & CHỐT ĐƠN: Làm nổi bật quà tặng kèm (thiệp, banner), chính sách giao hỏa tốc, bảo hành hoa tươi và giá ưu đãi trong ngày."
}

def build_channel_prompt(
    channel: str,
    product_name: str,
    flower_details: str,
    colors: str,
    price: Optional[float],
    occasion: Optional[str],
    angle: str,
    brand_tone: str,
    forbidden_styles: str,
    custom_instructions: Optional[str] = None
) -> str:
    angle_guide = ANGLE_DESCRIPTIONS.get(angle, ANGLE_DESCRIPTIONS["emotional"])
    price_info = f"Mức giá tham khảo: {price:,.0f} VNĐ" if price else "Mức giá: Liên hệ tiệm"
    occasion_info = f"Dịp phù hợp: {occasion}" if occasion else "Dịp phù hợp: Sinh nhật, kỷ niệm, chúc mừng, tỏ tình"

    channel_rules = {
        "facebook": """
KÊNH FACEBOOK FANPAGE:
- Phong cách: Kể chuyện (storytelling), lôi cuốn, độ dài 3-4 đoạn ngắn dễ đọc.
- Cấu trúc:
  + Dòng 1: Tiêu đề lôi cuốn hoặc câu mở đầu gợi cảm xúc (Hook).
  + Thân bài: Mô tả nét đẹp của bó hoa, ý nghĩa loài hoa, cảm giác khi người nhận đón nhận món quà.
  + Cam kết của tiệm (hoa tươi mới mỗi ngày, giao tận tay).
  + CTA: Nhắn tin cho tiệm qua Inbox / Zalo để được tư vấn thiết kế riêng.
  + Cuối bài: 4-6 hashtag liên quan (ví dụ: #HoaTuoi #[TenQuan] #HoaSinhNhat).
""",
        "instagram": """
KÊNH INSTAGRAM:
- Phong cách: Aesthetic, thời thượng, câu chữ cô đọng, tinh tế chuẩn gu sang trọng.
- Cấu trúc:
  + Dòng 1: Caption ngắn gọn (1-2 câu) đầy chất thơ.
  + Thân bài: Điểm nhấn phối màu hoa và thông điệp ngắn.
  + CTA: 'Ghé link in bio' hoặc 'Direct để tiệm chuẩn bị hoa cho bạn nhé'.
  + Cuối bài: 15-20 hashtag niche hoa nghệ thuật và phong cách sống.
""",
        "tiktok": """
KÊNH TIKTOK (KỊCH BẢN VIDEO NGẮN & CAPTION):
- Phong cách: Năng động, thu hút người xem trong 3 giây đầu, format Hook-Body-CTA.
- Cấu trúc trả về gồm 2 phần:
  1. KỊCH BẢN VIDEO (30-45 giây):
     - Giây 0-3 (Hook): Hành động gây tò mò hoặc câu hỏi ấn tượng khi florist bắt đầu cắm hoa.
     - Giây 4-25 (Visual & Lời bình): Cận cảnh từng loại hoa, phối giấy gói, âm thanh lofi nhẹ nhàng.
     - Giây 26-35 (Thành phẩm & CTA): Góc quay 360 độ bó hoa hoàn thiện, kêu gọi đặt hoa tặng người thương.
  2. CAPTION ĐĂNG BÀI: 1-2 câu bắt trend + 4-6 hashtag thịnh hành (#FloristLife #GoiHoa #Trending).
""",
        "zalo": """
KÊNH ZALO OA / TIN NHẮN TƯ VẤN:
- Phong cách: Trang trọng, ân cần, riêng tư, tập trung vào dịch vụ khách hàng chu đáo.
- Cấu trúc:
  + Lời chào thân mật gửi đến quý khách.
  + Giới thiệu mẫu hoa mới phù hợp cho dịp đặc biệt.
  + Chi tiết các loại hoa chính, màu sắc và mức giá rõ ràng.
  + Ưu đãi freeship hoặc tặng thiệp thiết kế riêng.
  + CTA: 'Quý khách vui lòng phản hồi tin nhắn này để tiệm giữ hoa tươi đẹp nhất ạ.'
""",
        "linkedin": """
KÊNH LINKEDIN (B2B & THOUGHT LEADERSHIP):
- Phong cách: Chuyên nghiệp, lịch lãm, phong thái Thought Leadership, văn hóa doanh nghiệp, nghệ thuật không gian làm việc và tri ân đối tác.
- Cấu trúc:
  + Dòng 1: Tiêu đề gợi mở tư duy hoặc góc nhìn sâu sắc về văn hóa tri ân / không gian làm việc truyền cảm hứng.
  + Thân bài: Phân tích giá trị của nghệ thuật hoa tươi trong việc nâng tầm trải nghiệm khách hàng, gắn kết đội ngũ hoặc duy trì quan hệ đối tác B2B.
  + Giải pháp: Thiết kế định kỳ theo hợp đồng doanh nghiệp (Office Floral Styling), hoa sự kiện VIP, quà tặng đối tác chuẩn mực.
  + CTA: 'Kết nối hoặc liên hệ với chúng tôi để nhận hồ sơ năng lực & chính sách đối tác doanh nghiệp.'
  + Cuối bài: 4-6 hashtag B2B (#CorporateGifting #B2B #FloralDesign #OfficeWellness #FloraOS).
"""
    }

    selected_rule = channel_rules.get(channel.lower(), channel_rules["facebook"])

    prompt = f"""Bạn là Chuyên gia Sáng tạo Nội dung Tiếp thị chuyên sâu về Hoa tươi cao cấp (Florist Content Specialist).
Hãy soạn thảo nội dung tiếp thị bài bản cho sản phẩm hoa tươi sau:

THÔNG TIN SẢN PHẨM:
- Tên mẫu hoa: {product_name or 'Bó hoa tươi thiết kế'}
- Các loài hoa sử dụng: {flower_details or 'Hoa hồng nhập khẩu, baby trắng, lá phụ'}
- Tone màu chủ đạo: {colors or 'Tự nhiên, hài hòa'}
- {price_info}
- {occasion_info}

ĐỊNH HƯỚNG BÀI VIẾT:
- {angle_guide}
- Giọng điệu thương hiệu (Brand Tone): {brand_tone or 'Thân thiện, tinh tế, sang trọng, thấu hiểu'}

QUY TẮC BẮT BUỘC (BRAND SAFETY & ANTI-SLOP):
- TUYỆT ĐỐI KHÔNG DÙNG các từ ngữ cấm sau: {', '.join(FLOWER_BANNED_PHRASES[:12])}.
- Phong cách bị cấm của cửa hàng: {forbidden_styles or 'không dùng từ ngữ chợ búa, không cam kết sai sự thật'}.
- Không viết các ghi chú placeholder như [Chèn ảnh], [Link ở đây]. Trả về nội dung hoàn chỉnh có thể đăng ngay.

{selected_rule}

{custom_instructions or ''}

Hãy trả về kết quả bằng TIẾNG VIỆT, tự nhiên, truyền cảm hứng và kích thích chuyển đổi đơn hàng."""

    return prompt
