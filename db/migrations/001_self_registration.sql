-- Migration 001 (up): bạn đọc tự đăng ký.  Chạy được nhiều lần (CREATE OR ALTER).
--   sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -d video_archive -i db\migrations\001_self_registration.sql
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- Tự đăng ký tài khoản bạn đọc (trang /register).  Không cần người thao tác
-- trong ngữ cảnh phiên, nên quyền được khoá cứng ngay trong thủ tục: chỉ vai
-- trò 'reader', không gắn đơn vị, không bao giờ là cán bộ ('staff').  Ứng
-- dụng chỉ được EXECUTE thủ tục này, không có INSERT trên users/user_roles.
CREATE OR ALTER PROCEDURE dbo.sp_register_reader
    @username      VARCHAR(32),
    @password_hash VARCHAR(255),
    @full_name     NVARCHAR(100) = N'',
    @user_type     VARCHAR(16)   = 'other'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @user_type IS NULL OR @user_type NOT IN ('journalist', 'researcher', 'student', 'other')
        SET @user_type = 'other';
    IF @password_hash IS NULL OR @password_hash NOT LIKE 'scrypt$%'
        THROW 50007, N'Mật khẩu phải được băm bằng scrypt.', 1;

    DECLARE @name VARCHAR(32) = LOWER(LTRIM(RTRIM(@username)));
    BEGIN TRY
        BEGIN TRANSACTION;
            INSERT INTO dbo.users (username, full_name, password_hash, organization_id, user_type)
            VALUES (@name, COALESCE(LTRIM(RTRIM(@full_name)), N''), @password_hash, NULL, @user_type);
            DECLARE @user_id INT = SCOPE_IDENTITY();

            INSERT INTO dbo.user_roles (user_id, role_id)
            SELECT @user_id, role_id FROM dbo.roles WHERE role_name = 'reader';
            IF @@ROWCOUNT <> 1
                THROW 50002, N'Thiếu vai trò reader.', 1;

            INSERT INTO dbo.audit_log (user_id, action, detail)
            VALUES (@user_id, 'user.register', @name);
        COMMIT TRANSACTION;
        SELECT @user_id AS user_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

GRANT EXECUTE ON OBJECT::dbo.sp_register_reader TO archive_writer;
GO
