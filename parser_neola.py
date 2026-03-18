# parser_neola.py — Parsea la foto del cierre de caja usando Anthropic Claude
import base64
import json
import os
import re
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

PROMPT_TICKET = """Analiza esta foto de un ticket de cierre de caja de un restaurante.
Extrae TODOS los platos de la sección VENTAS_ALIMENTOS.
Ignora únicamente postres (POSTRE CHEESECAK, POSTRE MINI BROW, etc.).
Incluye TODOS los demás items aunque tengan precio $0.00 — son ventas reales (cortesías, invitaciones, etc.).

IMPORTANTE: Si el mismo plato aparece varias veces en el ticket (con o sin precio), agrúpalos en una sola entrada sumando las cantidades y los precios. Usa siempre el mismo nombre truncado para el mismo plato — no generes variantes distintas del mismo nombre.

Si alguna línea está borrosa, tapada, ilegible o no estás seguro del nombre del plato, antes del JSON puedes poner:
- una línea opcional con este formato exacto: OBSERVACION: <frase corta en español, sin lenguaje técnico>
- una línea opcional con este formato exacto: DUDOSOS: <nombre o fragmento 1> | <nombre o fragmento 2>
Usa DUDOSOS solo para nombres que no logras leer bien o que tuviste que omitir por falta de claridad.
Si hay líneas tapadas por trazos o marcas y no se pueden leer, no inventes esos platos: ignóralos y sigue con el resto del ticket.
Eso NO debe impedir el análisis del precierre; simplemente reporta la observación y continúa.
Fuera de esas líneas opcionales, no agregues texto adicional, markdown ni backticks.
Devuelve el resultado como un JSON array.
Cada elemento debe tener exactamente estos campos:
- "plato": el nombre tal como aparece en el ticket (texto truncado está bien, pero consistente para el mismo plato)
- "cantidad": número entero (suma si aparece varias veces)
- "precio_total": número decimal (suma de todos los precios del mismo plato)

Ejemplo de formato esperado:
[{"plato": "HAMBURGUESA GOLD", "cantidad": 3, "precio_total": 29.97}]"""

TIMEOUT_CLAUDE_SECONDS = 60
_ULTIMAS_OBSERVACIONES_LECTURA = []
_ULTIMO_DIAGNOSTICO_LECTURA = {
    "problema": "",
    "platos_dudosos": [],
    "requiere_aclaracion": False,
    "mensajes": [],
}


def get_client():
    api_key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en config.py o como variable de entorno")
    return Anthropic(api_key=api_key)


def obtener_observaciones_lectura() -> list[str]:
    return list(_ULTIMAS_OBSERVACIONES_LECTURA)


def obtener_diagnostico_lectura() -> dict:
    return {
        "problema": _ULTIMO_DIAGNOSTICO_LECTURA["problema"],
        "platos_dudosos": list(_ULTIMO_DIAGNOSTICO_LECTURA["platos_dudosos"]),
        "requiere_aclaracion": bool(_ULTIMO_DIAGNOSTICO_LECTURA["requiere_aclaracion"]),
        "mensajes": list(_ULTIMO_DIAGNOSTICO_LECTURA["mensajes"]),
    }


def _texto_previo_a_json(text: str) -> str:
    texto = str(text or "").strip()
    if not texto:
        return ""

    texto_sin_fence = texto
    if texto_sin_fence.startswith("```"):
        texto_sin_fence = texto_sin_fence.split("\n", 1)[1] if "\n" in texto_sin_fence else texto_sin_fence[3:]
    if texto_sin_fence.endswith("```"):
        texto_sin_fence = texto_sin_fence[:-3]

    inicio_json = texto_sin_fence.find("[")
    if inicio_json <= 0:
        return ""

    prefijo = texto_sin_fence[:inicio_json].strip()
    return prefijo


def _extraer_items_dudosos(text: str) -> list[str]:
    valor = str(text or "").strip()
    if not valor:
        return []

    partes = re.split(r"[|;]", valor)
    if len(partes) == 1:
        partes = re.split(r",\s*", valor)

    items = []
    for parte in partes:
        item = re.sub(r"\s+", " ", parte).strip(" ,-•")
        if item:
            items.append(item)
    return items


def _extraer_diagnostico_lectura(text: str) -> dict:
    prefijo = _texto_previo_a_json(text)
    if not prefijo:
        return {
            "problema": "",
            "platos_dudosos": [],
            "requiere_aclaracion": False,
            "mensajes": [],
        }

    problema = ""
    platos_dudosos = []
    for linea in prefijo.splitlines():
        linea_limpia = re.sub(r"\s+", " ", linea).strip()
        if not linea_limpia:
            continue

        observacion = re.match(r"(?i)^observacion\s*:\s*(.+)$", linea_limpia)
        if observacion:
            problema = observacion.group(1).strip(" :-")
            continue

        dudosos = re.match(r"(?i)^dudosos?\s*:\s*(.+)$", linea_limpia)
        if dudosos:
            platos_dudosos.extend(_extraer_items_dudosos(dudosos.group(1)))

    if not problema:
        prefijo_limpio = re.sub(r"\s+", " ", prefijo).strip(" :-")
        marcadores = (
            "borros",
            "blurry",
            "cubiert",
            "tapad",
            "ilegible",
            "no se nota",
            "no se lee",
            "no se distingue",
            "no estoy seguro",
            "parcialmente",
            "difícil de leer",
            "dificil de leer",
            "marcas azules",
        )
        if any(marker in prefijo_limpio.lower() for marker in marcadores):
            problema = prefijo_limpio

    platos_dudosos_unicos = []
    for plato in platos_dudosos:
        if plato not in platos_dudosos_unicos:
            platos_dudosos_unicos.append(plato)

    requiere_aclaracion = bool(platos_dudosos_unicos)
    texto_problema = problema.lower()
    if any(marker in texto_problema for marker in (
        "omito",
        "omit",
        "no puedo determinar",
        "no logro identificar",
        "falt",
        "ilegible",
        "no se lee",
        "no se distingue",
    )):
        requiere_aclaracion = True

    mensajes = [problema] if problema else []
    return {
        "problema": problema,
        "platos_dudosos": platos_dudosos_unicos,
        "requiere_aclaracion": requiere_aclaracion,
        "mensajes": mensajes,
    }


def _extraer_observaciones_lectura(text: str) -> list[str]:
    return _extraer_diagnostico_lectura(text)["mensajes"]


def _parsear_respuesta(text: str) -> list[dict]:
    original = text.strip()
    candidatos = [original]

    fenced = original
    if fenced.startswith("```"):
        fenced = fenced.split("\n", 1)[1] if "\n" in fenced else fenced[3:]
    if fenced.endswith("```"):
        fenced = fenced[:-3]
    fenced = fenced.strip()
    if fenced != original:
        candidatos.append(fenced)

    inicio = fenced.find("[")
    fin = fenced.rfind("]")
    if inicio != -1 and fin != -1 and fin > inicio:
        embebido = fenced[inicio:fin + 1].strip()
        if embebido not in candidatos:
            candidatos.append(embebido)

    ultimo_error = None
    for candidato in candidatos:
        try:
            return json.loads(candidato)
        except json.JSONDecodeError as e:
            ultimo_error = e

    raise ValueError(f"No se recibió JSON válido: {ultimo_error}\nRespuesta: {original}")


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
        temperature=0,
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
    global _ULTIMAS_OBSERVACIONES_LECTURA, _ULTIMO_DIAGNOSTICO_LECTURA
    client = get_client()

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    ext = image_path.lower().split(".")[-1]
    media_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = media_types.get(ext, "image/jpeg")

    response = _crear_mensaje_ticket(client, image_data, media_type)
    text = _extraer_texto_respuesta(response)
    _ULTIMO_DIAGNOSTICO_LECTURA = _extraer_diagnostico_lectura(text)
    _ULTIMAS_OBSERVACIONES_LECTURA = list(_ULTIMO_DIAGNOSTICO_LECTURA["mensajes"])
    return _parsear_respuesta(text)


def parsear_foto_bytes(image_bytes: bytes, media_type: str = "image/jpeg") -> list[dict]:
    """Parsea una foto del ticket desde bytes (para integración con OpenClaw)."""
    global _ULTIMAS_OBSERVACIONES_LECTURA, _ULTIMO_DIAGNOSTICO_LECTURA
    client = get_client()
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = _crear_mensaje_ticket(client, image_data, media_type)
    text = _extraer_texto_respuesta(response)
    _ULTIMO_DIAGNOSTICO_LECTURA = _extraer_diagnostico_lectura(text)
    _ULTIMAS_OBSERVACIONES_LECTURA = list(_ULTIMO_DIAGNOSTICO_LECTURA["mensajes"])
    return _parsear_respuesta(text)
