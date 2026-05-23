from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
import sqlite3
import requests
import os
import socket
import datetime
import ipaddress
import argparse
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "books.db")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn       TEXT UNIQUE NOT NULL,
            title      TEXT,
            authors    TEXT,
            publisher  TEXT,
            year       TEXT,
            cover_url  TEXT,
            added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Book lookup helpers
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
# SSL helper
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

    conn = get_db()
    existing = conn.execute("SELECT id FROM books WHERE isbn = ?", (isbn,)).fetchone()
    conn.close()
    result["already_saved"] = existing is not None

    return result


@app.get("/api/books")
async def get_books():
    conn = get_db()
    rows = conn.execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


class BookIn(BaseModel):
    isbn: str
    title: Optional[str] = ""
    authors: Optional[str] = ""
    publisher: Optional[str] = ""
    year: Optional[str] = ""
    cover_url: Optional[str] = ""


@app.post("/api/books")
async def save_book(data: BookIn):
    conn = get_db()
    existing = conn.execute("SELECT id FROM books WHERE isbn = ?", (data.isbn,)).fetchone()
    if existing:
        conn.close()
        return {"success": False, "error": "duplicate", "message": "Este libro ya está en tu biblioteca"}
    try:
        conn.execute(
            "INSERT INTO books (isbn, title, authors, publisher, year, cover_url) VALUES (?,?,?,?,?,?)",
            (data.isbn, data.title, data.authors, data.publisher, data.year, data.cover_url),
        )
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/books/{book_id}")
async def delete_book(book_id: int):
    conn = get_db()
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()
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
    parser = argparse.ArgumentParser(description="Book Scanner")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)), help="Puerto (default: 5000)")
    parser.add_argument("--no-ssl", action="store_true", help="Run without SSL (HTTP only)")
    args = parser.parse_args()
    port = args.port

    init_db()
    local_ip = get_local_ip()

    if args.no_ssl:
        print(f"\n  Book Scanner  (HTTP, no SSL)")
        print(f"  Local:   http://localhost:{port}")
        print(f"  Network: http://{local_ip}:{port}")
        print()
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cert = os.path.join(base_dir, "cert.pem")
        key  = os.path.join(base_dir, "key.pem")

        if os.path.exists(cert) and os.path.exists(key):
            print("\n  Book Scanner  (certificado mkcert)")
        else:
            print("\n  Generando certificado auto-firmado (puede fallar en Safari)...")
            generate_self_signed_cert(cert, key, local_ip)

        print(f"  Local:   https://localhost:{port}")
        print(f"  Network: https://{local_ip}:{port}")
        print()

        uvicorn.run(app, host="0.0.0.0", port=port, ssl_certfile=cert, ssl_keyfile=key)
