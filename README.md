# 📚 Book Scanner

A mobile-friendly web app to scan book barcodes with your phone camera and build a personal library catalogue. Point the camera at any ISBN barcode — the app looks up the title, author, publisher and cover art automatically.

Book data can be stored in two backends:

| Backend | File | Best for |
|---|---|---|
| SQLite (local database) | `app.py` | Quick local use, no external accounts needed |
| Google Sheets | `app_sheets.py` | Shared access, data visible in a spreadsheet |

---

## Requirements

- Python 3.10+
- A device with a camera and a browser on the same local network (your phone works great)

---

## Installation

```bash
# Clone the repo
git clone <repo-url>
cd book-organizer

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---


## Environment variables

| Variable | Used by | Description |
|---|---|---|
| `PORT` | both | Default port (overridden by `--port`) |
| `GOOGLE_BOOKS_API_KEY` | both | Optional Google Books API key for higher lookup rate limits |

---

## Running with SQLite

No external accounts or configuration needed. A local `books.db` file is created automatically on first run.

```bash
python app.py
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--port PORT` | `5000` | Port to listen on |
| `--no-ssl` | off | Run over HTTP instead of HTTPS |

**Example:**

```bash
# HTTPS (default) — required for camera access on most mobile browsers
python app.py --port 5000

# HTTP — useful for quick local testing on desktop
python app.py --no-ssl
```

> **SSL note:** HTTPS is required for camera access on iOS Safari and most Android browsers. The app generates a self-signed certificate automatically if no `cert.pem` / `key.pem` are found. You'll see a browser security warning on first visit — accept it to proceed. For a trusted certificate, use [mkcert](https://github.com/FiloSottile/mkcert) and place `cert.pem` / `key.pem` in the project root.

Open `https://localhost:5000` (or `https://<your-local-ip>:5000` from your phone).

---

## Running with Google Sheets

Books are appended to a Google Spreadsheet as rows. You can view, filter, and export your library directly from Google Sheets.

### 1. Create a Google Cloud service account

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or select an existing one).
2. Navigate to **APIs & Services → Library** and enable the **Google Sheets API**.
3. Go to **APIs & Services → Credentials → Create Credentials → Service account**.
4. Give it any name, click through to finish.
5. Open the service account, go to the **Keys** tab → **Add Key → Create new key → JSON**.
6. Save the downloaded file as `credentials.json` in the project root (or anywhere — you'll pass the path via `--credentials`).

### 2. Share your spreadsheet with the service account

1. Create a new Google Spreadsheet (or use an existing one).
2. Open the service account JSON file and copy the `client_email` value (looks like `name@project.iam.gserviceaccount.com`).
3. Share the spreadsheet with that email address, giving it **Editor** access.
4. Make sure the first tab is named **Sheet1** (this is the default for new spreadsheets).

### 3. Run the app

```bash
python app_sheets.py \
  --spreadsheet "https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit" \
  --credentials credentials.json
```

The app writes the header row automatically on first run. After that, each scanned book appears as a new row:

| id | isbn | title | authors | publisher | year | cover_url | added_at |
|---|---|---|---|---|---|---|---|
| uuid | 9780... | Book Title | Author Name | Publisher | 2023 | https://... | 2026-05-23T... |

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--spreadsheet URL` | *(required)* | Full Google Sheets URL |
| `--credentials PATH` | `credentials.json` | Path to service account JSON |
| `--port PORT` | `5000` | Port to listen on |
| `--no-ssl` | off | Run over HTTP instead of HTTPS |

**Example (HTTP for local testing):**

```bash
python app_sheets.py \
  --spreadsheet "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit" \
  --credentials credentials.json \
  --no-ssl
```

---

## Using the app

1. Open the app URL in your phone's browser.
2. **Scan tab:** Point the camera at a book's barcode. The app detects the ISBN and looks up the book automatically.
3. If found, tap **Guardar** to add it to your library.
4. If not found, fill in the details manually and save.
5. **Biblioteca tab:** Browse your saved books. Tap the trash icon to remove a book.

### Book data sources

The app tries two sources in order:
1. [Open Library](https://openlibrary.org/) (free, no key needed)
2. [Google Books API](https://developers.google.com/books) (free tier, optional key via `GOOGLE_BOOKS_API_KEY` env var for higher rate limits)

---

## Project structure

```
book-organizer/
├── app.py              # SQLite backend
├── app_sheets.py       # Google Sheets backend
├── requirements.txt
├── templates/
│   └── index.html      # Single-page app (shared by both backends)
├── credentials.json    # Google service account key (not committed)
├── cert.pem            # Auto-generated SSL certificate (not committed)
└── key.pem             # Auto-generated SSL key (not committed)
```

---
