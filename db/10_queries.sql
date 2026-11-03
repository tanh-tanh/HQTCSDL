-- =====================================================================
-- 10. Truy vấn nghiệp vụ (chạy sau full_setup.sql)
--   sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -d video_archive -i db\10_queries.sql -o queries.txt
-- (ghi ra file bằng -o: console của Windows làm hỏng tiếng Việt)
-- Mỗi truy vấn trả lời một câu hỏi nghiệp vụ; dòng "Kỹ thuật" ghi các
-- thành phần SQL được dùng.
-- =====================================================================
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- Q1. Danh sách video của lô lưu trữ L23 và tốc độ khung hình.
-- Kỹ thuật: SELECT *, WHERE trên cột tính toán, ORDER BY.
PRINT N'Q1. Video của lô L23';
SELECT * FROM dbo.videos WHERE batch = 'L23' ORDER BY video_id;
GO

-- Q2. Video dài (trên 300 khung hình) và tỉ lệ khung hình có chữ.
-- Kỹ thuật: INNER JOIN, LEFT JOIN, GROUP BY, HAVING, hàm tổng hợp.
PRINT N'Q2. Video có hơn 300 khung hình và tỉ lệ khung hình có chữ';
SELECT v.video_id,
       COUNT(k.keyframe_id) AS keyframes,
       COUNT(o.keyframe_id) AS ocr_frames,
       CAST(100.0 * COUNT(o.keyframe_id) / COUNT(k.keyframe_id) AS DECIMAL(5,1)) AS ocr_percent
FROM dbo.videos v
INNER JOIN dbo.keyframes k ON k.video_id = v.video_id
LEFT JOIN dbo.ocr_texts o  ON o.keyframe_id = k.keyframe_id
GROUP BY v.video_id
HAVING COUNT(k.keyframe_id) > 300
ORDER BY keyframes DESC;
GO

-- Q3. Video có hơn một nửa số khung hình không chứa chữ (khó tìm bằng OCR,
--     cần dựa vào hình ảnh hoặc lời nói).
-- Kỹ thuật: LEFT JOIN ... IS NULL (anti-join), CASE trong SUM, GROUP BY, HAVING.
PRINT N'Q3. Video có hơn 50% khung hình không có chữ';
SELECT k.video_id,
       COUNT(*) AS keyframes,
       SUM(CASE WHEN o.keyframe_id IS NULL THEN 1 ELSE 0 END) AS frames_without_text
FROM dbo.keyframes k
LEFT JOIN dbo.ocr_texts o ON o.keyframe_id = k.keyframe_id
GROUP BY k.video_id
HAVING SUM(CASE WHEN o.keyframe_id IS NULL THEN 1 ELSE 0 END) * 2 > COUNT(*)
ORDER BY frames_without_text DESC;
GO

-- Q4. Số yêu cầu khai thác đã / chưa có đoạn tư liệu được chọn, theo nguồn yêu cầu.
-- Kỹ thuật: RIGHT JOIN (giữ mọi yêu cầu), bảng dẫn xuất, CASE.
PRINT N'Q4. Yêu cầu đã và chưa có đoạn tư liệu được chọn';
SELECT origin,
       SUM(CASE WHEN selections > 0 THEN 1 ELSE 0 END) AS with_selection,
       SUM(CASE WHEN selections = 0 THEN 1 ELSE 0 END) AS without_selection
FROM (
    SELECT q.request_id, q.origin, COUNT(a.selection_id) AS selections
    FROM dbo.selections a
    RIGHT JOIN dbo.requests q ON q.request_id = a.request_id
    GROUP BY q.request_id, q.origin
) AS per_query
GROUP BY origin;
GO

-- Q5. Đối chiếu đoạn cán bộ thư viện chọn đầu tiên với vị trí đã xác minh.
-- Kỹ thuật: FULL OUTER JOIN, BETWEEN, CASE, GROUP BY.
PRINT N'Q5. Đối chiếu đoạn đã chọn với vị trí đã xác minh (lệch tối đa 3 giây)';
SELECT status, COUNT(*) AS requests
FROM (
    SELECT CASE
               WHEN g.request_id IS NULL THEN N'Đã chọn đoạn, chưa xác minh'
               WHEN c.request_id IS NULL THEN N'Đã xác minh, chưa chọn đoạn'
               WHEN c.video_id = g.video_id
                AND c.pts_time BETWEEN g.start_sec - 3 AND g.end_sec + 3 THEN N'Chọn đúng đoạn'
               ELSE N'Chọn sai đoạn'
           END AS status
    FROM (SELECT request_id, video_id, pts_time FROM dbo.v_selection_export WHERE rank_no = 1) AS c
    FULL OUTER JOIN dbo.ground_truth g ON g.request_id = c.request_id
) AS compared
GROUP BY status;
GO

-- Q6. Video dài hơn thời lượng trung bình.
-- Kỹ thuật: truy vấn con vô hướng (scalar subquery) trong WHERE và SELECT.
PRINT N'Q6. Video dài hơn trung bình';
SELECT video_id, last_keyframe_sec,
       CAST(last_keyframe_sec - (SELECT AVG(last_keyframe_sec) FROM dbo.v_video_stats)
            AS DECIMAL(10,1)) AS above_average_sec
FROM dbo.v_video_stats
WHERE last_keyframe_sec > (SELECT AVG(last_keyframe_sec) FROM dbo.v_video_stats)
ORDER BY last_keyframe_sec DESC;
GO

-- Q7. Người có quyền chọn đoạn tư liệu và số đoạn họ đã chọn.
-- Kỹ thuật: truy vấn con dạng bảng với IN, truy vấn con tương quan.
PRINT N'Q7. Người có quyền chọn đoạn tư liệu';
SELECT u.username, u.full_name,
       (SELECT COUNT(*) FROM dbo.selections a WHERE a.user_id = u.user_id) AS selections
FROM dbo.users u
WHERE u.user_id IN (SELECT user_id FROM dbo.v_user_permissions WHERE permission_code = 'selection.write')
ORDER BY u.username;
GO

-- Q8. Dòng thời gian văn bản (chữ trên hình + lời nói) của L23_V001 từ giây 0 đến 30.
-- Kỹ thuật: UNION ALL, BETWEEN, ORDER BY trên kết quả hợp.
PRINT N'Q8. Chữ và lời nói của L23_V001 trong 30 giây đầu';
SELECT 'OCR' AS kind, k.pts_time AS at_sec, o.ocr_text AS text
FROM dbo.ocr_texts o
JOIN dbo.keyframes k ON k.keyframe_id = o.keyframe_id
WHERE k.video_id = 'L23_V001' AND k.pts_time BETWEEN 0 AND 30
UNION ALL
SELECT 'ASR', s.start_sec, s.asr_text
FROM dbo.asr_segments s
WHERE s.video_id = 'L23_V001' AND s.start_sec BETWEEN 0 AND 30
ORDER BY at_sec;
GO

-- Q9. Từ khoá xuất hiện ở cả chữ trên hình lẫn lời nói (gợi ý từ khoá mạnh).
-- Kỹ thuật: INTERSECT, truy vấn con tương quan trong bảng dẫn xuất, TOP.
PRINT N'Q9. Từ khoá có ở cả OCR và ASR';
SELECT TOP (15) term, ocr_frames, asr_segments
FROM (
    SELECT t.term,
           (SELECT COUNT(*) FROM dbo.ocr_postings p WHERE p.term_id = t.term_id) AS ocr_frames,
           (SELECT COUNT(*) FROM dbo.asr_postings p WHERE p.term_id = t.term_id) AS asr_segments
    FROM dbo.terms t
    WHERE t.term_id IN (SELECT term_id FROM dbo.ocr_postings
                        INTERSECT
                        SELECT term_id FROM dbo.asr_postings)
) AS shared
ORDER BY ocr_frames + asr_segments DESC, term;
GO

-- Q10. Video có yêu cầu đã xác minh nhưng chưa có đoạn nào được chọn từ video đó.
-- Kỹ thuật: EXCEPT.
PRINT N'Q10. Video có yêu cầu đã xác minh nhưng chưa có đoạn được chọn';
SELECT video_id FROM dbo.ground_truth
EXCEPT
SELECT k.video_id FROM dbo.selections a JOIN dbo.keyframes k ON k.keyframe_id = a.keyframe_id
ORDER BY video_id;
GO

-- Q11. Khung hình có chữ "Hồ Chí Minh" trên màn hình (không phân biệt dấu).
-- Kỹ thuật: LIKE trên cột tính toán text_folded.
PRINT N'Q11. Khung hình có chữ "ho chi minh"';
SELECT TOP (10) k.video_id, k.pts_time, o.ocr_text
FROM dbo.ocr_texts o
JOIN dbo.keyframes k ON k.keyframe_id = o.keyframe_id
WHERE o.text_folded LIKE N'%ho chi minh%'
ORDER BY k.video_id, k.pts_time;
GO

-- Q12. 1% khung hình nhiều chữ nhất (bảng điểm, biển hiệu: dễ tìm bằng chữ).
-- Kỹ thuật: TOP (n) PERCENT, GROUP BY trên bảng chỉ mục ngược.
PRINT N'Q12. 1% khung hình nhiều từ khoá OCR nhất';
SELECT TOP (1) PERCENT p.keyframe_id, k.video_id, k.pts_time, COUNT(*) AS distinct_terms
FROM dbo.ocr_postings p
JOIN dbo.keyframes k ON k.keyframe_id = p.keyframe_id
GROUP BY p.keyframe_id, k.video_id, k.pts_time
ORDER BY distinct_terms DESC, p.keyframe_id;
GO

-- Q13. Xếp hạng video theo mật độ lời nói (số đoạn ASR mỗi phút).
-- Kỹ thuật: CTE, hàm cửa sổ RANK() OVER, NULLIF chống chia cho 0.
PRINT N'Q13. Xếp hạng video theo mật độ lời nói';
WITH density AS (
    SELECT video_id, asr_segments, last_keyframe_sec,
           CAST(60.0 * asr_segments / NULLIF(last_keyframe_sec, 0) AS DECIMAL(6,2)) AS segments_per_minute
    FROM dbo.v_video_stats
)
SELECT RANK() OVER (ORDER BY segments_per_minute DESC) AS rank_no, *
FROM density
ORDER BY rank_no;
GO

-- Q14. Gắn 10 đoạn lời nói đầu tiên của L23_V001 vào khung hình gần nhất.
-- Kỹ thuật: gọi hàm vô hướng fn_nearest_keyframe trong SELECT, JOIN kết quả hàm.
PRINT N'Q14. Lời nói gắn với khung hình gần nhất';
SELECT TOP (10) s.segment_id, s.start_sec, s.end_sec, k.keyframe_id, k.pts_time AS keyframe_sec,
       LEFT(s.asr_text, 60) AS speech
FROM dbo.asr_segments s
JOIN dbo.keyframes k
  ON k.keyframe_id = dbo.fn_nearest_keyframe(s.video_id, (s.start_sec + s.end_sec) / 2)
WHERE s.video_id = 'L23_V001'
ORDER BY s.start_sec;
GO

-- Q15. Từ khoá rút ra từ một yêu cầu khai thác và độ hiếm của chúng.
-- Kỹ thuật: hàm bảng fn_tokens với CROSS APPLY, LEFT JOIN, truy vấn con.
PRINT N'Q15. Từ khoá của yêu cầu YC-001';
SELECT k.term, df.ocr_frames, df.asr_segments
FROM dbo.requests q
CROSS APPLY dbo.fn_tokens(q.request_text) AS k
LEFT JOIN dbo.terms t ON t.term = k.term
CROSS APPLY (
    SELECT (SELECT COUNT(*) FROM dbo.ocr_postings p WHERE p.term_id = t.term_id) AS ocr_frames,
           (SELECT COUNT(*) FROM dbo.asr_postings p WHERE p.term_id = t.term_id) AS asr_segments
) AS df
WHERE q.request_id = 'YC-001'
ORDER BY df.ocr_frames + df.asr_segments, k.term;
GO

-- Q16. Tìm đoạn tư liệu cho yêu cầu YC-041 bằng thủ tục (chỉ OCR + ASR).
-- Kỹ thuật: EXEC thủ tục có tham số.
PRINT N'Q16. sp_search_footage cho yêu cầu YC-041';
DECLARE @text NVARCHAR(2000) = (SELECT request_text FROM dbo.requests WHERE request_id = 'YC-041');
EXEC dbo.sp_search_footage @query = @text, @top_k = 5;
SELECT video_id, start_sec FROM dbo.ground_truth WHERE request_id = 'YC-041';
GO

-- Q17. Tiến độ xử lý yêu cầu: tham số OUTPUT của sp_progress_report.
-- Kỹ thuật: EXEC với OUTPUT.
PRINT N'Q17. Tiến độ xử lý yêu cầu';
DECLARE @total INT, @selected INT, @percent DECIMAL(5,2);
EXEC dbo.sp_progress_report @total = @total OUTPUT, @selected = @selected OUTPUT,
                            @percent_done = @percent OUTPUT;
SELECT @total AS total_requests, @selected AS with_selection, @percent AS percent_done;
GO

-- Q18. Kết quả các lượt đánh giá (có sau khi chạy `footage eval`).
-- Kỹ thuật: đọc view tổng hợp nhiều tầng (v_eval_summary -> v_eval_first_hit).
PRINT N'Q18. Kết quả đánh giá';
SELECT run_id, run_name, sources, requests, recall_at_1, recall_at_5, recall_at_20, recall_at_k,
       mrr, avg_latency_ms, max_latency_ms
FROM dbo.v_eval_summary
ORDER BY run_id;
GO

-- ---------------------------------------------------------------------
-- Danh mục thư viện, yêu cầu khai thác và giấy phép
-- ---------------------------------------------------------------------

-- Q19. Cây chủ đề "Thể thao" và số video của mỗi nhánh (tính cả chủ đề con).
-- Kỹ thuật: CTE đệ quy (trong hàm fn_topic_subtree), CROSS APPLY hàm bảng
--           hai lần, COUNT(DISTINCT), REPLICATE để thụt lề.
PRINT N'Q19. Cây chủ đề Thể thao và số video theo nhánh';
SELECT CONCAT(REPLICATE(N'    ', tree.depth), tree.topic_name) AS topic,
       COUNT(DISTINCT vt.video_id) AS videos_in_branch
FROM dbo.topics root
CROSS APPLY dbo.fn_topic_subtree(root.topic_id) AS tree
CROSS APPLY dbo.fn_topic_subtree(tree.topic_id) AS below
LEFT JOIN dbo.video_topics vt ON vt.topic_id = below.topic_id
WHERE root.topic_name = N'Thể thao'
GROUP BY tree.topic_id, tree.topic_name, tree.depth, tree.path
ORDER BY tree.path;
GO

-- Q20. Đơn vị thành viên: số bộ sưu tập, số video đóng góp, số bạn đọc.
--      Đơn vị chưa nạp video hoặc chưa có bạn đọc vẫn được liệt kê.
-- Kỹ thuật: LEFT JOIN nhiều tầng, COUNT(DISTINCT), truy vấn con tương quan.
PRINT N'Q20. Đóng góp và bạn đọc theo đơn vị';
SELECT o.org_code, o.org_name, o.org_type,
       COUNT(DISTINCT c.collection_id) AS collections,
       COUNT(v.video_id) AS videos,
       (SELECT COUNT(*) FROM dbo.users u WHERE u.organization_id = o.organization_id) AS members
FROM dbo.organizations o
LEFT JOIN dbo.collections c ON c.organization_id = o.organization_id
LEFT JOIN dbo.videos v      ON v.collection_id = c.collection_id
GROUP BY o.organization_id, o.org_code, o.org_name, o.org_type
ORDER BY videos DESC, o.org_code;
GO

-- Q21. Yêu cầu khai thác theo đơn vị của người gửi và trạng thái.
-- Kỹ thuật: PIVOT (xoay trạng thái thành cột), COALESCE cho bạn đọc cá nhân.
PRINT N'Q21. Yêu cầu theo đơn vị và trạng thái';
SELECT organization, [pending], [approved], [rejected]
FROM (
    SELECT COALESCE(o.org_name, N'(cá nhân)') AS organization, r.status, r.request_id
    FROM dbo.requests r
    JOIN dbo.users u              ON u.user_id = r.requested_by
    LEFT JOIN dbo.organizations o ON o.organization_id = u.organization_id
) AS source_rows
PIVOT (COUNT(request_id) FOR status IN ([pending], [approved], [rejected])) AS by_status
ORDER BY organization;
GO

-- Q22. Tỉ lệ duyệt theo mục đích sử dụng, kèm dòng tổng.
-- Kỹ thuật: GROUP BY ROLLUP, GROUPING(), CAST tỉ lệ phần trăm.
PRINT N'Q22. Tỉ lệ duyệt theo mục đích sử dụng';
SELECT CASE WHEN GROUPING(purpose) = 1 THEN N'(tổng)' ELSE purpose END AS purpose,
       COUNT(*) AS requests,
       SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
       CAST(100.0 * SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) / COUNT(*)
            AS DECIMAL(5,1)) AS approved_percent
FROM dbo.requests
GROUP BY ROLLUP (purpose)
ORDER BY GROUPING(purpose), purpose;
GO

-- Q23. Giấy phép đã hết hạn hoặc sẽ hết hạn trong 30 ngày tới.
-- Kỹ thuật: view có cột tính theo ngày hiện tại, DATEDIFF, CASE, BETWEEN.
PRINT N'Q23. Giấy phép hết hạn hoặc sắp hết hạn';
SELECT license_id, request_id, licensee, valid_from, valid_to, days_left,
       CASE WHEN days_left < 0 THEN N'Đã hết hạn' ELSE N'Sắp hết hạn' END AS warning
FROM dbo.v_license_overview
WHERE days_left <= 30
ORDER BY days_left;
GO

-- Q24. Thời gian xử lý: từ lúc gửi yêu cầu đến lúc được cấp phép, theo bạn đọc.
-- Kỹ thuật: JOIN bốn bảng, DATEDIFF trên hai bảng, AVG/MIN/MAX.
PRINT N'Q24. Thời gian từ khi gửi đến khi được cấp phép';
SELECT u.username, u.user_type, COUNT(*) AS licensed_requests,
       AVG(DATEDIFF(HOUR, r.created_at, l.granted_at)) AS avg_hours,
       MAX(DATEDIFF(HOUR, r.created_at, l.granted_at)) AS max_hours
FROM dbo.usage_licenses l
JOIN dbo.requests r ON r.request_id = l.request_id
JOIN dbo.users u    ON u.user_id = r.requested_by
GROUP BY u.username, u.user_type
ORDER BY u.username;
GO

-- Q25. Video có chủ đề "Về đích" mà chưa có đoạn nào được khai thác.
-- Kỹ thuật: EXISTS và NOT EXISTS lồng nhau.
PRINT N'Q25. Video chủ đề "Về đích" chưa từng được khai thác';
SELECT v.video_id, v.title
FROM dbo.videos v
WHERE EXISTS (SELECT 1 FROM dbo.video_topics vt JOIN dbo.topics t ON t.topic_id = vt.topic_id
              WHERE vt.video_id = v.video_id AND t.topic_name = N'Về đích')
  AND NOT EXISTS (SELECT 1 FROM dbo.selections s JOIN dbo.keyframes k ON k.keyframe_id = s.keyframe_id
                  WHERE k.video_id = v.video_id)
ORDER BY v.video_id;
GO
