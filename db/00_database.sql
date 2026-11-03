-- =====================================================================
-- 00. Tạo cơ sở dữ liệu `video_archive` - thư viện số tư liệu nghe nhìn
--     (SQL Server 2017 trở lên, bản Express được)
--
-- CẢNH BÁO: script tạo lại CSDL từ đầu, dữ liệu cũ của `video_archive` sẽ bị xoá.
-- Mức tương thích 140 (SQL Server 2017) để script chạy giống nhau trên
-- mọi phiên bản 2017/2019/2022/2025.
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

USE master;
GO

IF DB_ID(N'video_archive') IS NOT NULL
BEGIN
    ALTER DATABASE video_archive SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE video_archive;
END;
GO

CREATE DATABASE video_archive COLLATE Latin1_General_100_CI_AS;
GO

ALTER DATABASE video_archive SET COMPATIBILITY_LEVEL = 140;
GO

USE video_archive;
GO
