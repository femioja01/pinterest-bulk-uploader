# 📌 Pinterest Bulk Pin Uploader & Scheduler

Automated daily and scheduled bulk pin uploading to Pinterest via Playwright browser automation with a full **Web Dashboard**, multi-account management, proxy routing, master CSV auto-splitting (max 100 pins per batch), and Telegram notifications.

---

## 🌟 Key Features

- **🖥️ Web Dashboard**: Clean modern UI to manage accounts, drop master CSVs, monitor uploads, edit schedules, and configure alerts.
- **👥 Multi-Account Support**: Manage multiple Pinterest accounts simultaneously with separate credentials, directories, and schedules.
- **🛡️ Per-Account Proxies**: Route each account through its own dedicated HTTP/HTTPS proxy.
- **✂️ Smart CSV Auto-Splitting**: Pinterest limits bulk uploads to 100 pins per CSV. The system automatically splits master CSVs into batches of your desired size (e.g., 50 or 100 pins), preserving all column headers across batches.
- **🔔 Telegram Notifications**: Real-time alerts for successful batch uploads, upload failures, session expiry warnings, and daily summaries.
- **⏰ Flexible Scheduling**: Change the upload schedule anytime via the dashboard using visual presets or cron expressions (no container restart required).
- **🚀 One-Click Upload Now**: Trigger immediate background uploads for any batch or account from the UI.
- **🔄 Auto-Retry**: Instant retry for failed batches directly from the Upload History table.
- **🐳 Coolify / Docker Ready**: Single lightweight container running FastAPI + Playwright Chromium + SQLite.

---

## 📋 CSV Format

The app expects Pinterest's standard bulk creation CSV layout:

| Column | Description |
|---|---|
| `Title` | Pin title (max 100 characters) |
| `Media URL` | Direct public link to image or video (`.jpg`, `.png`, `.mp4`) |
| `Pinterest Board` | Target board name where the pin will be published |
| `Thumbnail` | Thumbnail URL (optional, for video pins) |
| `Description` | Pin description (max 500 characters) |
| `Link` | Destination click-through URL |
| `Publish Date` | Scheduled publish date / time |
| `Keywords` | Keywords associated with the pin |

*Note: Pinterest automatically reads the target board from the `Pinterest Board` column — no manual board dropdown selection is needed.*

---

## 🚀 Quick Start (Local or Server)

### 1. Initial Setup
```bash
# Clone or navigate to the project directory
cd pinterest-bulk-uploader

# Copy environment variables template
cp .env.example .env

# (Optional) Install local requirements if running outside Docker
pip install -r requirements.txt
playwright install chromium
```

### 2. Capture Pinterest Login Session (Run Locally)
Because Pinterest uses bot detection and 2FA, authenticate once from your local computer:

```bash
# For a direct connection:
python login_helper.py --account my-account-name

# For an account with a proxy:
python login_helper.py --account my-account-name --proxy "http://username:password@proxy-ip:port"
```
1. A Chromium browser window will open and navigate to `https://www.pinterest.com/login/`.
2. Log into your Pinterest Business account manually.
3. Once logged in, the helper automatically captures cookies and localStorage tokens into `data/accounts/<account>/auth/state.json`.
4. You can also upload this `state.json` file anytime directly through the Web Dashboard under **Accounts → Upload Session**.

---

## 🐳 Deploying with Docker & Coolify

### Deploying via Docker Compose:
```bash
docker compose up -d --build
```
Access the web dashboard at `http://<your-server-ip>:8000`.

### Deploying on Coolify:
1. In your Coolify dashboard, select **Create New Resource → Docker Compose** (or point directly to your Git repository).
2. Set the port mapping to `8000:8000`.
3. Add a persistent volume mount:
   - `./data:/app/data` (to persist your SQLite database, session files, and CSV batches).
4. Set your environment variables in Coolify (`TZ=Africa/Lagos`, `SECRET_KEY=...`).
5. Click **Deploy**.

---

## 📱 Telegram Bot Setup

1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow the prompts to get your **Bot Token**.
3. Search for `@userinfobot` on Telegram and send `/start` to get your **Chat ID**.
4. In the Web Dashboard, go to **Settings**:
   - Enter your **Bot Token** and **Chat ID**.
   - Click **Send Test Message** to verify.
   - Toggle which notifications you want (Upload Success, Failure, Session Expiry, Daily Summary).
   - Click **Save All Settings**.

---

## 📁 Directory Structure

```
pinterest-bulk-uploader/
├── src/
│   ├── app.py               # FastAPI entry point & lifespan
│   ├── config.py            # Paths, settings & constants
│   ├── models/
│   │   └── database.py      # SQLite models (Account, Batch, Setting, ActivityLog)
│   ├── routes/
│   │   ├── dashboard.py     # Stats and quick actions
│   │   ├── accounts.py      # Account CRUD & session upload
│   │   ├── upload.py        # CSV preview, split, and upload routes
│   │   ├── history.py       # Batch history, retry & download
│   │   ├── schedule.py      # Cron schedule management
│   │   └── settings.py      # Telegram & general settings
│   ├── services/
│   │   ├── splitter.py      # CSV validation & batch splitting logic
│   │   ├── uploader.py      # Playwright browser automation
│   │   ├── scheduler.py     # APScheduler background runner
│   │   ├── notifier.py      # Telegram bot notifications
│   │   └── session.py       # Session health verification
│   ├── templates/           # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── accounts.html
│   │   ├── upload.html
│   │   ├── history.html
│   │   ├── schedule.html
│   │   └── settings.html
│   └── static/
│       ├── css/style.css    # Clean modern dashboard theme
│       └── js/app.js        # Drag-and-drop, preview & interactivity
├── data/
│   ├── app.db               # SQLite database
│   └── accounts/
│       └── <account-name>/
│           ├── auth/state.json   # Session cookies & storage
│           ├── pins/             # Raw master CSVs
│           ├── queue/            # Split batches waiting to upload
│           ├── done/             # Completed uploads + screenshots
│           └── failed/           # Failed batches + error screenshots
├── login_helper.py          # Local interactive login script
├── Dockerfile               # Playwright Chromium container
├── docker-compose.yml       # Production & local compose
├── requirements.txt         # Dependencies
└── README.md
```

---

## 🛠️ Usage Workflow

1. **Add Account**: Go to **Accounts** in the dashboard → click **+ Add Account** (enter name, proxy, default batch size).
2. **Upload Session**: Run `python login_helper.py --account <name>` locally and upload the resulting `state.json` via the UI.
3. **Upload CSV**:
   - Go to **Upload CSV**.
   - Select the account and drop your master CSV file.
   - Review the **Interactive Preview** (displays detected boards, pin count, and calculated batch splits).
   - Click **Upload Now** for immediate background upload or **Queue for Scheduled Run**.
4. **Monitor & Relax**:
   - Check **Dashboard** and **History** for live status.
   - Receive instant Telegram alerts on your phone whenever pins are published.
