"""
store.py

Capa de almacenamiento para dos cosas nuevas que no dependen de Gmail ni de
Render directamente:

1. El panel web (dashboard): guarda un registro reciente de la actividad del
   bot para que una páginita aparte lo pueda mostrar sin leer logs de Render
   ni etiquetas de Gmail.
2. El seguimiento de "borradores pendientes" (SLA): para poder avisarte si
   dejaste un borrador (categoría C o B-sin-activar) sin revisar por más de
   cierto número de horas.

Usa Render Key Value (compatible con Redis). Si la variable de entorno
REDIS_URL no está configurada, TODAS las funciones de aquí se vuelven no-op
silencioso — el bot sigue clasificando y respondiendo exactamente igual,
simplemente sin panel ni seguimiento de SLA. Nunca debe tumbar la corrida
principal por un problema de este almacenamiento secundario.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Callable, Optional

try:
    import redis
except ImportError:  # pragma: no cover - por si el paquete no está instalado
    redis = None  # type: ignore[assignment]

LOG_KEY = "email_admon:log"
LOG_MAX_ENTRIES = 500
PENDING_HASH = "email_admon:pending_since"
ALERTED_SET = "email_admon:sla_alertado"


def get_client():
    """Devuelve un cliente de Redis listo para usar, o None si no está
    configurado o no se pudo conectar (en cuyo caso se avisa por stdout pero
    nunca se lanza una excepción hacia main.py)."""
    url = os.environ.get("REDIS_URL")
    if not url or redis is None:
        return None
    try:
        client = redis.from_url(url, decode_responses=True, socket_timeout=5)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        print(f"[AVISO] No se pudo conectar al panel (Redis): {exc}. El bot sigue funcionando normal, solo sin panel.")
        return None


def push_log_entry(client, entry: dict) -> None:
    """Agrega una entrada al registro reciente (para el panel web)."""
    if client is None:
        return
    try:
        client.lpush(LOG_KEY, json.dumps(entry, ensure_ascii=False))
        client.ltrim(LOG_KEY, 0, LOG_MAX_ENTRIES - 1)
    except Exception as exc:  # noqa: BLE001
        print(f"[AVISO] No se pudo registrar en el panel ({exc}).")


def track_pending_draft(client, thread_id: str, subject: str, sender: str) -> None:
    """Marca un hilo como 'borrador pendiente desde ahora', solo si no se
    estaba rastreando ya (para no reiniciar el reloj del SLA en cada corrida)."""
    if client is None:
        return
    try:
        if not client.hexists(PENDING_HASH, thread_id):
            payload = json.dumps(
                {
                    "subject": subject,
                    "sender": sender,
                    "since": datetime.datetime.utcnow().isoformat() + "Z",
                },
                ensure_ascii=False,
            )
            client.hset(PENDING_HASH, thread_id, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[AVISO] No se pudo registrar seguimiento de borrador pendiente ({exc}).")


def check_sla(
    client,
    is_still_pending_fn: Callable[[str], bool],
    sla_horas: float,
    on_alert_fn: Callable[[str, str, str, float], None],
) -> None:
    """Revisa todos los hilos rastreados como 'borrador pendiente'.

    - Si `is_still_pending_fn(thread_id)` devuelve False (ya se resolvió,
      la etiqueta ya no está), se deja de rastrear.
    - Si sigue pendiente y ya pasaron `sla_horas` y no se había avisado antes,
      llama a `on_alert_fn(thread_id, subject, sender, horas_transcurridas)`
      y lo marca como ya avisado (para no repetir el aviso en cada corrida).
    """
    if client is None:
        return

    try:
        all_pending: dict[str, str] = client.hgetall(PENDING_HASH)
    except Exception as exc:  # noqa: BLE001
        print(f"[AVISO] No se pudo leer el seguimiento de borradores pendientes ({exc}).")
        return

    now = datetime.datetime.utcnow()

    for thread_id, raw in all_pending.items():
        try:
            data = json.loads(raw)
            since = datetime.datetime.fromisoformat(data["since"].rstrip("Z"))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue

        try:
            still_pending = is_still_pending_fn(thread_id)
        except Exception:  # noqa: BLE001
            # Si no se pudo verificar, no se asume nada: se deja para la próxima corrida.
            continue

        if not still_pending:
            try:
                client.hdel(PENDING_HASH, thread_id)
                client.srem(ALERTED_SET, thread_id)
            except Exception:  # noqa: BLE001
                pass
            continue

        horas = (now - since).total_seconds() / 3600
        try:
            ya_avisado = client.sismember(ALERTED_SET, thread_id)
        except Exception:  # noqa: BLE001
            ya_avisado = False

        if horas >= sla_horas and not ya_avisado:
            on_alert_fn(thread_id, data.get("subject", ""), data.get("sender", ""), horas)
            try:
                client.sadd(ALERTED_SET, thread_id)
            except Exception:  # noqa: BLE001
                pass
