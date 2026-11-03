-- =====================================================================
-- 07. Dữ liệu danh mục: quyền, vai trò, cây chủ đề, nguồn tìm kiếm, từ dừng
--
--   reader    : bạn đọc - tra cứu kho, xem khung hình, gửi yêu cầu khai thác
--   librarian : cán bộ thư viện - reader + chọn đoạn tư liệu, duyệt và cấp phép
--   admin     : toàn quyền, gồm quản lý tài khoản và xem đánh giá
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

INSERT INTO dbo.permissions (permission_code, description) VALUES
    ('search.run',      N'Tra cứu kho và xem khung hình'),
    ('request.submit',  N'Gửi yêu cầu khai thác tư liệu'),
    ('selection.write', N'Chọn và bỏ chọn đoạn tư liệu cho yêu cầu'),
    ('license.grant',   N'Duyệt yêu cầu, cấp giấy phép sử dụng hoặc từ chối'),
    ('user.manage',     N'Tạo tài khoản, đổi vai trò, đặt lại mật khẩu'),
    ('eval.view',       N'Xem kết quả đánh giá chất lượng tìm kiếm');

INSERT INTO dbo.roles (role_name, description) VALUES
    ('reader',    N'Bạn đọc: tra cứu và gửi yêu cầu khai thác'),
    ('librarian', N'Cán bộ thư viện: chọn đoạn tư liệu, duyệt và cấp phép'),
    ('admin',     N'Quản trị thư viện số');

INSERT INTO dbo.role_permissions (role_id, permission_code)
SELECT r.role_id, p.permission_code
FROM dbo.roles r
JOIN dbo.permissions p
  ON (r.role_name = 'reader'    AND p.permission_code IN ('search.run', 'request.submit'))
  OR (r.role_name = 'librarian' AND p.permission_code IN ('search.run', 'request.submit',
                                                          'selection.write', 'license.grant'))
  OR (r.role_name = 'admin');

-- Cây chủ đề (khung phân loại của thư viện).  Video được gắn chủ đề lá
-- trong 09_sample_data.sql; chủ đề cha gom qua fn_topic_subtree.
INSERT INTO dbo.topics (topic_name) VALUES
    (N'Thể thao'), (N'Kỹ thuật truyền hình'), (N'Thời sự');

INSERT INTO dbo.topics (topic_name, parent_topic_id)
SELECT v.topic_name, p.topic_id
FROM (VALUES
    (N'Đua xe đạp', N'Thể thao'),
    (N'Bóng đá', N'Thể thao'),
    (N'Quay flycam', N'Kỹ thuật truyền hình'),
    (N'Đồ hoạ theo dõi chuyển động', N'Kỹ thuật truyền hình'),
    (N'Kinh tế', N'Thời sự'),
    (N'Văn hoá - xã hội', N'Thời sự')
) AS v (topic_name, parent_name)
JOIN dbo.topics p ON p.topic_name = v.parent_name;

INSERT INTO dbo.topics (topic_name, parent_topic_id)
SELECT v.topic_name, p.topic_id
FROM (VALUES
    (N'Diễn biến cuộc đua', N'Đua xe đạp'),
    (N'Đội đua và địa phương', N'Đua xe đạp'),
    (N'Áo vàng', N'Đua xe đạp')
) AS v (topic_name, parent_name)
JOIN dbo.topics p ON p.topic_name = v.parent_name;

INSERT INTO dbo.topics (topic_name, parent_topic_id)
SELECT v.topic_name, p.topic_id
FROM (VALUES
    (N'Nước rút', N'Diễn biến cuộc đua'),
    (N'Tấn công và bứt phá', N'Diễn biến cuộc đua'),
    (N'Chiến thuật đồng đội', N'Diễn biến cuộc đua'),
    (N'Về đích', N'Diễn biến cuộc đua'),
    (N'Vĩnh Long', N'Đội đua và địa phương'),
    (N'TP. Hồ Chí Minh', N'Đội đua và địa phương'),
    (N'An Giang', N'Đội đua và địa phương'),
    (N'Đồng Tháp', N'Đội đua và địa phương'),
    (N'Đồng Nai', N'Đội đua và địa phương')
) AS v (topic_name, parent_name)
JOIN dbo.topics p ON p.topic_name = v.parent_name;

INSERT INTO dbo.sources (source_code, description) VALUES
    ('visual', N'Hình ảnh: SigLIP2 + FAISS'),
    ('ocr',    N'Chữ trên khung hình'),
    ('asr',    N'Lời nói trong video');

-- Hư từ xuất hiện ở hầu hết mọi câu (đã bỏ dấu).  Không đưa vào những âm
-- tiết mà một dạng có dấu mang nghĩa: "do" còn là đỏ, "den" là đen,
-- "sau" là sáu.
INSERT INTO dbo.stopwords (word) VALUES
    (N'an'), (N'the'), (N'cac'), (N'co'), (N'cua'), (N'dang'), (N'duoc'), (N'la'),
    (N'mot'), (N'nguoi'), (N'nhung'), (N'trong'), (N'va'), (N'voi'), (N'nghe'),
    (N'tim'), (N'roi'), (N'canh'), (N'khung'), (N'hinh'), (N'doan'), (N'cho'),
    (N'khi'), (N've'), (N'lai'), (N'nay'), (N'thi'), (N'se'), (N'nhu'), (N'video'),
    (N'clip'), (N'phan'), (N'tiep'), (N'theo'), (N'truoc'), (N'khoanh'), (N'khac');
GO
