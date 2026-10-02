"""
generar_credenciales_render.py

Este script se corre UNA SOLA VEZ, en tu propia computadora (con navegador),
para generar las tres credenciales que necesita la versión desplegada en
Render (o cualquier servidor sin pantalla): GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN.

Por qué hace falta: Google exige que una persona real inicie sesión y dé
clic en "Permitir acceso" al menos una vez, en un navegador de verdad. Eso
no se puede hacer desde un servidor headless ni desde Claude. Este script
hace exactamente ese paso una sola vez y te entrega los tres valores listos
para copiar y pegar tú mismo en el panel de variables de entorno de Render
(Dashboard de Render -> tu cron job -> Environment). Nadie más que tú ve
estos valores — ni este script ni Claude los envían a ningún lado.

Uso:
    cd email_admon
    python scripts/generar_credenciales_render.py

Requisitos previos (iguales a los del README, sección 2):
    - credentials/client_secret.json ya descargado de Google Cloud Console
      (OAuth client ID tipo "Desktop app").
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

CLIENT_SECRETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "credentials", "client_secret.json"
)


def main() -> None:
    if not os.path.exists(CLIENT_SECRETS_PATH):
        print(f"No se encontró {CLIENT_SECRETS_PATH}.")
        print("Descárgalo desde Google Cloud Console (ver README.md, sección 2) y vuelve a correr este script.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\nListo. Copia estos tres valores al panel de variables de entorno")
    print("de Render (Dashboard -> tu cron job -> Environment) — NO los compartas")
    print("por chat ni los subas a ningún repositorio:\n")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print()


if __name__ == "__main__":
    main()
