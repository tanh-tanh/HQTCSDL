-- Migration 001 (down): gỡ chức năng tự đăng ký.  Tài khoản đã đăng ký vẫn giữ nguyên.
REVOKE EXECUTE ON OBJECT::dbo.sp_register_reader FROM archive_writer;
GO
DROP PROCEDURE IF EXISTS dbo.sp_register_reader;
GO
