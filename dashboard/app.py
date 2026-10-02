"""
app.py — Panel web de Email Admon.

Página de solo lectura que muestra un resumen de la actividad del bot (lo
que ya procesó, qué quedó pendiente de tu revisión, y las urgencias
recientes), leyendo directamente del mismo almacenamiento (Render Key Value
/ Redis) donde el bot ya escribe en cada corrida. No depende de Gmail ni del
dashboard de Render — ábrela desde cualquier computadora o celular.

Variables de entorno:
- REDIS_URL: misma conexión que usa el bot (ver src/store.py).
- DASHBOARD_USER / DASHBOARD_PASS (opcional): si los configuras, la página
  pide usuario/contraseña (autenticación básica). Si los dejas vacíos, la
  página queda abierta para quien tenga el link — no recomendado si el link
  llega a compartirse.
"""

from __future__ import annotations

import datetime
import json
import os
from collections import defaultdict
from functools import wraps

from flask import Flask, Response, render_template_string, request

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

app = Flask(__name__)

REDIS_URL = os.environ.get("REDIS_URL")
LOG_KEY = "email_admon:log"
PENDING_HASH = "email_admon:pending_since"
ALERTED_SET = "email_admon:sla_alertado"

CATEGORY_LABELS = {
    "A1": "A1 · basura eliminada",
    "A2": "A2 · spam de proveedor (sugerido)",
    "B": "B · rutinario",
    "C": "C · borrador para revisar",
    "INFORMATIVO": "Informativo",
}
CATEGORY_COLORS = {
    "A1": "#9ca3af",
    "A2": "#f59e0b",
    "B": "#10b981",
    "C": "#3b82f6",
    "INFORMATIVO": "#d1d5db",
}

TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Admon — Panel</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background:#f3f4f6;
         margin:0; padding:16px; color:#111827; }
  .wrap { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 2px 0; }
  .sub { color:#6b7280; font-size: 13px; margin-bottom: 20px; }
  .cards { display:flex; gap:8px; flex-wrap:wrap; margin-bottom: 20px; }
  .card { background:white; border-radius:10px; padding:10px 16px; min-width:84px;
          box-shadow:0 1px 2px rgba(0,0,0,0.06); text-align:center; }
  .card .n { font-size: 22px; font-weight:700; }
  .card .l { font-size: 10px; color:#6b7280; text-transform:uppercase; letter-spacing:.03em; }
  .section { background:white; border-radius:12px; padding:16px; margin-bottom:16px;
             box-shadow:0 1px 2px rgba(0,0,0,0.06); }
  .section h2 { font-size:15px; margin:0 0 10px 0; }
  .row { display:flex; align-items:flex-start; gap:10px; padding:10px 0; border-bottom:1px solid #f3f4f6; }
  .row:last-child { border-bottom:none; }
  .badge { font-size:10px; font-weight:700; color:white; padding:3px 8px; border-radius:999px;
           white-space:nowrap; margin-top:2px; }
  .meta { font-size:12px; color:#6b7280; margin-top:2px; }
  .subj { font-weight:600; font-size:14px; line-height:1.3; }
  .empty { color:#9ca3af; font-size:13px; padding: 4px 0 10px 0; }
  .pill { font-size:12px; color:#374151; }
  .refresh { font-size:12px; color:#6b7280; text-decoration:none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📬 Email Admon</h1>
  <div class="sub">Trueline Trucking · actualizado: {{ now }} · <a class="refresh" href="/">refrescar</a></div>

  <div class="cards">
    {% for cat, n in counts.items() %}
    <div class="card"><div class="n">{{ n }}</div><div class="l">{{ cat }}</div></div>
    {% endfor %}
    {% if not counts %}
    <div class="empty">Todavía no hay actividad registrada aquí (el bot empieza a llenar esto en su próxima corrida).</div>
    {% endif %}
  </div>

  <div class="section">
    <h2>⏳ Pendientes de tu revisión ({{ pending|length }})</h2>
    {% if pending %}
      {% for p in pending %}
      <div class="row">
        <div style="flex:1">
          <div class="subj">{{ p.subject }}</div>
          <div class="meta">{{ p.sender }} · esperando hace {{ p.horas }} h{{ ' · ya se mandó aviso de seguimiento' if p.alertado else '' }}</div>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">Nada pendiente de revisión ahora mismo.</div>
    {% endif %}
  </div>

  <div class="section">
    <h2>🚨 Urgentes recientes</h2>
    {% if urgentes %}
      {% for e in urgentes %}
      <div class="row">
        <span class="badge" style="background:#dc2626">URGENTE</span>
        <div style="flex:1">
          <div class="subj">{{ e.asunto }}</div>
          <div class="meta">{{ e.remitente }} · {{ e.timestamp }}</div>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">Sin urgencias recientes.</div>
    {% endif %}
  </div>

  <div class="section">
    <h2>📋 Actividad reciente</h2>
    {% for e in recientes %}
    <div class="row">
      <span class="badge" style="background:{{ e.color }}">{{ e.categoria }}</span>
      <div style="flex:1">
        <div class="subj">{{ e.asunto }}</div>
        <div class="meta">{{ e.remitente }} · {{ e.accion }} · {{ e.timestamp }}</div>
      </div>
    </div>
    {% endfor %}
    {% if not recientes %}
    <div class="empty">Sin actividad todavía.</div>
    {% endif %}
  </div>

  <div class="section">
    <h2>🗂️ Informativos agrupados por día</h2>
    {% if digest %}
      {% for dia, n in digest.items() %}
      <div class="row"><div class="pill">{{ dia }}: {{ n }} correo(s) informativo(s), sin acción necesaria</div></div>
      {% endfor %}
    {% else %}
      <div class="empty">Sin informativos registrados todavía.</div>
    {% endif %}
  </div>
</div>
</body>
</html>
"""


def _get_client():
    if not REDIS_URL or redis is None:
        return None
    try:
        return redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
    except Exception:  # noqa: BLE001
        return None


def _requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        expected_pass = os.environ.get("DASHBOARD_PASS")
        if not expected_pass:
            return f(*args, **kwargs)

        expected_user = os.environ.get("DASHBOARD_USER", "admin")
        auth = request.authorization
        if not auth or auth.username != expected_user or auth.password != expected_pass:
            return Response(
                "Acceso restringido.", 401,
                {"WWW-Authenticate": 'Basic realm="Email Admon"'},
            )
        return f(*args, **kwargs)

    return decorated


@app.route("/")
@_requires_auth
def index():
    client = _get_client()
    entries = []

    if client is not None:
        try:
            raw_entries = client.lrange(LOG_KEY, 0, 299)
            for raw in raw_entries:
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except Exception:  # noqa: BLE001
            entries = []

    counts: dict[str, int] = defaultdict(int)
    digest: dict[str, int] = defaultdict(int)
    urgentes = []

    for e in entries:
        cat = e.get("categoria", "?")
        counts[CATEGORY_LABELS.get(cat, cat)] += 1
        if e.get("urgente"):
            urgentes.append(e)
        if cat == "INFORMATIVO":
            dia = (e.get("timestamp") or "")[:10]
            if dia:
                digest[dia] += 1

    for e in entries:
        e["color"] = CATEGORY_COLORS.get(e.get("categoria"), "#9ca3af")

    pending = []
    if client is not None:
        try:
            pend_raw = client.hgetall(PENDING_HASH)
            alertados = client.smembers(ALERTED_SET)
        except Exception:  # noqa: BLE001
            pend_raw, alertados = {}, set()

        now = datetime.datetime.utcnow()
        for thread_id, raw in pend_raw.items():
            try:
                data = json.loads(raw)
                since = datetime.datetime.fromisoformat(data["since"].rstrip("Z"))
                horas = round((now - since).total_seconds() / 3600, 1)
                pending.append({
                    "subject": data.get("subject", "(sin asunto)"),
                    "sender": data.get("sender", ""),
                    "horas": horas,
                    "alertado": thread_id in alertados,
                })
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
        pending.sort(key=lambda p: -p["horas"])

    return render_template_string(
        TEMPLATE,
        counts=dict(counts),
        recientes=entries[:40],
        urgentes=urgentes[:15],
        digest=dict(sorted(digest.items(), reverse=True)),
        pending=pending,
        now=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
