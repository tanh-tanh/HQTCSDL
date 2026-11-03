-- =====================================================================
-- 06. Trigger ghi nhật ký (audit)
--
-- Trigger của SQL Server chạy một lần cho mỗi câu lệnh, nên luôn xử lý cả
-- tập dòng trong `inserted` / `deleted`.  Người thao tác lấy từ
-- SESSION_CONTEXT(N'app_user_id') do ứng dụng đặt.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

CREATE OR ALTER TRIGGER dbo.trg_selections_insert ON dbo.selections
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT i.user_id, 'selection.add', CONCAT(i.request_id, N' -> keyframe ', i.keyframe_id)
    FROM inserted i;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_selections_delete ON dbo.selections
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT CAST(SESSION_CONTEXT(N'app_user_id') AS INT), 'selection.remove',
           CONCAT(d.request_id, N' -> keyframe ', d.keyframe_id)
    FROM deleted d;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_users_update ON dbo.users
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @actor INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);

    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT @actor, CASE WHEN i.is_active = 1 THEN 'user.enable' ELSE 'user.disable' END, i.username
    FROM inserted i JOIN deleted d ON d.user_id = i.user_id
    WHERE i.is_active <> d.is_active;

    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT @actor, 'user.password', i.username
    FROM inserted i JOIN deleted d ON d.user_id = i.user_id
    WHERE i.password_hash <> d.password_hash;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_user_roles_insert ON dbo.user_roles
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT CAST(SESSION_CONTEXT(N'app_user_id') AS INT), 'role.grant',
           CONCAT(u.username, N' + ', r.role_name)
    FROM inserted i
    JOIN dbo.users u ON u.user_id = i.user_id
    JOIN dbo.roles r ON r.role_id = i.role_id;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_user_roles_delete ON dbo.user_roles
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT CAST(SESSION_CONTEXT(N'app_user_id') AS INT), 'role.revoke',
           CONCAT(u.username, N' - ', r.role_name)
    FROM deleted d
    JOIN dbo.users u ON u.user_id = d.user_id
    JOIN dbo.roles r ON r.role_id = d.role_id;
END;
GO

-- Duyệt hoặc từ chối yêu cầu khai thác (status chỉ đổi qua thủ tục).
CREATE OR ALTER TRIGGER dbo.trg_requests_status ON dbo.requests
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.audit_log (user_id, action, detail)
    SELECT CAST(SESSION_CONTEXT(N'app_user_id') AS INT),
           CASE i.status WHEN 'approved' THEN 'request.approve'
                         WHEN 'rejected' THEN 'request.reject'
                         ELSE 'request.reopen' END,
           CONCAT(i.request_id, N': ', d.status, N' -> ', i.status)
    FROM inserted i
    JOIN deleted d ON d.request_id = i.request_id
    WHERE i.status <> d.status;
END;
GO
