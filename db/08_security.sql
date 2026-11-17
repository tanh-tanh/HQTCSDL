-- =====================================================================
-- 08. Bảo mật mức CSDL: role và người dùng với quyền tối thiểu
--
--   archive_reader : đọc mọi bảng/view, chạy được thủ tục tìm kiếm
--   archive_writer : ghi dữ liệu nghiệp vụ (phiên, tài khoản, nhật ký, đánh giá)
--                    và chạy các thủ tục nghiệp vụ (gửi yêu cầu, chọn đoạn tư
--                    liệu, cấp phép); không có quyền DDL, không sửa được danh
--                    mục và dữ liệu video, không ghi thẳng vào requests,
--                    selections hay usage_licenses.
--
--   archive_app    = archive_reader + archive_writer  (ứng dụng web)
--   archive_report = archive_reader               (báo cáo, chỉ đọc)
--
-- Hai người dùng này tạo WITHOUT LOGIN: máy chủ chỉ bật xác thực Windows,
-- nên ứng dụng đăng nhập bằng Windows rồi hạ quyền bằng
--   EXECUTE AS USER = 'archive_app' WITH NO REVERT;
-- Muốn có login riêng (xác thực SQL) xem phụ lục trong README.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

CREATE ROLE archive_reader;
CREATE ROLE archive_writer;
GO

GRANT SELECT ON SCHEMA::dbo TO archive_reader;
GRANT EXECUTE ON OBJECT::dbo.sp_search_footage       TO archive_reader;
GRANT EXECUTE ON OBJECT::dbo.sp_progress_report  TO archive_reader;
GRANT EXECUTE ON OBJECT::dbo.fn_nearest_keyframe TO archive_reader;
GRANT EXECUTE ON OBJECT::dbo.fn_fold_vi          TO archive_reader;

GRANT INSERT, DELETE         ON OBJECT::dbo.sessions           TO archive_writer;
GRANT UPDATE                 ON OBJECT::dbo.users              TO archive_writer;
GRANT INSERT, DELETE         ON OBJECT::dbo.user_roles         TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.audit_log          TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.search_log         TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.search_log_sources TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.eval_runs          TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.eval_run_sources   TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.eval_requests       TO archive_writer;
GRANT INSERT                 ON OBJECT::dbo.eval_results       TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_submit_request   TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_add_selection    TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_remove_selection TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_grant_license    TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_reject_request   TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_create_user   TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.sp_register_reader TO archive_writer;
GRANT EXECUTE ON OBJECT::dbo.fn_has_permission TO archive_writer;
GO

CREATE USER archive_app WITHOUT LOGIN;
CREATE USER archive_report WITHOUT LOGIN;
GO

ALTER ROLE archive_reader ADD MEMBER archive_app;
ALTER ROLE archive_writer ADD MEMBER archive_app;
ALTER ROLE archive_reader ADD MEMBER archive_report;
GO
