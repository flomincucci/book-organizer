from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
import re
import sys
import uuid
import requests
import os
import socket
import datetime
import ipaddress
import argparse
import uvicorn
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))

# ---------------------------------------------------------------------------
# Google Sheets global state
# ---------------------------------------------------------------------------

SHEETS_SERVICE = None
SPREADSHEET_ID = None
SHEET_TAB_ID = None          # integer tab ID needed for batchUpdate (≠ spreadsheet ID string)
SHEET_NAME = "Sheet1"
HEADER = ["id", "isbn", "title", "authors", "publisher", "year", "cover_url", "added_at"]


# ---------------------------------------------------------------------------
# Sheets auth + init
# ---------------------------------------------------------------------------

def init_sheets_client(credentials_path: str):
    global SHEETS_SERVICE
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
        SHEETS_SERVICE = build("sheets", "v4", credentials=creds)
    except FileNotFoundError:
        print(f"ERROR: Credentials file not found: {credentials_path}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to authenticate with Google Sheets: {e}")
        sys.exit(1)


def init_sheet():
    """Ensure the header row exists and cache the sheet tab's integer ID."""
    global SHEET_TAB_ID

    # Fetch spreadsheet metadata to get the integer sheetId for the target tab
    try:
        meta = SHEETS_SERVICE.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    except HttpError as e:
        print(f"ERROR: Could not access spreadsheet (id={SPREADSHEET_ID}): {e}")
        sys.exit(1)

    tab_id = None
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == SHEET_NAME:
            tab_id = sheet["properties"]["sheetId"]
            break

    if tab_id is None:
        print(f"ERROR: Sheet tab '{SHEET_NAME}' not found in the spreadsheet. "
              f"Please rename the first tab to '{SHEET_NAME}' or update SHEET_NAME.")
        sys.exit(1)

    SHEET_TAB_ID = tab_id

    # Check / write header row
    try:
        result = SHEETS_SERVICE.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A1:H1"
        ).execute()
    except HttpError as e:
        print(f"ERROR: Could not read from sheet: {e}")
        sys.exit(1)

    values = result.get("values", [])
    if not values or "isbn" not in values[0]:
        SHEETS_SERVICE.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [HEADER]}
        ).execute()
        print("  Sheet header row written.")


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

def get_all_rows() -> list:
    """Return all raw rows (including header at index 0) from the sheet."""
    result = SHEETS_SERVICE.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:H"
    ).execute()
    return result.get("values", [])


def get_books() -> list:
    """Return all books as a list of dicts, sorted by added_at descending."""
    rows = get_all_rows()
    if len(rows) <= 1:
        return []
    books = []
    for row in rows[1:]:
        # Sheets API omits trailing empty cells — pad to full width
        padded = row + [""] * (len(HEADER) - len(row))
        books.append(dict(zip(HEADER, padded)))
    books.sort(key=lambda b: b.get("added_at", ""), reverse=True)
    return books


def find_by_isbn(isbn: str) -> dict | None:
    """Return the book dict for the given ISBN, or None if not found."""
    rows = get_all_rows()
    if len(rows) <= 1:
        return None
    isbn_col = HEADER.index("isbn")
    for row in rows[1:]:
        if len(row) > isbn_col and row[isbn_col] == isbn:
            padded = row + [""] * (len(HEADER) - len(row))
            return dict(zip(HEADER, padded))
    return None


def append_book(data: dict) -> dict:
    """Append a new book row to the sheet. Returns the saved book dict."""
    book_id = str(uuid.uuid4())
    added_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    row = [
        book_id,
        data.get("isbn", ""),
        data.get("title", ""),
        data.get("authors", ""),
        data.get("publisher", ""),
        data.get("year", ""),
        data.get("cover_url", ""),
        added_at,
    ]
    SHEETS_SERVICE.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]}
    ).execute()
    return dict(zip(HEADER, row))


def delete_book(book_id: str) -> bool:
    """Delete the row whose 'id' column matches book_id. Returns False if not found."""
    rows = get_all_rows()
    id_col = HEADER.index("id")
    row_index = None
    for i, row in enumerate(rows):
        if i == 0:
            continue  # skip header
        if len(row) > id_col and row[id_col] == book_id:
            row_index = i  # 0-based sheet row index
            break
    if row_index is None:
        return False

    body = {
        "requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": SHEET_TAB_ID,
                    "dimension": "ROWS",
                    "startIndex": row_index,
                    "endIndex": row_index + 1,
                }
            }
        }]
    }
    SHEETS_SERVICE.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body=body
    ).execute()
    return True


# ---------------------------------------------------------------------------
# Book lookup helpers (unchanged from app.py)
# ---------------------------------------------------------------------------

def lookup_open_library(isbn):
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    resp = requests.get(url, timeout=6)
    data = resp.json()
    key = f"ISBN:{isbn}"
    if key not in data:
        return None
    book = data[key]
    authors = ", ".join(a["name"] for a in book.get("authors", []))
    publishers = book.get("publishers", [])
    publisher = publishers[0].get("name", "") if publishers else ""
    cover = book.get("cover", {}).get("medium", "")
    return {
        "found": True,
        "isbn": isbn,
        "title": book.get("title", ""),
        "authors": authors,
        "publisher": publisher,
        "year": book.get("publish_date", ""),
        "cover_url": cover,
    }


def lookup_google_books(isbn):
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    if api_key:
        url += f"&key={api_key}"
    resp = requests.get(url, timeout=6)
    data = resp.json()
    if not data.get("items"):
        return None
    info = data["items"][0]["volumeInfo"]
    cover = info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
    year = info.get("publishedDate", "")
    if len(year) > 4:
        year = year[:4]
    return {
        "found": True,
        "isbn": isbn,
        "title": info.get("title", ""),
        "authors": ", ".join(info.get("authors", [])),
        "publisher": info.get("publisher", ""),
        "year": year,
        "cover_url": cover,
    }


# ---------------------------------------------------------------------------
# SSL helper (unchanged from app.py)
# ---------------------------------------------------------------------------

def generate_self_signed_cert(cert_path, key_path, local_ip):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    san = [x509.DNSName("localhost")]
    try:
        san.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
    except Exception:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/lookup/{isbn}")
async def lookup_book(isbn: str):
    result = None
    try:
        result = lookup_open_library(isbn)
    except Exception:
        pass
    if not result:
        try:
            result = lookup_google_books(isbn)
        except Exception:
            pass

    if result is None:
        result = {"found": False, "isbn": isbn}

    result["already_saved"] = find_by_isbn(isbn) is not None
    return result


@app.get("/api/books")
async def get_books_route():
    return get_books()


class BookIn(BaseModel):
    isbn: str
    title: Optional[str] = ""
    authors: Optional[str] = ""
    publisher: Optional[str] = ""
    year: Optional[str] = ""
    cover_url: Optional[str] = ""


@app.post("/api/books")
async def save_book(data: BookIn):
    existing = find_by_isbn(data.isbn)
    if existing:
        return {"success": False, "error": "duplicate", "message": "Este libro ya está en tu biblioteca"}
    try:
        append_book(data.dict())
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/books/{book_id}")
async def delete_book_route(book_id: str):
    delete_book(book_id)
    return {"success": True}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Book Scanner (Google Sheets backend)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)), help="Puerto (default: 5000)")
    parser.add_argument("--no-ssl", action="store_true", help="Run without SSL (HTTP only)")
    parser.add_argument("--spreadsheet", required=True,
                        help="Google Sheets URL (e.g. https://docs.google.com/spreadsheets/d/SHEET_ID/edit)")
    parser.add_argument("--credentials", default="credentials.json",
                        help="Path to Google service account JSON credentials (default: credentials.json)")
    args = parser.parse_args()
    port = args.port

    # Extract spreadsheet ID from URL
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', args.spreadsheet)
    if not m:
        print("ERROR: Could not extract a spreadsheet ID from the provided URL.")
        print("  Expected format: https://docs.google.com/spreadsheets/d/<ID>/edit")
        sys.exit(1)
    SPREADSHEET_ID = m.group(1)

    # Authenticate and initialise the sheet
    init_sheets_client(args.credentials)
    init_sheet()

    local_ip = get_local_ip()

    if args.no_ssl:
        print(f"\n  Book Scanner  (Google Sheets · HTTP, no SSL)")
        print(f"  Spreadsheet ID: {SPREADSHEET_ID}")
        print(f"  Local:   http://localhost:{port}")
        print(f"  Network: http://{local_ip}:{port}")
        print()
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cert = os.path.join(base_dir, "cert.pem")
        key  = os.path.join(base_dir, "key.pem")

        if os.path.exists(cert) and os.path.exists(key):
            print("\n  Book Scanner  (Google Sheets · certificado mkcert)")
        else:
            print("\n  Generando certificado auto-firmado (puede fallar en Safari)...")
            generate_self_signed_cert(cert, key, local_ip)

        print(f"  Spreadsheet ID: {SPREADSHEET_ID}")
        print(f"  Local:   https://localhost:{port}")
        print(f"  Network: https://{local_ip}:{port}")
        print()

        uvicorn.run(app, host="0.0.0.0", port=port, ssl_certfile=cert, ssl_keyfile=key)
