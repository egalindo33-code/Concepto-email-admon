"""
notify.py

Notificación de urgencias. Se manda usando la misma conexión de Gmail (API)
que el bot ya tiene autorizada para leer/responder correos — no SMTP, no
contraseña de aplicación. Esto evita el problema de cuentas de Google
Workspace que tienen las "contraseñas de aplicación" deshabilitadas por el
administrador del dominio (común en organizaciones).
"""

from __future__ import annotations

import os

import gmail_client


def send_urgent_alert(
    service,
    destinatario: str,
    asunto_original: str,
    remitente_original: str,
    razon: str,
    remitente_alerta: str,
) -> None:
    canal = os.environ.get("NOTIFY_CHANNEL", "email")
    if canal == "ninguno":
        return

    body = (
        f"Email Admon detectó un correo URGENTE.\n\n"
        f"De: {remitente_original}\n"
        f"Asunto: {asunto_original}\n"
        f"Motivo: {razon}\n\n"
        f"Revisa tu bandeja de egalindo@truelinetllc.com lo antes posible."
    )

    try:
        gmail_client.send_plain_message(
            service,
            to_address=destinatario,
            from_address=remitente_alerta,
            subject=f"[URGENTE] Email Admon: {asunto_original}",
            body=body,
        )
    except Exception as exc:  # noqa: BLE001 - nunca debe tumbar la corrida principal
        print(
            f"[AVISO] No se pudo enviar la alerta urgente por correo ({exc}). "
            f"Correo urgente detectado de todas formas: '{asunto_original}' de {remitente_original}."
        )


def send_followup_alert(
    service,
    destinatario: str,
    asunto_original: str,
    remitente_original: str,
    horas: float,
    remitente_alerta: str,
) -> None:
    """Aviso de seguimiento (SLA): un borrador quedó esperando tu revisión
    por más tiempo del configurado en directrices.yaml. Se manda una sola vez
    por hilo (lo controla store.py), no en cada corrida."""
    canal = os.environ.get("NOTIFY_CHANNEL", "email")
    if canal == "ninguno":
        return

    body = (
        f"Tienes un borrador de Email Admon esperando tu revisión desde hace "
        f"más de {horas:.0f} horas.\n\n"
        f"De: {remitente_original}\n"
        f"Asunto: {asunto_original}\n\n"
        f"Revísalo en Gmail (etiqueta MailAdmon/BorradorPendiente) cuando puedas."
    )

    try:
        gmail_client.send_plain_message(
            service,
            to_address=destinatario,
            from_address=remitente_alerta,
            subject=f"[SEGUIMIENTO] Email Admon: {asunto_original}",
            body=body,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[AVISO] No se pudo enviar el aviso de seguimiento ({exc}). "
            f"Borrador pendiente de todas formas: '{asunto_original}'."
        )
