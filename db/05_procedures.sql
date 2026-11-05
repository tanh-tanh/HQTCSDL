-- =====================================================================
-- 05. Thủ tục lưu trữ (stored procedures)
--   sp_rebuild_text_index  lập lại chỉ mục ngược từ OCR/ASR
--   sp_search_footage      tìm khung hình: gộp hình ảnh + OCR + ASR bằng RRF
--   sp_submit_request      bạn đọc gửi yêu cầu khai thác (mã lấy từ SEQUENCE)
--   sp_add_selection       chọn đoạn tư liệu cho yêu cầu (kiểm tra quyền, trạng thái)
--   sp_remove_selection    bỏ đoạn đã chọn (kiểm tra quyền, trạng thái)
--   sp_grant_license       duyệt yêu cầu và cấp giấy phép trong một giao dịch
--   sp_reject_request      từ chối yêu cầu
--   sp_create_user         tạo tài khoản + vai trò trong một giao dịch
--   sp_register_reader     bạn đọc tự đăng ký (chỉ vai trò reader)
--   sp_progress_report     báo cáo tiến độ, trả kết quả qua tham số OUTPUT
--
-- Người đang thao tác được ứng dụng đặt bằng
--   EXEC sp_set_session_context N'app_user_id', <id>, @read_only = 1;
-- nên thủ tục không phải tin vào một tham số user_id do bên gọi tự khai.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

CREATE OR ALTER PROCEDURE dbo.sp_rebuild_text_index
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
        DELETE FROM dbo.ocr_postings;
        DELETE FROM dbo.asr_postings;
        DELETE FROM dbo.terms;

        INSERT INTO dbo.terms (term)
        SELECT k.term FROM dbo.ocr_texts o CROSS APPLY dbo.fn_tokens(o.ocr_text) k
        UNION
        SELECT k.term FROM dbo.asr_segments a CROSS APPLY dbo.fn_tokens(a.asr_text) k;

        INSERT INTO dbo.ocr_postings (term_id, keyframe_id)
        SELECT DISTINCT t.term_id, o.keyframe_id
        FROM dbo.ocr_texts o
        CROSS APPLY dbo.fn_tokens(o.ocr_text) k
        JOIN dbo.terms t ON t.term = k.term;

        INSERT INTO dbo.asr_postings (term_id, segment_id)
        SELECT DISTINCT t.term_id, a.segment_id
        FROM dbo.asr_segments a
        CROSS APPLY dbo.fn_tokens(a.asr_text) k
        JOIN dbo.terms t ON t.term = k.term;
    COMMIT TRANSACTION;

    SELECT (SELECT COUNT(*) FROM dbo.terms)        AS terms,
           (SELECT COUNT(*) FROM dbo.ocr_postings) AS ocr_postings,
           (SELECT COUNT(*) FROM dbo.asr_postings) AS asr_postings;
END;
GO

-- Gộp tối đa ba danh sách xếp hạng bằng Reciprocal Rank Fusion:
--     điểm(khung hình) = tổng theo nguồn của 1 / (60 + thứ hạng trong nguồn đó)
-- Trong mỗi nguồn văn bản, điểm của một khung hình là tổng IDF của các từ
-- khoá khớp: từ càng hiếm càng có trọng số cao.
--   @visual  mảng JSON các keyframe_id do kênh hình ảnh (SigLIP2 + FAISS)
--            trả về, tốt nhất đứng đầu; NULL nếu không dùng.
CREATE OR ALTER PROCEDURE dbo.sp_search_footage
    @query   NVARCHAR(2000),
    @visual  NVARCHAR(MAX) = NULL,
    @use_ocr BIT = 1,
    @use_asr BIT = 1,
    @pool    INT = 200,
    @top_k   INT = 50
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @ocr_docs FLOAT = (SELECT COUNT(*) FROM dbo.ocr_texts);
    DECLARE @asr_docs FLOAT = (SELECT COUNT(*) FROM dbo.asr_segments);

    DECLARE @terms TABLE (term_id INT PRIMARY KEY);
    INSERT INTO @terms (term_id)
    SELECT t.term_id FROM dbo.fn_tokens(@query) k JOIN dbo.terms t ON t.term = k.term;

    WITH visual AS (
        SELECT CAST(j.value AS INT) AS keyframe_id, CAST(j.[key] AS INT) + 1 AS rn
        FROM OPENJSON(COALESCE(@visual, N'[]')) AS j
    ),
    ocr_df AS (
        SELECT p.term_id, COUNT(*) AS df
        FROM dbo.ocr_postings p JOIN @terms q ON q.term_id = p.term_id
        GROUP BY p.term_id
    ),
    ocr_hits AS (
        SELECT TOP (@pool) p.keyframe_id, SUM(LOG(1.0 + @ocr_docs / d.df)) AS score
        FROM dbo.ocr_postings p
        JOIN ocr_df d ON d.term_id = p.term_id
        WHERE @use_ocr = 1
        GROUP BY p.keyframe_id
        ORDER BY score DESC, p.keyframe_id
    ),
    asr_df AS (
        SELECT p.term_id, COUNT(*) AS df
        FROM dbo.asr_postings p JOIN @terms q ON q.term_id = p.term_id
        GROUP BY p.term_id
    ),
    asr_hits AS (
        SELECT TOP (@pool) p.segment_id, SUM(LOG(1.0 + @asr_docs / d.df)) AS score
        FROM dbo.asr_postings p
        JOIN asr_df d ON d.term_id = p.term_id
        WHERE @use_asr = 1
        GROUP BY p.segment_id
        ORDER BY score DESC, p.segment_id
    ),
    ranked AS (
        SELECT 'visual' AS source, keyframe_id, rn FROM visual WHERE rn <= @pool
        UNION ALL
        SELECT 'ocr', keyframe_id, ROW_NUMBER() OVER (ORDER BY score DESC, keyframe_id)
        FROM ocr_hits
        UNION ALL
        SELECT 'asr', dbo.fn_nearest_keyframe(s.video_id, (s.start_sec + s.end_sec) / 2),
               ROW_NUMBER() OVER (ORDER BY h.score DESC, h.segment_id)
        FROM asr_hits h JOIN dbo.asr_segments s ON s.segment_id = h.segment_id
    ),
    -- Nhiều đoạn lời nói có thể rơi vào cùng một khung hình: giữ thứ hạng tốt nhất.
    per_source AS (
        SELECT source, keyframe_id, MIN(rn) AS rn
        FROM ranked
        WHERE keyframe_id IS NOT NULL
        GROUP BY source, keyframe_id
    ),
    fused AS (
        SELECT keyframe_id,
               SUM(1.0 / (60 + rn)) AS rrf_score,
               STRING_AGG(CONCAT(source, '#', rn), ' ') WITHIN GROUP (ORDER BY source) AS evidence
        FROM per_source
        GROUP BY keyframe_id
    )
    SELECT TOP (@top_k)
           k.keyframe_id, k.video_id, k.sample_n, k.pts_time, k.frame_idx,
           CAST(f.rrf_score AS DECIMAL(9,6)) AS rrf_score, f.evidence
    FROM fused f
    JOIN dbo.keyframes k ON k.keyframe_id = f.keyframe_id
    ORDER BY f.rrf_score DESC, k.keyframe_id;
END;
GO

-- Bạn đọc gửi yêu cầu khai thác.  Người gửi lấy từ ngữ cảnh phiên, mã yêu
-- cầu sinh từ SEQUENCE nên hai người gửi cùng lúc không bao giờ trùng mã.
CREATE OR ALTER PROCEDURE dbo.sp_submit_request
    @request_text NVARCHAR(2000),
    @purpose      VARCHAR(16) = 'research'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @user_id INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF @user_id IS NULL OR dbo.fn_has_permission(@user_id, 'request.submit') = 0
        THROW 50001, N'Không có quyền request.submit.', 1;

    DECLARE @n INT = NEXT VALUE FOR dbo.seq_request_no;
    DECLARE @request_id VARCHAR(32) = CONCAT('YC-U', REPLICATE('0', 4 - LEN(@n)), @n);
    INSERT INTO dbo.requests (request_id, request_text, origin, requested_by, purpose)
    VALUES (@request_id, LTRIM(RTRIM(@request_text)), 'user', @user_id, @purpose);
    SELECT @request_id AS request_id;
END;
GO

-- Cơ sở dữ liệu tự kiểm tra quyền, nên lỗi ở tầng web cũng không cho
-- bạn đọc chọn được đoạn tư liệu.  Yêu cầu đã duyệt hoặc đã từ chối thì
-- danh sách đoạn tư liệu bị khoá (giấy phép cấp cho đúng các đoạn đó).
CREATE OR ALTER PROCEDURE dbo.sp_add_selection
    @request_id  VARCHAR(32),
    @keyframe_id INT,
    @note        NVARCHAR(255) = N''
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @user_id INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF @user_id IS NULL OR dbo.fn_has_permission(@user_id, 'selection.write') = 0
        THROW 50001, N'Không có quyền selection.write.', 1;
    IF EXISTS (SELECT 1 FROM dbo.requests WHERE request_id = @request_id AND status <> 'pending')
        THROW 50005, N'Yêu cầu đã được xử lý, không sửa danh sách đoạn tư liệu.', 1;

    UPDATE dbo.selections SET note = COALESCE(@note, N'')
    WHERE request_id = @request_id AND keyframe_id = @keyframe_id;
    IF @@ROWCOUNT = 0
        INSERT INTO dbo.selections (request_id, keyframe_id, user_id, note)
        VALUES (@request_id, @keyframe_id, @user_id, COALESCE(@note, N''));
END;
GO

CREATE OR ALTER PROCEDURE dbo.sp_remove_selection
    @selection_id INT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @user_id INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF @user_id IS NULL OR dbo.fn_has_permission(@user_id, 'selection.write') = 0
        THROW 50001, N'Không có quyền selection.write.', 1;
    IF EXISTS (SELECT 1 FROM dbo.selections s JOIN dbo.requests r ON r.request_id = s.request_id
               WHERE s.selection_id = @selection_id AND r.status <> 'pending')
        THROW 50005, N'Yêu cầu đã được xử lý, không sửa danh sách đoạn tư liệu.', 1;
    DELETE FROM dbo.selections WHERE selection_id = @selection_id;
END;
GO

-- Duyệt yêu cầu: tạo giấy phép và chuyển trạng thái sang approved trong
-- CÙNG một giao dịch, nên không thể có yêu cầu "approved" mà thiếu giấy
-- phép hay ngược lại.  UPDLOCK giữ dòng yêu cầu để hai cán bộ duyệt cùng
-- lúc không cấp hai giấy phép.
CREATE OR ALTER PROCEDURE dbo.sp_grant_license
    @request_id VARCHAR(32),
    @valid_days INT = 365,
    @conditions NVARCHAR(500) = N''
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @actor INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF @actor IS NULL OR dbo.fn_has_permission(@actor, 'license.grant') = 0
        THROW 50001, N'Không có quyền license.grant.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;
            DECLARE @status VARCHAR(16), @org SMALLINT;
            SELECT @status = r.status, @org = u.organization_id
            FROM dbo.requests r WITH (UPDLOCK, HOLDLOCK)
            JOIN dbo.users u ON u.user_id = r.requested_by
            WHERE r.request_id = @request_id;

            IF @status IS NULL
                THROW 50004, N'Yêu cầu không tồn tại.', 1;
            IF @status <> 'pending'
                THROW 50005, N'Yêu cầu đã được xử lý.', 1;
            IF NOT EXISTS (SELECT 1 FROM dbo.selections WHERE request_id = @request_id)
                THROW 50006, N'Chưa chọn đoạn tư liệu nào cho yêu cầu.', 1;

            DECLARE @today DATE = CAST(SYSDATETIME() AS DATE);
            INSERT INTO dbo.usage_licenses (request_id, licensee_org_id, granted_by,
                                            valid_from, valid_to, conditions)
            VALUES (@request_id, @org, @actor, @today, DATEADD(DAY, @valid_days, @today),
                    COALESCE(@conditions, N''));
            DECLARE @license_id INT = SCOPE_IDENTITY();

            UPDATE dbo.requests SET status = 'approved' WHERE request_id = @request_id;
        COMMIT TRANSACTION;
        SELECT @license_id AS license_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE dbo.sp_reject_request
    @request_id VARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @actor INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF @actor IS NULL OR dbo.fn_has_permission(@actor, 'license.grant') = 0
        THROW 50001, N'Không có quyền license.grant.', 1;

    UPDATE dbo.requests SET status = 'rejected'
    WHERE request_id = @request_id AND status = 'pending';
    IF @@ROWCOUNT = 0
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM dbo.requests WHERE request_id = @request_id)
            THROW 50004, N'Yêu cầu không tồn tại.', 1;
        THROW 50005, N'Yêu cầu đã được xử lý.', 1;
    END;
END;
GO

-- Tạo người dùng cùng các vai trò (danh sách phân tách bằng dấu phẩy) trong
-- MỘT giao dịch: vai trò sai thì không có người dùng nào được tạo.
CREATE OR ALTER PROCEDURE dbo.sp_create_user
    @username        VARCHAR(32),
    @password_hash   VARCHAR(255),
    @roles           VARCHAR(200),
    @full_name       NVARCHAR(100) = N'',
    @organization_id SMALLINT      = NULL,
    @user_type       VARCHAR(16)   = 'other'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @actor INT = CAST(SESSION_CONTEXT(N'app_user_id') AS INT);
    IF IS_MEMBER('db_owner') = 0 AND (@actor IS NULL OR dbo.fn_has_permission(@actor, 'user.manage') = 0)
        THROW 50001, N'Không có quyền user.manage.', 1;

    DECLARE @wanted TABLE (role_name VARCHAR(32) PRIMARY KEY);
    INSERT INTO @wanted (role_name)
    SELECT DISTINCT LTRIM(RTRIM(value)) FROM STRING_SPLIT(@roles, ',') WHERE LTRIM(RTRIM(value)) <> '';

    BEGIN TRY
        BEGIN TRANSACTION;
            INSERT INTO dbo.users (username, full_name, password_hash, organization_id, user_type)
            VALUES (LOWER(LTRIM(RTRIM(@username))), COALESCE(@full_name, N''), @password_hash,
                    @organization_id, COALESCE(@user_type, 'other'));
            DECLARE @user_id INT = SCOPE_IDENTITY();

            IF EXISTS (SELECT 1 FROM @wanted w WHERE NOT EXISTS (
                           SELECT 1 FROM dbo.roles r WHERE r.role_name = w.role_name))
                THROW 50002, N'Vai trò không tồn tại.', 1;
            IF NOT EXISTS (SELECT 1 FROM @wanted)
                THROW 50003, N'Người dùng phải có ít nhất một vai trò.', 1;

            INSERT INTO dbo.user_roles (user_id, role_id)
            SELECT @user_id, r.role_id FROM dbo.roles r JOIN @wanted w ON w.role_name = r.role_name;
        COMMIT TRANSACTION;
        SELECT @user_id AS user_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
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

-- Báo cáo tiến độ: số yêu cầu, số yêu cầu đã có đoạn tư liệu được chọn và tỉ
-- lệ qua tham số OUTPUT, kèm bảng chi tiết theo nguồn yêu cầu và trạng thái.
CREATE OR ALTER PROCEDURE dbo.sp_progress_report
    @origin       VARCHAR(16) = NULL,
    @total        INT          OUTPUT,
    @selected     INT          OUTPUT,
    @percent_done DECIMAL(5,2) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT @total = COUNT(*),
           @selected = SUM(CASE WHEN selection_count > 0 THEN 1 ELSE 0 END)
    FROM dbo.v_request_progress
    WHERE @origin IS NULL OR origin = @origin;

    SET @selected = COALESCE(@selected, 0);
    SET @percent_done = CASE WHEN @total = 0 THEN 0 ELSE 100.0 * @selected / @total END;

    SELECT origin,
           COUNT(*) AS requests,
           SUM(CASE WHEN selection_count > 0 THEN 1 ELSE 0 END) AS with_selection,
           SUM(CASE WHEN status = 'pending'  THEN 1 ELSE 0 END) AS pending,
           SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
           SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
           SUM(CASE WHEN has_ground_truth = 1 THEN 1 ELSE 0 END) AS verified
    FROM dbo.v_request_progress
    WHERE @origin IS NULL OR origin = @origin
    GROUP BY origin
    ORDER BY origin;
END;
GO
