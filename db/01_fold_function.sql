-- =====================================================================
-- 01. Hàm bỏ dấu tiếng Việt
--
-- Phải tạo trước các bảng vì cột tính toán `text_folded` (PERSISTED) gọi
-- hàm này.  WITH SCHEMABINDING + chỉ dùng hàm tất định (LOWER, TRANSLATE)
-- nên SQL Server coi hàm là tất định và cho phép lưu kết quả vào bảng.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

CREATE OR ALTER FUNCTION dbo.fn_fold_vi (@text NVARCHAR(4000))
RETURNS NVARCHAR(4000)
WITH SCHEMABINDING
AS
BEGIN
    -- 67 nguyên âm có dấu và chữ đ, mỗi ký tự ánh xạ sang chữ không dấu
    -- ở cùng vị trí trong chuỗi thứ hai.
    RETURN TRANSLATE(LOWER(@text),
        N'àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ',
        N'aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd');
END;
GO
