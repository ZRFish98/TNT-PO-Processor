"""
Gmail Monitor — background daemon thread that polls Gmail for T&T PO PDF attachments
and stores extracted data in PostgreSQL.

OAuth flow works inside Docker by using Streamlit's query_params to handle the
Google redirect URI callback (see frontend/app.py for the callback handler).
"""
import base64
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.pdf_extractor import PDFExtractor

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Gmail search query — tune to match actual T&T PO email patterns
GMAIL_SEARCH_QUERY = 'has:attachment filename:pdf subject:"Purchase Order"'


class GmailMonitor:
    """
    Manages Google OAuth credentials and runs a background polling thread
    that checks Gmail for new T&T PO PDFs and inserts them into PostgreSQL.

    Must be instantiated as a singleton (use @st.cache_resource in app.py).
    The background thread writes to the DB directly — never touches st.* objects.
    """

    def __init__(
        self,
        db_conn_factory: Callable,
        poll_interval_seconds: int = 300,
        token_path: str = '/data/gmail/token.json',
        credentials_path: str = '/data/gmail/credentials.json',
    ):
        self.db_conn_factory = db_conn_factory
        self.poll_interval = poll_interval_seconds
        self.token_path = Path(token_path)
        self.credentials_path = Path(credentials_path)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status_lock = threading.Lock()
        self._status: Dict = {
            'running': False,
            'authenticated': False,
            'last_poll_at': None,
            'last_error': None,
            'emails_processed': 0,
        }

    # ──────────────────────────────────────────────
    # OAuth helpers (called from Streamlit UI thread)
    # ──────────────────────────────────────────────

    def credentials_file_exists(self) -> bool:
        return self.credentials_path.exists()

    def save_credentials_file(self, file_bytes: bytes):
        """Write uploaded credentials.json bytes to the token volume path."""
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.credentials_path.write_bytes(file_bytes)
        logger.info(f"credentials.json saved to {self.credentials_path}")

    def is_authenticated(self) -> bool:
        """Returns True if a valid (or refreshable) token exists."""
        if not self.token_path.exists():
            return False
        try:
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            if creds.valid:
                return True
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_token(creds)
                return True
        except Exception as e:
            logger.warning(f"Token check failed: {e}")
        return False

    def get_authorization_url(self, redirect_uri: str) -> Tuple[str, Flow]:
        """
        Generate Google OAuth authorization URL.

        redirect_uri must match what is registered in Google Cloud Console.
        For Docker local:  http://localhost:8501/
        For Hostinger:     https://internal.atiara.cloud/
        """
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"credentials.json not found at {self.credentials_path}. "
                "Upload it in the Settings page first."
            )
        flow = Flow.from_client_secrets_file(
            str(self.credentials_path),
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # Ensures refresh_token is always issued
        )
        return auth_url, flow

    def exchange_code_for_token(self, flow: Flow, authorization_code: str):
        """Complete the OAuth flow and persist the token."""
        flow.fetch_token(code=authorization_code)
        self._save_token(flow.credentials)
        logger.info("Gmail OAuth token saved successfully")

    def revoke_token(self):
        """Delete the stored token (forces re-auth)."""
        if self.token_path.exists():
            self.token_path.unlink()
        with self._status_lock:
            self._status['authenticated'] = False
        logger.info("Gmail token revoked")

    # ──────────────────────────────────────────────
    # Thread lifecycle
    # ──────────────────────────────────────────────

    def start(self):
        """Start the background polling thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name='gmail-monitor',
            daemon=True,
        )
        self._thread.start()
        with self._status_lock:
            self._status['running'] = True
        logger.info("Gmail monitor thread started")

    def stop(self):
        """Signal the polling thread to stop."""
        self._stop_event.set()
        with self._status_lock:
            self._status['running'] = False
        logger.info("Gmail monitor thread stop requested")

    def get_status(self) -> Dict:
        with self._status_lock:
            return dict(self._status)

    # ──────────────────────────────────────────────
    # Internal polling loop (runs in daemon thread)
    # ──────────────────────────────────────────────

    def _poll_loop(self):
        logger.info("Gmail monitor poll loop running")
        while not self._stop_event.is_set():
            try:
                creds = self._load_credentials()
                if creds:
                    self._check_new_emails(creds)
                    with self._status_lock:
                        self._status['authenticated'] = True
                        self._status['last_poll_at'] = datetime.now(timezone.utc).isoformat()
                        self._status['last_error'] = None
                else:
                    with self._status_lock:
                        self._status['authenticated'] = False
            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)
                with self._status_lock:
                    self._status['last_error'] = str(e)

            self._stop_event.wait(timeout=self.poll_interval)

        logger.info("Gmail monitor poll loop stopped")

    def _load_credentials(self) -> Optional[Credentials]:
        """Load and refresh credentials from token.json."""
        if not self.token_path.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_token(creds)
            return creds if creds.valid else None
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return None

    def _check_new_emails(self, creds: Credentials):
        """Search Gmail for PDF attachments and process new ones."""
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)

        try:
            results = service.users().messages().list(
                userId='me',
                q=GMAIL_SEARCH_QUERY,
                maxResults=50,
            ).execute()
        except HttpError as e:
            logger.error(f"Gmail API list error: {e}")
            return

        messages = results.get('messages', [])
        logger.info(f"Gmail search returned {len(messages)} messages")

        for msg_meta in messages:
            msg_id = msg_meta['id']
            if self._is_duplicate(msg_id):
                continue

            try:
                msg = service.users().messages().get(
                    userId='me',
                    id=msg_id,
                    format='full',
                ).execute()
                self._process_message(service, msg)
            except Exception as e:
                logger.error(f"Error processing message {msg_id}: {e}")

    def _process_message(self, service, msg: dict):
        """Extract all PDF attachments from a Gmail message and store them."""
        msg_id = msg['id']
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        subject = headers.get('Subject', '')
        date_str = headers.get('Date', '')

        parts = msg.get('payload', {}).get('parts', [])

        # Flatten nested parts (some emails have nested multipart)
        def _flatten_parts(parts_list):
            flat = []
            for p in parts_list:
                flat.append(p)
                if 'parts' in p:
                    flat.extend(_flatten_parts(p['parts']))
            return flat

        all_parts = _flatten_parts(parts)
        pdf_found = False

        for part in all_parts:
            filename = part.get('filename', '')
            mime_type = part.get('mimeType', '')
            is_pdf = filename.lower().endswith('.pdf') or mime_type == 'application/pdf'

            if not is_pdf:
                continue

            attachment_id = part.get('body', {}).get('attachmentId')
            if not attachment_id:
                continue

            pdf_found = True

            try:
                attachment = service.users().messages().attachments().get(
                    userId='me',
                    messageId=msg_id,
                    id=attachment_id,
                ).execute()
                pdf_bytes = base64.urlsafe_b64decode(attachment['data'])
            except Exception as e:
                logger.error(f"Failed to download attachment {filename} from {msg_id}: {e}")
                self._save_to_db(
                    gmail_message_id=f"{msg_id}_{filename}",
                    gmail_subject=subject,
                    gmail_received_at=date_str,
                    filename=filename or 'attachment.pdf',
                    extracted_data=[],
                    errors=[f"Download failed: {e}"],
                )
                continue

            pdf_io = BytesIO(pdf_bytes)
            extracted_data, errors = PDFExtractor.extract_from_file(pdf_io, filename)

            # Use msg_id + filename as dedup key when one email has multiple PDFs
            dedup_key = f"{msg_id}_{filename}" if filename else msg_id

            self._save_to_db(
                gmail_message_id=dedup_key,
                gmail_subject=subject,
                gmail_received_at=date_str,
                filename=filename or 'attachment.pdf',
                extracted_data=extracted_data,
                errors=errors,
            )

        if not pdf_found:
            logger.debug(f"Message {msg_id} matched query but contained no PDF attachments")

    def _save_to_db(
        self,
        gmail_message_id: str,
        gmail_subject: str,
        gmail_received_at: str,
        filename: str,
        extracted_data: list,
        errors: list,
    ):
        """Insert a staged_pos record into PostgreSQL."""
        po_number = store_id = store_name = order_date = delivery_date = None

        if extracted_data:
            first = extracted_data[0]
            po_number = str(first.get('PO No.', '')) or None
            store_id = first.get('Store ID')
            store_name = first.get('Store Name', '') or None
            order_date = first.get('Order Date', '') or None
            delivery_date = first.get('Delivery Date', '') or None

        status = 'unprocessed' if extracted_data else 'error'
        error_message = '; '.join(errors) if errors and not extracted_data else None

        sql = """
            INSERT INTO staged_pos (
                source, gmail_message_id, gmail_subject, gmail_received_at,
                original_filename, po_number, store_id, store_name,
                order_date, delivery_date, extracted_data, status, error_message
            ) VALUES (
                'gmail', %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s, %s
            )
            ON CONFLICT (gmail_message_id) DO NOTHING
        """

        try:
            with self.db_conn_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        gmail_message_id, gmail_subject, gmail_received_at,
                        filename, po_number, store_id, store_name,
                        order_date, delivery_date,
                        json.dumps(extracted_data),
                        status, error_message,
                    ))
                conn.commit()

            with self._status_lock:
                self._status['emails_processed'] += 1

            logger.info(f"Saved staged PO: {filename} (status={status})")

        except Exception as e:
            logger.error(f"DB write failed for {filename}: {e}")

    def _is_duplicate(self, gmail_message_id: str) -> bool:
        """Check if this Gmail message ID already exists in staged_pos."""
        sql = "SELECT 1 FROM staged_pos WHERE gmail_message_id LIKE %s LIMIT 1"
        try:
            with self.db_conn_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (f"{gmail_message_id}%",))
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"Dedup check failed for {gmail_message_id}: {e}")
            return False

    def _save_token(self, creds: Credentials):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json())
