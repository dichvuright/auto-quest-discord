# 🎮 Discord Quest Auto-Completer Bot

Bot Discord tự động quét và hoàn thành Discord Quests.

## ✨ Tính năng

- **`/quest`** — Slash command nhận token, tự động quét và hoàn thành quest
- **Điều khoản sử dụng** — Embed tương tác với nút Đồng ý/Từ chối (ephemeral)
- **Hỗ trợ nhiều loại quest** — `WATCH_VIDEO`, `PLAY_ON_DESKTOP`, `STREAM_ON_DESKTOP`, `PLAY_ACTIVITY`
- **Progress bar real-time** — `██████░░░░ 60%` cập nhật liên tục qua DM
- **Báo cáo tổng kết** — Thống kê chi tiết gửi qua DM, tóm tắt gửi ở channel
- **Proxy xoay** — Tích hợp [proxy.vn](https://proxy.vn), tự xoay IP mỗi 60s
- **Session persistence** — Lưu tiến trình vào `sessions.json`, tự resume khi bot restart
- **Giới hạn concurrent** — Max 4 user xử lý đồng thời
- **Bảo mật** — Token xóa ngay sau khi hoàn thành, kiểm tra User ID khớp

## 📦 Cài đặt

```bash
# Clone repo
git clone https://github.com/dichvuright/auto-quest-discord.git
cd auto-quest-discord

# Cài dependencies
pip install -r requirements.txt

# Tạo file .env từ template
cp .env.example .env
```

## ⚙️ Cấu hình

Chỉnh sửa file `.env`:

```env
# Bot token từ Discord Developer Portal
DISCORD_BOT_TOKEN=your_bot_token_here

# Channel IDs được phép dùng /quest (phân cách bằng dấu phẩy, để trống = tất cả)
ALLOWED_CHANNEL_IDS=123456789,987654321

# Proxy API key (để trống = không dùng proxy)
PROXY_API_KEY=your_proxy_key_here
```

## 🚀 Chạy bot

```bash
python bot.py
```

```
╔══════════════════════════════════════════════╗
║     Discord Quest Auto-Completer Bot        ║
║  Auto quét · Auto nhận · Auto hoàn thành    ║
╚══════════════════════════════════════════════╝

[OK] Build number: 507841
[PROXY] New proxy: 160.250.166.36:10059 | vnpt | HaNoi1 | TTL: 1377s
[OK] Slash commands synced
[OK] Bot online: Auto Quest#4446
```

## 📖 Sử dụng

1. Gõ `/quest token:<discord_user_token>` trong channel được phép
2. Nhấn **Đồng ý điều khoản**
3. Bot tự động:
   - Quét danh sách quest → DM
   - Auto-enroll quest mới
   - Hoàn thành từng quest với progress bar → DM
   - Gửi báo cáo tổng kết → DM + tóm tắt ở channel

## 🔒 Bảo mật

- Token chỉ hiện ephemeral (chỉ bạn thấy)
- Kiểm tra token khớp User ID trước khi xử lý
- Token xóa khỏi bộ nhớ ngay sau khi hoàn thành
- `sessions.json` tự xóa khi không còn session nào
- File `.env` và `sessions.json` không push lên Git

## 📁 Cấu trúc

```
├── bot.py              # Bot chính
├── requirements.txt    # Dependencies
├── .env.example        # Template cấu hình
├── .env                # Cấu hình (không push)
├── sessions.json       # Session tạm (tự tạo/xóa)
└── .gitignore
```
