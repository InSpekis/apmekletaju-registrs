#!/usr/bin/env python3
"""Apmeklētāju reģistrs: servera SQLite datubāze un planšetēm piemērota tīmekļa lietotne."""

from __future__ import annotations

import csv
import base64
import binascii
import calendar
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import ssl
import sys
import time
import zipfile
from datetime import UTC, datetime, time as clock_time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("VISITOR_REGISTRY_DB", ROOT / "data" / "visitor_registry.sqlite3"))
HOST = os.environ.get("VISITOR_REGISTRY_HOST", "0.0.0.0")
PORT = int(os.environ.get("VISITOR_REGISTRY_PORT", "8080"))
ADMIN_PASSWORD = os.environ.get("VISITOR_REGISTRY_ADMIN_PASSWORD", "")
ADMIN_SECRET_CONFIGURED = bool(os.environ.get("VISITOR_REGISTRY_SESSION_SECRET", ""))
ADMIN_SECRET = os.environ.get("VISITOR_REGISTRY_SESSION_SECRET", "") or secrets.token_urlsafe(48)
VISITED_COMPANY_DEFAULT = os.environ.get("VISITOR_REGISTRY_COMPANY", "Jūsu uzņēmums")
TIMEZONE = ZoneInfo(os.environ.get("VISITOR_REGISTRY_TIMEZONE", "Europe/Riga"))
SECURE_COOKIES = os.environ.get("VISITOR_REGISTRY_SECURE_COOKIES", "false").lower() == "true"
TLS_CERTIFICATE_PATH = os.environ.get("VISITOR_REGISTRY_TLS_CERTIFICATE", "")
TLS_PRIVATE_KEY_PATH = os.environ.get("VISITOR_REGISTRY_TLS_PRIVATE_KEY", "")


def positive_environment_integer(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default

MAX_BODY_BYTES = 2_300_000
MAX_PHOTO_BYTES = 1_500_000
SESSION_TTL_SECONDS = 8 * 60 * 60
RETENTION_SWEEP_SECONDS = positive_environment_integer("VISITOR_REGISTRY_RETENTION_SWEEP_SECONDS", 3600, minimum=60)
DEFAULT_SHORT_PRIVACY_NOTICE = (
    "Reģistrācijas laikā tiek apstrādāti apmeklējuma dati un uzņemts foto. "
    "Datus izmanto tikai apmeklētāju reģistram un drošības vajadzībām, nevis mārketingam vai datu tirdzniecībai."
)
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
PERSONAL_CODE_LOOKUPS: dict[str, list[float]] = {}
RATE_LOCK = Lock()

OPTION_FIELDS = {
    "representedCompany",
    "documentType",
    "visitedCompany",
    "hostPerson",
    "visitPurpose",
}
DEFAULT_FORM_OPTIONS = {
    "documentType": ["Pase", "Personas apliecība (eID)", "Vadītāja apliecība", "Cits dokuments"],
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_time(iso_time: str) -> str:
    return datetime.fromisoformat(iso_time.replace("Z", "+00:00")).astimezone(TIMEZONE).strftime("%d.%m.%Y. %H:%M")


def photo_export_filename(visit: sqlite3.Row, media_type: str) -> str:
    """Veido drošu foto nosaukumu ZIP atskaitei; CSV saglabā pilno sasaisti."""
    extension = "webp" if media_type == "image/webp" else "jpg"
    timestamp = datetime.fromisoformat(visit["created_at"].replace("Z", "+00:00")).astimezone(TIMEZONE).strftime("%Y%m%d-%H%M%S")
    code_tail = "".join(character for character in visit["personal_code"] if character.isalnum())[-4:] or "XXXX"
    return f"foto_{timestamp}_{code_tail}_{visit['id']}.{extension}"


def csv_report_body(rows: list[sqlite3.Row], photo_files: dict[int, str] | None = None) -> bytes:
    """Izveido UTF-8 CSV failu; ZIP variantā pievieno foto faila sasaisti."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "Datums un laiks", "Vārds", "Uzvārds", "Personas kods", "Pārstāvētais uzņēmums / iestāde",
        "Dokumenta veids", "Dokumenta numurs", "Apmeklējamais uzņēmums", "Persona, pie kuras ieradies", "Vizītes mērķis",
        "Foto fails" if photo_files is not None else "Foto pieejams",
    ])
    for row in rows:
        photo_reference = photo_files.get(row["id"], "") if photo_files is not None else ("Jā" if row["has_photo"] else "Nē")
        writer.writerow([
            local_time(row["created_at"]), row["first_name"], row["last_name"], row["personal_code"],
            row["represented_company"], row["document_type"], row["document_number"], row["visited_company"],
            row["host_person"], row["visit_purpose"], photo_reference,
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def db_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


CURRENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS visitor_profiles (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    personal_code TEXT NOT NULL,
    personal_code_key TEXT NOT NULL,
    represented_company TEXT NOT NULL,
    document_type TEXT NOT NULL,
    document_number TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES visitor_profiles(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    personal_code TEXT NOT NULL,
    represented_company TEXT NOT NULL,
    document_type TEXT NOT NULL,
    document_number TEXT NOT NULL,
    visited_company TEXT NOT NULL,
    host_person TEXT NOT NULL,
    visit_purpose TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS form_options (
    id INTEGER PRIMARY KEY,
    field_key TEXT NOT NULL,
    option_value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(field_key, option_value)
);

CREATE TABLE IF NOT EXISTS visit_photos (
    id INTEGER PRIMARY KEY,
    visit_id INTEGER NOT NULL UNIQUE REFERENCES visits(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL,
    image_data BLOB NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS privacy_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    controller_name TEXT NOT NULL,
    controller_registration_number TEXT NOT NULL,
    controller_contact TEXT NOT NULL,
    dpo_contact TEXT NOT NULL,
    legal_basis TEXT NOT NULL,
    legal_basis_details TEXT NOT NULL,
    processing_purpose TEXT NOT NULL,
    short_notice TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL,
    retention_period TEXT NOT NULL,
    retention_months INTEGER NOT NULL DEFAULT 6,
    storage_description TEXT NOT NULL,
    next_registration_seconds INTEGER NOT NULL DEFAULT 5,
    privacy_reading_seconds INTEGER NOT NULL DEFAULT 60,
    updated_at TEXT NOT NULL
);
"""


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS visitor_profiles_personal_code_key_idx
            ON visitor_profiles(personal_code_key);
        CREATE INDEX IF NOT EXISTS visits_created_at_idx ON visits(created_at);
        CREATE INDEX IF NOT EXISTS visits_personal_code_idx ON visits(personal_code);
        CREATE INDEX IF NOT EXISTS form_options_field_key_idx ON form_options(field_key);
        CREATE INDEX IF NOT EXISTS visit_photos_visit_id_idx ON visit_photos(visit_id);
        """
    )


def seed_form_options(conn: sqlite3.Connection) -> None:
    for field_key, values in DEFAULT_FORM_OPTIONS.items():
        for value in values:
            conn.execute(
                "INSERT OR IGNORE INTO form_options (field_key, option_value, created_at) VALUES (?, ?, ?)",
                (field_key, value, utc_now()),
            )


def grouped_form_options(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    options: dict[str, list[dict[str, Any]]] = {field_key: [] for field_key in OPTION_FIELDS}
    for row in conn.execute("SELECT id, field_key, option_value FROM form_options ORDER BY field_key, option_value COLLATE NOCASE"):
        if row["field_key"] in options:
            options[row["field_key"]].append({"id": row["id"], "value": row["option_value"]})
    return options


def seed_privacy_settings(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO privacy_settings (
            id, controller_name, controller_registration_number, controller_contact, dpo_contact,
            legal_basis, legal_basis_details, processing_purpose, short_notice, recipients, retention_period,
            storage_description, updated_at
        ) VALUES (1, ?, '', '', '', '', '', '', ?, '', '6 mēneši', '', ?)""",
        (VISITED_COMPANY_DEFAULT, DEFAULT_SHORT_PRIVACY_NOTICE, utc_now()),
    )
    conn.execute(
        "UPDATE privacy_settings SET short_notice = ? WHERE id = 1 AND short_notice = ''",
        (DEFAULT_SHORT_PRIVACY_NOTICE,),
    )


def ensure_privacy_settings_columns(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "privacy_settings")
    if "retention_months" not in columns:
        conn.execute("ALTER TABLE privacy_settings ADD COLUMN retention_months INTEGER NOT NULL DEFAULT 6")
    if "next_registration_seconds" not in columns:
        conn.execute("ALTER TABLE privacy_settings ADD COLUMN next_registration_seconds INTEGER NOT NULL DEFAULT 5")
    if "privacy_reading_seconds" not in columns:
        conn.execute("ALTER TABLE privacy_settings ADD COLUMN privacy_reading_seconds INTEGER NOT NULL DEFAULT 60")
    if "short_notice" not in columns:
        conn.execute("ALTER TABLE privacy_settings ADD COLUMN short_notice TEXT NOT NULL DEFAULT ''")


def privacy_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM privacy_settings WHERE id = 1").fetchone()
    if not row:
        return {}
    return {
        "controllerName": row["controller_name"],
        "controllerRegistrationNumber": row["controller_registration_number"],
        "controllerContact": row["controller_contact"],
        "dpoContact": row["dpo_contact"],
        "legalBasis": row["legal_basis"],
        "legalBasisDetails": row["legal_basis_details"],
        "processingPurpose": row["processing_purpose"],
        "shortNotice": row["short_notice"],
        "recipients": row["recipients"],
        "retentionPeriod": row["retention_period"],
        "retentionMonths": row["retention_months"],
        "storageDescription": row["storage_description"],
        "nextRegistrationSeconds": row["next_registration_seconds"],
        "privacyReadingSeconds": row["privacy_reading_seconds"],
        "updatedAt": row["updated_at"],
    }


def migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    """Noņem vēsturiskos tālruņu un nejaušo kodu laukus, saglabājot vizīšu vēsturi."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        DROP INDEX IF EXISTS visitor_profiles_phone_number_idx;
        DROP INDEX IF EXISTS visitor_profiles_personal_code_key_idx;
        DROP INDEX IF EXISTS visits_registration_code_idx;
        DROP INDEX IF EXISTS visits_personal_code_idx;

        CREATE TABLE visitor_profiles_new (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            personal_code TEXT NOT NULL,
            personal_code_key TEXT NOT NULL,
            represented_company TEXT NOT NULL,
            document_type TEXT NOT NULL,
            document_number TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT INTO visitor_profiles_new (
            id, first_name, last_name, personal_code, personal_code_key, represented_company,
            document_type, document_number, created_at, updated_at
        )
        SELECT id, first_name, last_name, personal_code,
               UPPER(REPLACE(REPLACE(personal_code, ' ', ''), '-', '')),
               represented_company, document_type, document_number, created_at, updated_at
        FROM visitor_profiles;

        CREATE TABLE visits_new (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES visitor_profiles_new(id),
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            personal_code TEXT NOT NULL,
            represented_company TEXT NOT NULL,
            document_type TEXT NOT NULL,
            document_number TEXT NOT NULL,
            visited_company TEXT NOT NULL,
            host_person TEXT NOT NULL,
            visit_purpose TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        INSERT INTO visits_new (
            id, profile_id, first_name, last_name, personal_code, represented_company,
            document_type, document_number, visited_company, host_person, visit_purpose, created_at
        )
        SELECT id, profile_id, first_name, last_name, personal_code, represented_company,
               document_type, document_number, visited_company, host_person, visit_purpose, created_at
        FROM visits;

        UPDATE visits_new
        SET profile_id = (
            SELECT MIN(duplicate.id)
            FROM visitor_profiles_new AS duplicate
            WHERE duplicate.personal_code_key = (
                SELECT current.personal_code_key FROM visitor_profiles_new AS current WHERE current.id = visits_new.profile_id
            )
        );

        DELETE FROM visitor_profiles_new
        WHERE id NOT IN (
            SELECT MIN(id) FROM visitor_profiles_new GROUP BY personal_code_key
        );

        DROP TABLE visits;
        DROP TABLE visitor_profiles;
        ALTER TABLE visitor_profiles_new RENAME TO visitor_profiles;
        ALTER TABLE visits_new RENAME TO visits;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def initialise_database() -> None:
    conn = db_connection()
    try:
        profile_columns = table_columns(conn, "visitor_profiles")
        if not profile_columns:
            conn.executescript(CURRENT_SCHEMA)
        elif {"personal_code_key", "registration_code", "phone_number"} & profile_columns:
            migrate_legacy_schema(conn)
        conn.executescript(CURRENT_SCHEMA)
        create_indexes(conn)
        ensure_privacy_settings_columns(conn)
        seed_form_options(conn)
        seed_privacy_settings(conn)
        purge_expired_data(conn)
        conn.commit()
    finally:
        conn.close()


def clean_text(value: Any, field_name: str, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Lauks “{field_name}” ir obligāts.")
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError(f"Lauks “{field_name}” ir obligāts.")
    if len(cleaned) > maximum:
        raise ValueError(f"Lauks “{field_name}” ir pārāk garš.")
    return cleaned


def clean_optional_text(value: Any, field_name: str, maximum: int = 500) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Lauks “{field_name}” nav derīgs.")
    cleaned = " ".join(value.strip().split())
    if len(cleaned) > maximum:
        raise ValueError(f"Lauks “{field_name}” ir pārāk garš.")
    return cleaned


def validate_privacy_settings(payload: dict[str, Any]) -> dict[str, str]:
    required_fields = {
        "controllerName": ("Pārziņa nosaukums", 200),
        "controllerContact": ("Pārziņa kontaktinformācija", 300),
        "legalBasis": ("Tiesiskais pamats", 300),
        "legalBasisDetails": ("Tiesiskā pamata atsauce", 500),
        "processingPurpose": ("Apstrādes mērķis", 600),
        "shortNotice": ("Īsais paziņojums pirms reģistrācijas", 1200),
        "recipients": ("Datu saņēmēji", 600),
        "storageDescription": ("Glabāšanas informācija", 600),
    }
    optional_fields = {
        "controllerRegistrationNumber": ("Reģistrācijas numurs", 80),
        "dpoContact": ("Datu aizsardzības speciālista kontakts", 300),
    }
    values = {key: clean_text(payload.get(key), label, maximum) for key, (label, maximum) in required_fields.items()}
    values.update({key: clean_optional_text(payload.get(key), label, maximum) for key, (label, maximum) in optional_fields.items()})
    retention_months = validate_months(payload.get("retentionMonths"))
    values["retentionPeriod"] = f"{retention_months} mēneši"
    values["retentionMonths"] = str(retention_months)
    values["nextRegistrationSeconds"] = str(validate_seconds(payload.get("nextRegistrationSeconds"), "Laiks līdz nākamajai reģistrācijai"))
    values["privacyReadingSeconds"] = str(validate_seconds(payload.get("privacyReadingSeconds"), "Privātuma paziņojuma lasīšanas laiks"))
    return values


def validate_seconds(value: Any, field_name: str) -> int:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Laukā “{field_name}” ievadiet sekunžu skaitu.") from error
    if not 1 <= seconds <= 3600:
        raise ValueError(f"Laukā “{field_name}” norādiet vērtību no 1 līdz 3600 sekundēm.")
    return seconds


def validate_months(value: Any) -> int:
    try:
        months = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("Glabāšanas termiņu norādiet pilnos mēnešos.") from error
    if not 1 <= months <= 120:
        raise ValueError("Glabāšanas termiņš var būt no 1 līdz 120 mēnešiem.")
    return months


def retention_cutoff(months: int) -> str:
    now = datetime.now(UTC).replace(microsecond=0)
    month_index = now.month - months
    year = now.year
    while month_index <= 0:
        month_index += 12
        year -= 1
    day = min(now.day, calendar.monthrange(year, month_index)[1])
    return now.replace(year=year, month=month_index, day=day).isoformat().replace("+00:00", "Z")


def purge_expired_data(conn: sqlite3.Connection) -> None:
    settings = conn.execute("SELECT retention_months FROM privacy_settings WHERE id = 1").fetchone()
    months = settings["retention_months"] if settings else 6
    conn.execute("DELETE FROM visits WHERE created_at < ?", (retention_cutoff(months),))
    conn.execute("DELETE FROM visitor_profiles WHERE id NOT IN (SELECT DISTINCT profile_id FROM visits)")


def retention_maintenance_loop() -> None:
    """Dzēš termiņdatus arī tad, ja serverim nav ienākošu pieprasījumu."""
    while True:
        time.sleep(RETENTION_SWEEP_SECONDS)
        try:
            with db_connection() as conn:
                purge_expired_data(conn)
        except sqlite3.Error:
            # Žurnālā neiekļaujam personas datus; nākamajā ciklā mēģinām vēlreiz.
            print("BRĪDINĀJUMS: termiņdatu automātiskā dzēšana neizdevās.")


def start_retention_maintenance() -> None:
    Thread(target=retention_maintenance_loop, name="retention-maintenance", daemon=True).start()


def personal_code_key(value: Any) -> str:
    code = clean_text(value, "Personas kods", 32)
    key = "".join(character for character in code.upper() if character not in " -")
    if len(key) < 6:
        raise ValueError("Personas kods nav derīgs.")
    return key


def validate_visit(payload: dict[str, Any]) -> dict[str, str]:
    fields = {
        "firstName": ("Vārds", 80),
        "lastName": ("Uzvārds", 80),
        "personalCode": ("Personas kods", 32),
        "representedCompany": ("Uzņēmums / iestāde", 160),
        "documentType": ("Dokumenta veids", 80),
        "documentNumber": ("Dokumenta numurs", 80),
        "visitedCompany": ("Apmeklējamais uzņēmums", 160),
        "hostPerson": ("Persona, pie kuras ieradies", 120),
        "visitPurpose": ("Vizītes mērķis", 500),
    }
    values = {key: clean_text(payload.get(key), label, length) for key, (label, length) in fields.items()}
    values["personalCodeKey"] = personal_code_key(values["personalCode"])
    return values


def validate_photo(payload: dict[str, Any]) -> tuple[str, bytes]:
    """Pieņem tikai pārlūkā iegūtu JPEG/WebP foto nelielā, drošā izmērā."""
    photo = payload.get("photo")
    if not isinstance(photo, str) or "," not in photo:
        raise ValueError("Foto fiksācija ir obligāta. Lūdzu, atļaujiet kameras izmantošanu.")
    prefix, encoded = photo.split(",", 1)
    media_types = {
        "data:image/jpeg;base64": "image/jpeg",
        "data:image/webp;base64": "image/webp",
    }
    media_type = media_types.get(prefix.lower())
    if not media_type:
        raise ValueError("Foto formāts nav derīgs.")
    try:
        image_data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Foto datus neizdevās nolasīt.") from error
    if not image_data or len(image_data) > MAX_PHOTO_BYTES:
        raise ValueError("Foto fails nav derīgā apjomā.")
    is_jpeg = image_data.startswith(b"\xff\xd8\xff")
    is_webp = image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP"
    if (media_type == "image/jpeg" and not is_jpeg) or (media_type == "image/webp" and not is_webp):
        raise ValueError("Foto formāts neatbilst iesniegtajiem datiem.")
    return media_type, image_data


def find_profile_by_personal_code(conn: sqlite3.Connection, code_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM visitor_profiles WHERE personal_code_key = ?", (code_key,)
    ).fetchone()


def too_many_attempts(bucket: dict[str, list[float]], key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with RATE_LOCK:
        current = [moment for moment in bucket.get(key, []) if now - moment < window_seconds]
        current.append(now)
        bucket[key] = current
        return len(current) > limit


def sign_session(expires_at: int) -> str:
    message = str(expires_at).encode()
    signature = hmac.new(ADMIN_SECRET.encode(), message, hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def valid_session(cookie_value: str | None) -> bool:
    if not cookie_value or not ADMIN_SECRET:
        return False
    try:
        raw_expiry, raw_signature = cookie_value.rsplit(".", 1)
        expiry = int(raw_expiry)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(ADMIN_SECRET.encode(), raw_expiry.encode(), hashlib.sha256).hexdigest()
    return expiry >= int(time.time()) and hmac.compare_digest(raw_signature, expected)


class TLSRegistryServer(ThreadingHTTPServer):
    """TLS rokasspiedienu veic pieprasījuma pavedienā, nevis galvenajā accept ciklā."""

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], context: ssl.SSLContext):
        super().__init__(address, handler)
        self.tls_context = context

    def get_request(self) -> tuple[ssl.SSLSocket, tuple[str, int]]:
        raw_socket, client_address = self.socket.accept()
        return self.tls_context.wrap_socket(raw_socket, server_side=True, do_handshake_on_connect=False), client_address

    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        """Klusējot ignorē parastus HTTP mēģinājumus HTTPS pieslēgvietā."""
        _, exception, _ = sys.exc_info()
        if isinstance(exception, ssl.SSLError):
            return
        super().handle_error(request, client_address)


class RegistryHandler(BaseHTTPRequestHandler):
    server_version = "VisitorRegistry/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        # Pieprasījumu saturs var ietvert personas datus, tādēļ to žurnālā nerakstām.
        return

    @property
    def client_key(self) -> str:
        return self.client_address[0]

    def send_json(self, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file_path: Path) -> None:
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/manifest+json; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store" if file_path.name in {"index.html", "admin.html", "service-worker.js"} else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, status: HTTPStatus, body: bytes, media_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Nederīgs pieprasījums.") from error
        if not 0 < length <= MAX_BODY_BYTES:
            raise ValueError("Nederīgs pieprasījuma apjoms.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Nederīgs pieprasījums.") from error
        if not isinstance(payload, dict):
            raise ValueError("Nederīgs pieprasījums.")
        return payload

    def is_admin(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        admin_cookie = cookie.get("visitor_registry_admin")
        return valid_session(admin_cookie.value if admin_cookie else None)

    def require_admin(self) -> bool:
        if self.is_admin():
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Nepieciešama administrācijas piekļuve."})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/config":
            with db_connection() as conn:
                options = grouped_form_options(conn)
                privacy = privacy_settings(conn)
            self.send_json(HTTPStatus.OK, {"visitedCompany": VISITED_COMPANY_DEFAULT, "timezone": str(TIMEZONE), "options": options, "privacy": privacy})
            return
        if path.startswith("/api/visitors/by-personal-code/"):
            self.get_visitor_by_personal_code(unquote(path.removeprefix("/api/visitors/by-personal-code/")))
            return
        if path == "/api/admin/session":
            self.send_json(HTTPStatus.OK, {"authenticated": self.is_admin()})
            return
        if path == "/api/admin/visits":
            self.get_admin_visits(parse_qs(parsed.query))
            return
        if path.startswith("/api/admin/visits/") and path.endswith("/photo"):
            self.get_visit_photo(path)
            return
        if path == "/api/admin/options":
            self.get_admin_options()
            return
        if path == "/api/admin/privacy":
            self.get_admin_privacy()
            return
        if path == "/api/admin/export.csv":
            self.export_csv(parse_qs(parsed.query))
            return
        if path == "/api/admin/export.zip":
            self.export_zip(parse_qs(parsed.query))
            return
        static_paths = {"/": "index.html", "/admin": "admin.html", "/admin/": "admin.html", "/privacy": "privacy.html"}
        requested = static_paths.get(path, path.lstrip("/"))
        static_root = (ROOT / "public").resolve()
        candidate = (static_root / requested).resolve()
        if candidate.is_file() and candidate.is_relative_to(static_root):
            self.send_file(candidate)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_payload()
            if path == "/api/visits":
                self.create_visit(payload)
                return
            if path == "/api/admin/options":
                self.create_admin_option(payload)
                return
            if path == "/api/admin/login":
                self.admin_login(payload)
                return
            if path == "/api/admin/logout":
                self.admin_logout()
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except sqlite3.Error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Neizdevās saglabāt datus. Lūdzu, mēģiniet vēlreiz."})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/admin/options/"):
            self.delete_admin_option(path)
            return
        if not path.startswith("/api/admin/visits/") or not self.require_admin():
            return
        try:
            visit_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Nederīgs ieraksta numurs."})
            return
        with db_connection() as conn:
            deleted = conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,)).rowcount
            conn.execute("DELETE FROM visitor_profiles WHERE id NOT IN (SELECT DISTINCT profile_id FROM visits)")
        if not deleted:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Ieraksts netika atrasts."})
            return
        self.send_json(HTTPStatus.OK, {"ok": True})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_payload()
            if path == "/api/admin/privacy":
                self.update_admin_privacy(payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except sqlite3.Error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Neizdevās saglabāt iestatījumus. Lūdzu, mēģiniet vēlreiz."})

    def get_admin_options(self) -> None:
        if not self.require_admin():
            return
        with db_connection() as conn:
            self.send_json(HTTPStatus.OK, {"options": grouped_form_options(conn)})

    def get_admin_privacy(self) -> None:
        if not self.require_admin():
            return
        with db_connection() as conn:
            self.send_json(HTTPStatus.OK, {"privacy": privacy_settings(conn)})

    def update_admin_privacy(self, payload: dict[str, Any]) -> None:
        if not self.require_admin():
            return
        values = validate_privacy_settings(payload)
        timestamp = utc_now()
        with db_connection() as conn:
            conn.execute(
                """UPDATE privacy_settings SET
                    controller_name=?, controller_registration_number=?, controller_contact=?, dpo_contact=?,
                    legal_basis=?, legal_basis_details=?, processing_purpose=?, short_notice=?, recipients=?, retention_period=?,
                    retention_months=?, storage_description=?, next_registration_seconds=?, privacy_reading_seconds=?, updated_at=?
                WHERE id=1""",
                (
                    values["controllerName"], values["controllerRegistrationNumber"], values["controllerContact"],
                    values["dpoContact"], values["legalBasis"], values["legalBasisDetails"],
                    values["processingPurpose"], values["shortNotice"], values["recipients"], values["retentionPeriod"],
                    values["retentionMonths"], values["storageDescription"], values["nextRegistrationSeconds"], values["privacyReadingSeconds"], timestamp,
                ),
            )
            # Ja administrators samazina termiņu, neatbilstošos ierakstus dzēšam uzreiz.
            purge_expired_data(conn)
            updated = privacy_settings(conn)
        self.send_json(HTTPStatus.OK, {"privacy": updated})

    def create_admin_option(self, payload: dict[str, Any]) -> None:
        if not self.require_admin():
            return
        field_key = payload.get("fieldKey")
        if field_key not in OPTION_FIELDS:
            raise ValueError("Izvēlieties derīgu veidlapas lauku.")
        option_value = clean_text(payload.get("value"), "Izvēlnes vērtība", 160)
        with db_connection() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO form_options (field_key, option_value, created_at) VALUES (?, ?, ?)",
                (field_key, option_value, utc_now()),
            )
            if not cursor.rowcount:
                raise ValueError("Šāda izvēlnes vērtība jau pastāv.")
        self.send_json(HTTPStatus.CREATED, {"id": cursor.lastrowid, "fieldKey": field_key, "value": option_value})

    def delete_admin_option(self, path: str) -> None:
        if not self.require_admin():
            return
        try:
            option_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Nederīgs izvēlnes ieraksts."})
            return
        with db_connection() as conn:
            deleted = conn.execute("DELETE FROM form_options WHERE id = ?", (option_id,)).rowcount
        if not deleted:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Izvēlnes ieraksts netika atrasts."})
            return
        self.send_json(HTTPStatus.OK, {"ok": True})

    def get_visitor_by_personal_code(self, raw_personal_code: str) -> None:
        if too_many_attempts(PERSONAL_CODE_LOOKUPS, self.client_key, limit=12, window_seconds=60):
            self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Pārāk daudz mēģinājumu. Lūdzu, uzgaidiet minūti."})
            return
        try:
            code_key = personal_code_key(raw_personal_code)
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Personas kods nav derīgs."})
            return
        with db_connection() as conn:
            profile = find_profile_by_personal_code(conn, code_key)
            last_visit = conn.execute(
                """SELECT visited_company, host_person, visit_purpose FROM visits
                   WHERE profile_id = ? ORDER BY created_at DESC, id DESC LIMIT 1""",
                (profile["id"],),
            ).fetchone() if profile else None
        if not profile:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Iepriekšējs ieraksts netika atrasts."})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "firstName": profile["first_name"],
                "lastName": profile["last_name"],
                "personalCode": profile["personal_code"],
                "representedCompany": profile["represented_company"],
                "documentType": profile["document_type"],
                "documentNumber": profile["document_number"],
                "visitedCompany": last_visit["visited_company"] if last_visit else VISITED_COMPANY_DEFAULT,
                "hostPerson": last_visit["host_person"] if last_visit else "",
                "visitPurpose": last_visit["visit_purpose"] if last_visit else "",
            },
        )

    def create_visit(self, payload: dict[str, Any]) -> None:
        fields = validate_visit(payload)
        media_type, image_data = validate_photo(payload)
        timestamp = utc_now()
        with db_connection() as conn:
            purge_expired_data(conn)
            profile = find_profile_by_personal_code(conn, fields["personalCodeKey"])
            if profile:
                profile_id = profile["id"]
                conn.execute(
                    """UPDATE visitor_profiles SET first_name=?, last_name=?, personal_code=?, represented_company=?,
                       document_type=?, document_number=?, updated_at=? WHERE id=?""",
                    (
                        fields["firstName"], fields["lastName"], fields["personalCode"], fields["representedCompany"],
                        fields["documentType"], fields["documentNumber"], timestamp, profile_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO visitor_profiles (
                        first_name, last_name, personal_code, personal_code_key, represented_company,
                        document_type, document_number, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fields["firstName"], fields["lastName"], fields["personalCode"], fields["personalCodeKey"],
                        fields["representedCompany"], fields["documentType"], fields["documentNumber"], timestamp, timestamp,
                    ),
                )
                profile_id = cursor.lastrowid
            visit = conn.execute(
                """INSERT INTO visits (
                    profile_id, first_name, last_name, personal_code, represented_company,
                    document_type, document_number, visited_company, host_person, visit_purpose, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_id, fields["firstName"], fields["lastName"], fields["personalCode"],
                    fields["representedCompany"], fields["documentType"], fields["documentNumber"],
                    fields["visitedCompany"], fields["hostPerson"], fields["visitPurpose"], timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO visit_photos (visit_id, media_type, image_data, captured_at) VALUES (?, ?, ?, ?)",
                (visit.lastrowid, media_type, image_data, timestamp),
            )
        self.send_json(HTTPStatus.CREATED, {"personalCode": fields["personalCode"], "registeredAt": local_time(timestamp)})

    def admin_login(self, payload: dict[str, Any]) -> None:
        if too_many_attempts(LOGIN_ATTEMPTS, self.client_key, limit=8, window_seconds=15 * 60):
            self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Pārāk daudz pieteikšanās mēģinājumu. Lūdzu, uzgaidiet."})
            return
        if not ADMIN_PASSWORD or not ADMIN_SECRET:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Administrācijas piekļuve nav nokonfigurēta."})
            return
        password = payload.get("password")
        if not isinstance(password, str) or not hmac.compare_digest(password, ADMIN_PASSWORD):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Nepareiza parole."})
            return
        session = sign_session(int(time.time()) + SESSION_TTL_SECONDS)
        flags = f"HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_SECONDS}"
        if SECURE_COOKIES:
            flags += "; Secure"
        self.send_json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": f"visitor_registry_admin={session}; {flags}"})

    def admin_logout(self) -> None:
        flags = "HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        if SECURE_COOKIES:
            flags += "; Secure"
        self.send_json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": f"visitor_registry_admin=; {flags}"})

    @staticmethod
    def date_range(query: dict[str, list[str]]) -> tuple[str | None, str | None, str]:
        date_from = query.get("from", [""])[0]
        date_to = query.get("to", [""])[0]
        search = query.get("q", [""])[0].strip()[:100]
        try:
            start_date = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
            end_date = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
        except ValueError as error:
            raise ValueError("Datuma formāts nav derīgs.") from error
        start = datetime.combine(start_date, clock_time.min, TIMEZONE).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z") if start_date else None
        end = datetime.combine(end_date, clock_time.max, TIMEZONE).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z") if end_date else None
        return start, end, search

    def visit_rows(self, query: dict[str, list[str]]) -> list[sqlite3.Row]:
        start, end, search = self.date_range(query)
        conditions: list[str] = []
        parameters: list[str] = []
        if start:
            conditions.append("created_at >= ?")
            parameters.append(start)
        if end:
            conditions.append("created_at <= ?")
            parameters.append(end)
        if search:
            conditions.append("(first_name || ' ' || last_name || ' ' || personal_code || ' ' || represented_company || ' ' || host_person) LIKE ?")
            parameters.append(f"%{search}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with db_connection() as conn:
            purge_expired_data(conn)
            return conn.execute(
                f"""SELECT visits.*, EXISTS(
                    SELECT 1 FROM visit_photos WHERE visit_photos.visit_id = visits.id
                ) AS has_photo
                FROM visits{where} ORDER BY created_at DESC LIMIT 1000""",
                parameters,
            ).fetchall()

    def get_admin_visits(self, query: dict[str, list[str]]) -> None:
        if not self.require_admin():
            return
        try:
            rows = self.visit_rows(query)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "visits": [
                    {
                        "id": row["id"], "firstName": row["first_name"], "lastName": row["last_name"],
                        "personalCode": row["personal_code"], "representedCompany": row["represented_company"],
                        "documentType": row["document_type"], "documentNumber": row["document_number"],
                        "visitedCompany": row["visited_company"], "hostPerson": row["host_person"],
                        "visitPurpose": row["visit_purpose"], "createdAt": row["created_at"],
                        "displayTime": local_time(row["created_at"]), "hasPhoto": bool(row["has_photo"]),
                    }
                    for row in rows
                ],
                "count": len(rows), "timezone": str(TIMEZONE),
            },
        )

    def get_visit_photo(self, path: str) -> None:
        if not self.require_admin():
            return
        parts = path.split("/")
        try:
            visit_id = int(parts[4])
        except (IndexError, ValueError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Nederīgs apmeklējuma numurs."})
            return
        with db_connection() as conn:
            photo = conn.execute(
                "SELECT media_type, image_data FROM visit_photos WHERE visit_id = ?", (visit_id,)
            ).fetchone()
        if not photo:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Šai vizītei foto nav pieejams."})
            return
        self.send_binary(HTTPStatus.OK, photo["image_data"], photo["media_type"])

    def export_csv(self, query: dict[str, list[str]]) -> None:
        if not self.require_admin():
            return
        try:
            rows = self.visit_rows(query)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        body = csv_report_body(rows)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="apmekletaju-registra-atskaite.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def export_zip(self, query: dict[str, list[str]]) -> None:
        """Lejupielādē atlasīto reģistru ar foto atsevišķā mapē vienā ZIP failā."""
        if not self.require_admin():
            return
        try:
            rows = self.visit_rows(query)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        photo_files: dict[int, str] = {}
        photos: list[tuple[str, bytes]] = []
        with db_connection() as conn:
            for row in rows:
                photo = conn.execute(
                    "SELECT media_type, image_data FROM visit_photos WHERE visit_id = ?", (row["id"],)
                ).fetchone()
                if not photo:
                    continue
                filename = photo_export_filename(row, photo["media_type"])
                photo_files[row["id"]] = f"foto/{filename}"
                photos.append((filename, photo["image_data"]))

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="apmekletaju-registra-atskaite-ar-foto.zip"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            # Rakstām tieši atbildes plūsmā, lai liels foto arhīvs netiktu pilnībā turēts servera atmiņā.
            with zipfile.ZipFile(self.wfile, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("apmekletaju-registra-atskaite.csv", csv_report_body(rows, photo_files))
                for filename, image_data in photos:
                    archive.writestr(f"foto/{filename}", image_data)
        except (BrokenPipeError, ConnectionResetError):
            return


if __name__ == "__main__":
    initialise_database()
    start_retention_maintenance()
    if not ADMIN_PASSWORD:
        print("BRĪDINĀJUMS: administrācijas panelis nav aktivizēts. Iestatiet administrācijas paroli.")
    elif not ADMIN_SECRET_CONFIGURED:
        print("BRĪDINĀJUMS: sesijas atslēga nav iestatīta; pēc servera pārstarta administratoram būs jāpiesakās no jauna.")
    if bool(TLS_CERTIFICATE_PATH) != bool(TLS_PRIVATE_KEY_PATH):
        raise RuntimeError("HTTPS nepieciešams gan sertifikāta, gan privātās atslēgas ceļš.")
    protocol = "http"
    if TLS_CERTIFICATE_PATH:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(TLS_CERTIFICATE_PATH, TLS_PRIVATE_KEY_PATH)
        server = TLSRegistryServer((HOST, PORT), RegistryHandler, tls_context)
        protocol = "https"
    else:
        server = ThreadingHTTPServer((HOST, PORT), RegistryHandler)
    print(f"Apmeklētāju reģistrs darbojas: {protocol}://{HOST}:{PORT}")
    server.serve_forever()
