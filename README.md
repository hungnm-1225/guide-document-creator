# 🚀 Guide Document Creator (Automated User-Guide Content Factory)

> **Hệ thống tự động hóa khảo sát website và sản xuất tài liệu hướng dẫn người dùng đa định dạng (Văn bản DOCX, Slides 16:9, Video thuyết minh MP4 với giọng đọc AI) — Chi phí 0đ (100% Free Stack).**

Hỗ trợ chuẩn song ngữ: **Tiếng Việt 🇻🇳 & Tiếng Anh 🇬🇧**.  
Tích hợp giọng đọc AI tiếng Việt tự nhiên **VieNeu-TTS** (`pnnbao97/VieNeu-TTS`) và **Google Gemini Multimodal API** (`gemini-3.5-flash-lite` 500 RPD).

---

## 🌟 Điểm nổi bật & Triết lý thiết kế

1. **Kiến trúc 2 Lượt Độc Bản (Two-Pass Architecture - Không đoán mò, đi sâu từng ngóc ngách)**:
   - **Lượt 1 (Pioneer Scout - Thám hiểm & Lập bản đồ)**: AI đi tiên phong truy cập website, tự động vượt qua cổng đăng nhập OpenID SSO liên miền (như `learn.pythaverse.space` ➔ `eid.pythaverse.space`), tiến sâu vào trong Dashboard. Tự động quét toàn bộ cây tính năng sau đăng nhập (thanh menu điều hướng, các thẻ khóa học, tiến độ học tập, cài đặt) để tổng hợp thành **`SiteKnowledgeMap`** (Sơ đồ kiến thức ngóc ngách của hệ thống).
   - **Lượt 2 (Deep Guide Producer - Thực thi & Đi sâu chức năng)**: Bám sát Sơ đồ kiến thức và Ghi chú nghiệp vụ của người dùng để điều hướng chi tiết (click vào chuyên mục, đi sâu vào từng khóa học/tài liệu bài giảng), đảm bảo tài liệu và video có chiều sâu nghiệp vụ, không làm sơ sài, chung chung.

2. **Bảo mật Thông tin Tuyệt đối (Sanitization by Default)**:
   - **Không bao giờ đọc hoặc in Username / Password thật**: Khi hướng dẫn đăng nhập, hệ thống tự động làm sạch và diễn giải chuẩn mực: *"Nhập tên tài khoản hoặc email của bạn vào ô đăng nhập"*, *"Nhập mật khẩu bảo mật của bạn vào ô Mật khẩu"*. Tuyệt đối không đọc lộ tài khoản thật (`hsdttemd`, `password`) ra video hay in vào slide/Word.
   - **Loại bỏ toàn bộ câu chữ sáo rỗng**: Đã xóa bỏ các câu thừa thãi như *"Quan sát kỹ vị trí khung viền đỏ"* trong Word hay *"Mục tiêu: ..."* trong Slide.

3. **Ghi Chú & Tùy Chỉnh Nghiệp Vụ (User Directives / Notes)**:
   - Cho phép người dùng nhập các chỉ đạo, lưu ý nghiệp vụ riêng biệt (ví dụ: *"Sau khi đăng nhập xong, hãy bấm vào xem khóa học Leanbot ASP và hướng dẫn xem nội dung bài giảng. Thuyết minh rõ ràng từng bước"*).
   - AI sẽ tự động phân tích và lồng ghép trực tiếp các ghi chú này vào lời thoại thuyết minh video và nội dung tài liệu Word.

4. **Tích hợp Giọng Đọc AI Tiếng Việt VieNeu-TTS (`pnnbao97/VieNeu-TTS`)**:
   - Hỗ trợ mô hình TTS thần kinh tiếng Việt chất lượng cao chạy trên thiết bị (on-device) với âm thanh 48kHz chuẩn phòng thu.
   - Đa dạng giọng đọc 3 miền: Hà Nội - Nữ (`hn_nu`), Sài Gòn - Nam (`sg_nam`), Sài Gòn - Nữ (`sg_nu`), Hà Nội - Nam (`hn_nam`).
   - Tích hợp cơ chế dự phòng tự động (Fallback sang Microsoft Edge-TTS) đảm bảo quy trình luôn mượt mà 100% không bao giờ bị nghẽn.

5. **Tận dụng Hạn ngạch Model Gemini Mới Nhất**:
   - Hỗ trợ các model Gemini thế hệ mới với hạn ngạch miễn phí cực lớn:
     * **`gemini-3.5-flash-lite`** (Khuyên dùng: **500 RPD** & 15 RPM).
     * **`gemini-3.1-flash-lite`** (**500 RPD** & 15 RPM).
     * **`gemini-3.8-flash`** (20 RPD - Suy luận thông minh).
     * `gemini-3.7-flash`, `gemini-3.5-flash`, `gemini-2.5-flash`.

6. **Xử lý Hình ảnh Thị giác Chuyên nghiệp (Open-Design & OpenMontage Inspired)**:
   - **Spotlight Effect**: Làm mờ tối 35% vùng nền xung quanh, giữ nguyên độ sáng rực rỡ tại vị trí nút/ô nhập để mắt người xem tập trung ngay lập tức.
   - **Rounded Bounding Box**: Khung viền đỏ bo tròn hiện đại (`#EB2323`), độ tương phản cao trên mọi màu nền.
   - **Step Badge**: Huy hiệu tròn màu đỏ mang số bước (1, 2, 3...) nổi bật tại góc phần tử.
   - **Mouse Cursor Pointer**: Biểu tượng con trỏ chuột và sóng tương tác chân thực.

7. **Bộ 3 Định dạng Đầu ra Hoàn chỉnh (Artifact Factory)**:
   - 📄 **Văn bản Word (.docx) & Markdown (.md)**: Định dạng chuẩn tài liệu nghiệp vụ, bảng lộ trình các bước, tiêu đề nổi bật, ảnh minh họa bo viền nét.
   - 📊 **Trình chiếu Slides 16:9 (.pptx)**: Triết lý tối giản (*Maximize whitespace, minimize text*) phong cách Open-Design. Cột trái tóm tắt cô đọng, cột phải ảnh minh họa lớn.
   - 🎥 **Video Thuyết minh (.mp4)**: Dựng bằng MoviePy kết hợp lồng tiếng AI (VieNeu-TTS / Edge-TTS), có intro card, lower-third banner hiển thị số bước + tiêu đề và thanh tiến trình.

8. **Giao diện Web Studio Trực quan (Guide Creator Studio Dashboard)**:
   - Thiết kế chuẩn **Tailwind CSS + Font chữ Roboto** thanh thoát, không khoảng trắng chết (Widescreen Full-Width).
   - Nút con mắt xem/ẩn mật khẩu tiện lợi.
   - **Custom Cinema Video Player**: Tự chế thanh scrubber timeline tua video, bộ đếm thời gian, thanh volume slider, chọn tốc độ phát (`0.75x` đến `1.5x`), phóng toàn màn hình và tự động dừng video khi chuyển tab.
   - Lưu trữ API Key vĩnh viễn vào tệp **`.env`** trên đĩa — chỉ cần nhập đúng một lần duy nhất.

---

## 📂 Cấu trúc thư mục & Toàn bộ danh mục Files

```text
guide-document-creator/
├── config/
│   ├── __init__.py              # Export module config
│   └── settings.py              # Cấu hình hệ thống, biến môi trường, theme thị giác & hàm persist_env ghi file .env
├── src/
│   ├── browser/
│   │   ├── __init__.py          # Export crawler, executor, autonomous agent, recorder
│   │   ├── crawler.py           # Khảo sát địa hình giao diện thực tế (UI Reconnaissance)
│   │   ├── executor.py          # Tự động hóa thao tác trình duyệt theo kịch bản (Action Tracker)
│   │   ├── autonomous.py        # AI Tự động hóa 2 lượt (Pioneer Scout, vượt OpenID SSO, đi sâu ngóc ngách)
│   │   └── recorder.py          # Trợ lý ghi hình trực quan (Tango / Scribe style - click tự do trên trình duyệt thật)
│   ├── ai/
│   │   ├── __init__.py          # Export schemas và GeminiAnalyzer
│   │   ├── schemas.py           # Pydantic Schemas: BoundingBox, FeatureModule, SiteKnowledgeMap, ProcessedStep, GuideBook
│   │   └── gemini_client.py     # Multimodal AI (Gemini 3.5 Flash Lite, 3.8 Flash + Smart Offline Sanitized Fallback)
│   ├── visual/
│   │   ├── __init__.py          # Export VisualAnnotator
│   │   └── annotator.py         # OpenCV & Pillow: Spotlight, Bounding Box viền đỏ bo tròn, Huy hiệu số bước, Con trỏ chuột
│   ├── generators/
│   │   ├── __init__.py          # Export DocumentGenerator, SlideGenerator, VideoGenerator
│   │   ├── docx_gen.py          # Bộ sinh tài liệu Word DOCX chuẩn mực & Markdown
│   │   ├── pptx_gen.py          # Bộ sinh Slides PowerPoint 16:9 hiện đại (Open-Design style)
│   │   └── video_gen.py         # Bộ dựng Video MP4 lồng tiếng AI (Tích hợp VieNeu-TTS & Edge-TTS + MoviePy)
│   ├── pipeline/
│   │   ├── __init__.py          # Export GuidePipeline
│   │   └── orchestrator.py      # Bộ điều phối toàn bộ quy trình: Scout ➔ Deep Walkthrough ➔ AI ➔ Word, Slide, Video
│   └── web/
│       ├── __init__.py          # Export Web App
│       └── app.py               # Backend FastAPI + Giao diện Web Studio SPA (Tailwind + Lucide + Roboto + Custom Player)
├── examples/
│   ├── demo_app.html            # Trang web mẫu chạy thử offline
│   ├── demo_scenario.json       # Kịch bản mẫu đăng nhập quản trị
│   └── wikipedia_search.json    # Kịch bản mẫu tìm kiếm Wikipedia
├── output/                      # Thư mục lưu trữ thành phẩm sinh ra (được gitignore an toàn)
│   ├── recon/                   # Ảnh chụp & Sơ đồ kiến thức (site_knowledge_map.json)
│   ├── raw_screenshots/         # Ảnh chụp màn hình gốc
│   ├── annotated_images/        # Ảnh chụp màn hình đã phủ Spotlight và viền đỏ bo tròn
│   ├── audio/                   # File âm thanh giọng đọc AI từng bước (.mp3, .wav)
│   ├── documents/               # File Word (.docx) và Markdown (.md)
│   ├── slides/                  # File PowerPoint (.pptx) 16:9
│   └── videos/                  # Video hướng dẫn thuyết minh hoàn chỉnh (.mp4)
├── main.py                      # Điểm khởi chạy CLI và Web Studio chính của dự án
├── requirements.txt             # Danh sách toàn bộ thư viện dependencies Python
├── .env                         # Tệp lưu cấu hình và Gemini API Key cục bộ (được gitignore an toàn)
├── .env.example                 # Mẫu cấu hình môi trường ban đầu
├── .gitignore                   # Quy tắc bỏ qua file nhạy cảm và binary tạm
└── README.md                    # Tài liệu hướng dẫn sử dụng toàn diện
```

---

## 🛠️ Hướng dẫn cài đặt

### 1. Yêu cầu môi trường
- Python 3.10 trở lên (khuyên dùng Python 3.12).
- FFmpeg (đã có sẵn trên máy để nén video MP4 và chuyển đổi âm thanh).
- Trình duyệt Microsoft Edge hoặc Google Chrome trên máy.

### 2. Cài đặt thư viện
```bash
pip install -r requirements.txt
```

### 3. Cấu hình biến môi trường
Tệp `.env` sẽ tự động được tạo và cập nhật khi bạn nhập API Key trên giao diện Web Studio. Bạn cũng có thể tạo thủ công từ `.env.example`:
```env
# Google Gemini API Key (Lấy miễn phí tại https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite

# Công cụ giọng nói AI tiếng Việt (vieneu hoặc edge-tts)
TTS_ENGINE_VI=vieneu
VIENEU_VOICE=hn_nu
VIENEU_MODE=v3nano

# Giọng đọc thuyết minh Edge-TTS
TTS_VOICE_VI=vi-VN-HoaiMyNeural
TTS_VOICE_EN=en-US-AriaNeural

# Cấu hình trình duyệt
HEADLESS=true
VIEWPORT_WIDTH=1920
VIEWPORT_HEIGHT=1080
```

---

## 🚀 Hướng dẫn sử dụng

### Cách 1: Khởi chạy Giao diện Trực quan (Web Studio UI - Khuyên dùng)

Mở terminal tại thư mục dự án và chạy:
```bash
python main.py web
```
*(hoặc dùng lệnh tắt: `python main.py ui`)*

Trình duyệt sẽ **tự động mở** tại địa chỉ: **`http://127.0.0.1:8000`**

#### Các chức năng trên giao diện Web Studio:
1. **🌟 AI Tự Động Hóa 100% (Two-Pass Auto Pilot)**:
   - Nhập URL (ví dụ: `https://learn.pythaverse.space`).
   - Nhập Mục tiêu và Ghi chú nghiệp vụ bổ sung (ví dụ: *"Sau khi đăng nhập xong hãy vào xem mục Categories"*).
   - Nhập Tài khoản / Mật khẩu (có nút con mắt xem mật khẩu).
   - Chọn Mô hình Gemini (`gemini-3.5-flash-lite`, `gemini-3.8-flash`...).
   - Bấm **`BẮT ĐẦU TẠO HƯỚNG DẪN TỰ ĐỘNG`**: Hệ thống tự động vượt OpenID SSO, đi sâu vào từng chức năng và xuất trọn bộ tài liệu!
2. **🎥 Trợ Lý Ghi Hình Trực Tiếp (Smart Recorder - Tango Style)**:
   - Bấm **`MỞ TRÌNH DUYỆT & BẮT ĐẦU GHI HÌNH`**: Cửa sổ trình duyệt thật bật lên, bạn thao tác tự do (bấm, cuộn, gõ tùy thích).
   - Khi xong, bạn chỉ cần **Đóng cửa sổ trình duyệt**: Hệ thống tự động sinh Word, Slide 16:9 và Video MP4!
3. **📂 Thư Viện Thành Phẩm (Artifacts Showcase)**:
   - **Custom Video Player**: Xem video có giọng đọc AI tiếng Việt tự nhiên (VieNeu-TTS), chỉnh âm lượng, tốc độ, tua timeline, fullscreen.
   - **Tải về**: Tải nhanh file Word (`.docx`), Slide (`.pptx`), Markdown (`.md`) và Video (`.mp4`).
   - **Gallery ảnh**: Xem ảnh minh họa từng bước đã được phủ hiệu ứng Spotlight làm mờ nền và viền đỏ bo tròn.
4. **🛠️ Chế Độ Nâng Cao (Developer Studio)**:
   - Tra cứu cây DOM, kiểm tra tọa độ Bounding Box pixel.
5. **⚙️ Cài Đặt (Settings)**:
   - Nhập API Key một lần duy nhất (lưu vĩnh viễn vào `.env`).
   - Đổi công cụ giọng đọc giữa VieNeu-TTS và Edge-TTS.

---

### Cách 2: Sử dụng qua dòng lệnh CLI

Hệ thống hỗ trợ đầy đủ các lệnh CLI chuyên dụng:

```bash
# 1. Chạy kịch bản Demo hoàn chỉnh offline (không cần mạng):
python main.py demo --lang both --video

# 2. Chạy kịch bản từ file JSON có sẵn:
python main.py run-scenario --scenario examples/wikipedia_search.json --lang both --video

# 3. Tự động tạo hướng dẫn theo mục tiêu tự nhiên:
python main.py auto-guide \
  --url "https://learn.pythaverse.space" \
  --goal "Đăng nhập hệ thống Pythaverse và xem các khóa học" \
  --user "demo_student@pythaverse.space" \
  --password "MatKhauBaoMat@123" \
  --lang vi \
  --video

# 4. Chỉ khảo sát địa hình giao diện:
python main.py survey --url "https://vi.wikipedia.org"
```

---

## 🔗 Liên kết Repository

- **GitHub Repository**: [https://github.com/hungnm-1225/guide-document-creator.git](https://github.com/hungnm-1225/guide-document-creator.git)
- **VieNeu-TTS Project**: [https://github.com/pnnbao97/VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS)

---

## 📜 Giấy phép
Dự án được phát hành theo giấy phép mã nguồn mở **MIT License**. 100% Miễn phí và sẵn sàng cho các dự án tự động hóa tài liệu doanh nghiệp!
