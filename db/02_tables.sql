-- =====================================================================
-- 02. Bảng và ràng buộc
--
-- Mọi ràng buộc đều có tên: PK_ (khoá chính), FK_ (khoá ngoại),
-- UQ_ (duy nhất), CK_ (kiểm tra), DF_ (mặc định).  Chuỗi tiếng Việt dùng
-- NVARCHAR; mã định danh ASCII dùng VARCHAR.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- ---------------------------------------------------------------------
-- Danh mục thư viện: đơn vị thành viên, bộ sưu tập, chủ đề
-- ---------------------------------------------------------------------

-- Đơn vị tham gia thư viện: vừa có thể đóng góp bộ sưu tập, vừa có thể là
-- nơi công tác của bạn đọc gửi yêu cầu khai thác.
CREATE TABLE dbo.organizations (
    organization_id SMALLINT IDENTITY(1,1) NOT NULL,
    org_code        VARCHAR(16)   NOT NULL,
    org_name        NVARCHAR(200) NOT NULL,
    org_type        VARCHAR(16)   NOT NULL,
    created_at      DATETIME2(0)  NOT NULL CONSTRAINT DF_organizations_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_organizations PRIMARY KEY (organization_id),
    CONSTRAINT UQ_organizations_code UNIQUE (org_code),
    CONSTRAINT UQ_organizations_name UNIQUE (org_name),
    -- library: đơn vị vận hành; tv: đài truyền hình; press: báo; school: trường; museum: bảo tàng
    CONSTRAINT CK_organizations_type CHECK (org_type IN ('library', 'tv', 'press', 'school', 'museum'))
);

-- Bộ sưu tập do một đơn vị đóng góp và giữ bản quyền.
CREATE TABLE dbo.collections (
    collection_id   INT IDENTITY(1,1) NOT NULL,
    collection_code VARCHAR(16)    NOT NULL,
    title           NVARCHAR(200)  NOT NULL,
    organization_id SMALLINT       NOT NULL,
    -- open: tra cứu và dùng tự do; restricted: dùng lại phải có giấy phép
    access_level    VARCHAR(16)    NOT NULL CONSTRAINT DF_collections_access DEFAULT 'restricted',
    description     NVARCHAR(1000) NOT NULL CONSTRAINT DF_collections_description DEFAULT N'',
    created_at      DATETIME2(0)   NOT NULL CONSTRAINT DF_collections_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_collections PRIMARY KEY (collection_id),
    CONSTRAINT UQ_collections_code UNIQUE (collection_code),
    CONSTRAINT FK_collections_organization FOREIGN KEY (organization_id)
        REFERENCES dbo.organizations (organization_id),
    CONSTRAINT CK_collections_access CHECK (access_level IN ('open', 'restricted'))
);
CREATE INDEX IX_collections_organization ON dbo.collections (organization_id);

-- Cây chủ đề: mỗi chủ đề có tối đa một chủ đề cha (quan hệ đệ quy).
CREATE TABLE dbo.topics (
    topic_id        SMALLINT IDENTITY(1,1) NOT NULL,
    topic_name      NVARCHAR(100) NOT NULL,
    parent_topic_id SMALLINT      NULL,
    CONSTRAINT PK_topics PRIMARY KEY (topic_id),
    CONSTRAINT UQ_topics_name UNIQUE (topic_name),
    CONSTRAINT FK_topics_parent FOREIGN KEY (parent_topic_id) REFERENCES dbo.topics (topic_id),
    CONSTRAINT CK_topics_parent CHECK (parent_topic_id IS NULL OR parent_topic_id <> topic_id)
);
CREATE INDEX IX_topics_parent ON dbo.topics (parent_topic_id);

-- ---------------------------------------------------------------------
-- Kho dữ liệu video: video, khung hình, chữ trên hình, lời nói
-- ---------------------------------------------------------------------

CREATE TABLE dbo.videos (
    video_id      VARCHAR(16)   NOT NULL,
    -- L23_V001 -> L23.  Cột tính toán nên không thể lệch với video_id.
    batch         AS CAST(LEFT(video_id, 3) AS VARCHAR(3)) PERSISTED,
    collection_id INT           NOT NULL,
    title         NVARCHAR(200) NOT NULL,
    fps           DECIMAL(6,3)  NULL,
    CONSTRAINT PK_videos PRIMARY KEY (video_id),
    -- Đơn vị sở hữu suy ra qua bộ sưu tập, không lưu lại ở đây (3NF).
    CONSTRAINT FK_videos_collection FOREIGN KEY (collection_id)
        REFERENCES dbo.collections (collection_id),
    CONSTRAINT CK_videos_id CHECK (video_id LIKE 'L[0-9][0-9][_]V[0-9][0-9][0-9]'),
    CONSTRAINT CK_videos_title CHECK (LEN(title) > 0),
    CONSTRAINT CK_videos_fps CHECK (fps IS NULL OR fps > 0)
);
CREATE INDEX IX_videos_batch ON dbo.videos (batch);
CREATE INDEX IX_videos_collection ON dbo.videos (collection_id);

-- Gắn chủ đề cho video (N:N).
CREATE TABLE dbo.video_topics (
    video_id VARCHAR(16) NOT NULL,
    topic_id SMALLINT    NOT NULL,
    CONSTRAINT PK_video_topics PRIMARY KEY (video_id, topic_id),
    CONSTRAINT FK_video_topics_video FOREIGN KEY (video_id)
        REFERENCES dbo.videos (video_id) ON DELETE CASCADE,
    CONSTRAINT FK_video_topics_topic FOREIGN KEY (topic_id)
        REFERENCES dbo.topics (topic_id) ON DELETE CASCADE
);
CREATE INDEX IX_video_topics_topic ON dbo.video_topics (topic_id);

-- Một dòng cho mỗi khung hình được trích.  keyframe_id chính là số thứ tự
-- của vector trong chỉ mục FAISS.  Ảnh nằm ở
-- keyframes/keyframes/<video_id>/<sample_n, 3 chữ số>.jpg (suy ra được).
CREATE TABLE dbo.keyframes (
    keyframe_id INT           NOT NULL,
    video_id    VARCHAR(16)   NOT NULL,
    sample_n    INT           NOT NULL,
    pts_time    DECIMAL(10,3) NOT NULL,
    frame_idx   INT           NOT NULL,
    CONSTRAINT PK_keyframes PRIMARY KEY (keyframe_id),
    CONSTRAINT FK_keyframes_video FOREIGN KEY (video_id) REFERENCES dbo.videos (video_id),
    CONSTRAINT UQ_keyframes_video_n UNIQUE (video_id, sample_n),
    -- Phục vụ tra "khung hình gần nhất theo thời gian" và "khung hình lân cận".
    CONSTRAINT UQ_keyframes_video_time UNIQUE (video_id, pts_time),
    CONSTRAINT UQ_keyframes_video_frame UNIQUE (video_id, frame_idx),
    CONSTRAINT CK_keyframes_values CHECK (pts_time >= 0 AND frame_idx >= 0 AND sample_n > 0)
);

-- Chữ xuất hiện trên khung hình (OCR), tối đa một dòng mỗi khung hình.
CREATE TABLE dbo.ocr_texts (
    keyframe_id INT            NOT NULL,
    ocr_text    NVARCHAR(2000) NOT NULL,
    text_folded AS dbo.fn_fold_vi(ocr_text) PERSISTED,
    CONSTRAINT PK_ocr_texts PRIMARY KEY (keyframe_id),
    CONSTRAINT FK_ocr_texts_keyframe FOREIGN KEY (keyframe_id)
        REFERENCES dbo.keyframes (keyframe_id) ON DELETE CASCADE,
    CONSTRAINT CK_ocr_texts_text CHECK (LEN(ocr_text) > 0)
);

-- Lời nói (ASR) theo đoạn thời gian; được gắn vào khung hình gần nhất khi
-- truy vấn (fn_nearest_keyframe), không lưu sẵn để tránh phụ thuộc bắc cầu.
CREATE TABLE dbo.asr_segments (
    segment_id  INT IDENTITY(1,1) NOT NULL,
    video_id    VARCHAR(16)    NOT NULL,
    start_sec   DECIMAL(10,3)  NOT NULL,
    end_sec     DECIMAL(10,3)  NOT NULL,
    asr_text    NVARCHAR(2000) NOT NULL,
    text_folded AS dbo.fn_fold_vi(asr_text) PERSISTED,
    CONSTRAINT PK_asr_segments PRIMARY KEY (segment_id),
    CONSTRAINT FK_asr_segments_video FOREIGN KEY (video_id)
        REFERENCES dbo.videos (video_id) ON DELETE CASCADE,
    CONSTRAINT CK_asr_segments_span CHECK (start_sec >= 0 AND end_sec >= start_sec),
    CONSTRAINT CK_asr_segments_text CHECK (LEN(asr_text) > 0)
);
CREATE INDEX IX_asr_segments_video_time ON dbo.asr_segments (video_id, start_sec);

-- ---------------------------------------------------------------------
-- Chỉ mục ngược cho tìm kiếm văn bản (thay cho Full-Text Search, vốn không
-- có trong SQL Server Express): từ -> danh sách khung hình / đoạn lời nói.
-- ---------------------------------------------------------------------

CREATE TABLE dbo.stopwords (
    word NVARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    CONSTRAINT PK_stopwords PRIMARY KEY (word)
);

CREATE TABLE dbo.terms (
    term_id INT IDENTITY(1,1) NOT NULL,
    term    NVARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
    CONSTRAINT PK_terms PRIMARY KEY (term_id),
    CONSTRAINT UQ_terms_term UNIQUE (term),
    CONSTRAINT CK_terms_length CHECK (LEN(term) >= 2)
);

CREATE TABLE dbo.ocr_postings (
    term_id     INT NOT NULL,
    keyframe_id INT NOT NULL,
    CONSTRAINT PK_ocr_postings PRIMARY KEY (term_id, keyframe_id),
    CONSTRAINT FK_ocr_postings_term FOREIGN KEY (term_id) REFERENCES dbo.terms (term_id) ON DELETE CASCADE,
    CONSTRAINT FK_ocr_postings_text FOREIGN KEY (keyframe_id)
        REFERENCES dbo.ocr_texts (keyframe_id) ON DELETE CASCADE
);

CREATE TABLE dbo.asr_postings (
    term_id    INT NOT NULL,
    segment_id INT NOT NULL,
    CONSTRAINT PK_asr_postings PRIMARY KEY (term_id, segment_id),
    CONSTRAINT FK_asr_postings_term FOREIGN KEY (term_id) REFERENCES dbo.terms (term_id) ON DELETE CASCADE,
    CONSTRAINT FK_asr_postings_segment FOREIGN KEY (segment_id)
        REFERENCES dbo.asr_segments (segment_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- Tài khoản và phân quyền theo vai trò (RBAC)
-- ---------------------------------------------------------------------

-- user_type là đối tượng bạn đọc (nhà báo, nhà nghiên cứu...), dùng để thống
-- kê; quyền thao tác do vai trò (roles) quyết định, hai khái niệm độc lập.
CREATE TABLE dbo.users (
    user_id         INT IDENTITY(1,1) NOT NULL,
    username        VARCHAR(32)   NOT NULL,
    full_name       NVARCHAR(100) NOT NULL CONSTRAINT DF_users_full_name DEFAULT N'',
    password_hash   VARCHAR(255)  NOT NULL,
    organization_id SMALLINT      NULL,
    user_type       VARCHAR(16)   NOT NULL CONSTRAINT DF_users_type DEFAULT 'other',
    is_active       BIT           NOT NULL CONSTRAINT DF_users_is_active DEFAULT 1,
    created_at      DATETIME2(0)  NOT NULL CONSTRAINT DF_users_created DEFAULT SYSDATETIME(),
    last_login_at   DATETIME2(0)  NULL,
    CONSTRAINT PK_users PRIMARY KEY (user_id),
    CONSTRAINT UQ_users_username UNIQUE (username),
    CONSTRAINT FK_users_organization FOREIGN KEY (organization_id)
        REFERENCES dbo.organizations (organization_id),
    -- 3-32 ký tự thường a-z, chữ số, _ . -  (so khớp nhị phân nên phân biệt hoa/thường)
    CONSTRAINT CK_users_username CHECK (
        LEN(username) BETWEEN 3 AND 32
        AND username COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^a-z0-9_.-]%'),
    CONSTRAINT CK_users_type CHECK (user_type IN ('staff', 'journalist', 'researcher', 'student', 'other'))
);
CREATE INDEX IX_users_organization ON dbo.users (organization_id);

CREATE TABLE dbo.roles (
    role_id     TINYINT IDENTITY(1,1) NOT NULL,
    role_name   VARCHAR(32)   NOT NULL,
    description NVARCHAR(255) NOT NULL CONSTRAINT DF_roles_description DEFAULT N'',
    CONSTRAINT PK_roles PRIMARY KEY (role_id),
    CONSTRAINT UQ_roles_name UNIQUE (role_name)
);

CREATE TABLE dbo.permissions (
    permission_code VARCHAR(32)   NOT NULL,
    description     NVARCHAR(255) NOT NULL CONSTRAINT DF_permissions_description DEFAULT N'',
    CONSTRAINT PK_permissions PRIMARY KEY (permission_code)
);

CREATE TABLE dbo.user_roles (
    user_id INT     NOT NULL,
    role_id TINYINT NOT NULL,
    CONSTRAINT PK_user_roles PRIMARY KEY (user_id, role_id),
    CONSTRAINT FK_user_roles_user FOREIGN KEY (user_id) REFERENCES dbo.users (user_id) ON DELETE CASCADE,
    CONSTRAINT FK_user_roles_role FOREIGN KEY (role_id) REFERENCES dbo.roles (role_id) ON DELETE CASCADE
);

CREATE TABLE dbo.role_permissions (
    role_id         TINYINT     NOT NULL,
    permission_code VARCHAR(32) NOT NULL,
    CONSTRAINT PK_role_permissions PRIMARY KEY (role_id, permission_code),
    CONSTRAINT FK_role_permissions_role FOREIGN KEY (role_id)
        REFERENCES dbo.roles (role_id) ON DELETE CASCADE,
    CONSTRAINT FK_role_permissions_permission FOREIGN KEY (permission_code)
        REFERENCES dbo.permissions (permission_code) ON DELETE CASCADE
);

-- Chỉ lưu SHA-256 của cookie đăng nhập, không lưu bản gốc.
CREATE TABLE dbo.sessions (
    session_hash CHAR(64)     NOT NULL,
    user_id      INT          NOT NULL,
    created_at   DATETIME2(0) NOT NULL CONSTRAINT DF_sessions_created DEFAULT SYSDATETIME(),
    expires_at   DATETIME2(0) NOT NULL,
    CONSTRAINT PK_sessions PRIMARY KEY (session_hash),
    CONSTRAINT FK_sessions_user FOREIGN KEY (user_id) REFERENCES dbo.users (user_id) ON DELETE CASCADE,
    CONSTRAINT CK_sessions_expiry CHECK (expires_at > created_at)
);
CREATE INDEX IX_sessions_expires ON dbo.sessions (expires_at);

-- ---------------------------------------------------------------------
-- Yêu cầu khai thác, đoạn tư liệu được chọn và giấy phép sử dụng
-- ---------------------------------------------------------------------

-- Số thứ tự cho mã yêu cầu bạn đọc gửi qua web (YC-U0001, YC-U0002...).
CREATE SEQUENCE dbo.seq_request_no AS INT START WITH 1 INCREMENT BY 1;

-- Bạn đọc mô tả đoạn tư liệu cần dùng và mục đích; cán bộ thư viện tìm,
-- chọn đoạn phù hợp rồi duyệt (cấp giấy phép) hoặc từ chối.
--   origin  sample: yêu cầu mẫu có vị trí đúng đã xác minh (dùng để đo chất
--           lượng tìm kiếm); user: yêu cầu gửi qua web.
--   status  chỉ đổi qua thủ tục: pending -> approved (sp_grant_license)
--           hoặc pending -> rejected (sp_reject_request).
-- Người yêu cầu bị khoá chứ không bị xoá, nên khoá ngoại tới users để
-- NO ACTION (ON DELETE CASCADE ở đây gây lỗi 1785 "nhiều đường cascade":
-- users -> selections trực tiếp và users -> requests -> selections).
CREATE TABLE dbo.requests (
    request_id   VARCHAR(32)    NOT NULL,
    request_text NVARCHAR(2000) NOT NULL,
    origin       VARCHAR(16)    NOT NULL,
    requested_by INT            NOT NULL,
    purpose      VARCHAR(16)    NOT NULL CONSTRAINT DF_requests_purpose DEFAULT 'research',
    status       VARCHAR(16)    NOT NULL CONSTRAINT DF_requests_status DEFAULT 'pending',
    created_at   DATETIME2(0)   NOT NULL CONSTRAINT DF_requests_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_requests PRIMARY KEY (request_id),
    CONSTRAINT FK_requests_user FOREIGN KEY (requested_by) REFERENCES dbo.users (user_id),
    CONSTRAINT CK_requests_origin CHECK (origin IN ('sample', 'user')),
    CONSTRAINT CK_requests_text CHECK (LEN(request_text) > 0),
    CONSTRAINT CK_requests_purpose CHECK (purpose IN ('broadcast', 'research', 'education', 'exhibition')),
    CONSTRAINT CK_requests_status CHECK (status IN ('pending', 'approved', 'rejected'))
);
CREATE INDEX IX_requests_user ON dbo.requests (requested_by);
CREATE INDEX IX_requests_status ON dbo.requests (status);

-- Vị trí đúng (đã xác minh thủ công) của đoạn tư liệu mà một yêu cầu mẫu
-- cần tìm; dùng để đo chất lượng tìm kiếm.
CREATE TABLE dbo.ground_truth (
    request_id VARCHAR(32)   NOT NULL,
    video_id   VARCHAR(16)   NOT NULL,
    start_sec  DECIMAL(10,3) NOT NULL,
    end_sec    DECIMAL(10,3) NOT NULL,
    CONSTRAINT PK_ground_truth PRIMARY KEY (request_id),
    CONSTRAINT FK_ground_truth_request FOREIGN KEY (request_id)
        REFERENCES dbo.requests (request_id) ON DELETE CASCADE,
    CONSTRAINT FK_ground_truth_video FOREIGN KEY (video_id) REFERENCES dbo.videos (video_id),
    CONSTRAINT CK_ground_truth_span CHECK (start_sec >= 0 AND end_sec >= start_sec)
);

-- Đoạn tư liệu (khung hình) cán bộ thư viện chọn cho một yêu cầu.
CREATE TABLE dbo.selections (
    selection_id INT IDENTITY(1,1) NOT NULL,
    request_id   VARCHAR(32)   NOT NULL,
    keyframe_id  INT           NOT NULL,
    user_id      INT           NULL,
    note         NVARCHAR(255) NOT NULL CONSTRAINT DF_selections_note DEFAULT N'',
    created_at   DATETIME2(0)  NOT NULL CONSTRAINT DF_selections_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_selections PRIMARY KEY (selection_id),
    CONSTRAINT UQ_selections_request_frame UNIQUE (request_id, keyframe_id),
    CONSTRAINT FK_selections_request FOREIGN KEY (request_id)
        REFERENCES dbo.requests (request_id) ON DELETE CASCADE,
    CONSTRAINT FK_selections_keyframe FOREIGN KEY (keyframe_id) REFERENCES dbo.keyframes (keyframe_id),
    CONSTRAINT FK_selections_user FOREIGN KEY (user_id) REFERENCES dbo.users (user_id) ON DELETE SET NULL
);

-- Giấy phép sử dụng cấp cho một yêu cầu đã duyệt (tối đa một giấy phép mỗi
-- yêu cầu).  licensee_org_id là ảnh chụp đơn vị của người yêu cầu tại thời
-- điểm cấp: người đó có thể chuyển đơn vị sau này nhưng giấy phép thì không
-- đổi chủ.  NULL nghĩa là cấp cho cá nhân.  Giấy phép là chứng từ nên
-- không bị xoá dây chuyền: phải xoá giấy phép trước khi xoá yêu cầu.
CREATE TABLE dbo.usage_licenses (
    license_id      INT IDENTITY(1,1) NOT NULL,
    request_id      VARCHAR(32)   NOT NULL,
    licensee_org_id SMALLINT      NULL,
    granted_by      INT           NOT NULL,
    granted_at      DATETIME2(0)  NOT NULL CONSTRAINT DF_usage_licenses_granted DEFAULT SYSDATETIME(),
    valid_from      DATE          NOT NULL,
    valid_to        DATE          NOT NULL,
    conditions      NVARCHAR(500) NOT NULL CONSTRAINT DF_usage_licenses_conditions DEFAULT N'',
    CONSTRAINT PK_usage_licenses PRIMARY KEY (license_id),
    CONSTRAINT UQ_usage_licenses_request UNIQUE (request_id),
    CONSTRAINT FK_usage_licenses_request FOREIGN KEY (request_id) REFERENCES dbo.requests (request_id),
    CONSTRAINT FK_usage_licenses_organization FOREIGN KEY (licensee_org_id)
        REFERENCES dbo.organizations (organization_id),
    CONSTRAINT FK_usage_licenses_user FOREIGN KEY (granted_by) REFERENCES dbo.users (user_id),
    CONSTRAINT CK_usage_licenses_period CHECK (valid_to > valid_from)
);
CREATE INDEX IX_usage_licenses_valid_to ON dbo.usage_licenses (valid_to);

-- ---------------------------------------------------------------------
-- Nhật ký và đánh giá
-- ---------------------------------------------------------------------

-- Danh mục nguồn tìm kiếm (thay cho cột chứa danh sách "ocr,asr" -> 1NF).
CREATE TABLE dbo.sources (
    source_code VARCHAR(16)   NOT NULL,
    description NVARCHAR(100) NOT NULL CONSTRAINT DF_sources_description DEFAULT N'',
    CONSTRAINT PK_sources PRIMARY KEY (source_code)
);

CREATE TABLE dbo.audit_log (
    log_id     BIGINT IDENTITY(1,1) NOT NULL,
    user_id    INT           NULL,
    action     VARCHAR(32)   NOT NULL,
    detail     NVARCHAR(500) NOT NULL CONSTRAINT DF_audit_log_detail DEFAULT N'',
    created_at DATETIME2(0)  NOT NULL CONSTRAINT DF_audit_log_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_audit_log PRIMARY KEY (log_id),
    CONSTRAINT FK_audit_log_user FOREIGN KEY (user_id) REFERENCES dbo.users (user_id) ON DELETE SET NULL
);
CREATE INDEX IX_audit_log_created ON dbo.audit_log (created_at);

CREATE TABLE dbo.search_log (
    search_id    BIGINT IDENTITY(1,1) NOT NULL,
    user_id      INT            NULL,
    request_text   NVARCHAR(2000) NOT NULL,
    top_k        SMALLINT       NOT NULL,
    result_count SMALLINT       NOT NULL,
    latency_ms   INT            NOT NULL,
    created_at   DATETIME2(0)   NOT NULL CONSTRAINT DF_search_log_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_search_log PRIMARY KEY (search_id),
    CONSTRAINT FK_search_log_user FOREIGN KEY (user_id) REFERENCES dbo.users (user_id) ON DELETE SET NULL,
    CONSTRAINT CK_search_log_values CHECK (top_k > 0 AND result_count >= 0 AND latency_ms >= 0)
);

CREATE TABLE dbo.search_log_sources (
    search_id   BIGINT      NOT NULL,
    source_code VARCHAR(16) NOT NULL,
    CONSTRAINT PK_search_log_sources PRIMARY KEY (search_id, source_code),
    CONSTRAINT FK_search_log_sources_search FOREIGN KEY (search_id)
        REFERENCES dbo.search_log (search_id) ON DELETE CASCADE,
    CONSTRAINT FK_search_log_sources_source FOREIGN KEY (source_code) REFERENCES dbo.sources (source_code)
);

CREATE TABLE dbo.eval_runs (
    run_id        INT IDENTITY(1,1) NOT NULL,
    run_name      NVARCHAR(64) NOT NULL,
    top_k         SMALLINT     NOT NULL,
    tolerance_sec DECIMAL(5,2) NOT NULL CONSTRAINT DF_eval_runs_tolerance DEFAULT 3.0,
    created_at    DATETIME2(0) NOT NULL CONSTRAINT DF_eval_runs_created DEFAULT SYSDATETIME(),
    CONSTRAINT PK_eval_runs PRIMARY KEY (run_id),
    CONSTRAINT CK_eval_runs_values CHECK (top_k > 0 AND tolerance_sec >= 0)
);

CREATE TABLE dbo.eval_run_sources (
    run_id      INT         NOT NULL,
    source_code VARCHAR(16) NOT NULL,
    CONSTRAINT PK_eval_run_sources PRIMARY KEY (run_id, source_code),
    CONSTRAINT FK_eval_run_sources_run FOREIGN KEY (run_id) REFERENCES dbo.eval_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT FK_eval_run_sources_source FOREIGN KEY (source_code) REFERENCES dbo.sources (source_code)
);

CREATE TABLE dbo.eval_requests (
    run_id     INT         NOT NULL,
    request_id   VARCHAR(32) NOT NULL,
    latency_ms INT         NOT NULL,
    CONSTRAINT PK_eval_requests PRIMARY KEY (run_id, request_id),
    CONSTRAINT FK_eval_requests_run FOREIGN KEY (run_id) REFERENCES dbo.eval_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT FK_eval_requests_truth FOREIGN KEY (request_id)
        REFERENCES dbo.ground_truth (request_id) ON DELETE CASCADE,
    CONSTRAINT CK_eval_requests_latency CHECK (latency_ms >= 0)
);

CREATE TABLE dbo.eval_results (
    run_id      INT         NOT NULL,
    request_id    VARCHAR(32) NOT NULL,
    rank_no     SMALLINT    NOT NULL,
    keyframe_id INT         NOT NULL,
    CONSTRAINT PK_eval_results PRIMARY KEY (run_id, request_id, rank_no),
    CONSTRAINT UQ_eval_results_frame UNIQUE (run_id, request_id, keyframe_id),
    CONSTRAINT FK_eval_results_request FOREIGN KEY (run_id, request_id)
        REFERENCES dbo.eval_requests (run_id, request_id) ON DELETE CASCADE,
    CONSTRAINT FK_eval_results_keyframe FOREIGN KEY (keyframe_id) REFERENCES dbo.keyframes (keyframe_id),
    CONSTRAINT CK_eval_results_rank CHECK (rank_no > 0)
);
GO
