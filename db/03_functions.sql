-- =====================================================================
-- 03. Hàm người dùng định nghĩa (UDF)
--   fn_tokens            hàm bảng (inline TVF): tách một đoạn văn thành từ khoá
--   fn_nearest_keyframe  hàm vô hướng: khung hình gần một thời điểm nhất
--   fn_has_permission    hàm vô hướng: người dùng có quyền hay không
--   fn_topic_subtree     hàm bảng đệ quy: một chủ đề và mọi chủ đề con cháu
-- (fn_fold_vi ở file 01 vì các bảng cần nó.)
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- Bỏ dấu, đổi dấu câu thành khoảng trắng, tách theo khoảng trắng, bỏ từ
-- dừng và từ một ký tự.  Dùng chung cho cả văn bản (khi lập chỉ mục) và câu
-- truy vấn, nên hai phía luôn tách từ giống hệt nhau.
CREATE OR ALTER FUNCTION dbo.fn_tokens (@text NVARCHAR(4000))
RETURNS TABLE
AS
RETURN
    SELECT DISTINCT CAST(s.value AS NVARCHAR(64)) COLLATE Latin1_General_100_BIN2 AS term
    FROM STRING_SPLIT(
            TRANSLATE(dbo.fn_fold_vi(@text),
                N'.,;:!?"''()[]{}<>/\|-_+*=&%$#@~^`“”‘’…–—•·' + NCHAR(9) + NCHAR(10) + NCHAR(13),
                REPLICATE(N' ', LEN(N'.,;:!?"''()[]{}<>/\|-_+*=&%$#@~^`“”‘’…–—•·') + 3)),
            N' ') AS s
    WHERE LEN(s.value) BETWEEN 2 AND 64
      AND NOT EXISTS (
          SELECT 1 FROM dbo.stopwords w
          WHERE w.word = CAST(s.value AS NVARCHAR(64)) COLLATE Latin1_General_100_BIN2);
GO

-- Hai lần dò chỉ mục UQ_keyframes_video_time (một lùi, một tiến) thay vì
-- sắp xếp cả video theo khoảng cách thời gian.
CREATE OR ALTER FUNCTION dbo.fn_nearest_keyframe (@video_id VARCHAR(16), @time DECIMAL(10,3))
RETURNS INT
AS
BEGIN
    DECLARE @before_id INT, @before_t DECIMAL(10,3), @after_id INT, @after_t DECIMAL(10,3);

    SELECT TOP (1) @before_id = keyframe_id, @before_t = pts_time
    FROM dbo.keyframes
    WHERE video_id = @video_id AND pts_time <= @time
    ORDER BY pts_time DESC;

    SELECT TOP (1) @after_id = keyframe_id, @after_t = pts_time
    FROM dbo.keyframes
    WHERE video_id = @video_id AND pts_time >= @time
    ORDER BY pts_time ASC;

    IF @before_id IS NULL RETURN @after_id;
    IF @after_id IS NULL RETURN @before_id;
    RETURN CASE WHEN @time - @before_t <= @after_t - @time THEN @before_id ELSE @after_id END;
END;
GO

CREATE OR ALTER FUNCTION dbo.fn_has_permission (@user_id INT, @permission VARCHAR(32))
RETURNS BIT
AS
BEGIN
    RETURN CASE WHEN EXISTS (
        SELECT 1
        FROM dbo.users u
        JOIN dbo.user_roles ur       ON ur.user_id = u.user_id
        JOIN dbo.role_permissions rp ON rp.role_id = ur.role_id
        WHERE u.user_id = @user_id
          AND u.is_active = 1
          AND rp.permission_code = @permission
    ) THEN 1 ELSE 0 END;
END;
GO

-- CTE đệ quy đi xuống cây chủ đề.  depth = 0 là chủ đề gốc được hỏi; path
-- là đường dẫn từ gốc, dùng để sắp xếp khi in cây.
CREATE OR ALTER FUNCTION dbo.fn_topic_subtree (@root_id SMALLINT)
RETURNS TABLE
AS
RETURN
    WITH tree AS (
        SELECT t.topic_id, t.topic_name, t.parent_topic_id, 0 AS depth,
               CAST(t.topic_name AS NVARCHAR(1000)) AS path
        FROM dbo.topics t
        WHERE t.topic_id = @root_id
        UNION ALL
        SELECT c.topic_id, c.topic_name, c.parent_topic_id, p.depth + 1,
               CAST(CONCAT(p.path, N' > ', c.topic_name) AS NVARCHAR(1000))
        FROM dbo.topics c
        JOIN tree p ON c.parent_topic_id = p.topic_id
    )
    SELECT topic_id, topic_name, parent_topic_id, depth, path FROM tree;
GO
