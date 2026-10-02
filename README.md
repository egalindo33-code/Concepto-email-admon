# Email Admon (CONCEPTO) — versión standalone

> Código entregado el 2026-10-02 junto con `concepto-email-admon-especificacion-tecnica.md`
> en el proyecto de Claude. Este código es el punto de partida real para un
> desarrollador — no un pseudocódigo ni un boceto.

Programa independiente que replica la lógica de "Mail Admon" validada en
Claude (ver `especificacion-mail-admon.md` y `directrices-mail-admon.md` en
el proyecto de Claude), pero corriendo como software propio, sin depender de
ninguna suscripción de chat de IA — solo de una clave de API que se paga por
uso.

**Importante — quién tiene que hacer qué:** este código lo puede desplegar y
mantener cualquier desarrollador (o tú mismo, si te animas). Claude no puede
correrlo ni hospedarlo de forma permanente — eso requiere una máquina/servidor
que tú controles, encendida y accesible todo el tiempo.

## Qué hace

1. Revisa la bandeja de `egalindo@truelinetllc.com` (o la cuenta que
   configures) buscando correos nuevos sin procesar.
2. Clasifica cada uno en A1 (basura sin relación al giro → elimina y
   registra), A2 (spam de proveedores del giro → sugiere, no elimina), B
   (rutinario → responde directo, **apagado por default**), o C (requiere
   decisión humana → deja un borrador en Gmail para tu aprobación).
3. Detecta urgencias (accidente, unidad varada, amenaza, plazo legal) y
   manda una alerta por correo de inmediato, sin esperar a que revises nada.
4. Responde siempre con el remitente correcto (dispatch@, accounting@, etc.
   según a cuál llegó el correo original) — usa los alias "Send mail as" que
   ya configuraste manualmente en Gmail.

## 1. Requisitos previos

- Python 3.10 o superior.
- Una cuenta de Google Cloud (gratis) para crear credenciales de la API de
  Gmail.
- Una clave de API de Anthropic ([console.anthropic.com](https://console.anthropic.com))
  — se paga por uso, no es una suscripción. Ver estimado de costo abajo.
- Que ya tengas configurado en Gmail (Configuración → Cuentas e Importación)
  los 5 alias de "Send mail as" y la opción "Reply from the same address the
  message was sent to" — **esto ya quedó hecho hoy**, no hay que repetirlo.
- Un servidor o máquina que puedas dejar prendida (o un servicio en la nube)
  para que el cron corra periódicamente. No sirve tu laptop si la apagas.

## 2. Crear credenciales de Gmail API

1. Ve a [console.cloud.google.com](https://console.cloud.google.com) y crea
   un proyecto nuevo (o usa uno existente).
2. En "APIs & Services" → "Library", busca "Gmail API" y actívala.
3. En "APIs & Services" → "OAuth consent screen": configúralo como "Internal"
   si tu Workspace lo permite (más simple), o "External" + modo de prueba.
4. En "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth
   client ID" → tipo de aplicación **"Desktop app"**.
5. Descarga el JSON generado y guárdalo como:
   `email_admon/credentials/client_secret.json`

## 3. Instalar y configurar

```bash
cd email_admon
python3 -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edita .env y pon tu ANTHROPIC_API_KEY real, y los datos de notificación si quieres alertas por correo.
```

Revisa y ajusta `config/directrices.yaml` — ahí están las reglas de
negocio (dominios conocidos, palabras que fuerzan categoría C, palabras de
urgencia, si categoría B ya puede auto-enviarse, etc.). Puedes editarlo sin
tocar código, igual que hacíamos con las directrices dentro de Claude.

## 4. Primera corrida (autorización)

```bash
python src/main.py
```

La primera vez va a abrir una ventana de navegador pidiéndote iniciar sesión
con **egalindo@truelinetllc.com** y autorizar el acceso. Esto genera
`credentials/token.json`, que se reutiliza automáticamente después (se
renueva solo, no hay que volver a iniciar sesión cada vez).

> Si vas a correr esto en un servidor sin pantalla (headless), hay que hacer
> esta primera autorización desde una máquina con navegador y luego copiar
> el `token.json` generado al servidor — o usar el flujo de "Service Account
> con Domain-Wide Delegation" de Google Workspace, que es más robusto para
> producción pero requiere que un admin de Workspace lo habilite. Pregúntale
> a tu desarrollador cuál conviene más para tu caso.

## 5. Programarlo para que corra solo

Ejemplo de cron (Linux/Mac), cada hora:

```cron
0 * * * * cd /ruta/a/email_admon && venv/bin/python src/main.py >> log/run.log 2>&1
```

En Windows, usa el Programador de Tareas apuntando al mismo comando. Si lo
hospedas en la nube, puedes usar Google Cloud Scheduler + Cloud Run, o un
cron normal dentro de un VPS (DigitalOcean, Linode, etc.).

## 6. Dónde hospedarlo

Cualquiera de estas opciones funciona; la diferencia es costo y qué tanto
mantenimiento quieres hacer tú mismo:

- **VPS pequeño** (DigitalOcean, Linode, Vultr): ~$5–12 USD/mes, tú instalas
  todo y configuras el cron. Control total, requiere un poco de
  mantenimiento técnico.
- **Google Cloud Run + Cloud Scheduler**: pago por ejecución (normalmente
  centavos al mes a este volumen), menos mantenimiento, pero requiere
  adaptar `main.py` para correr como función en vez de script de cron
  (cambio menor).
- **Una computadora de la oficina que se quede prendida**: $0 extra de
  hosting, pero depende de que esa máquina esté encendida y conectada a
  internet todo el tiempo.

## 7. Desplegarlo en Render (vía el conector de Render en Claude)

Esta es la ruta que se siguió el 2026-10-02: en vez de un VPS manual, Render
corre el cron job por ti, y Claude puede crear y configurar el servicio
directamente (conectando el conector de Render en la conversación). Pasos:

1. **Sube este código a un repositorio de GitHub público.** Tiene que ser
   público porque la herramienta de Render usada aquí no pasa por la
   integración nativa de GitHub de Render (que sí soporta repos privados,
   pero esa conexión se hace desde el dashboard de Render, no desde Claude).
   Ningún secreto va en el repo — `.env`, `client_secret.json` y
   `token.json` ya están en `.gitignore`.
2. **Genera las credenciales headless, una sola vez, en tu propia
   computadora** (requiere navegador — no se puede hacer desde Render ni
   desde Claude):
   ```bash
   cd email_admon
   python scripts/generar_credenciales_render.py
   ```
   Esto te va a pedir iniciar sesión con `egalindo@truelinetllc.com` y dar
   clic en "Permitir acceso", igual que la primera corrida local. Al final
   te imprime tres valores: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REFRESH_TOKEN`.
3. **Dale a Claude la URL del repositorio público.** Con eso, Claude crea el
   cron job en Render (nombre, horario, runtime Python, comando de build
   `pip install -r requirements.txt`, comando de arranque
   `python src/main.py`).
4. **Tú agregas las variables de entorno directamente en el panel de
   Render** (Dashboard → tu cron job → Environment) — Claude no debe
   escribir claves ni tokens por ti, por seguridad:
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (del
     paso 2)
   - `ANTHROPIC_API_KEY` (de console.anthropic.com)
   - `ANTHROPIC_MODEL` (ej. `claude-haiku-4-5-20251001`)
   - `NOTIFY_CHANNEL` (`email` o `ninguno`) — la alerta de urgencia se manda
     con la misma conexión de Gmail del bot, no requiere SMTP ni contraseña
     de aplicación (útil en Google Workspace, donde el administrador suele
     tenerlas deshabilitadas).
5. **Dispara el primer deploy** y revisa los logs desde el dashboard de
   Render (o pídele a Claude que los revise con el conector).

Nota sobre el log de auditoría: en Render, el disco del cron job es
efímero — no sobrevive entre corridas. `main.py` ya manda cada decisión
también a stdout (`LOG_ACCION {...}`), así que el historial completo queda
en los logs de Render, consultables en cualquier momento.

## 7.1. Panel web (opcional) — ver el resumen sin entrar a Gmail ni a Render

Agregado el 2026-10-02. Es una páginita de solo lectura que muestra lo que
el bot procesó, qué quedó pendiente de tu revisión y las urgencias
recientes — para consultarla desde cualquier computadora o celular, sin
pedírselo a Claude y sin entrar al dashboard de Render.

Son dos piezas nuevas en Render (ambas caben en el plan gratis):

1. **Key Value (Redis)**: el almacenamiento donde el bot escribe su
   actividad reciente. Dale la variable `REDIS_URL` resultante tanto al
   cron job (`crn-davk8o49v7es7384p370`) como al servicio del panel.
2. **Web Service** apuntando a `dashboard/app.py` de este mismo
   repositorio:
   - Build command: `pip install -r dashboard/requirements.txt`
   - Start command: `cd dashboard && gunicorn app:app --bind 0.0.0.0:$PORT`
   - Variables de entorno: `REDIS_URL` (la misma del paso 1), y
     opcionalmente `DASHBOARD_USER` / `DASHBOARD_PASS` si quieres que la
     página pida usuario y contraseña (recomendado si vas a compartir el
     link o si contiene información que prefieres no dejar abierta a quien
     tenga la URL).

El plan gratis de Render "duerme" el servicio tras un rato sin visitas —
la primera vez que abres el panel después de un rato puede tardar unos
30-40 segundos en cargar mientras despierta. Si eso te molesta, se puede
subir a un plan de pago barato (~$7 USD/mes) para que esté siempre
despierto.

También agregado el mismo día: un seguimiento de "borrador pendiente"
(`sla_horas_borrador_pendiente` en `config/directrices.yaml`, 24 horas por
default) que manda un único aviso de seguimiento si dejaste un borrador sin
revisar por más de ese tiempo; y que los borradores ahora usan como
referencia el tono de tus correos ya enviados desde ese mismo alias, para
sonar más parecidos a como tú escribes.

## 8. Costos esperados (aproximados)

- **Gmail API:** sin costo al volumen de uso de una sola cuenta de correo.
- **API de Anthropic:** con `claude-haiku-4-5-20251001` (configurado por default),
  aproximadamente $1 por millón de tokens de entrada y $5 por millón de
  salida. Procesar un correo típico (leer + clasificar + redactar un
  borrador) usa aproximadamente 1,000–3,000 tokens — para el volumen de
  correos de esta cuenta, el costo mensual debería ser de unos cuantos
  dólares, no decenas.
- **Hosting:** ver sección 6.

## 9. Seguridad

- Nunca compartas ni subas a un repositorio público: `.env`,
  `credentials/client_secret.json`, ni `credentials/token.json`. El
  `.gitignore` incluido ya los excluye.
- El log de acciones (`log/acciones.jsonl`) registra cada decisión tomada
  (remitente, asunto, categoría, qué se hizo) — úsalo para auditar el
  comportamiento del bot con el tiempo.

## 10. Estructura del proyecto

```
email_admon/
├── config/
│   └── directrices.yaml      # reglas de negocio editables (sin tocar código)
├── credentials/               # client_secret.json y token.json van aquí (no incluidos)
├── dashboard/
│   ├── app.py                  # panel web de solo lectura (ver sección 7.1)
│   └── requirements.txt
├── log/                        # se genera solo: acciones.jsonl, run.log
├── scripts/
│   └── generar_credenciales_render.py  # genera credenciales headless (una vez, local)
├── src/
│   ├── gmail_client.py        # todo lo que toca la API de Gmail
│   ├── classifier.py          # clasificación + redacción vía API de Anthropic
│   ├── notify.py               # alerta de correos urgentes y de seguimiento (SLA)
│   ├── store.py                 # almacenamiento del panel + seguimiento de SLA (Redis)
│   └── main.py                  # orquestador, punto de entrada
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md                    # este archivo
```

## 11. Diferencias importantes vs. la versión que corre dentro de Claude

- Aquí, **categoría B (auto-envío) viene apagada por default** en
  `directrices.yaml` — igual que como quedó la versión de Claude hoy.
  Actívala (`categoria_b_auto_envio_activa: true`) solo cuando confíes en la
  clasificación tras varios ciclos de borradores revisados.
- Ajustar una regla aquí significa editar `config/directrices.yaml` (texto
  plano) y nada más — no hace falta redeploy ni tocar el código de
  `classifier.py`, salvo que quieras cambiar la lógica de fondo.
- El registro de qué se eliminó/respondió vive en `log/acciones.jsonl`
  (permanente, no depende de los 30 días de la papelera de Gmail como en la
  versión de Claude).
