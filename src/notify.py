"""
notify.py

Notificación de urgencias. Implementación simple por correo (SMTP) usando
una cuenta de Gmail con contraseña de aplicación. Pensado para reemplazarse
después por push/SMS (ej. Twilio) si se quiere algo que llegue al teléfono
sin depender de revisar el correo.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText


def send_urgent_alert(destinatario: str, asunto_original: str, remitente_original: str, razon: str) -> None:
    canal = os.environ.get("NOTIFY_CHANNEL", "email")
    if canal == "ninguno":
        return

    smtp_user = os.environ.get("NOTIFY_SMTP_USER")
    smtp_pass = os.environ.get("NOTIFY_SMTP_APP_PASSWORD")
    if not smtp_user or not smtp_pass:
        print(
            "[AVISO] Falta configurar NOTIFY_SMTP_USER / NOTIFY_SMTP_APP_PASSWORD "
            "en .env — no se pudo enviar la alerta urgente por correo. "
            f"Correo urgente detectado de todas formas: '{asunto_original}' de {remitente_original}."
        )
        return

    body = (
        f"Email Admon detectó un correo URGENTE.\n\n"
        f"De: {remitente_original}\n"
        f"Asunto: {asunto_original}\n"
        f"Motivo: {razon}\n\n"
        f"Revisa tu bandeja de egalindo@truelinetllc.com lo antes posible."
    )
    message = MIMEText(body)
    message["Subject"] = f"[URGENTE] Email Admon: {asunto_original}"
    message["From"] = smtp_user
    message["To"] = destinatario

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [destinatario], message.as_string())
