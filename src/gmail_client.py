"""
gmail_client.py

Capa delgada sobre la API de Gmail: autenticación, lectura de hilos,
etiquetado, papelera, creación de borradores y envío con el remitente
correcto (alias de "Send mail as").

Requiere que ya hayas configurado en Gmail (Configuración > Cuentas e
Importación > "Send mail as") los alias listados en config/directrices.yaml,
y que hayas marcado "Reply from the same address the message was sent to".
Ese ajuste resuelve el "de dónde sale la respuesta" a nivel Gmail; este
código además fija el remitente explícitamente para no depender solo de
esa configuración.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # leer, etiquetar, papelera
    "https://www.googleapis.com/auth/gmail.compose",  # crear borradores y enviar
]

CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "..", "credentials")
CLIENT_SECRETS_PATH = os.path.join(CREDENTIALS_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(CREDENTIALS_DIR, "token.json")


@dataclass
class EmailThread:
    thread_id: str
    message_id: str          # id del último mensaje del hilo (para responder/borrador)
    subject: str
    sender: str
    to_recipients: list[str]
    plaintext_body: str
    original_account: str    # a qué cuenta llegó originalmente (para responder con el alias correcto)


def _get_service_from_env() -> Optional["Credentials"]:
    """Construye credenciales directo desde variables de entorno, sin tocar
    disco ni abrir navegador. Pensado para correr en un servidor sin pantalla
    (ej. un cron job de Render): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y
    GOOGLE_REFRESH_TOKEN se generan UNA VEZ en una máquina local con
    navegador (ver scripts/generar_credenciales_render.py) y se guardan como
    variables de entorno/secretos en la plataforma de hosting — nunca en el
    código ni en el repositorio.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def get_service():
    """Autentica y devuelve el objeto de servicio de la API de Gmail.

    Dos modos, resueltos automáticamente:

    1. **Producción/headless (ej. Render):** si existen las variables de
       entorno GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN,
       se usan directamente — no se toca el disco, no se abre navegador.
    2. **Local (primera vez o pruebas en tu máquina):** si no están esas
       variables, cae al flujo de archivo de siempre — abre el navegador la
       primera vez (requiere credentials/client_secret.json) y reutiliza/
       renueva credentials/token.json en corridas siguientes.
    """
    creds = _get_service_from_env()
    if creds:
        return build("gmail", "v1", credentials=creds)

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_PATH):
                raise FileNotFoundError(
                    f"No se encontró {CLIENT_SECRETS_PATH}. Descárgalo desde "
                    "Google Cloud Console (OAuth client ID tipo 'Desktop app') "
                    "y colócalo ahí. Ver README.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_plaintext(payload: dict) -> str:
    """Extrae el cuerpo en texto plano de un mensaje de Gmail (maneja multipart)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        text = _extract_plaintext(part)
        if text:
            return text
    return ""


def _resolve_original_account(headers: list[dict], to_recipients: list[str], known_aliases: list[str]) -> str:
    """Determina a qué cuenta (alias) llegó originalmente el correo.

    Prioridad:
    1. Header X-Gm-Original-To (lo agrega la regla de forwarding del Admin Console).
    2. Header Delivered-To.
    3. El primer destinatario en To: que coincida con un alias conocido.
    4. Si nada aplica, usa la cuenta principal (primer alias de la lista = cuenta monitoreada).
    """
    original_to = _header(headers, "X-Gm-Original-To") or _header(headers, "Delivered-To")
    if original_to:
        original_to = original_to.strip().lower()
        for alias in known_aliases:
            if alias.lower() == original_to:
                return alias

    for recipient in to_recipients:
        for alias in known_aliases:
            if alias.lower() in recipient.lower():
                return alias

    return known_aliases[0] if known_aliases else ""


def list_candidate_threads(service, query: str = "in:inbox is:unread", max_results: int = 20) -> list[str]:
    """Devuelve IDs de hilos candidatos a procesar."""
    result = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
    return [t["id"] for t in result.get("threads", [])]


def get_thread_detail(service, thread_id: str, known_aliases: list[str]) -> EmailThread:
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    last_message = thread["messages"][-1]
    headers = last_message["payload"].get("headers", [])

    subject = _header(headers, "Subject")
    sender = _header(headers, "From")
    to_raw = _header(headers, "To")
    to_recipients = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
    body = _extract_plaintext(last_message["payload"])
    original_account = _resolve_original_account(headers, to_recipients, known_aliases)

    return EmailThread(
        thread_id=thread_id,
        message_id=last_message["id"],
        subject=subject,
        sender=sender,
        to_recipients=to_recipients,
        plaintext_body=body[:6000],  # recorte por costo/tamaño al pasarlo al modelo
        original_account=original_account,
    )


def get_or_create_label(service, display_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == display_name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": display_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def apply_label(service, thread_id: str, label_id: str) -> None:
    service.users().threads().modify(
        userId="me", id=thread_id, body={"addLabelIds": [label_id]}
    ).execute()


def trash_thread(service, thread_id: str) -> None:
    service.users().threads().trash(userId="me", id=thread_id).execute()


def _extract_email_address(from_header: str) -> str:
    match = re.search(r"<([^>]+)>", from_header)
    return match.group(1) if match else from_header.strip()


def create_draft_reply(service, thread: EmailThread, subject: str, body: str) -> dict:
    to_address = _extract_email_address(thread.sender)
    message = MIMEText(body)
    message["to"] = to_address
    message["from"] = thread.original_account
    message["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    return service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": thread.thread_id}},
    ).execute()


def send_plain_message(service, to_address: str, from_address: str, subject: str, body: str) -> dict:
    """Envía un mensaje nuevo (no respuesta a un hilo existente) vía la API de
    Gmail. Se usa para notificaciones internas (ej. alerta de correo urgente)
    reutilizando la misma conexión ya autorizada del bot — sin SMTP ni
    contraseña de aplicación."""
    message = MIMEText(body)
    message["to"] = to_address
    message["from"] = from_address
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    return service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()


def send_reply(service, thread: EmailThread, subject: str, body: str) -> dict:
    """Envía directo (sin borrador) — solo debe usarse para categoría B
    cuando categoria_b_auto_envio_activa esté en true en las directrices."""
    to_address = _extract_email_address(thread.sender)
    message = MIMEText(body)
    message["to"] = to_address
    message["from"] = thread.original_account
    message["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    return service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread.thread_id},
    ).execute()
