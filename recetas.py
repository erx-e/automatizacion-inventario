from config import PLATOS_IGNORADOS
from difflib import SequenceMatcher
import re
import unicodedata


def normalizar_nombre(nombre: str) -> str:
    texto = unicodedata.normalize("NFKD", str(nombre).upper())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace("CAESAR", "CESAR")
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return " ".join(texto.split())


PLATOS_IGNORADOS_NORMALIZADOS = {
    normalizar_nombre(plato) for plato in PLATOS_IGNORADOS
}
PLATOS_IGNORADOS_SOLO_EXACTOS = {
    normalizar_nombre("ROLLITOS RELLENO"),
}
STOPWORDS_SIMILITUD = {"DE", "DEL", "LA", "EL", "Y", "CON"}
ENSALADA_CESAR = normalizar_nombre("ENSALADA CAESAR")
VARIANTES_ENSALADA_CESAR = {
    "POLLO": "POLLO",
    "CAMARON": "CAMARON",
    "FALAFEL": "FALAFEL",
}
UMBRAL_AMBIGUEDAD_VARIANTE = 0.03


def plato_ignorado(plato_neola: str) -> bool:
    plato_norm = normalizar_nombre(plato_neola)
    for omitido in PLATOS_IGNORADOS_NORMALIZADOS:
        if omitido in PLATOS_IGNORADOS_SOLO_EXACTOS:
            if plato_norm == omitido:
                return True
            continue

        if plato_norm == omitido or plato_norm.startswith(omitido) or omitido.startswith(plato_norm):
            return True

    return False


def es_ensalada_cesar_sin_proteina(plato_neola: str) -> bool:
    plato_norm = normalizar_nombre(plato_neola)
    if not plato_norm.startswith(ENSALADA_CESAR):
        return False
    return not any(keyword in plato_norm for keyword in VARIANTES_ENSALADA_CESAR)


def _seleccionar_variante_ensalada_cesar(plato_neola: str, recetas_plato: list[dict]) -> tuple[list[dict], list[str]]:
    plato_norm = normalizar_nombre(plato_neola)
    for keyword in VARIANTES_ENSALADA_CESAR:
        if keyword in plato_norm:
            matches = [
                receta for receta in recetas_plato
                if keyword in normalizar_nombre(receta.get("nombre_menu", ""))
            ]
            if matches:
                return matches, []

    matches = [
        receta for receta in recetas_plato
        if "POLLO" in normalizar_nombre(receta.get("nombre_menu", ""))
    ]
    if matches:
        return matches, [
            "ℹ️ ENSALADA CAESAR se toma por defecto como ENSALADA CÉSAR (POLLO)."
        ]

    return recetas_plato, []


def _agrupar_variantes_por_nombre_menu(recetas_plato: list[dict]) -> list[dict]:
    variantes = {}
    for receta in recetas_plato:
        nombre_menu = receta.get("nombre_menu", "").strip() or receta.get("plato", "").strip()
        clave = normalizar_nombre(nombre_menu)
        if clave not in variantes:
            variantes[clave] = {
                "nombre_menu": nombre_menu,
                "recetas": [],
            }
        variantes[clave]["recetas"].append(receta)

    return list(variantes.values())


def _firma_inventariable(recetas_plato: list[dict]) -> tuple[tuple[str, str, int, str, str], ...]:
    items = []
    for receta in recetas_plato:
        if not receta.get("sku") and not receta.get("insumo"):
            continue
        items.append((
            receta.get("sku", "").strip(),
            receta.get("insumo", "").strip(),
            int(receta.get("cantidad", 0) or 0),
            receta.get("unidad", "").strip(),
            receta.get("ubicacion", "").strip(),
        ))
    return tuple(sorted(items))


def resolver_variantes_receta(plato_neola: str, recetas_plato: list[dict]) -> tuple[list[dict], list[str]]:
    variantes = _agrupar_variantes_por_nombre_menu(recetas_plato)
    if len(variantes) <= 1 or es_ensalada_cesar_sin_proteina(plato_neola):
        return recetas_plato, []

    firmas = {_firma_inventariable(variante["recetas"]) for variante in variantes}
    if len(firmas) == 1:
        variantes.sort(
            key=lambda item: (
                len(normalizar_nombre(item["nombre_menu"])),
                item["nombre_menu"],
            )
        )
        return variantes[0]["recetas"], []

    scored = []
    for variante in variantes:
        nombre_menu = variante["nombre_menu"]
        score = max(
            _similitud_platos(plato_neola, nombre_menu),
            _similitud_platos(plato_neola, variante["recetas"][0].get("plato", "")),
        )
        scored.append({
            "score": score,
            "nombre_menu": nombre_menu,
            "recetas": variante["recetas"],
        })

    scored.sort(
        key=lambda item: (
            -item["score"],
            len(normalizar_nombre(item["nombre_menu"])),
            item["nombre_menu"],
        )
    )
    mejor = scored[0]
    segundo = scored[1] if len(scored) > 1 else None

    if segundo and abs(mejor["score"] - segundo["score"]) < UMBRAL_AMBIGUEDAD_VARIANTE:
        opciones = "', '".join(item["nombre_menu"] for item in scored[:3])
        return [], [
            f"❓ Varias recetas coinciden con '{plato_neola}': '{opciones}'. "
            "Confirma cuál corresponde antes de continuar."
        ]

    return mejor["recetas"], []


def _tokens_similares(nombre: str) -> list[str]:
    return [token for token in normalizar_nombre(nombre).split() if token not in STOPWORDS_SIMILITUD]


def _similitud_platos(origen: str, candidato: str) -> float:
    a = " ".join(_tokens_similares(origen))
    b = " ".join(_tokens_similares(candidato))
    if not a or not b:
        return 0.0

    ratio = SequenceMatcher(None, a, b).ratio()
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    prefix = 1.0 if a.startswith(b) or b.startswith(a) else 0.0
    return max(ratio, (ratio * 0.7) + (overlap * 0.3), prefix * 0.9)


def _nombres_neola_receta(receta: dict) -> list[str]:
    nombres = []
    vistos = set()

    for candidato in [receta.get("plato", ""), *(receta.get("nombres_neola") or [])]:
        nombre = str(candidato).strip()
        if not nombre:
            continue
        clave = normalizar_nombre(nombre)
        if clave in vistos:
            continue
        vistos.add(clave)
        nombres.append(nombre)

    return nombres


def sugerir_receta_similar(plato_neola: str, recetas: list[dict]) -> str | None:
    candidatos = {}
    for receta in recetas:
        plato = receta.get("plato", "").strip()
        if not plato:
            continue
        for candidato in _nombres_neola_receta(receta):
            clave = normalizar_nombre(candidato)
            if clave not in candidatos:
                candidatos[clave] = {
                    "candidato": candidato,
                    "canonico": plato,
                }

    mejor = None
    mejor_score = 0.0
    for datos in candidatos.values():
        score = _similitud_platos(plato_neola, datos["candidato"])
        if score > mejor_score:
            mejor = datos["canonico"]
            mejor_score = score

    if mejor and mejor_score >= 0.74:
        return mejor
    return None


def buscar_receta(plato_neola: str, recetas: list[dict]) -> list[dict]:
    plato_norm = normalizar_nombre(plato_neola)

    matches = [
        receta
        for receta in recetas
        if plato_norm in {normalizar_nombre(nombre) for nombre in _nombres_neola_receta(receta)}
    ]
    if matches:
        return matches

    return []


def calcular_consumo_teorico(ventas: list[dict], recetas: list[dict]) -> tuple[list[dict], list[str]]:
    consumo = []
    alertas = []

    for venta in ventas:
        plato = venta["plato"]
        cantidad_vendida = venta["cantidad"]

        if plato_ignorado(plato):
            continue

        recetas_plato = buscar_receta(plato, recetas)
        alertas_plato = []

        if recetas_plato:
            recetas_plato, alertas_plato = resolver_variantes_receta(plato, recetas_plato)
            alertas.extend(alertas_plato)

        if recetas_plato and es_ensalada_cesar_sin_proteina(plato):
            recetas_plato, alertas_plato = _seleccionar_variante_ensalada_cesar(plato, recetas_plato)
            alertas.extend(alertas_plato)

        if not recetas_plato:
            sugerencia = sugerir_receta_similar(plato, recetas)
            if sugerencia:
                alertas.append(
                    f"❓ Posible receta similar: en recetas existe '{sugerencia}', "
                    f"pero en el ticket sale '{plato}'. "
                    f"¿Cambio el nombre de la receta a '{plato}' y aplico esa receta?"
                )
            else:
                alertas.append(f"⚠️ Plato sin receta: {plato} x{cantidad_vendida}")
            continue

        for receta in recetas_plato:
            if not receta["sku"] and not receta["insumo"]:
                continue

            cantidad_total = cantidad_vendida * receta["cantidad"]

            consumo.append({
                "plato": plato,
                "cantidad_platos": cantidad_vendida,
                "insumo": receta["insumo"],
                "sku": receta["sku"],
                "cantidad_por_plato": receta["cantidad"],
                "cantidad_total": cantidad_total,
                "unidad": receta["unidad"],
                "ubicacion": receta["ubicacion"]
            })

    return consumo, alertas


def agrupar_consumo_por_insumo(consumo: list[dict]) -> dict:
    agrupado = {}
    for c in consumo:
        insumo = c["insumo"]
        if insumo not in agrupado:
            agrupado[insumo] = {
                "total": 0,
                "unidad": c["unidad"],
                "ubicacion": c["ubicacion"],
                "sku": c["sku"],
                "detalle": []
            }
        agrupado[insumo]["total"] += c["cantidad_total"]
        agrupado[insumo]["detalle"].append({
            "plato": c["plato"],
            "cant_platos": c["cantidad_platos"],
            "cant_insumo": c["cantidad_total"]
        })

    return agrupado
