-- =====================================================================
-- 04. View phục vụ báo cáo và giao diện
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- Quyền thực tế của mỗi người dùng đang hoạt động (hợp các vai trò).
CREATE OR ALTER VIEW dbo.v_user_permissions AS
SELECT DISTINCT u.user_id, u.username, rp.permission_code
FROM dbo.users u
JOIN dbo.user_roles ur       ON ur.user_id = u.user_id
JOIN dbo.role_permissions rp ON rp.role_id = ur.role_id
WHERE u.is_active = 1;
GO

CREATE OR ALTER VIEW dbo.v_user_overview AS
SELECT u.user_id, u.username, u.full_name, u.user_type, o.org_name AS organization,
       u.is_active, u.created_at, u.last_login_at,
       COALESCE((SELECT STRING_AGG(r.role_name, ', ') WITHIN GROUP (ORDER BY r.role_name)
                 FROM dbo.user_roles ur JOIN dbo.roles r ON r.role_id = ur.role_id
                 WHERE ur.user_id = u.user_id), '') AS roles
FROM dbo.users u
LEFT JOIN dbo.organizations o ON o.organization_id = u.organization_id;
GO

-- View cập nhật được: chỉ người dùng đang hoạt động.  WITH CHECK OPTION chặn
-- mọi thao tác qua view làm một dòng "rơi ra ngoài" view (vd. khoá tài khoản).
CREATE OR ALTER VIEW dbo.v_active_users AS
SELECT user_id, username, full_name, is_active
FROM dbo.users
WHERE is_active = 1
WITH CHECK OPTION;
GO

-- Tình trạng xử lý của từng yêu cầu khai thác: ai gửi, thuộc đơn vị nào,
-- đã chọn bao nhiêu đoạn tư liệu, đã cấp giấy phép đến ngày nào.
CREATE OR ALTER VIEW dbo.v_request_progress AS
SELECT q.request_id, q.origin, q.request_text, q.purpose, q.status, q.created_at,
       u.username AS requested_by, o.org_name AS organization,
       COALESCE(a.selection_count, 0) AS selection_count,
       a.last_selected_at,
       CAST(CASE WHEN g.request_id IS NULL THEN 0 ELSE 1 END AS BIT) AS has_ground_truth,
       l.license_id, l.valid_to AS license_valid_to
FROM dbo.requests q
JOIN dbo.users u                 ON u.user_id = q.requested_by
LEFT JOIN dbo.organizations o    ON o.organization_id = u.organization_id
LEFT JOIN (SELECT request_id, COUNT(*) AS selection_count, MAX(created_at) AS last_selected_at
           FROM dbo.selections
           GROUP BY request_id) a ON a.request_id = q.request_id
LEFT JOIN dbo.ground_truth g     ON g.request_id = q.request_id
LEFT JOIN dbo.usage_licenses l   ON l.request_id = q.request_id;
GO

-- Đoạn tư liệu đã chọn theo thứ tự, kèm frame_idx để xuất danh sách trích đoạn.
CREATE OR ALTER VIEW dbo.v_selection_export AS
SELECT a.request_id,
       ROW_NUMBER() OVER (PARTITION BY a.request_id ORDER BY a.created_at, a.selection_id) AS rank_no,
       k.video_id, k.frame_idx, k.pts_time, k.sample_n,
       a.selection_id, a.keyframe_id, a.note, u.username AS selected_by, a.created_at
FROM dbo.selections a
JOIN dbo.keyframes k  ON k.keyframe_id = a.keyframe_id
LEFT JOIN dbo.users u ON u.user_id = a.user_id;
GO

CREATE OR ALTER VIEW dbo.v_video_stats AS
SELECT v.video_id, v.batch, v.title, c.collection_code, v.fps,
       COUNT(k.keyframe_id)                        AS keyframe_count,
       MAX(k.pts_time)                             AS last_keyframe_sec,
       (SELECT COUNT(*) FROM dbo.ocr_texts o JOIN dbo.keyframes k2 ON k2.keyframe_id = o.keyframe_id
        WHERE k2.video_id = v.video_id)            AS ocr_frames,
       (SELECT COUNT(*) FROM dbo.asr_segments s WHERE s.video_id = v.video_id) AS asr_segments
FROM dbo.videos v
JOIN dbo.collections c    ON c.collection_id = v.collection_id
LEFT JOIN dbo.keyframes k ON k.video_id = v.video_id
GROUP BY v.video_id, v.batch, v.title, c.collection_code, v.fps;
GO

-- Phiếu mô tả video: nhan đề, bộ sưu tập, đơn vị giữ bản quyền, chủ đề.
CREATE OR ALTER VIEW dbo.v_video_catalog AS
SELECT v.video_id, v.title, c.collection_code, c.title AS collection_title, c.access_level,
       o.org_name AS owner,
       COALESCE((SELECT STRING_AGG(t.topic_name, N', ') WITHIN GROUP (ORDER BY t.topic_name)
                 FROM dbo.video_topics vt JOIN dbo.topics t ON t.topic_id = vt.topic_id
                 WHERE vt.video_id = v.video_id), N'') AS topics
FROM dbo.videos v
JOIN dbo.collections c   ON c.collection_id = v.collection_id
JOIN dbo.organizations o ON o.organization_id = c.organization_id;
GO

-- Quy mô từng bộ sưu tập; bộ sưu tập chưa nạp video vẫn hiện với số 0.
CREATE OR ALTER VIEW dbo.v_collection_stats AS
SELECT c.collection_id, c.collection_code, c.title, o.org_name AS owner, o.org_type,
       c.access_level,
       COUNT(v.video_id) AS videos,
       (SELECT COUNT(*) FROM dbo.keyframes k JOIN dbo.videos v2 ON v2.video_id = k.video_id
        WHERE v2.collection_id = c.collection_id) AS keyframes,
       (SELECT COUNT(DISTINCT vt.topic_id) FROM dbo.video_topics vt
        JOIN dbo.videos v3 ON v3.video_id = vt.video_id
        WHERE v3.collection_id = c.collection_id) AS topics
FROM dbo.collections c
JOIN dbo.organizations o ON o.organization_id = c.organization_id
LEFT JOIN dbo.videos v   ON v.collection_id = c.collection_id
GROUP BY c.collection_id, c.collection_code, c.title, o.org_name, o.org_type, c.access_level;
GO

-- Giấy phép kèm hiệu lực tính theo ngày hôm nay (is_active, days_left).
CREATE OR ALTER VIEW dbo.v_license_overview AS
SELECT l.license_id, l.request_id, r.purpose,
       ru.username AS requested_by, o.org_name AS licensee, gu.username AS granted_by,
       l.granted_at, l.valid_from, l.valid_to, l.conditions,
       CAST(CASE WHEN CAST(SYSDATETIME() AS DATE) BETWEEN l.valid_from AND l.valid_to
                 THEN 1 ELSE 0 END AS BIT) AS is_active,
       DATEDIFF(DAY, CAST(SYSDATETIME() AS DATE), l.valid_to) AS days_left,
       (SELECT COUNT(*) FROM dbo.selections s WHERE s.request_id = l.request_id) AS clip_count
FROM dbo.usage_licenses l
JOIN dbo.requests r           ON r.request_id = l.request_id
JOIN dbo.users ru             ON ru.user_id = r.requested_by
JOIN dbo.users gu             ON gu.user_id = l.granted_by
LEFT JOIN dbo.organizations o ON o.organization_id = l.licensee_org_id;
GO

-- Thứ hạng đầu tiên mà một lượt đánh giá trả về khung hình đúng video và
-- lệch không quá tolerance_sec giây so với vị trí đã xác minh; NULL nghĩa là trượt.
CREATE OR ALTER VIEW dbo.v_eval_first_hit AS
SELECT eq.run_id, eq.request_id, eq.latency_ms,
       MIN(CASE
               WHEN k.video_id = g.video_id
                AND k.pts_time BETWEEN g.start_sec - er.tolerance_sec AND g.end_sec + er.tolerance_sec
               THEN r.rank_no
           END) AS first_hit
FROM dbo.eval_requests eq
JOIN dbo.eval_runs er        ON er.run_id = eq.run_id
JOIN dbo.ground_truth g      ON g.request_id = eq.request_id
LEFT JOIN dbo.eval_results r ON r.run_id = eq.run_id AND r.request_id = eq.request_id
LEFT JOIN dbo.keyframes k    ON k.keyframe_id = r.keyframe_id
GROUP BY eq.run_id, eq.request_id, eq.latency_ms;
GO

CREATE OR ALTER VIEW dbo.v_eval_summary AS
SELECT er.run_id, er.run_name, er.top_k, er.tolerance_sec, er.created_at,
       COALESCE((SELECT STRING_AGG(s.source_code, ',') WITHIN GROUP (ORDER BY s.source_code)
                 FROM dbo.eval_run_sources s WHERE s.run_id = er.run_id), '') AS sources,
       COUNT(h.request_id) AS requests,
       CAST(AVG(CASE WHEN h.first_hit <= 1 THEN 1.0 ELSE 0.0 END) AS DECIMAL(5,3))        AS recall_at_1,
       CAST(AVG(CASE WHEN h.first_hit <= 5 THEN 1.0 ELSE 0.0 END) AS DECIMAL(5,3))        AS recall_at_5,
       CAST(AVG(CASE WHEN h.first_hit <= 20 THEN 1.0 ELSE 0.0 END) AS DECIMAL(5,3))       AS recall_at_20,
       CAST(AVG(CASE WHEN h.first_hit <= er.top_k THEN 1.0 ELSE 0.0 END) AS DECIMAL(5,3)) AS recall_at_k,
       CAST(AVG(CASE WHEN h.first_hit IS NULL THEN 0.0 ELSE 1.0 / h.first_hit END) AS DECIMAL(5,3)) AS mrr,
       AVG(h.latency_ms) AS avg_latency_ms,
       MAX(h.latency_ms) AS max_latency_ms
FROM dbo.eval_runs er
LEFT JOIN dbo.v_eval_first_hit h ON h.run_id = er.run_id
GROUP BY er.run_id, er.run_name, er.top_k, er.tolerance_sec, er.created_at;
GO
