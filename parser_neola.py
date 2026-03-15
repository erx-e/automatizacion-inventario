# parser_neola.py — Parsea la foto del cierre de caja usando Anthropic Claude
import base64
import json
import os
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

PROMPT_TICKET = """Analiza esta foto de un ticket de cierre de caja de un restaurante.
Extrae TODOS los platos de la sección VENTAS_ALIMENTOS.
Ignora únicamente postres (POSTRE CHEESECAK, POSTRE MINI BROW, etc.).
Incluye TODOS los demás items aunque tengan precio $0.00 — son ventas reales (cortesías, invitaciones, etc.).

IMPORTANTE: Si el mismo plato aparece varias veces en el ticket (con o sin precio), agrúpalos en una sola entrada sumando las cantidades y los precios. Usa siempre el mismo nombre truncado para el mismo plato — no generes variantes distintas del mismo nombre.

Devuelve ÚNICAMENTE un JSON array, sin texto adicional, sin markdown, sin backticks.
Cada elemento debe tener exactamente estos campos:
- "plato": el nombre tal como aparece en el ticket (texto truncado está bien, pero consistente para el mismo plato)
- "cantidad": número entero (suma si aparece varias veces)
- "precio_total": número decimal (suma de todos los precios del mismo plato)

Ejemplo de formato esperado:
[{"plato": "HAMBURGUESA GOLD", "cantidad": 3, "precio_total": 29.97}]"""

TIMEOUT_CLAUDE_SECONDS = 60


def get_client():
    api_key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en config.py o como variable de entorno")
    return Anthropic(api_key=api_key)


def _parsear_respuesta(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"No se recibió JSON válido: {e}\nRespuesta: {text}")


def _extraer_texto_respuesta(response) -> str:
    bloques = getattr(response, "content", None) or []
    textos = [
        getattr(bloque, "text", "")
        for bloque in bloques
        if getattr(bloque, "type", None) == "text"
    ]
    text = "".join(textos).strip()
    if not text:
        raise ValueError("Claude no devolvió texto utilizable")
    return text


def _crear_mensaje_ticket(client, image_data: str, media_type: str):
    return client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        timeout=TIMEOUT_CLAUDE_SECONDS,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data
                    }
                },
                {"type": "text", "text": PROMPT_TICKET}
            ]
        }]
    )


def parsear_foto_ticket(image_path: str) -> list[dict]:
    """Parsea una foto del ticket desde un archivo local."""
    client = get_client()

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    ext = image_path.lower().split(".")[-1]
    media_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = media_types.get(ext, "image/jpeg")

    response = _crear_mensaje_ticket(client, image_data, media_type)
    return _parsear_respuesta(_extraer_texto_respuesta(response))


def parsear_foto_bytes(image_bytes: bytes, media_type: str = "image/jpeg") -> list[dict]:
    """Parsea una foto del ticket desde bytes (para integración con OpenClaw)."""
    client = get_client()
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = _crear_mensaje_ticket(client, image_data, media_type)
    return _parsear_respuesta(_extraer_texto_respuesta(response))
