"""
main.py

Punto de entrada de Email Admon. Pensado para correr vía cron (o el scheduler
que prefieras) cada cierto tiempo, por ejemplo cada hora:

    0 * * * * cd /ruta/a/email_admon/src && /ruta/a/venv/bin/python main.py >> ../log/run.log 2>&1

Cada corrida:
1. Lee config/directrices.yaml (se puede editar sin tocar código).
2. Busca correos nuevos sin procesar en la bandeja.
3. Clasifica cada uno (classifier.py).
4. Actúa según la categoría (gmail_client.py).
5. Marca el hilo como procesado (etiqueta MailAdmon/Procesado) para no repetirlo.
6. Deja un registro en log/acciones.jsonl.
"""

from __future__ import annotations

import datetime
import json
import os

import anthropic
import yaml
from dotenv import load_dotenv

import gmail_client
import notify
from classifier import classify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "directrices.yaml")
LOG_DIR = os.path.join(BASE_DIR, "log")
LOG_PATH = os.path.join(LOG_DIR, "acciones.jsonl")

LABEL_PROCESADO = "MailAdmon/Procesado"
LABEL_ELIMINADO_AUTO = "MailAdmon/EliminadoAuto"
LABEL_SUGERIDO_REVISION = "MailAdmon/SugeridoRevision"
LABEL_URGENTE = "MailAdmon/Urgente"
LABEL_BORRADOR_PENDIENTE = "MailAdmon/BorradorPendiente"


def load_directrices() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def log_action(entry: dict) -> None:
    entry["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
    line = json.dumps(entry, ensure_ascii=False)

    # Siempre va a stdout: en un cron job de Render el disco es efímero
    # (no sobrevive entre corridas), así que la bitácora "de verdad" vive en
    # los logs de Render (Dashboard -> tu cron job -> Logs), consultables
    # también con la herramienta list_logs del conector. En tu máquina local
    # esto además queda en log/acciones.jsonl de forma permanente.
    print(f"LOG_ACCION {line}")

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run() -> None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    directrices = load_directrices()
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    service = gmail_client.get_service()

    label_ids = {
        "procesado": gmail_client.get_or_create_label(service, LABEL_PROCESADO),
        "eliminado_auto": gmail_client.get_or_create_label(service, LABEL_ELIMINADO_AUTO),
        "sugerido_revision": gmail_client.get_or_create_label(service, LABEL_SUGERIDO_REVISION),
        "urgente": gmail_client.get_or_create_label(service, LABEL_URGENTE),
        "borrador_pendiente": gmail_client.get_or_create_label(service, LABEL_BORRADOR_PENDIENTE),
    }

    # Excluye lo ya procesado en corridas anteriores.
    query = f"in:inbox -label:{LABEL_PROCESADO}"
    thread_ids = gmail_client.list_candidate_threads(service, query=query, max_results=25)

    print(f"Email Admon: {len(thread_ids)} hilo(s) nuevo(s) por revisar.")

    for thread_id in thread_ids:
        thread = gmail_client.get_thread_detail(service, thread_id, directrices["alias_disponibles"])
        result = classify(thread, directrices, anthropic_client)

        action_taken = "sin_accion"

        if result.urgente:
            gmail_client.apply_label(service, thread_id, label_ids["urgente"])
            notify.send_urgent_alert(
                destinatario=directrices["notificacion_urgente"]["destinatario"],
                asunto_original=thread.subject,
                remitente_original=thread.sender,
                razon=result.razonamiento,
            )

        if result.categoria == "A1":
            gmail_client.apply_label(service, thread_id, label_ids["eliminado_auto"])
            gmail_client.trash_thread(service, thread_id)
            action_taken = "eliminado_a1"

        elif result.categoria == "A2":
            gmail_client.apply_label(service, thread_id, label_ids["sugerido_revision"])
            action_taken = "sugerido_a2"

        elif result.categoria == "B":
            if directrices.get("categoria_b_auto_envio_activa", False) and result.cuerpo_respuesta:
                gmail_client.send_reply(
                    service, thread,
                    subject=result.asunto_respuesta or thread.subject,
                    body=result.cuerpo_respuesta,
                )
                action_taken = "respondido_b_auto"
            else:
                # Mientras categoria_b_auto_envio_activa sea false, se trata
                # como C (deja borrador) para no perder la respuesta sugerida.
                if result.cuerpo_respuesta:
                    gmail_client.create_draft_reply(
                        service, thread,
                        subject=result.asunto_respuesta or thread.subject,
                        body=result.cuerpo_respuesta,
                    )
                    gmail_client.apply_label(service, thread_id, label_ids["borrador_pendiente"])
                action_taken = "borrador_b_pendiente_activacion"

        elif result.categoria == "C":
            if result.cuerpo_respuesta:
                gmail_client.create_draft_reply(
                    service, thread,
                    subject=result.asunto_respuesta or thread.subject,
                    body=result.cuerpo_respuesta,
                )
                gmail_client.apply_label(service, thread_id, label_ids["borrador_pendiente"])
            action_taken = "borrador_c"

        # INFORMATIVO y cualquier otro caso: no se toca nada más.

        gmail_client.apply_label(service, thread_id, label_ids["procesado"])

        log_action({
            "thread_id": thread_id,
            "remitente": thread.sender,
            "asunto": thread.subject,
            "categoria": result.categoria,
            "urgente": result.urgente,
            "razonamiento": result.razonamiento,
            "accion": action_taken,
        })

        print(f"- [{result.categoria}{' URGENTE' if result.urgente else ''}] {thread.subject!r} -> {action_taken}")

    print("Email Admon: corrida completa.")


if __name__ == "__main__":
    run()
