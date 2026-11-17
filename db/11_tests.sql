-- =====================================================================
-- 11. Kiểm thử: ràng buộc, giao dịch, phân quyền, chỉ mục
--   sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -d video_archive -i db\11_tests.sql -o tests.txt
-- (ghi ra file bằng -o: console của Windows làm hỏng tiếng Việt)
-- Mọi thay đổi đều nằm trong giao dịch và được ROLLBACK: chạy xong dữ
-- liệu giữ nguyên, chạy lại bao nhiêu lần cũng được.
-- =====================================================================
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- ---------------------------------------------------------------------
-- A. Mỗi loại ràng buộc chặn một dòng sai
-- ---------------------------------------------------------------------
PRINT N'== A. Ràng buộc toàn vẹn ==';

BEGIN TRY  -- PRIMARY KEY: trùng khoá chính
    INSERT INTO dbo.videos (video_id, collection_id, title, fps)
    SELECT 'L23_V001', collection_id, N'trùng', 25 FROM dbo.collections WHERE collection_code = 'DXD';
END TRY
BEGIN CATCH PRINT CONCAT(N'PK       -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- UNIQUE: hai khung hình cùng video cùng số thứ tự
    INSERT INTO dbo.keyframes (keyframe_id, video_id, sample_n, pts_time, frame_idx)
    VALUES (999999, 'L23_V001', 1, 9999, 999999);
END TRY
BEGIN CATCH PRINT CONCAT(N'UNIQUE   -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- FOREIGN KEY: khung hình của video không tồn tại
    INSERT INTO dbo.keyframes (keyframe_id, video_id, sample_n, pts_time, frame_idx)
    VALUES (999999, 'L99_V999', 1, 0, 0);
END TRY
BEGIN CATCH PRINT CONCAT(N'FK       -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- FOREIGN KEY ... NO ACTION: không xoá được bạn đọc đã gửi yêu cầu (chỉ khoá)
    DELETE FROM dbo.users WHERE username = 'bandoc1';
END TRY
BEGIN CATCH PRINT CONCAT(N'FK       -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- FOREIGN KEY ... NO ACTION: giấy phép là chứng từ, chặn xoá yêu cầu đã cấp phép
    DELETE FROM dbo.requests WHERE request_id = 'YC-001';
END TRY
BEGIN CATCH PRINT CONCAT(N'FK       -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- FOREIGN KEY tự tham chiếu: chủ đề cha không tồn tại
    INSERT INTO dbo.topics (topic_name, parent_topic_id) VALUES (N'Chủ đề mồ côi', 999);
END TRY
BEGIN CATCH PRINT CONCAT(N'FK       -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- NOT NULL: thiếu nội dung yêu cầu khai thác
    INSERT INTO dbo.requests (request_id, request_text, origin, requested_by)
    SELECT 'test-null', NULL, 'user', user_id FROM dbo.users WHERE username = 'bandoc1';
END TRY
BEGIN CATCH PRINT CONCAT(N'NOT NULL -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- CHECK: tên đăng nhập có chữ hoa và khoảng trắng
    INSERT INTO dbo.users (username, password_hash) VALUES ('Bad Name', 'x');
END TRY
BEGIN CATCH PRINT CONCAT(N'CHECK    -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- CHECK: đoạn lời nói kết thúc trước khi bắt đầu
    INSERT INTO dbo.asr_segments (video_id, start_sec, end_sec, asr_text) VALUES ('L23_V001', 5, 4, N'x');
END TRY
BEGIN CATCH PRINT CONCAT(N'CHECK    -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- CHECK: mục đích sử dụng ngoài danh mục
    UPDATE dbo.requests SET purpose = 'resale' WHERE request_id = 'YC-003';
END TRY
BEGIN CATCH PRINT CONCAT(N'CHECK    -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- CHECK: giấy phép hết hạn trước (hoặc đúng) ngày bắt đầu
    UPDATE dbo.usage_licenses SET valid_to = valid_from WHERE request_id = 'YC-001';
END TRY
BEGIN CATCH PRINT CONCAT(N'CHECK    -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

BEGIN TRY  -- WITH CHECK OPTION: khoá tài khoản qua view chỉ chứa tài khoản đang hoạt động
    UPDATE dbo.v_active_users SET is_active = 0 WHERE username = 'bandoc1';
END TRY
BEGIN CATCH PRINT CONCAT(N'VIEW CHECK OPTION -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;

-- DEFAULT: bỏ trống full_name, is_active, created_at thì được điền giá trị mặc định.
BEGIN TRANSACTION;
    INSERT INTO dbo.users (username, password_hash) VALUES ('default_demo', 'x');
    SELECT username, full_name, is_active, created_at FROM dbo.users WHERE username = 'default_demo';
ROLLBACK TRANSACTION;
PRINT N'DEFAULT  -> full_name = N'''', is_active = 1, created_at = SYSDATETIME() (đã rollback)';
GO

-- ---------------------------------------------------------------------
-- B. Giao dịch
-- ---------------------------------------------------------------------
PRINT N'== B. Giao dịch ==';

-- B1. ROLLBACK huỷ toàn bộ: chọn thêm một đoạn rồi huỷ, số đoạn đã chọn trở lại như cũ.
DECLARE @before INT = (SELECT COUNT(*) FROM dbo.selections);
BEGIN TRANSACTION;
    INSERT INTO dbo.selections (request_id, keyframe_id, note)
    SELECT TOP (1) q.request_id, (SELECT MIN(keyframe_id) FROM dbo.keyframes), N'thử giao dịch'
    FROM dbo.requests q
    WHERE q.status = 'pending'
      AND NOT EXISTS (SELECT 1 FROM dbo.selections s WHERE s.request_id = q.request_id)
    ORDER BY q.request_id;
    DECLARE @inside INT = (SELECT COUNT(*) FROM dbo.selections);
    PRINT CONCAT(N'B1. Trong giao dịch: ', @inside, N' đoạn đã chọn');
ROLLBACK TRANSACTION;
DECLARE @after INT = (SELECT COUNT(*) FROM dbo.selections);
PRINT CONCAT(N'B1. Sau ROLLBACK:    ', @after, N' đoạn đã chọn (trước đó ', @before, N')');
GO

-- B2. Tính nguyên tử của sp_create_user: vai trò sai -> không tạo người dùng nào.
BEGIN TRY
    EXEC dbo.sp_create_user @username = 'atomic_demo', @password_hash = 'x', @roles = 'reader,wizard';
END TRY
BEGIN CATCH
    PRINT CONCAT(N'B2. sp_create_user lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE());
END CATCH;
DECLARE @atomic INT = (SELECT COUNT(*) FROM dbo.users WHERE username = 'atomic_demo');
PRINT CONCAT(N'B2. Số tài khoản atomic_demo: ', @atomic);
GO

-- B3. Giao dịch thành công rồi huỷ ở mức ngoài: tạo người dùng + vai trò
--     bằng thủ tục, kiểm tra, sau đó ROLLBACK để không để lại dữ liệu.
BEGIN TRANSACTION;
    EXEC dbo.sp_create_user @username = 'tx_demo', @password_hash = 'x', @roles = 'reader,librarian';
    SELECT username, roles FROM dbo.v_user_overview WHERE username = 'tx_demo';
ROLLBACK TRANSACTION;
DECLARE @tx INT = (SELECT COUNT(*) FROM dbo.users WHERE username = 'tx_demo');
PRINT CONCAT(N'B3. Sau ROLLBACK, số tài khoản tx_demo: ', @tx);
GO

-- B4. sp_grant_license ghi hai bảng (usage_licenses + requests.status) trong
--     một giao dịch.  Chạy thành công bên trong giao dịch ngoài rồi ROLLBACK.
DECLARE @librarian INT = (SELECT user_id FROM dbo.users WHERE username = 'thuthu1');
DECLARE @pending VARCHAR(32) = (
    SELECT TOP (1) r.request_id FROM dbo.requests r
    WHERE r.status = 'pending' AND EXISTS (SELECT 1 FROM dbo.selections s WHERE s.request_id = r.request_id)
    ORDER BY r.request_id);
EXEC sp_set_session_context @key = N'app_user_id', @value = @librarian;
BEGIN TRANSACTION;
    EXEC dbo.sp_grant_license @request_id = @pending, @valid_days = 90;
    SELECT r.request_id, r.status, l.valid_from, l.valid_to
    FROM dbo.requests r JOIN dbo.usage_licenses l ON l.request_id = r.request_id
    WHERE r.request_id = @pending;
ROLLBACK TRANSACTION;
DECLARE @status_after VARCHAR(16) = (SELECT status FROM dbo.requests WHERE request_id = @pending);
DECLARE @licenses_after INT = (SELECT COUNT(*) FROM dbo.usage_licenses WHERE request_id = @pending);
PRINT CONCAT(N'B4. Sau ROLLBACK, ', @pending, N' trở lại trạng thái ', @status_after,
             N', số giấy phép: ', @licenses_after);
EXEC sp_set_session_context @key = N'app_user_id', @value = NULL;
GO

-- B5. Cấp phép cho yêu cầu chưa có đoạn nào: thủ tục báo lỗi, không ghi gì.
DECLARE @librarian INT = (SELECT user_id FROM dbo.users WHERE username = 'thuthu1');
DECLARE @empty VARCHAR(32) = (
    SELECT TOP (1) r.request_id FROM dbo.requests r
    WHERE r.status = 'pending' AND NOT EXISTS (SELECT 1 FROM dbo.selections s WHERE s.request_id = r.request_id)
    ORDER BY r.request_id);
EXEC sp_set_session_context @key = N'app_user_id', @value = @librarian;
BEGIN TRY
    EXEC dbo.sp_grant_license @request_id = @empty;
END TRY
BEGIN CATCH PRINT CONCAT(N'B5. sp_grant_license ', @empty, N' -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
DECLARE @status_now VARCHAR(16) = (SELECT status FROM dbo.requests WHERE request_id = @empty);
DECLARE @licenses_now INT = (SELECT COUNT(*) FROM dbo.usage_licenses WHERE request_id = @empty);
PRINT CONCAT(N'B5. ', @empty, N' vẫn ', @status_now, N', số giấy phép: ', @licenses_now);
EXEC sp_set_session_context @key = N'app_user_id', @value = NULL;
GO

-- ---------------------------------------------------------------------
-- C. Phân quyền
-- ---------------------------------------------------------------------
PRINT N'== C. Phân quyền ==';

-- C1. Người dùng CSDL chỉ đọc archive_report.
EXECUTE AS USER = 'archive_report';
DECLARE @visible INT = (SELECT COUNT(*) FROM dbo.keyframes);
PRINT CONCAT(N'C1. archive_report đọc được ', @visible, N' khung hình');
BEGIN TRY
    INSERT INTO dbo.audit_log (action) VALUES ('hack');
END TRY
BEGIN CATCH PRINT CONCAT(N'C1. archive_report INSERT -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY
    EXEC dbo.sp_add_selection @request_id = 'YC-001', @keyframe_id = 65387;
END TRY
BEGIN CATCH PRINT CONCAT(N'C1. archive_report EXEC sp_add_selection -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY
    EXEC dbo.sp_grant_license @request_id = 'YC-003';
END TRY
BEGIN CATCH PRINT CONCAT(N'C1. archive_report EXEC sp_grant_license -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
REVERT;
GO

-- C2. Ứng dụng archive_app không được sửa dữ liệu video hay cấu trúc bảng.
EXECUTE AS USER = 'archive_app';
BEGIN TRY
    DELETE FROM dbo.keyframes WHERE keyframe_id = 65387;
END TRY
BEGIN CATCH PRINT CONCAT(N'C2. archive_app DELETE keyframes -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY
    EXEC (N'ALTER TABLE dbo.users ADD hacked INT;');
END TRY
BEGIN CATCH PRINT CONCAT(N'C2. archive_app ALTER TABLE -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY  -- phải đi qua sp_grant_license, không ghi thẳng giấy phép
    UPDATE dbo.requests SET status = 'approved' WHERE request_id = 'YC-003';
END TRY
BEGIN CATCH PRINT CONCAT(N'C2. archive_app UPDATE requests -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
REVERT;
GO

-- C3. Quyền nghiệp vụ trong thủ tục: bạn đọc (reader) không được chọn đoạn
--     tư liệu hay tự cấp phép cho yêu cầu của mình.
DECLARE @reader INT = (SELECT user_id FROM dbo.users WHERE username = 'bandoc1');
EXEC sp_set_session_context @key = N'app_user_id', @value = @reader;
BEGIN TRY
    EXEC dbo.sp_add_selection @request_id = 'YC-004', @keyframe_id = 65387;
END TRY
BEGIN CATCH PRINT CONCAT(N'C3. bandoc1 sp_add_selection -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY
    EXEC dbo.sp_grant_license @request_id = 'YC-004';
END TRY
BEGIN CATCH PRINT CONCAT(N'C3. bandoc1 sp_grant_license -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
EXEC sp_set_session_context @key = N'app_user_id', @value = NULL;
GO

-- C4. Quy tắc nghiệp vụ: cán bộ thư viện cũng không sửa được danh sách đoạn
--     của yêu cầu đã cấp phép.
DECLARE @librarian INT = (SELECT user_id FROM dbo.users WHERE username = 'thuthu1');
EXEC sp_set_session_context @key = N'app_user_id', @value = @librarian;
BEGIN TRY
    EXEC dbo.sp_add_selection @request_id = 'YC-001', @keyframe_id = 65387;
END TRY
BEGIN CATCH PRINT CONCAT(N'C4. thuthu1 sp_add_selection YC-001 (approved) -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
EXEC sp_set_session_context @key = N'app_user_id', @value = NULL;
GO

-- C5. Tự đăng ký (trang /register): ứng dụng chỉ gọi được sp_register_reader,
--     thủ tục luôn cấp đúng vai trò reader, không gắn đơn vị.  Chạy trong giao
--     dịch rồi ROLLBACK để không để lại tài khoản.
BEGIN TRANSACTION;
    EXECUTE AS USER = 'archive_app';
    EXEC dbo.sp_register_reader @username = 'TuDangKy_Demo ', @password_hash = 'scrypt$demo',
                                @full_name = N'Bạn đọc tự đăng ký', @user_type = 'student';
    REVERT;
    SELECT username, roles, organization, user_type FROM dbo.v_user_overview
    WHERE username = 'tudangky_demo';
ROLLBACK TRANSACTION;
DECLARE @registered INT = (SELECT COUNT(*) FROM dbo.users WHERE username = 'tudangky_demo');
PRINT CONCAT(N'C5. Sau ROLLBACK, số tài khoản tudangky_demo: ', @registered);
GO

EXECUTE AS USER = 'archive_app';
BEGIN TRY  -- mật khẩu chưa băm scrypt bị từ chối ngay trong CSDL
    EXEC dbo.sp_register_reader @username = 'plain_demo', @password_hash = 'matkhau123';
END TRY
BEGIN CATCH PRINT CONCAT(N'C5. sp_register_reader mật khẩu chưa băm -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
BEGIN TRY  -- tạo tài khoản chỉ qua thủ tục, không chèn thẳng vào users
    INSERT INTO dbo.users (username, password_hash) VALUES ('direct_demo', 'scrypt$x');
END TRY
BEGIN CATCH PRINT CONCAT(N'C5. archive_app INSERT users -> lỗi ', ERROR_NUMBER(), N': ', ERROR_MESSAGE()); END CATCH;
REVERT;
GO

-- ---------------------------------------------------------------------
-- D. Chỉ mục: so sánh số trang đọc (logical reads)
-- ---------------------------------------------------------------------
PRINT N'== D. Chỉ mục ==';
SET STATISTICS IO ON;

-- D1. Khung hình chứa từ "chang" (chặng) qua chỉ mục ngược: seek trên
--     UQ_terms_term rồi PK_ocr_postings, chỉ khớp nguyên từ...
PRINT N'D1a. Qua chỉ mục ngược terms/ocr_postings:';
SELECT COUNT(*) AS frames
FROM dbo.terms t JOIN dbo.ocr_postings p ON p.term_id = t.term_id
WHERE t.term = N'chang';

-- ...so với LIKE quét toàn bảng ocr_texts: đọc nhiều trang hơn và khớp cả
--     "chang" nằm bên trong từ khác (khang, nhang...).
PRINT N'D1b. LIKE quét toàn bảng:';
SELECT COUNT(*) AS frames FROM dbo.ocr_texts WHERE text_folded LIKE N'%chang%';

-- D2. Khung hình gần thời điểm 100 giây: seek trên UQ_keyframes_video_time...
PRINT N'D2a. Dùng chỉ mục (video_id, pts_time):';
SELECT TOP (1) keyframe_id, pts_time FROM dbo.keyframes
WHERE video_id = 'L23_V001' AND pts_time <= 100 ORDER BY pts_time DESC;

-- ...so với biểu thức không dùng được chỉ mục (hàm bọc lấy cột).
PRINT N'D2b. Bọc cột trong hàm, phải quét bảng:';
SELECT TOP (1) keyframe_id, pts_time FROM dbo.keyframes
WHERE LEFT(video_id, 8) = 'L23_V001' AND pts_time + 0 <= 100 ORDER BY pts_time DESC;

SET STATISTICS IO OFF;

-- Các chỉ mục đang có.
SELECT OBJECT_NAME(i.object_id) AS table_name, i.name AS index_name, i.type_desc,
       i.is_unique, i.is_primary_key
FROM sys.indexes i
WHERE OBJECTPROPERTY(i.object_id, 'IsUserTable') = 1 AND i.name IS NOT NULL
ORDER BY table_name, index_name;
GO

-- ---------------------------------------------------------------------
-- E. Dư thừa có kiểm soát
-- ---------------------------------------------------------------------
PRINT N'== E1. Kiểm tra nhất quán pts_time = frame_idx / fps ==';
SELECT COUNT(*) AS keyframes,
       SUM(CASE WHEN ABS(k.pts_time - k.frame_idx / v.fps) > 0.02 THEN 1 ELSE 0 END) AS mismatches,
       MAX(ABS(k.pts_time - k.frame_idx / v.fps)) AS max_diff_sec
FROM dbo.keyframes k
JOIN dbo.videos v ON v.video_id = k.video_id
WHERE v.fps IS NOT NULL;
GO

-- E2. status = 'approved' khi và chỉ khi yêu cầu có giấy phép
--     (sp_grant_license ghi cả hai trong một giao dịch).
PRINT N'== E2. Kiểm tra nhất quán status = approved <=> có giấy phép ==';
SELECT COUNT(*) AS requests,
       SUM(CASE WHEN r.status = 'approved' AND l.license_id IS NULL THEN 1
                WHEN r.status <> 'approved' AND l.license_id IS NOT NULL THEN 1
                ELSE 0 END) AS mismatches
FROM dbo.requests r
LEFT JOIN dbo.usage_licenses l ON l.request_id = r.request_id;
GO
