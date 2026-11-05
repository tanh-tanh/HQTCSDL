# Thư viện số tư liệu nghe nhìn — tra cứu, khai thác và cấp phép video trên SQL Server

Bài tập lớn môn **Hệ quản trị cơ sở dữ liệu**.

**Tình huống (giả định).** Nhiều đơn vị (đài truyền hình, toà báo, trường đại học, bảo tàng)
cùng gửi video đã số hoá vào một thư viện số dùng chung. Mỗi đơn vị đóng góp các **bộ sưu
tập** và giữ bản quyền; video được phân loại theo một **cây chủ đề**. **Bạn đọc** (nhà báo,
nhà nghiên cứu, sinh viên) tra cứu kho bằng mô tả tiếng Việt, ví dụ "tay đua áo vàng về đích,
phía sau là biển hiệu Hồng Thái Phúc Lộc", rồi gửi **yêu cầu khai thác** kèm mục đích sử dụng
(phát sóng, nghiên cứu, giảng dạy, trưng bày). **Cán bộ thư viện** tìm và chọn đúng đoạn tư
liệu, rồi **cấp giấy phép sử dụng** có thời hạn hoặc từ chối. Mọi thao tác được ghi nhật ký.
Giống mô hình mượn sách của thư viện, nhưng cho video.

Toàn bộ dữ liệu, tìm kiếm văn bản (chỉ mục ngược tự xây), gộp kết quả, quy trình yêu cầu và
cấp phép, tài khoản và phân quyền đều nằm trong SQL Server (2017 trở lên, bản Express được).
Chỉ riêng kênh hình ảnh chạy ngoài CSDL: model SigLIP2 chạy CPU biến câu mô tả thành vector
rồi tra chỉ mục FAISS, kết quả được đưa vào thủ tục `sp_search_footage` dưới dạng JSON.

```text
Trình duyệt ──cookie HttpOnly──> FastAPI (HTML render sẵn)
                                   │  EXECUTE AS USER = 'archive_app' (quyền tối thiểu)
                                   │  sp_set_session_context 'app_user_id'
                                   ├─ SigLIP2 (CPU) + FAISS ──> [keyframe_id...] (JSON)
                                   └─ SQL Server
                                        ├─ sp_search_footage: chỉ mục ngược (IDF) + RRF
                                        ├─ sp_submit_request → sp_add_selection → sp_grant_license / sp_reject_request
                                        └─ organizations · collections · topics · videos · requests
                                           · selections · usage_licenses · users/roles · audit_log
```

**Dữ liệu mẫu.** Video là lô L23 của một bộ video tiếng Việt công khai: 25 đoạn tường thuật một
giải đua xe đạp. Nhóm đã trích khung hình, OCR và ASR, rồi tự viết 120 yêu cầu khai thác mẫu và
xác minh thủ công vị trí đúng của từng yêu cầu (`labels/l23_requests.csv`) để đo chất lượng
tìm kiếm. Phần danh mục quanh đó là **dữ liệu minh hoạ**: các đơn vị thành viên khác, bộ sưu
tập chưa nạp video, bạn đọc, trạng thái yêu cầu và giấy phép đều có tên chứa chữ "mẫu" và
không mô tả tổ chức có thật nào. Chủ đề của video được gắn bằng luật trên lời nói thật (một
video thuộc "Nước rút" nếu lời bình có cụm "nước rút"), nên truy được nguồn gốc mọi nhãn.

## 1. Chạy script SQL (cho người chấm, không cần Python)

Yêu cầu: SQL Server 2017+ (Express được) và SSMS hoặc sqlcmd.

- **SSMS:** mở `db/full_setup.sql` → Execute. Script tạo lại CSDL `video_archive` từ đầu
  (xoá bản cũ nếu có), tạo bảng, hàm, view, thủ tục, trigger, phân quyền, chèn dữ liệu mẫu
  rồi lập chỉ mục văn bản. Mất khoảng 5 giây.
- Sau đó chạy `db/10_queries.sql` (25 truy vấn nghiệp vụ) và `db/11_tests.sql` (kiểm thử
  ràng buộc, giao dịch, phân quyền, chỉ mục, nhất quán). Cả hai chỉ đọc hoặc rollback.

Bằng sqlcmd (ghi kết quả ra file để tiếng Việt không bị console đổi mã):

```bash
sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -b -i db\full_setup.sql
```

```bash
sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -d video_archive -i db\10_queries.sql -o queries.txt
```

```bash
sqlcmd -S .\SQLEXPRESS -E -C -f 65001 -I -d video_archive -i db\11_tests.sql -o tests.txt
```

Kết quả chạy trên máy phát triển nằm trong `docs/output/`. Ngày tạo yêu cầu và giấy phép mẫu
tính lùi từ lúc chạy script, nên cột "còn bao nhiêu ngày" thay đổi theo ngày chạy.

`full_setup.sql` được ghép tự động từ các file thành phần:

| File | Nội dung |
| --- | --- |
| `00_database.sql` | Tạo CSDL `video_archive`, collation `Latin1_General_100_CI_AS`, mức tương thích 140 |
| `01_fold_function.sql` | `fn_fold_vi`: bỏ dấu tiếng Việt (tất định, dùng cho cột tính toán) |
| `02_tables.sql` | 30 bảng và 1 SEQUENCE, mọi ràng buộc đặt tên: PK_, FK_, UQ_, CK_, DF_; chỉ mục IX_ |
| `03_functions.sql` | `fn_tokens` (hàm bảng), `fn_nearest_keyframe`, `fn_has_permission`, `fn_topic_subtree` (CTE đệ quy) |
| `04_views.sql` | 11 view, gồm `v_active_users WITH CHECK OPTION`, `v_license_overview`, `v_collection_stats` |
| `05_procedures.sql` | `sp_search_footage`, `sp_submit_request`, `sp_add_selection`, `sp_remove_selection`, `sp_grant_license`, `sp_reject_request`, `sp_create_user`, `sp_progress_report` (OUTPUT), `sp_rebuild_text_index` |
| `06_triggers.sql` | Ghi `audit_log` khi chọn/bỏ đoạn tư liệu, duyệt/từ chối yêu cầu, đổi vai trò, khoá tài khoản, đổi mật khẩu |
| `07_seed.sql` | Quyền, vai trò, cây chủ đề, nguồn tìm kiếm, từ dừng |
| `08_security.sql` | Role `archive_reader`, `archive_writer`; người dùng `archive_app`, `archive_report` |
| `09_sample_data.sql` | Dữ liệu mẫu L23 (sinh bởi `footage sample`) |

Dữ liệu mẫu: 5 đơn vị, 5 bộ sưu tập (1 có video), 25 video, 5.913 khung hình, 3.976 dòng
OCR, 1.238 đoạn lời nói, 120 yêu cầu khai thác đã xác minh (30 đã cấp phép, 6 bị từ chối,
còn lại chờ xử lý), 60 đoạn đã chọn, 30 giấy phép, 5 tài khoản demo.

Tài khoản demo (chỉ để demo):

| Tài khoản | Mật khẩu | Vai trò | Đơn vị |
| --- | --- | --- | --- |
| `admin` | `Admin@123` | Quản trị thư viện số | Thư viện (đơn vị vận hành) |
| `thuthu1` | `Thuthu@123` | Cán bộ thư viện (librarian) | Thư viện (đơn vị vận hành) |
| `bandoc1` | `Bandoc@123` | Bạn đọc (reader), nhà báo | Báo điện tử mẫu B |
| `nghiencuu1` | `Nghiencuu@123` | Bạn đọc (reader), nhà nghiên cứu | Trường đại học mẫu C |
| `sinhvien1` | `Sinhvien@123` | Bạn đọc (reader), sinh viên | Trường đại học mẫu C |

## 2. Chạy ứng dụng web

Cần thêm: [uv](https://docs.astral.sh/uv/) (`pip install uv`) và ODBC Driver 18 for SQL Server.

```bash
copy .env.example .env
```

```bash
uv sync --extra visual
```

```bash
uv run --env-file .env footage db init --reset --yes
```

```bash
uv run --env-file .env footage serve
```

Mở <http://127.0.0.1:8000> và đăng nhập bằng tài khoản demo. `footage db init` chạy các file
`db/01..09` vào CSDL `ARCHIVE_DB_NAME` (tương đương `full_setup.sql`). Không có
`--extra visual` thì chỉ tìm được bằng chữ và lời nói, không cần torch.

Các trang: **Tra cứu** (mô tả → khung hình khớp nhất), trang chi tiết khung hình (phiếu mô tả
video: bộ sưu tập, đơn vị, chủ đề; chọn đoạn cho yêu cầu đang chờ), **Yêu cầu khai thác**
(bạn đọc gửi và theo dõi yêu cầu của mình; cán bộ thư viện xem mọi yêu cầu, cấp phép hoặc từ
chối), **Đánh giá** và **Tài khoản** (quản trị).

### Giao diện lưu trữ 3D (RING · CORRIDOR · CONSTELLATION)

Trang chủ `/` là giao diện 3D: kéo để xoay vòng kết quả (RING), cuộn để đi xuyên hành lang
(CORRIDOR), kéo để xoay đám mây (CONSTELLATION); nhấp một khung hình để mở trang chi tiết. Ba chế
độ dùng chung một truy vấn và cùng gọi `sp_search_footage`. Dạng danh sách cũ vẫn ở `/list`.
Khách vào được trang mở đầu và có thể tự tạo tài khoản bạn đọc ở `/register`
(`ARCHIVE_SELF_REGISTRATION=0` để tắt). CSDL đã tạo trước bản này cần chạy thêm
`db/migrations/001_self_registration.sql`. Bundle giao diện đã build sẵn trong
`src/footage/static/orbit/`; chỉ cần Node khi sửa `frontend/` (`npm ci && npm run build`).
Chi tiết: `docs/ORBIT_IMPLEMENTATION.md`.

### Tải dữ liệu (càng ít càng tốt)

Dữ liệu mẫu đã có sẵn trong `db/09_sample_data.sql`. Ảnh và kênh hình ảnh cần tải thêm từ
kho Hugging Face của nhóm (tên kho giữ nguyên như khi tạo):

| Thứ cần tải | Dung lượng | Để làm gì |
| --- | ---: | --- |
| `L23.zip` từ [oh-i-ace/aic-frames](https://huggingface.co/datasets/oh-i-ace/aic-frames), giải nén vào `data/archive/keyframes/` | 1,1 GB | Hiển thị ảnh |
| Model [google/siglip2-so400m-patch16-384](https://huggingface.co/google/siglip2-so400m-patch16-384) vào `models/siglip2-so400m-patch16-384/` | 4,5 GB | Kênh hình ảnh |
| `footage fetch-index --batches L23` | ~17 MB | Chỉ các vector của L23, thay vì cả file 928 MB |

Muốn sinh lại dữ liệu mẫu (hoặc cho lô khác) cần `frames.csv`, `asr.sqlite`, `ocr.sqlite` từ
[oh-i-ace/aic-indexes](https://huggingface.co/datasets/oh-i-ace/aic-indexes) (~135 MB):

```bash
uv run --env-file .env footage sample --batches L23
```

```bash
uv run --env-file .env footage db build-script
```

## 3. Phân quyền hai tầng

**Ứng dụng.** Mật khẩu băm scrypt. Cookie đăng nhập HttpOnly, SameSite=Lax; bảng `sessions`
chỉ lưu SHA-256 của cookie.

| Vai trò | search.run | request.submit | selection.write | license.grant | eval.view | user.manage |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| reader (bạn đọc) | ✓ | ✓ | | | | |
| librarian (cán bộ thư viện) | ✓ | ✓ | ✓ | ✓ | | |
| admin (quản trị) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Vai trò quyết định *được làm gì*; còn `users.user_type` (nhà báo, nhà nghiên cứu, sinh viên...)
và đơn vị công tác chỉ dùng để thống kê và ghi vào giấy phép.

**CSDL.** Máy chủ Express mặc định chỉ bật xác thực Windows, nên ứng dụng đăng nhập bằng
Windows rồi hạ quyền ngay bằng `EXECUTE AS USER = 'archive_app' WITH NO REVERT`.
`archive_app` (role `archive_reader` + `archive_writer`) không có DDL, không sửa được danh mục
hay dữ liệu video, và chỉ ghi yêu cầu, đoạn tư liệu, giấy phép qua thủ tục. `archive_report`
chỉ đọc. Người đang thao tác được đặt bằng `sp_set_session_context ... @read_only = 1`; các
thủ tục đọc giá trị này và tự kiểm tra quyền (`THROW 50001` nếu thiếu) cùng trạng thái yêu cầu
(`50004` không tồn tại, `50005` đã xử lý, `50006` chưa chọn đoạn nào).

Muốn có login SQL riêng cho người chỉ đọc (cần bật Mixed Mode trong SQL Server):

```sql
CREATE LOGIN archive_report_login WITH PASSWORD = N'<mật khẩu mạnh>';
USE video_archive;
CREATE USER archive_report_sql FOR LOGIN archive_report_login;
ALTER ROLE archive_reader ADD MEMBER archive_report_sql;
```

## 4. Đánh giá chất lượng tìm kiếm

```bash
uv run --env-file .env footage eval --name "SigLIP2 + OCR + ASR" --sources visual,ocr,asr
```

Mỗi lượt chạy 120 yêu cầu mẫu đã xác minh, lưu thứ hạng vào `eval_results`; view
`v_eval_summary` tính Recall@K, MRR và độ trễ. Một kết quả là đúng nếu cùng video và lệch
không quá 3 giây so với vị trí đã xác minh. Kết quả đo trên máy phát triển (CPU):

| Nguồn | R@1 | R@5 | R@20 | R@100 | MRR | Trung bình | Chậm nhất |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OCR + ASR | 0,042 | 0,158 | 0,258 | 0,308 | 0,098 | 6 ms | 77 ms |
| SigLIP2 | 0,075 | 0,125 | 0,217 | 0,358 | 0,104 | 215 ms | 495 ms |
| SigLIP2 + OCR + ASR | 0,025 | 0,158 | 0,367 | 0,525 | 0,097 | 222 ms | 513 ms |

Mọi lượt tìm đều xong dưới 1 giây, rất xa giới hạn 30 giây (`ARCHIVE_SEARCH_TIMEOUT`; quá hạn
thì ODBC huỷ câu lệnh trên máy chủ và trang báo lỗi).

## 5. Kiểm thử

```bash
uv run --env-file .env pytest
```

Test chạy trên CSDL tạm `video_archive_test` (tự tạo và xoá).
