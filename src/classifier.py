"""
classifier.py

Motor de clasificación de Email Admon, en dos capas:

1. Reglas simples (sin costo de IA): dominio del remitente contra las listas
   de directrices.yaml -> resuelve A1/A2 obvios sin llamar al modelo.
2. Modelo de lenguaje (API de Anthropic) para todo lo demás: decide la
   categoría (A1/A2/B/C), si es urgente, y redacta el texto del borrador
   o de la sugerencia corta cuando aplica.

Esto replica exactamente el marco que se validó manualmente en Claude/Cowork
(especificacion-mail-admon.md y directrices-mail-admon.md), pero corriendo
como llamadas de API normales, pagadas por uso.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from gmail_client import EmailThread

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """Eres el motor de clasificación de "Email Admon", un bot de \
administración de correo para Trueline Trucking. Tu única tarea es clasificar \
UN correo a la vez y, si aplica, redactar el texto de respuesta. Sigue estas \
reglas exactamente:

CATEGORÍAS:
- A1: oferta/promoción SIN relación con el giro de transporte (ej. software \
genérico, cursos en línea, antivirus). Acción: eliminar + registrar.
- A2: spam o prospección de un proveedor SÍ relacionado con transporte \
(telemática, seguros de flotilla, factoring, refacciones). Acción: sugerir \
eliminar o responder corto, nunca eliminar solo.
- B: rutinario, auto-respondible. Sin tarifas, fechas, compromisos ni \
decisiones de negocio de por medio. Acción: responder y enviar directo.
- C: requiere elaboración o consenso humano (tarifas, dinero, fechas de \
entrega o de una cita/llamada, compromisos, quejas, cliente/bróker nuevo, \
temas legales/contractuales, o decisiones operativas con gasto o seguridad \
como mantenimiento de flotilla o documentación DOT). Acción: redactar \
borrador para aprobación humana, nunca enviarlo solo.
- INFORMATIVO: notificaciones automáticas (códigos de verificación, alertas \
de seguridad, reportes de cuenta) que no requieren ninguna respuesta.

REGLAS ADICIONALES:
- Una fecha o compromiso de agendar algo SIEMPRE fuerza C, incluso si el \
correo también parece prospección comercial.
- Si el destinatario principal (To) no es la cuenta monitoreada y esta solo \
va en copia (Cc), trátalo como INFORMATIVO salvo que sea urgente.
- Marca "urgente": true SOLO si hay señales de una emergencia operativa real \
de transporte: accidente, unidad o tráiler varado, amenaza/disputa seria con \
cliente o bróker, o un plazo legal/DOT inminente (ej. una cita o inspección \
ya programada para hoy/mañana).
- NO marques "urgente" por notificaciones automáticas de administración de \
cuentas: confirmaciones de alias de "send mail as", alertas de seguridad de \
Google/Workspace (passkeys, verificación en dos pasos, inicio de sesión), \
códigos de verificación, o avisos de proveedores de software. Esas son \
rutinarias aunque el texto mencione "seguridad" o "verificar de inmediato" \
— esa urgencia es sobre la cuenta de Google, no sobre la operación de \
transporte.
- Responde SIEMPRE en el mismo idioma en que está escrito el correo original.
- Nunca inventes datos (fechas, montos, nombres) que no estén en el correo.

Responde ÚNICAMENTE con un JSON válido, sin texto adicional, con esta forma \
exacta:
{
  "categoria": "A1" | "A2" | "B" | "C" | "INFORMATIVO",
  "urgente": true | false,
  "razonamiento": "explicación breve de por qué, en 1-2 frases",
  "asunto_respuesta": "string o null si no aplica",
  "cuerpo_respuesta": "string o null si no aplica (borrador para C, texto corto sugerido para A2, respuesta para B)"
}
"""


@dataclass
class Classification:
    categoria: str
    urgente: bool
    razonamiento: str
    asunto_respuesta: Optional[str] = None
    cuerpo_respuesta: Optional[str] = None


def _rule_based_prefilter(thread: EmailThread, directrices: dict) -> Optional[Classification]:
    sender_lower = thread.sender.lower()

    for domain in directrices.get("dominios_sin_relacion_transporte", []):
        if domain.lower() in sender_lower:
            return Classification(
                categoria="A1",
                urgente=False,
                razonamiento=f"Remitente coincide con dominio sin relación al giro ({domain}) — regla directa, sin IA.",
            )

    # Nota: los dominios "sí relacionados" (A2) NO se resuelven aquí directo,
    # porque todavía hay que revisar si el contenido fuerza C (p.ej. una fecha
    # de llamada) — eso requiere leer el cuerpo, así que pasa al modelo.
    return None


def _check_urgent_keywords(thread: EmailThread, directrices: dict) -> bool:
    text = f"{thread.subject} {thread.plaintext_body}".lower()
    return any(keyword.lower() in text for keyword in directrices.get("disparadores_urgencia", []))


def _parse_json_response(raw_text: str) -> Optional[dict]:
    """
    Intenta interpretar la respuesta del modelo como JSON, tolerando que
    venga envuelta en un bloque de markdown (```json ... ``` o ``` ... ```)
    o con texto extra alrededor — algo común en los modelos de lenguaje
    aunque el system prompt pida responder ÚNICAMENTE con JSON.

    Prueba, en orden: el texto tal cual, el contenido dentro de un bloque
    de markdown si existe, y el primer bloque {...} que se pueda extraer
    con una expresión regular. Devuelve None solo si ninguno de esos
    intentos produce JSON válido.
    """
    candidates = [raw_text]

    fence_match = re.search(r"```(?:json)?\s*(.*?)```", raw_text, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1).strip())

    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0).strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def classify(
    thread: EmailThread,
    directrices: dict,
    client: anthropic.Anthropic,
    estilo_previo: Optional[list[str]] = None,
) -> Classification:
    prefiltered = _rule_based_prefilter(thread, directrices)
    if prefiltered:
        return prefiltered

    estilo_hint = ""
    if estilo_previo:
        ejemplos = "\n---\n".join(estilo_previo)
        estilo_hint = (
            f"\n\nEstilo de referencia (correos ya enviados antes desde esta "
            f"misma cuenta — usa un tono/firma parecido si redactas un "
            f"borrador o respuesta, pero nunca copies datos de estos "
            f"ejemplos):\n{ejemplos}"
        )

    user_content = (
        f"Remitente: {thread.sender}\n"
        f"Para: {', '.join(thread.to_recipients)}\n"
        f"Cuenta original a la que llegó: {thread.original_account}\n"
        f"Asunto: {thread.subject}\n\n"
        f"Cuerpo:\n{thread.plaintext_body}\n\n"
        f"Dominios relacionados con el giro de transporte (para A2): "
        f"{', '.join(directrices.get('dominios_relacionados_transporte', []))}\n"
        f"Disparadores de categoría C ya conocidos: "
        f"{', '.join(directrices.get('disparadores_categoria_c', []))}"
        f"{estilo_hint}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw_text = response.content[0].text.strip()
    data = _parse_json_response(raw_text)
    if data is None:
        # Si el modelo no devolvió JSON interpretable de ninguna forma, se
        # marca para revisión manual en vez de arriesgar una mala
        # clasificación silenciosa.
        return Classification(
            categoria="C",
            urgente=True,
            razonamiento="No se pudo interpretar la respuesta del modelo — se marca para revisión manual por seguridad.",
        )

    result = Classification(
        categoria=data.get("categoria", "C"),
        urgente=bool(data.get("urgente", False)),
        razonamiento=data.get("razonamiento", ""),
        asunto_respuesta=data.get("asunto_respuesta"),
        cuerpo_respuesta=data.get("cuerpo_respuesta"),
    )

    # Capa extra de seguridad, independiente del modelo: si hay palabras de
    # urgencia explícitas por keyword, se respeta aunque el modelo no la marcara.
    if _check_urgent_keywords(thread, directrices):
        result.urgente = True

    return result
