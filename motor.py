# motor.py — Motor de Cierre Diario de Inventario Sambó
import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import UMBRAL_DESCUADRE
from parser_neola import (
    obtener_diagnostico_lectura,
    parsear_foto_ticket,
    parsear_foto_bytes,
)
from recetas import (
    agrupar_consumo_por_insumo,
    buscar_receta,
    calcular_consumo_teorico,
    es_ensalada_cesar_sin_proteina,
    normalizar_nombre,
    plato_ignorado,
    resolver_variantes_receta,
    sugerir_receta_similar,
)
from sheets_connector import (
    leer_recetas, leer_ubicacion_descuento,
    leer_registros_dia_completo, escribir_ventas_neola,
    escribir_inventario_dia, leer_diferencias_inventario_dia,
    corregir_inventario_insumos, verificar_inventario_dia_existe,
    leer_ventas_neola_dia, leer_insumos_registro_dia_completo,
)

# Zona horaria de Ecuador (UTC-5)
TZ_ECUADOR = timezone(timedelta(hours=-5))

# Carpeta donde se guardan las imágenes de cierre
CIERRES_DIR = Path(__file__).parent / "cierres-diarios"
ROLLITOS_AMBIGUO = "ROLLITOS RELLENO"
ROLLITOS_POLLO = "ROLLITOS RELLENO POLLO"
ROLLITOS_QUESO = "ROLLITOS RELLENO QUESO"
ROLLITOS_REGISTRO_C2 = {
    "pollo": "CREPE POLLO 2 unid",
    "queso": "CREPE QUESO 2 unid",
}


def _fecha_a_carpeta(fecha_iso: str) -> str:
    """Convierte '2026-03-12' a '12-03-2026' (dd-mm-yyyy)."""
    partes = fecha_iso.split("-")
    return f"{partes[2]}-{partes[1]}-{partes[0]}"


def _guardar_imagen_cierre(fecha: str, image_path: str = None, image_bytes: bytes = None):
    """
    Guarda la imagen del cierre en cierres-diarios/{dd-mm-yyyy}/{dd-mm-yyyy}.ext
    """
    nombre_carpeta = _fecha_a_carpeta(fecha)
    carpeta = CIERRES_DIR / nombre_carpeta
    carpeta.mkdir(parents=True, exist_ok=True)

    if image_path:
        ext = Path(image_path).suffix or ".jpg"
        destino = carpeta / f"{nombre_carpeta}{ext}"
        shutil.copy2(image_path, destino)
    elif image_bytes:
        destino = carpeta / f"{nombre_carpeta}.jpg"
        with open(destino, "wb") as f:
            f.write(image_bytes)


def _guardar_historial_cierre(fecha: str, ventas: list[dict],
                               consumo_agrupado: dict,
                               diferencias: dict | None = None,
                               metadata: dict | None = None):
    """Guarda un JSON con el resumen del cierre en cierres-diarios/{dd-mm-yyyy}/."""
    nombre_carpeta = _fecha_a_carpeta(fecha)
    carpeta = CIERRES_DIR / nombre_carpeta
    carpeta.mkdir(parents=True, exist_ok=True)

    historial = {
        "fecha": fecha,
        "timestamp": datetime.now(TZ_ECUADOR).isoformat(),
        "ventas": ventas,
        "consumo_agrupado": {
            insumo: {
                "total": datos["total"],
                "unidad": datos["unidad"],
                "ubicacion": datos["ubicacion"],
            }
            for insumo, datos in consumo_agrupado.items()
        },
        "diferencias": diferencias,
        "metadata": metadata or {},
    }

    destino = carpeta / f"{nombre_carpeta}.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)


def _leer_historial_cierre(fecha: str) -> dict | None:
    nombre_carpeta = _fecha_a_carpeta(fecha)
    ruta = CIERRES_DIR / nombre_carpeta / f"{nombre_carpeta}.json"
    if not ruta.exists():
        return None

    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def fecha_ecuador():
    return datetime.now(TZ_ECUADOR).strftime("%Y-%m-%d")


def _normalizar_nombre(nombre: str) -> str:
    return nombre.upper().strip().replace("  ", " ")


def _es_rollitos_ambiguo(plato: str) -> bool:
    plato_norm = _normalizar_nombre(plato)
    return plato_norm.startswith(ROLLITOS_AMBIGUO) and "POLLO" not in plato_norm and "QUESO" not in plato_norm


def _cantidad_rollitos_ambiguos(ventas: list[dict]) -> int:
    return sum(venta["cantidad"] for venta in ventas if _es_rollitos_ambiguo(venta["plato"]))


def _normalizar_rollitos_override(rollitos_override: dict | None) -> dict[str, int] | None:
    if rollitos_override is None:
        return None

    override = {
        "pollo": int(rollitos_override.get("pollo", 0) or 0),
        "queso": int(rollitos_override.get("queso", 0) or 0),
    }
    if override["pollo"] < 0 or override["queso"] < 0:
        raise ValueError("Las cantidades de rollitos no pueden ser negativas")
    return override


def _distribuir_precio_rollitos(total_precio: float, cantidades: dict[str, int]) -> dict[str, float]:
    total_unidades = sum(cantidades.values())
    if total_unidades <= 0:
        return {"pollo": 0.0, "queso": 0.0}

    precio_unitario = total_precio / total_unidades
    precio_pollo = round(precio_unitario * cantidades["pollo"], 2)
    precio_queso = round(total_precio - precio_pollo, 2)
    return {
        "pollo": precio_pollo,
        "queso": precio_queso,
    }


def _resolver_rollitos_rellenos(ventas: list[dict], fecha: str,
                                registros: dict | None = None,
                                rollitos_override: dict | None = None,
                                permitir_pendiente: bool = False) -> tuple[list[dict], list[str], bool]:
    cantidad_ambigua = _cantidad_rollitos_ambiguos(ventas)
    if not cantidad_ambigua:
        return list(ventas), [], False

    override = _normalizar_rollitos_override(rollitos_override)
    if override is not None:
        total_override = override["pollo"] + override["queso"]
        if total_override != cantidad_ambigua:
            return list(ventas), [
                "❌ Rollitos rellenos ambiguos: en ventas salen "
                f"{cantidad_ambigua}, pero el override indica pollo={override['pollo']} y queso={override['queso']}."
            ], True
        cantidades = override
        fuente = "usuario"
    else:
        if registros is None:
            if permitir_pendiente:
                return list(ventas), [
                    "⚠️ Tipo de rollitos vendidos por confirmar: todavía no se indicó si fueron de pollo o de queso."
                ], False
            return list(ventas), [
                "❌ Rollitos rellenos ambiguos: no se pudo leer REGISTRO C2 para decidir si fueron de pollo o de queso."
            ], True

        salida_pollo = registros.get("C2", {}).get(ROLLITOS_REGISTRO_C2["pollo"], {}).get("salida", 0)
        salida_queso = registros.get("C2", {}).get(ROLLITOS_REGISTRO_C2["queso"], {}).get("salida", 0)
        total_registrado = salida_pollo + salida_queso

        if total_registrado != cantidad_ambigua:
            if permitir_pendiente:
                return list(ventas), [
                    "⚠️ Tipo de rollitos vendidos por confirmar: en ventas salen "
                    f"{cantidad_ambigua}, pero en REGISTRO C2 figuran "
                    f"{salida_pollo} de pollo y {salida_queso} de queso."
                ], False
            return list(ventas), [
                "❌ Rollitos rellenos ambiguos: en ventas salen "
                f"{cantidad_ambigua}, pero en REGISTRO C2 figuran "
                f"{salida_pollo} de pollo y {salida_queso} de queso. "
                "Indica cuántos fueron de pollo y cuántos de queso."
            ], True

        cantidades = {
            "pollo": salida_pollo,
            "queso": salida_queso,
        }
        fuente = "REGISTRO C2"

    total_precio = sum(venta.get("precio_total", 0) for venta in ventas if _es_rollitos_ambiguo(venta["plato"]))
    precios = _distribuir_precio_rollitos(total_precio, cantidades)

    nuevas_ventas = []
    ya_reemplazado = False
    for venta in ventas:
        if not _es_rollitos_ambiguo(venta["plato"]):
            nuevas_ventas.append(dict(venta))
            continue

        if ya_reemplazado:
            continue

        if cantidades["pollo"]:
            nuevas_ventas.append({
                "plato": ROLLITOS_POLLO,
                "cantidad": cantidades["pollo"],
                "precio_total": precios["pollo"],
            })
        if cantidades["queso"]:
            nuevas_ventas.append({
                "plato": ROLLITOS_QUESO,
                "cantidad": cantidades["queso"],
                "precio_total": precios["queso"],
            })
        ya_reemplazado = True

    if fuente == "usuario":
        return nuevas_ventas, [], False

    return nuevas_ventas, [
        "ℹ️ Rollitos rellenos resueltos desde REGISTRO C2: "
        f"pollo={cantidades['pollo']}, queso={cantidades['queso']}."
    ], False


def _preparar_datos_cierre(ventas_originales: list[dict], fecha: str, recetas: list[dict],
                           rollitos_override: dict | None = None,
                           registros: dict | None = None,
                           permitir_pendiente_rollitos: bool = False) -> dict:
    ventas_canonicas = _canonizar_ventas_segun_recetas(ventas_originales, recetas)
    ventas_resueltas, alertas_rollitos, requiere_aclaracion = _resolver_rollitos_rellenos(
        ventas_canonicas,
        fecha,
        registros=registros,
        rollitos_override=rollitos_override,
        permitir_pendiente=permitir_pendiente_rollitos,
    )
    consumo, alertas_recetas = calcular_consumo_teorico(ventas_resueltas, recetas)

    return {
        "ventas": ventas_resueltas,
        "consumo": consumo,
        "consumo_agrupado": agrupar_consumo_por_insumo(consumo),
        "alertas": alertas_recetas + alertas_rollitos,
        "requiere_aclaracion": requiere_aclaracion,
    }


def _agrupar_ventas_por_plato(ventas: list[dict]) -> list[dict]:
    agrupadas = {}
    for venta in ventas:
        plato = venta["plato"]
        if plato not in agrupadas:
            agrupadas[plato] = {
                "plato": plato,
                "cantidad": 0,
                "precio_total": 0,
            }
        agrupadas[plato]["cantidad"] += venta["cantidad"]
        agrupadas[plato]["precio_total"] += venta.get("precio_total", 0)
    return list(agrupadas.values())


def _plato_canonico_neola(plato: str, recetas: list[dict] | None = None) -> str:
    nombre = str(plato or "").strip()
    if not nombre or not recetas:
        return nombre

    recetas_plato = buscar_receta(nombre, recetas)
    if not recetas_plato:
        return nombre

    canonicos = []
    vistos = set()
    for receta in recetas_plato:
        canonico = str(receta.get("plato", "")).strip()
        if not canonico:
            continue
        clave = normalizar_nombre(canonico)
        if clave in vistos:
            continue
        vistos.add(clave)
        canonicos.append(canonico)

    if len(canonicos) == 1:
        return canonicos[0]

    recetas_resueltas, alertas = resolver_variantes_receta(nombre, recetas_plato)
    if alertas:
        return nombre

    canonicos_resueltos = []
    vistos_resueltos = set()
    for receta in recetas_resueltas:
        canonico = str(receta.get("plato", "")).strip()
        if not canonico:
            continue
        clave = normalizar_nombre(canonico)
        if clave in vistos_resueltos:
            continue
        vistos_resueltos.add(clave)
        canonicos_resueltos.append(canonico)

    if len(canonicos_resueltos) == 1:
        return canonicos_resueltos[0]

    return nombre


def _canonizar_ventas_segun_recetas(ventas: list[dict], recetas: list[dict] | None = None) -> list[dict]:
    agrupadas = {}
    for venta in ventas:
        plato = _plato_canonico_neola(venta.get("plato", ""), recetas)
        if plato not in agrupadas:
            agrupadas[plato] = {
                "plato": plato,
                "cantidad": 0,
                "precio_total": 0.0,
            }
        agrupadas[plato]["cantidad"] += int(venta.get("cantidad", 0) or 0)
        agrupadas[plato]["precio_total"] += float(venta.get("precio_total", 0) or 0)
    return list(agrupadas.values())


def _mapa_ventas_por_plato(ventas: list[dict], recetas: list[dict] | None = None) -> dict[str, dict]:
    agrupadas = {}
    for venta in _canonizar_ventas_segun_recetas(ventas, recetas):
        plato = venta["plato"]
        if plato not in agrupadas:
            agrupadas[plato] = {
                "plato": plato,
                "cantidad": 0,
                "precio_total": 0.0,
            }
        agrupadas[plato]["cantidad"] += int(venta.get("cantidad", 0) or 0)
        agrupadas[plato]["precio_total"] += float(venta.get("precio_total", 0) or 0)
    return agrupadas


def _lista_ventas_desde_mapa(mapa_ventas: dict[str, dict]) -> list[dict]:
    return [venta for venta in mapa_ventas.values() if venta["cantidad"] > 0]


def _calcular_cambios_ventas(
    ventas_actuales: list[dict],
    ventas_nuevas: list[dict],
    recetas: list[dict] | None = None,
) -> list[dict]:
    mapa_actual = _mapa_ventas_por_plato(ventas_actuales, recetas=recetas)
    mapa_nuevo = _mapa_ventas_por_plato(ventas_nuevas, recetas=recetas)

    cambios = []
    for plato in sorted(set(mapa_actual) | set(mapa_nuevo)):
        cantidad_actual = mapa_actual.get(plato, {}).get("cantidad", 0)
        cantidad_nueva = mapa_nuevo.get(plato, {}).get("cantidad", 0)
        delta = cantidad_nueva - cantidad_actual
        if delta == 0:
            continue
        cambios.append({
            "plato": plato,
            "cantidad_actual": cantidad_actual,
            "cantidad_nueva": cantidad_nueva,
            "delta": delta,
        })
    return cambios


def _formatear_cambios_ventas(cambios: list[dict]) -> list[str]:
    lineas = []
    for cambio in cambios:
        signo = "+" if cambio["delta"] > 0 else ""
        lineas.append(
            f"   • {cambio['plato']}: {cambio['cantidad_actual']} → "
            f"{cambio['cantidad_nueva']} ({signo}{cambio['delta']})"
        )
    return lineas


def _insumos_afectados(consumo_actual: dict, consumo_nuevo: dict) -> list[str]:
    afectados = []
    for insumo in sorted(set(consumo_actual) | set(consumo_nuevo)):
        total_actual = consumo_actual.get(insumo, {}).get("total", 0)
        total_nuevo = consumo_nuevo.get(insumo, {}).get("total", 0)
        if total_actual != total_nuevo:
            afectados.append(insumo)
    return afectados


def _aplicar_ajustes_ventas(
    ventas_actuales: list[dict],
    ajustes: list[dict],
    recetas: list[dict] | None = None,
) -> list[dict]:
    mapa_actual = _mapa_ventas_por_plato(ventas_actuales, recetas=recetas)
    mapa_normalizado = {
        normalizar_nombre(plato): plato
        for plato in mapa_actual
    }

    for ajuste in ajustes:
        plato_original = ajuste["plato"].strip()
        plato_canonico = _plato_canonico_neola(plato_original, recetas)
        clave_normalizada = normalizar_nombre(plato_canonico)
        plato = mapa_normalizado.get(clave_normalizada, plato_canonico)
        delta = int(ajuste["delta"])

        if plato not in mapa_actual:
            mapa_actual[plato] = {
                "plato": plato,
                "cantidad": 0,
                "precio_total": 0.0,
            }

        nueva_cantidad = mapa_actual[plato]["cantidad"] + delta
        if nueva_cantidad < 0:
            raise ValueError(
                f"El ajuste deja '{plato}' con cantidad negativa ({nueva_cantidad})."
            )

        mapa_actual[plato]["cantidad"] = nueva_cantidad
        mapa_normalizado[clave_normalizada] = plato

    return _lista_ventas_desde_mapa(mapa_actual)


def _agrupar_consumo_por_plato(consumo: list[dict]) -> dict[str, list[dict]]:
    agrupado = {}
    for item in consumo:
        plato = item["plato"]
        if plato not in agrupado:
            agrupado[plato] = {}

        insumo = item["insumo"]
        if insumo not in agrupado[plato]:
            agrupado[plato][insumo] = {
                "insumo": insumo,
                "cantidad_total": 0,
                "unidad": item["unidad"],
            }

        agrupado[plato][insumo]["cantidad_total"] += item["cantidad_total"]

    return {
        plato: list(insumos.values())
        for plato, insumos in agrupado.items()
    }


def _motivo_sin_insumos(plato: str, recetas: list[dict]) -> str:
    if _es_rollitos_ambiguo(plato):
        return "Pendiente definir si es de pollo o queso"

    if es_ensalada_cesar_sin_proteina(plato):
        return "Por defecto se toma como ENSALADA CÉSAR (POLLO)"

    if plato_ignorado(plato):
        return "Plato ignorado por configuración"

    recetas_plato = buscar_receta(plato, recetas)
    if not recetas_plato:
        sugerencia = sugerir_receta_similar(plato, recetas)
        if sugerencia:
            return f"Posible receta similar: {sugerencia}. Confirma si se debe renombrar"
        return "Sin receta encontrada"

    recetas_plato, alertas_plato = resolver_variantes_receta(plato, recetas_plato)
    if alertas_plato:
        return "Varias recetas posibles. Confirma cuál corresponde"

    if any(receta.get("sku") or receta.get("insumo") for receta in recetas_plato):
        return "Sin insumos calculados"

    return "Receta sin insumos inventariables"


def _formatear_desglose_por_plato(ventas: list[dict], consumo: list[dict], recetas: list[dict]) -> list[str]:
    ventas_agrupadas = _agrupar_ventas_por_plato(ventas)
    consumo_por_plato = _agrupar_consumo_por_plato(consumo)

    lineas = []
    for venta in ventas_agrupadas:
        lineas.append(f"   • {venta['plato']} x{venta['cantidad']}")
        insumos = consumo_por_plato.get(venta["plato"], [])
        if not insumos:
            lineas.append(f"      - Motivo: {_motivo_sin_insumos(venta['plato'], recetas)}")
            continue

        for item in insumos:
            lineas.append(f"      - {item['insumo']}: {item['cantidad_total']} {item['unidad']}")

        if es_ensalada_cesar_sin_proteina(venta["plato"]):
            lineas.append("      - Nota: Por defecto se toma como ENSALADA CÉSAR (POLLO)")

    return lineas


def _formatear_totales_por_insumo(consumo_agrupado: dict) -> list[str]:
    return [
        f"   • {insumo}: {datos['total']} {datos['unidad']}"
        for insumo, datos in sorted(consumo_agrupado.items())
    ]


def _titulo_total_insumos(consumo_agrupado: dict) -> str:
    cantidad = len(consumo_agrupado)
    etiqueta = "insumo" if cantidad == 1 else "insumos"
    return f"\n📦 Total por insumo ({cantidad} {etiqueta}):"


def _hay_alertas_de_receta_por_confirmar(alertas: list[str]) -> bool:
    return any(alerta.startswith("❓") for alerta in alertas)


def _normalizar_contexto(contexto: dict | None) -> dict:
    if contexto is None:
        return {}
    return contexto


def _ctx_get(contexto: dict | None, clave, loader):
    if contexto is None:
        return loader()
    cache = contexto.setdefault("_cache", {})
    if clave not in cache:
        cache[clave] = loader()
    return cache[clave]


def _ctx_sheets_cache(contexto: dict | None) -> dict | None:
    if contexto is None:
        return None
    return contexto.setdefault("_sheets_cache", {})


def _ctx_get_recetas(contexto: dict | None) -> list[dict]:
    return _ctx_get(
        contexto,
        "recetas",
        lambda: leer_recetas(cache=_ctx_sheets_cache(contexto)),
    )


def _ctx_get_ubicaciones(contexto: dict | None) -> dict:
    return _ctx_get(
        contexto,
        "ubicaciones",
        lambda: leer_ubicacion_descuento(cache=_ctx_sheets_cache(contexto)),
    )


def _ctx_get_registros(contexto: dict | None, fecha: str) -> dict:
    return _ctx_get(
        contexto,
        ("registros", fecha),
        lambda: leer_registros_dia_completo(fecha, cache=_ctx_sheets_cache(contexto)),
    )


def _ctx_get_insumos_registro(contexto: dict | None, fecha: str) -> dict:
    return _ctx_get(
        contexto,
        ("insumos_registro", fecha),
        lambda: leer_insumos_registro_dia_completo(fecha, cache=_ctx_sheets_cache(contexto)),
    )


def _ctx_get_ventas_neola(contexto: dict | None, fecha: str) -> list[dict]:
    return _ctx_get(
        contexto,
        ("ventas_neola", fecha),
        lambda: leer_ventas_neola_dia(fecha, cache=_ctx_sheets_cache(contexto)),
    )


def _ctx_get_existencia_inventario(contexto: dict | None, fecha: str) -> dict[str, bool]:
    return _ctx_get(
        contexto,
        ("existencia_inventario", fecha),
        lambda: verificar_inventario_dia_existe(fecha, cache=_ctx_sheets_cache(contexto)),
    )


def _normalizar_ubicacion_registro(ubicacion: str) -> str:
    valor = normalizar_nombre(str(ubicacion or ""))
    alias = {
        "C1": "C1",
        "C2": "C2",
        "LINEA": "LINEA",
        "LINEA CALIENTE": "LINEA",
    }
    return alias.get(valor, "")


def _registro_vacio(ubicacion_key: str) -> dict:
    return {
        "conteo": None if ubicacion_key == "LINEA" else None,
        "ingreso": 0,
        "salida": 0,
        "motivo": "",
        "cantidad": 0,
        "cantidad_ingreso_especial": 0,
        "cantidad_salida_especial": 0,
        "motivos_ingreso": [],
        "motivos_salida": [],
    }


def _valor_registro_para_mostrar(campo: str, valor):
    if campo == "motivo":
        return valor if str(valor).strip() else "Sin motivo"
    if campo == "conteo":
        return "vacío" if valor is None else valor
    return valor


def _normalizar_motivo_registro(valor) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    if normalizar_nombre(texto) == normalizar_nombre("Sin motivo"):
        return ""
    return texto


def _cantidad_motivo_registro(registro: dict) -> int:
    if not registro:
        return 0
    if "cantidad_salida_especial" in registro:
        return int(registro.get("cantidad_salida_especial", 0) or 0)
    if not registro.get("motivo"):
        return 0
    return int(registro.get("cantidad", 0) or 0)


def _cantidad_ingreso_especial_registro(registro: dict) -> int:
    if not registro:
        return 0
    return int(registro.get("cantidad_ingreso_especial", 0) or 0)


def _motivos_ingreso_registro(registro: dict) -> list[dict]:
    if not registro:
        return []
    return list(registro.get("motivos_ingreso") or [])


def _motivos_salida_registro(registro: dict) -> list[dict]:
    if not registro:
        return []
    return list(registro.get("motivos_salida") or [])


def _salida_operativa_registro(registro: dict) -> int:
    salida = int((registro or {}).get("salida", 0) or 0)
    return max(salida - _cantidad_motivo_registro(registro), 0)


def _resolver_insumo_en_registros(registros_ubicacion: dict, insumo: str) -> str | None:
    objetivo = normalizar_nombre(str(insumo or ""))
    for nombre_real in registros_ubicacion.keys():
        if normalizar_nombre(nombre_real) == objetivo:
            return nombre_real
    return None


def _resolver_insumo_en_lista(insumos: list[str], insumo: str) -> str | None:
    objetivo = normalizar_nombre(str(insumo or ""))
    for nombre_real in insumos or []:
        if normalizar_nombre(nombre_real) == objetivo:
            return nombre_real
    return None


def _agrupar_motivos_para_comparar(motivos: list[dict]) -> list[dict]:
    acumulado = {}
    for item in motivos or []:
        motivo = str(item.get("motivo", "") or "").strip()
        if not motivo:
            continue
        clave = normalizar_nombre(motivo)
        acumulado[clave] = {
            "motivo": motivo,
            "cantidad": int(acumulado.get(clave, {}).get("cantidad", 0) or 0) + int(item.get("cantidad", 0) or 0),
        }
    return sorted(acumulado.values(), key=lambda item: normalizar_nombre(item["motivo"]))


def _describir_motivos_para_usuario(motivos: list[dict]) -> str:
    agrupados = _agrupar_motivos_para_comparar(motivos)
    if not agrupados:
        return "sin movimientos especiales"
    return ", ".join(
        f"{item['motivo']} ({item['cantidad']})"
        for item in agrupados
    )


def _verificar_avisos_registros(registros_actuales: dict, avisos: list[dict],
                                insumos_disponibles: dict | None = None) -> tuple[list[dict], list[dict]]:
    confirmados = []
    diferencias = []

    for aviso in avisos:
        ubicacion_key = _normalizar_ubicacion_registro(aviso.get("ubicacion"))
        if ubicacion_key not in ("C1", "C2", "LINEA"):
            raise ValueError(f"Ubicación de registro no válida: {aviso.get('ubicacion')}")

        insumo = str(aviso.get("insumo", "")).strip()
        if not insumo:
            raise ValueError("Cada aviso de registro corregido debe indicar un insumo.")

        nombre_real = _resolver_insumo_en_registros(registros_actuales.get(ubicacion_key, {}), insumo)
        if nombre_real is None:
            nombre_real = _resolver_insumo_en_lista((insumos_disponibles or {}).get(ubicacion_key, []), insumo)
        if nombre_real is None:
            raise ValueError(
                f"No encontré '{insumo}' en {ubicacion_key} para revisar la corrección manual."
            )
        actual = dict(
            (registros_actuales.get(ubicacion_key, {}) or {}).get(nombre_real)
            or _registro_vacio(ubicacion_key)
        )

        cambios_confirmados = []
        for campo, valor_esperado in aviso.get("cambios", {}).items():
            if campo == "conteo" and ubicacion_key != "LINEA":
                raise ValueError(f"'{insumo}' en {ubicacion_key} no usa conteo.")
            if campo not in ("conteo", "ingreso", "salida"):
                raise ValueError(f"Campo de registro no válido: {campo}")

            if valor_esperado is None:
                esperado = None if campo == "conteo" else 0
            else:
                esperado = int(valor_esperado)
                if esperado < 0:
                    raise ValueError(f"El valor final de '{insumo}' para {campo} no puede ser negativo.")

            valor_actual = actual.get(campo)
            cambios_confirmados.append({
                "campo": campo,
                "actual": valor_actual,
                "esperado": esperado,
            })

            if valor_actual != esperado:
                diferencias.append({
                    "tipo": "campo",
                    "ubicacion": ubicacion_key,
                    "insumo": nombre_real or insumo,
                    "campo": campo,
                    "actual": valor_actual,
                    "esperado": esperado,
                })

        verificar_especiales = bool(aviso.get("verificar_especiales"))
        motivos_ingreso_actuales = _motivos_ingreso_registro(actual)
        motivos_salida_actuales = _motivos_salida_registro(actual)
        if verificar_especiales:
            esperado_ingreso = _agrupar_motivos_para_comparar(aviso.get("motivos_ingreso_esperados") or [])
            esperado_salida = _agrupar_motivos_para_comparar(aviso.get("motivos_salida_esperados") or [])
            actual_ingreso = _agrupar_motivos_para_comparar(motivos_ingreso_actuales)
            actual_salida = _agrupar_motivos_para_comparar(motivos_salida_actuales)

            if actual_ingreso != esperado_ingreso:
                diferencias.append({
                    "tipo": "especiales",
                    "ubicacion": ubicacion_key,
                    "insumo": nombre_real or insumo,
                    "campo": "movimientos especiales de ingreso",
                    "actual": _describir_motivos_para_usuario(actual_ingreso),
                    "esperado": _describir_motivos_para_usuario(esperado_ingreso),
                })
            if actual_salida != esperado_salida:
                diferencias.append({
                    "tipo": "especiales",
                    "ubicacion": ubicacion_key,
                    "insumo": nombre_real or insumo,
                    "campo": "movimientos especiales de salida",
                    "actual": _describir_motivos_para_usuario(actual_salida),
                    "esperado": _describir_motivos_para_usuario(esperado_salida),
                })

        confirmados.append({
            "ubicacion": ubicacion_key,
            "insumo": nombre_real or insumo,
            "campos": cambios_confirmados,
            "motivos_ingreso": motivos_ingreso_actuales,
            "motivos_salida": motivos_salida_actuales,
            "verificar_especiales": verificar_especiales,
            "motivos_ingreso_esperados": aviso.get("motivos_ingreso_esperados") or [],
            "motivos_salida_esperados": aviso.get("motivos_salida_esperados") or [],
        })

    return confirmados, diferencias


def _formatear_confirmaciones_registro(confirmados: list[dict]) -> list[str]:
    lineas = []
    for confirmado in confirmados:
        hoja = "LINEA CALIENTE" if confirmado["ubicacion"] == "LINEA" else confirmado["ubicacion"]
        lineas.append(f"   • {hoja} / {confirmado['insumo']}")
        if confirmado.get("verificar_especiales"):
            ingreso_esperado = _describir_motivos_para_usuario(confirmado.get("motivos_ingreso_esperados") or [])
            salida_esperada = _describir_motivos_para_usuario(confirmado.get("motivos_salida_esperados") or [])
            lineas.append(f"      - ingreso especial esperado: {ingreso_esperado}")
            lineas.append(f"      - salida especial esperada: {salida_esperada}")
        elif not confirmado["campos"]:
            especiales = []
            for item in confirmado.get("motivos_ingreso", []):
                especiales.append(
                    f"ingreso especial {item.get('motivo')} ({int(item.get('cantidad', 0) or 0)})"
                )
            for item in confirmado.get("motivos_salida", []):
                especiales.append(
                    f"salida especial {item.get('motivo')} ({int(item.get('cantidad', 0) or 0)})"
                )
            if especiales:
                lineas.append(f"      - movimientos especiales actuales: {', '.join(especiales)}")
            else:
                lineas.append("      - releeré los movimientos especiales actuales de este insumo.")
        for campo in confirmado["campos"]:
            lineas.append(
                f"      - {campo['campo']}: "
                f"{_valor_registro_para_mostrar(campo['campo'], campo['esperado'])}"
            )
    return lineas


def _formatear_diferencias_aviso_registro(diferencias: list[dict]) -> list[str]:
    lineas = []
    for item in diferencias:
        hoja = "LINEA CALIENTE" if item["ubicacion"] == "LINEA" else item["ubicacion"]
        if item.get("tipo") == "especiales":
            lineas.append(
                f"   • {hoja} / {item['insumo']} / {item['campo']}: "
                f"en la hoja veo {item['actual']} y me indicaste {item['esperado']}"
            )
            continue
        lineas.append(
            f"   • {hoja} / {item['insumo']} / {item['campo']}: "
            f"en la hoja veo {_valor_registro_para_mostrar(item['campo'], item['actual'])} "
            f"y me indicaste {_valor_registro_para_mostrar(item['campo'], item['esperado'])}"
        )
    return lineas


def _es_error_de_limite(exc: Exception) -> bool:
    texto = str(exc).upper()
    marcadores = (
        "429",
        "QUOTA EXCEEDED",
        "RATE LIMIT",
        "RESOURCE_EXHAUSTED",
        "READ REQUESTS PER MINUTE PER USER",
        "WRITE REQUESTS PER MINUTE PER USER",
        "TOO MANY REQUESTS",
    )
    return any(marker in texto for marker in marcadores)


def _mensaje_limite_para_cliente() -> str:
    return (
        "⏳ El sistema está recibiendo muchas solicitudes en este momento. "
        "El tiempo de espera puede aumentar por el uso de la aplicación. "
        "Espera un momento y vuelve a intentarlo."
    )


def _formatear_error_usuario(prefijo: str, exc: Exception) -> str:
    if _es_error_de_limite(exc):
        return f"{prefijo}. {_mensaje_limite_para_cliente()}"
    return f"{prefijo}: {str(exc)}"


def _mensaje_cuidado_hojas() -> str:
    return (
        "⚠️ Mientras este proceso esté activo, por favor no edites manualmente las hojas. "
        "Si las cambias en ese momento, al final pueden aparecer errores o diferencias que no correspondan. "
        "Si necesitas hacerlo, avísame cuando termine para revisarlo."
    )


def _recordatorio_edicion_manual() -> str:
    return (
        "Si hiciste algún cambio manual en las hojas mientras el proceso estaba activo, "
        "avísame para revisar que no haya quedado ningún error por eso."
    )


def _emitir_progreso(on_progress, mensaje: str) -> None:
    if not on_progress:
        return
    try:
        on_progress(mensaje)
    except Exception:
        pass


def _alertas_lectura_para_cliente(diagnostico: dict) -> list[str]:
    if not diagnostico.get("problema") and not diagnostico.get("platos_dudosos"):
        return []

    alertas = ["⚠️ La foto no se ve del todo clara en algunas partes."]
    platos_dudosos = diagnostico.get("platos_dudosos", [])
    if platos_dudosos:
        alertas.append(f"⚠️ No pude leer con seguridad estos nombres: {', '.join(platos_dudosos)}.")
    elif diagnostico.get("requiere_aclaracion"):
        alertas.append("⚠️ Puede faltar algún plato porque una parte del ticket no se alcanza a leer bien.")
    return alertas


def _sugerencias_lectura_para_cliente(diagnostico: dict) -> list[str]:
    if not diagnostico.get("problema") and not diagnostico.get("platos_dudosos"):
        return []

    platos_dudosos = diagnostico.get("platos_dudosos", [])
    lineas = ["Si puedes, envíame una foto más clara."]
    if platos_dudosos:
        lineas.append(f"Si no, ayúdame a confirmar estos nombres: {', '.join(platos_dudosos)}.")
        lineas.append("También dime si en el preview de arriba falta algún plato.")
    else:
        lineas.append("Si no, revisa el preview de arriba y dime si falta algún plato o si alguno quedó mal leído.")
    return lineas


def _parsear_ticket_desde_imagen(
    *,
    image_path: str = None,
    image_bytes: bytes = None,
    media_type: str = "image/jpeg",
) -> tuple[list[dict], dict]:
    if image_bytes:
        ventas = parsear_foto_bytes(image_bytes, media_type)
    elif image_path:
        ventas = parsear_foto_ticket(image_path)
    else:
        raise ValueError("No se recibió imagen del ticket")

    return ventas, obtener_diagnostico_lectura()


def sugerir_fecha() -> tuple[str, str]:
    """
    Sugiere la fecha del cierre basándose en la hora actual en Ecuador.
    - Entre 19:00 y 23:59 → fecha de hoy (cierre del día)
    - Entre 00:00 y 03:59 → fecha de ayer (cierre tardío del día anterior)
    - Entre 04:00 y 18:59 → fecha de hoy (caso inusual, pero asumimos hoy)

    Returns:
        (fecha_sugerida, motivo): ("2026-03-11", "Cierre nocturno del día de hoy")
    """
    ahora = datetime.now(TZ_ECUADOR)
    hora = ahora.hour

    if 19 <= hora <= 23:
        fecha = ahora.strftime("%Y-%m-%d")
        motivo = f"Noche ({ahora.strftime('%H:%M')}h) → cierre de hoy"
    elif 0 <= hora <= 3:
        fecha = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
        motivo = f"Cierre tardío ({ahora.strftime('%H:%M')}h) → corresponde al día anterior"
    else:
        fecha = ahora.strftime("%Y-%m-%d")
        motivo = f"Horario diurno ({ahora.strftime('%H:%M')}h) → usamos la fecha de hoy"

    return fecha, motivo


def preparar_cierre(image_path: str = None, image_bytes: bytes = None,
                     media_type: str = "image/jpeg", fecha: str = None,
                     rollitos_override: dict | None = None,
                     precierre: bool = False,
                     contexto: dict | None = None) -> dict:
    """
    PASO 1 del flujo: parsea la foto, calcula consumo teórico, sugiere fecha.
    NO escribe nada en Google Sheets. Devuelve un dict con toda la info
    para que el usuario confirme antes de proceder.

    Returns:
        {
            "ok": True/False,
            "fecha": "2026-03-11",
            "fecha_motivo": "Noche (22:15h) → cierre de hoy",
            "fecha_origen": "sugerida" | "usuario",
            "ventas": [...],
            "consumo": [...],
            "consumo_agrupado": {...},
            "alertas": [...],
            "resumen": "texto para mostrar al usuario"
        }
    """
    contexto = _normalizar_contexto(contexto)
    resultado = {
        "ok": False,
        "fecha": "",
        "fecha_motivo": "",
        "fecha_origen": "",
        "diagnostico_lectura": {},
        "observaciones_lectura": [],
        "ventas_originales": [],
        "ventas": [],
        "consumo": [],
        "consumo_agrupado": {},
        "alertas": [],
        "requiere_aclaracion": False,
        "rollitos_override": _normalizar_rollitos_override(rollitos_override),
        "precierre": precierre,
        "resumen": ""
    }

    # Determinar fecha
    if fecha:
        resultado["fecha"] = fecha
        resultado["fecha_motivo"] = "Fecha indicada por el usuario"
        resultado["fecha_origen"] = "usuario"
    else:
        fecha_sug, motivo = sugerir_fecha()
        resultado["fecha"] = fecha_sug
        resultado["fecha_motivo"] = motivo
        resultado["fecha_origen"] = "sugerida"

    # Parsear foto
    try:
        ventas, diagnostico_lectura = _parsear_ticket_desde_imagen(
            image_path=image_path,
            image_bytes=image_bytes,
            media_type=media_type,
        )
    except Exception as e:
        resultado["resumen"] = _formatear_error_usuario("❌ Error al leer el ticket", e)
        return resultado

    alertas_lectura = _alertas_lectura_para_cliente(diagnostico_lectura)
    resultado["diagnostico_lectura"] = diagnostico_lectura
    resultado["observaciones_lectura"] = alertas_lectura
    resultado["ventas_originales"] = list(ventas)

    # Calcular consumo teórico
    try:
        recetas = _ctx_get_recetas(contexto)
    except Exception as e:
        resultado["resumen"] = _formatear_error_usuario("❌ Error al leer recetas", e)
        return resultado

    registros = None
    if _cantidad_rollitos_ambiguos(ventas):
        try:
            registros = _ctx_get_registros(contexto, resultado["fecha"])
        except Exception:
            registros = None

    datos = _preparar_datos_cierre(
        ventas,
        resultado["fecha"],
        recetas,
        rollitos_override=resultado["rollitos_override"],
        registros=registros,
    )
    resultado["ventas"] = datos["ventas"]
    resultado["consumo"] = datos["consumo"]
    resultado["consumo_agrupado"] = datos["consumo_agrupado"]
    resultado["alertas"] = alertas_lectura + datos["alertas"]
    requiere_receta = _hay_alertas_de_receta_por_confirmar(resultado["alertas"])
    resultado["requiere_aclaracion"] = datos["requiere_aclaracion"] or requiere_receta
    resultado["ok"] = not resultado["requiere_aclaracion"]

    # Generar resumen para el usuario
    lineas = []
    lineas.append(f"📋 PREPARACIÓN DE CIERRE")
    lineas.append("=" * 40)

    # Fecha
    lineas.append(f"\n📅 Fecha: {resultado['fecha']}")
    lineas.append(f"   ({resultado['fecha_motivo']})")
    if precierre:
        lineas.append("   🟡 Ticket marcado como precierre")

    # Platos detectados con detalle de insumos
    total_platos = sum(v["cantidad"] for v in resultado["ventas"])
    lineas.append(f"\n🍽️ Platos e insumos ({total_platos} unidades):")
    lineas.extend(_formatear_desglose_por_plato(resultado["ventas"], resultado["consumo"], recetas))

    lineas.append(_titulo_total_insumos(resultado["consumo_agrupado"]))
    lineas.extend(_formatear_totales_por_insumo(resultado["consumo_agrupado"]))

    # Alertas
    if resultado["alertas"]:
        lineas.append(f"\n⚠️ Alertas:")
        for a in resultado["alertas"]:
            lineas.append(f"   {a}")

    sugerencias_lectura = _sugerencias_lectura_para_cliente(diagnostico_lectura)
    if sugerencias_lectura:
        lineas.append("\n💬 Si quieres afinar la lectura:")
        for sugerencia in sugerencias_lectura:
            lineas.append(f"   {sugerencia}")

    if resultado["requiere_aclaracion"]:
        lineas.append(f"\n{'=' * 40}")
        if any("Rollitos rellenos ambiguos" in alerta for alerta in resultado["alertas"]):
            lineas.append("❌ No se puede continuar hasta aclarar los rollitos rellenos.")
            lineas.append("¿Cuántos fueron de pollo y cuántos de queso?")
        elif requiere_receta:
            lineas.append("❌ Hay un plato que no pude relacionar con seguridad con una receta.")
            lineas.append("Revisa la sugerencia de arriba y dime si es ese mismo plato o si corresponde a otro distinto.")
        else:
            lineas.append("❌ No se puede continuar hasta aclarar la información pendiente.")
    else:
        lineas.append(f"\n{'=' * 40}")
        if sugerencias_lectura:
            lineas.append("Puedo seguir con el precierre tomando solo lo que sí se alcanza a leer.")
        lineas.append(_mensaje_cuidado_hojas())
        lineas.append("¿Todo correcto? ¿Procedo con el cierre?")

    resultado["resumen"] = "\n".join(lineas)
    return resultado


def confirmar_cierre(preparacion: dict, fecha_override: str = None,
                     image_path: str = None, image_bytes: bytes = None,
                     rollitos_override: dict | None = None,
                     contexto: dict | None = None,
                     on_progress=None) -> str:
    """
    PASO 2 del flujo: ejecuta el cierre con los datos ya preparados.
    Se llama después de que el usuario confirma.
    Guarda la imagen del ticket en cierres-diarios/{fecha}/{fecha}.ext
    """
    fecha = fecha_override or preparacion["fecha"]
    contexto = _normalizar_contexto(contexto)
    sheets_cache = _ctx_sheets_cache(contexto)
    _emitir_progreso(on_progress, "Estoy revisando los datos del día.")

    try:
        recetas = _ctx_get_recetas(contexto)
    except Exception as e:
        return _formatear_error_usuario("❌ Error al leer recetas", e)

    # Guardar imagen del cierre
    _guardar_imagen_cierre(fecha, image_path, image_bytes)

    reporte = []
    reporte.append(f"📊 CIERRE DE INVENTARIO — {fecha}")
    reporte.append("=" * 40)

    # Leer registros del día
    reporte.append("\n📦 Leyendo registros del día...")
    try:
        registros = _ctx_get_registros(contexto, fecha)
    except Exception as e:
        reporte.append(_formatear_error_usuario("⚠️ No se pudieron leer registros", e))
        registros = {"C1": {}, "C2": {}, "LINEA": {}}

    ventas_originales = preparacion.get("ventas_originales", preparacion["ventas"])
    override_final = (
        _normalizar_rollitos_override(rollitos_override)
        if rollitos_override is not None
        else preparacion.get("rollitos_override")
    )
    datos = _preparar_datos_cierre(
        ventas_originales,
        fecha,
        recetas,
        rollitos_override=override_final,
        registros=registros,
    )
    ventas = datos["ventas"]
    consumo = datos["consumo"]
    consumo_agrupado = datos["consumo_agrupado"]
    alertas_recetas = datos["alertas"]
    total_platos = sum(v["cantidad"] for v in ventas)
    requiere_receta = _hay_alertas_de_receta_por_confirmar(alertas_recetas)

    if datos["requiere_aclaracion"] or requiere_receta:
        reporte.append("❌ No se puede continuar.")
        for alerta in alertas_recetas:
            reporte.append(f"   {alerta}")
        if any("Rollitos rellenos ambiguos" in alerta for alerta in alertas_recetas):
            reporte.append("Indica cuántos rollitos fueron de pollo y cuántos de queso, y vuelve a ejecutar.")
        elif _hay_alertas_de_receta_por_confirmar(alertas_recetas):
            reporte.append("Confirma o actualiza la receta sugerida y vuelve a ejecutar.")
        else:
            reporte.append("Aclara la información pendiente y vuelve a ejecutar.")
        return "\n".join(reporte)

    total_movimientos = sum(len(r) for r in registros.values())
    reporte.append(f"✅ {total_movimientos} movimientos registrados")

    # Ubicaciones de descuento
    try:
        ubicaciones_defecto = _ctx_get_ubicaciones(contexto)
    except Exception as e:
        reporte.append(_formatear_error_usuario("⚠️ No se pudo leer tabla de ubicaciones", e))
        ubicaciones_defecto = {}

    # Conciliar
    reporte.append("\n🔄 Conciliando inventario...")

    descuadres = []
    movimientos_extraordinarios = []

    for insumo, datos in consumo_agrupado.items():
        consumo_teorico = datos["total"]
        ubicacion_defecto = ubicaciones_defecto.get(insumo, datos["ubicacion"])

        salida_c1 = _salida_operativa_registro(registros["C1"].get(insumo, {}))
        salida_c2 = _salida_operativa_registro(registros["C2"].get(insumo, {}))
        salida_total_cong = salida_c1 + salida_c2

        datos_linea = registros["LINEA"].get(insumo, {})
        salida_linea = _salida_operativa_registro(datos_linea)

        if ubicacion_defecto == "LINEA":
            salida_registrada = salida_linea
            dif = salida_linea - consumo_teorico
            fuente = "LINEA"
        elif ubicacion_defecto == "C1":
            salida_registrada = salida_c1
            dif = salida_c1 - consumo_teorico
            fuente = "C1"
        elif ubicacion_defecto == "C2":
            salida_registrada = salida_c2
            dif = salida_c2 - consumo_teorico
            fuente = "C2"
        else:
            salida_registrada = salida_total_cong + salida_linea
            dif = 0
            fuente = "?"

        if abs(dif) >= UMBRAL_DESCUADRE:
            descuadres.append({
                "insumo": insumo,
                "consumo_teorico": consumo_teorico,
                "salida": salida_registrada,
                "dif": dif,
                "ubicacion": fuente
            })

    for ubicacion, reg in registros.items():
        for insumo, datos_reg in reg.items():
            for item in _motivos_ingreso_registro(datos_reg):
                movimientos_extraordinarios.append({
                    "insumo": insumo,
                    "ubicacion": ubicacion,
                    "tipo": "Ingreso",
                    "cantidad": int(item.get("cantidad", 0) or 0),
                    "motivo": item.get("motivo", ""),
                })
            for item in _motivos_salida_registro(datos_reg):
                movimientos_extraordinarios.append({
                    "insumo": insumo,
                    "ubicacion": ubicacion,
                    "tipo": "Salida",
                    "cantidad": int(item.get("cantidad", 0) or 0),
                    "motivo": item.get("motivo", ""),
                })

    # Escribir en Google Sheets
    reporte.append("\n💾 Guardando en Google Sheets...")

    ventas_ok = False
    try:
        _emitir_progreso(on_progress, "Estoy guardando las ventas del día.")
        escribir_ventas_neola(fecha, ventas, consumo, cache=sheets_cache)
        ventas_ok = True
        reporte.append("✅ Ventas Neola actualizado")
    except Exception as e:
        reporte.append(_formatear_error_usuario("⚠️ Error al guardar ventas", e))

    if ventas_ok:
        try:
            _emitir_progreso(on_progress, "Estoy actualizando el inventario.")
            escribir_inventario_dia(
                fecha,
                consumo_agrupado,
                registros,
                ubicaciones_defecto,
                modo_ventas="final_ticket",
                cache=sheets_cache,
            )
            _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
            reporte.append("✅ Inventario diario actualizado")
        except Exception as e:
            reporte.append(_formatear_error_usuario("⚠️ Error al guardar inventario", e))
    else:
        reporte.append("⚠️ Inventario no actualizado porque no se pudo corregir automaticamente VENTAS NEOLA.")

    # Confirmar guardado de imagen
    nombre_carpeta = _fecha_a_carpeta(fecha)
    ruta_imagen = CIERRES_DIR / nombre_carpeta
    if ruta_imagen.exists():
        reporte.append(f"✅ Imagen guardada en cierres-diarios/{nombre_carpeta}/")

    # Reporte final
    reporte.append("\n" + "=" * 40)
    reporte.append("📊 RESUMEN DEL CIERRE")
    reporte.append("=" * 40)

    reporte.append(f"\n🍽️ Ventas: {total_platos} platos en {len(ventas)} items")

    top_insumos = sorted(consumo_agrupado.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
    if top_insumos:
        reporte.append("\n📦 Top insumos consumidos:")
        for insumo, datos in top_insumos:
            reporte.append(f"   • {insumo}: {datos['total']} {datos['unidad']}")

    try:
        _emitir_progreso(on_progress, "Estoy verificando que todo haya quedado cuadrado.")
        diferencias_finales = leer_diferencias_inventario_dia(fecha, cache=sheets_cache)
    except Exception as e:
        diferencias_finales = None
        reporte.append("\n" + _formatear_error_usuario("⚠️ No se pudieron leer las diferencias finales del inventario", e))

    if diferencias_finales is not None:
        total_diferencias = sum(len(items) for items in diferencias_finales.values())
        if total_diferencias:
            reporte.append(f"\n🔴 DIFERENCIAS FINALES ({total_diferencias}):")
            for hoja in ("C1", "C2", "LINEA CALIENTE"):
                reporte.append(f"\n{hoja}:")
                items = diferencias_finales.get(hoja, [])
                if not items:
                    reporte.append("   • Sin diferencias")
                    continue
                for item in items:
                    signo = "+" if item["dif"] > 0 else ""
                    reporte.append(
                        f"   • {item['insumo']}: INICIO={item['inicio']}, "
                        f"INGRESO={item['ingreso']}, SALIDA={item['salida']}, "
                        f"DIF={signo}{item['dif']}, VENTAS={item['ventas']}, CIERRE={item['cierre']}"
                    )
                    diagnosticos = _diagnosticar_diferencia_inventario(hoja, item)
                    if diagnosticos:
                        reporte.append("     Posibles causas:")
                        for diagnostico in diagnosticos:
                            reporte.append(f"       {diagnostico}")
        else:
            reporte.append("\n✅ Sin diferencias finales en C1, C2 y LINEA CALIENTE")
    elif descuadres:
        reporte.append(f"\n🔴 DESCUADRES ({len(descuadres)}):")
        for d in descuadres:
            signo = "+" if d["dif"] > 0 else ""
            reporte.append(
                f"   • {d['insumo']}: teórico={d['consumo_teorico']}, "
                f"registrado={d['salida']}, DIF={signo}{d['dif']} ({d['ubicacion']})"
            )
    else:
        reporte.append("\n✅ Sin descuadres significativos")

    if movimientos_extraordinarios:
        reporte.append(f"\n📝 Movimientos extraordinarios ({len(movimientos_extraordinarios)}):")
        for m in movimientos_extraordinarios:
            reporte.append(
                f"   • {m['insumo']} ({m['ubicacion']}): {m['tipo']} {m['cantidad']} — {m['motivo']}"
            )

    if alertas_recetas:
        reporte.append(f"\n⚠️ Alertas:")
        for a in alertas_recetas:
            reporte.append(f"   {a}")

    reporte.append("")
    reporte.append(_recordatorio_edicion_manual())

    # Guardar historial JSON del cierre
    _guardar_historial_cierre(
        fecha,
        ventas,
        consumo_agrupado,
        diferencias_finales,
        metadata={
            "ticket_tipo": "precierre" if preparacion.get("precierre") else "cierre",
            "origen": "cierre_completo",
        },
    )

    return "\n".join(reporte)


def preparar_correccion(image_path: str = None, image_bytes: bytes = None,
                        media_type: str = "image/jpeg", fecha: str = None,
                        insumos: list[str] | None = None,
                        rollitos_override: dict | None = None,
                        contexto: dict | None = None) -> dict:
    """Preview de corrección puntual sin escribir en Google Sheets."""
    contexto = _normalizar_contexto(contexto)
    if not insumos:
        return {"ok": False, "resumen": "❌ Debes indicar al menos un insumo para corregir."}

    if not fecha:
        fecha = sugerir_fecha()[0]

    prep = preparar_cierre(
        image_path=image_path,
        image_bytes=image_bytes,
        media_type=media_type,
        fecha=fecha,
        rollitos_override=rollitos_override,
        contexto=contexto,
    )
    if not prep["ok"]:
        return prep

    try:
        registros = _ctx_get_registros(contexto, fecha)
        ubicaciones = _ctx_get_ubicaciones(contexto)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer datos", e)}

    consumo_agrupado = prep["consumo_agrupado"]
    insumos_por_hoja = {"C1": [], "C2": [], "LINEA CALIENTE": []}
    for insumo_nombre in insumos:
        insumo_nombre = insumo_nombre.strip()
        ubicacion_defecto = ubicaciones.get(insumo_nombre, "")
        if insumo_nombre in consumo_agrupado:
            ubicacion_defecto = ubicacion_defecto or consumo_agrupado[insumo_nombre]["ubicacion"]
        hoja = "LINEA CALIENTE" if ubicacion_defecto == "LINEA" else ubicacion_defecto
        if hoja in insumos_por_hoja:
            insumos_por_hoja[hoja].append(insumo_nombre)

    lineas = [f"🛠️ PREVIEW DE CORRECCIÓN — {fecha}"]
    lineas.append("=" * 40)
    lineas.append(f"\nInsumos a corregir ({len(insumos)}):")
    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        for insumo_nombre in insumos_por_hoja[hoja]:
            ventas_teoricas = consumo_agrupado.get(insumo_nombre, {}).get("total", 0)
            lineas.append(f"   • {hoja}: {insumo_nombre} (ventas teóricas: {ventas_teoricas})")

    lineas.append(f"\n{'=' * 40}")
    lineas.append(_mensaje_cuidado_hojas())
    lineas.append("¿Confirmar corrección? (si/no)")

    return {
        "ok": True,
        "fecha": fecha,
        "insumos": insumos,
        "consumo_agrupado": consumo_agrupado,
        "registros": registros,
        "ubicaciones": ubicaciones,
        "resumen": "\n".join(lineas),
    }


def confirmar_correccion(preparacion: dict, contexto: dict | None = None,
                         on_progress=None) -> str:
    """Ejecuta la corrección puntual con los datos ya preparados."""
    fecha = preparacion["fecha"]
    insumos = preparacion["insumos"]
    contexto = _normalizar_contexto(contexto)
    sheets_cache = _ctx_sheets_cache(contexto)

    try:
        _emitir_progreso(on_progress, "Estoy corrigiendo los insumos indicados.")
        actualizados = corregir_inventario_insumos(
            fecha, insumos, preparacion["consumo_agrupado"],
            preparacion["registros"], preparacion["ubicaciones"],
            cache=sheets_cache,
        )
        _emitir_progreso(on_progress, "Estoy revisando si quedó alguna diferencia.")
        diferencias = leer_diferencias_inventario_dia(fecha, cache=sheets_cache)
    except Exception as e:
        return _formatear_error_usuario("❌ Error al corregir inventario puntual", e)

    lineas = [f"🛠️ CORRECCIÓN PUNTUAL DE INVENTARIO — {fecha}"]
    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        insumos_hoja = actualizados.get(hoja, [])
        if insumos_hoja:
            lineas.append(f"{hoja}: {', '.join(insumos_hoja)}")

    lineas.extend(_formatear_diferencias_inventario(diferencias))
    lineas.append("")
    lineas.append(_recordatorio_edicion_manual())
    return "\n".join(lineas)


def _formatear_diferencias_inventario(diferencias: dict) -> list[str]:
    """Formatea diferencias de inventario para reportes de corrección."""
    lineas = []
    total_difs = sum(len(items) for items in diferencias.values())
    if total_difs:
        lineas.append("\nDiferencias actuales:")
        for hoja in ("C1", "C2", "LINEA CALIENTE"):
            items = diferencias.get(hoja, [])
            lineas.append(f"{hoja}:")
            if not items:
                lineas.append("  Sin diferencias")
                continue
            for item in items:
                signo = "+" if item["dif"] > 0 else ""
                lineas.append(
                    f"  {item['insumo']}: INICIO={item['inicio']}, INGRESO={item['ingreso']}, "
                    f"SALIDA={item['salida']}, DIF={signo}{item['dif']}, "
                    f"VENTAS={item['ventas']}, CIERRE={item['cierre']}"
                )
                diagnosticos = _diagnosticar_diferencia_inventario(hoja, item)
                if diagnosticos:
                    lineas.append("    Posibles causas:")
                    for diagnostico in diagnosticos:
                        lineas.append(f"      {diagnostico}")
    else:
        lineas.append("\nSin diferencias en C1, C2 y LINEA CALIENTE.")
    return lineas


def _diagnosticar_diferencia_inventario(hoja: str, item: dict) -> list[str]:
    dif = int(item.get("dif", 0) or 0)
    ventas = int(item.get("ventas", 0) or 0)
    salida = int(item.get("salida", 0) or 0)

    if dif == 0:
        return []

    if ventas > 0 and salida == 0:
        if hoja == "LINEA CALIENTE":
            return [
                "Alta probabilidad: el conteo final quedó mayor al real o faltó registrar la salida del congelador que debía alimentar a línea.",
                "Media probabilidad: faltó registrar una transferencia o ingreso hacia línea.",
                "Baja probabilidad: la receta descuenta de más o el insumo está mal asociado.",
            ]
        return [
            "Alta probabilidad: faltó registrar una salida en el congelador.",
            "Media probabilidad: la salida real quedó en otra ubicación o hubo un error del personal de salón al registrar una venta de más en Neola.",
            "Baja probabilidad: la receta descuenta de más o el insumo está mal ubicado.",
        ]

    if salida > 0 and ventas == 0:
        if hoja == "LINEA CALIENTE":
            return [
                "Alta probabilidad: faltó una venta o un ticket en Neola, o hubo una salida real en línea sin marcar un motivo especial.",
                "Media probabilidad: el conteo final quedó menor al real.",
                "Baja probabilidad: la receta descuenta menos de lo que realmente se usa o el insumo está mal asociado.",
            ]
        return [
            "Alta probabilidad: hubo una salida real sin venta respaldada o falta un ticket.",
            "Media probabilidad: el insumo salió por producción interna o fue dado de baja por mal estado; eso solo suele confirmarse con un conteo físico.",
            "Baja probabilidad: el insumo está mal ubicado o la receta corresponde a otro insumo.",
        ]

    if dif > 0:
        if hoja == "LINEA CALIENTE":
            return [
                "Alta probabilidad: el conteo final quedó menor al real o faltó registrar un motivo especial en LINEA CALIENTE.",
                "Media probabilidad: falta una venta o un ticket en Neola.",
                "Baja probabilidad: la receta descuenta menos de lo que realmente se usa o el insumo está mal asociado.",
            ]
        return [
            "Alta probabilidad: se sacó más del congelador de lo que quedó respaldado por ventas o transferencias.",
            "Media probabilidad: falta un ticket, una transferencia a línea o un conteo físico que permita detectar una cantidad especial no marcada.",
            "Baja probabilidad: la receta descuenta menos de lo que realmente se usa o el insumo está mal asociado.",
        ]

    if hoja == "LINEA CALIENTE":
        return [
            "Alta probabilidad: el conteo final quedó mayor al real o faltó registrar una salida del congelador que debía alimentar a línea.",
            "Media probabilidad: falta registrar una transferencia o ingreso hacia línea, o hubo un error del personal de salón al registrar una venta de más en Neola.",
            "Baja probabilidad: la receta descuenta de más o el insumo está mal asociado.",
        ]

    return [
        "Alta probabilidad: faltó registrar una salida en el congelador.",
        "Media probabilidad: la salida real quedó en otra ubicación o hubo un error del personal de salón al registrar una venta de más en Neola.",
        "Baja probabilidad: la receta descuenta de más o el insumo está mal asociado.",
    ]


def _verificar_entrada_inventario(fecha: str, contexto: dict | None = None) -> tuple[bool, str]:
    try:
        existencia = _ctx_get_existencia_inventario(contexto, fecha)
    except Exception as e:
        return False, _formatear_error_usuario("❌ Error al verificar inventario", e)

    hojas_faltantes = [hoja for hoja, existe in existencia.items() if not existe]
    if hojas_faltantes:
        return False, (
            f"❌ No existe la entrada del {fecha} en: {', '.join(hojas_faltantes)}.\n"
            "Primero crea o confirma la entrada del día en inventario."
        )

    return True, ""


def _preparar_actualizacion_ventas(
    *,
    fecha: str,
    ventas_finales: list[dict],
    consumo_final: list[dict],
    consumo_agrupado_final: dict,
    alertas_finales: list[str],
    requiere_aclaracion: bool,
    recetas: list[dict],
    origen: str,
    descripcion: str,
    precierre: bool = False,
    contexto: dict | None = None,
) -> dict:
    contexto = _normalizar_contexto(contexto)
    ok_inventario, mensaje = _verificar_entrada_inventario(fecha, contexto)
    if not ok_inventario:
        return {"ok": False, "resumen": mensaje}

    if requiere_aclaracion or _hay_alertas_de_receta_por_confirmar(alertas_finales):
        lineas = [f"📋 {descripcion.upper()} — {fecha}", "=" * 40]
        lineas.append("❌ No se puede continuar hasta aclarar el ticket.")
        if alertas_finales:
            lineas.append("\n⚠️ Alertas:")
            for alerta in alertas_finales:
                lineas.append(f"   {alerta}")
        return {
            "ok": False,
            "resumen": "\n".join(lineas),
        }

    try:
        ventas_actuales = _ctx_get_ventas_neola(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer ventas actuales", e)}

    try:
        registros = _ctx_get_registros(contexto, fecha)
        ubicaciones = _ctx_get_ubicaciones(contexto)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer datos del día", e)}

    datos_actuales = _preparar_datos_cierre(
        ventas_actuales,
        fecha,
        recetas,
        registros=registros,
    )
    cambios = _calcular_cambios_ventas(ventas_actuales, ventas_finales, recetas=recetas)
    if not cambios:
        return {
            "ok": False,
            "resumen": f"✅ El ticket no cambia las ventas del {fecha}.",
        }

    insumos_afectados = _insumos_afectados(
        datos_actuales["consumo_agrupado"],
        consumo_agrupado_final,
    )
    historial_previo = _leer_historial_cierre(fecha) or {}
    ticket_tipo_previo = historial_previo.get("metadata", {}).get("ticket_tipo")
    recalcular_inventario_completo = ticket_tipo_previo == "precierre" and not precierre

    lineas = [f"📋 {descripcion.upper()} — {fecha}", "=" * 40]
    if ticket_tipo_previo == "precierre":
        lineas.append("ℹ️ Este día estaba marcado previamente como precierre.")
    if precierre:
        lineas.append("🟡 El nuevo ticket se registrará como precierre.")
    elif recalcular_inventario_completo:
        lineas.append("ℹ️ Como este día venía de un precierre, se volverá a sincronizar todo el inventario.")

    lineas.append(f"\n🧾 Cambios detectados ({len(cambios)}):")
    lineas.extend(_formatear_cambios_ventas(cambios))

    total_platos = sum(v["cantidad"] for v in ventas_finales)
    lineas.append(f"\n🍽️ Ventas finales ({total_platos} unidades):")
    lineas.extend(_formatear_desglose_por_plato(ventas_finales, consumo_final, recetas))
    lineas.append(_titulo_total_insumos(consumo_agrupado_final))
    lineas.extend(_formatear_totales_por_insumo(consumo_agrupado_final))

    lineas.append(f"\n🔧 Insumos a recalcular en inventario ({len(insumos_afectados)}):")
    if insumos_afectados:
        for insumo in insumos_afectados:
            total_anterior = datos_actuales["consumo_agrupado"].get(insumo, {}).get("total", 0)
            total_nuevo = consumo_agrupado_final.get(insumo, {}).get("total", 0)
            lineas.append(f"   • {insumo}: {total_anterior} → {total_nuevo}")
    else:
        lineas.append("   • Sin cambios de insumos")

    if alertas_finales:
        lineas.append(f"\n⚠️ Alertas:")
        for alerta in alertas_finales:
            lineas.append(f"   {alerta}")

    lineas.append(f"\n{'=' * 40}")
    lineas.append(_mensaje_cuidado_hojas())
    lineas.append("¿Todo correcto? ¿Aplico la actualización?")

    return {
        "ok": True,
        "fecha": fecha,
        "origen_actualizacion": origen,
        "precierre": precierre,
        "ventas_actuales": ventas_actuales,
        "ventas_finales": ventas_finales,
        "consumo_final": consumo_final,
        "consumo_agrupado_final": consumo_agrupado_final,
        "cambios": cambios,
        "insumos_afectados": insumos_afectados,
        "registros": registros,
        "ubicaciones": ubicaciones,
        "recalcular_inventario_completo": recalcular_inventario_completo,
        "resumen": "\n".join(lineas),
    }


def _confirmar_actualizacion_ventas(
    preparacion: dict,
    *,
    image_path: str = None,
    image_bytes: bytes = None,
    contexto: dict | None = None,
    on_progress=None,
) -> str:
    fecha = preparacion["fecha"]
    contexto = _normalizar_contexto(contexto)
    sheets_cache = _ctx_sheets_cache(contexto)

    try:
        _emitir_progreso(on_progress, "Estoy actualizando las ventas del día.")
        escribir_ventas_neola(
            fecha,
            preparacion["ventas_finales"],
            preparacion["consumo_final"],
            cache=sheets_cache,
        )
    except Exception as e:
        return _formatear_error_usuario("❌ Error al actualizar VENTAS NEOLA", e)

    if preparacion.get("recalcular_inventario_completo"):
        try:
            _emitir_progreso(on_progress, "Estoy actualizando todo el inventario del día.")
            escribir_inventario_dia(
                fecha,
                preparacion["consumo_agrupado_final"],
                preparacion["registros"],
                preparacion["ubicaciones"],
                modo_ventas="final_ticket",
                cache=sheets_cache,
            )
            _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
        except Exception as e:
            return _formatear_error_usuario("❌ Ventas actualizadas, pero falló la actualización completa del inventario", e)
    elif preparacion["insumos_afectados"]:
        try:
            _emitir_progreso(on_progress, "Estoy recalculando solo lo que cambió en inventario.")
            corregir_inventario_insumos(
                fecha,
                preparacion["insumos_afectados"],
                preparacion["consumo_agrupado_final"],
                preparacion["registros"],
                preparacion["ubicaciones"],
                cache=sheets_cache,
            )
            _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
        except Exception as e:
            return _formatear_error_usuario("❌ Ventas actualizadas, pero falló la corrección de inventario", e)

    if image_path or image_bytes:
        _guardar_imagen_cierre(fecha, image_path=image_path, image_bytes=image_bytes)

    try:
        _emitir_progreso(on_progress, "Estoy verificando que todo haya quedado cuadrado.")
        diferencias = leer_diferencias_inventario_dia(fecha, cache=sheets_cache)
        advertencia_diferencias = ""
    except Exception as e:
        diferencias = {"C1": [], "C2": [], "LINEA CALIENTE": []}
        advertencia_diferencias = _formatear_error_usuario("⚠️ No se pudieron leer las diferencias finales del inventario", e)

    _guardar_historial_cierre(
        fecha,
        preparacion["ventas_finales"],
        preparacion["consumo_agrupado_final"],
        diferencias,
        metadata={
            "ticket_tipo": "precierre" if preparacion.get("precierre") else "cierre",
            "origen": preparacion.get("origen_actualizacion", "actualizacion"),
        },
    )

    lineas = [f"✅ ACTUALIZACIÓN APLICADA — {fecha}", "=" * 40]
    lineas.append(f"\n🧾 Ventas modificadas ({len(preparacion['cambios'])}):")
    lineas.extend(_formatear_cambios_ventas(preparacion["cambios"]))
    lineas.append(f"\n🔧 Insumos recalculados: {len(preparacion['insumos_afectados'])}")
    if preparacion["insumos_afectados"]:
        for insumo in preparacion["insumos_afectados"]:
            lineas.append(f"   • {insumo}")

    if advertencia_diferencias:
        lineas.append("")
        lineas.append(advertencia_diferencias)
    lineas.extend(_formatear_diferencias_inventario(diferencias))
    lineas.append("")
    lineas.append(_recordatorio_edicion_manual())
    return "\n".join(lineas)


# ============================================================
# Actualizaciones incrementales de ventas
# ============================================================

def preparar_actualizacion_ticket(image_path: str = None, image_bytes: bytes = None,
                                  media_type: str = "image/jpeg", fecha: str = None,
                                  rollitos_override: dict | None = None,
                                  precierre: bool = False,
                                  contexto: dict | None = None) -> dict:
    contexto = _normalizar_contexto(contexto)
    if not fecha:
        fecha = sugerir_fecha()[0]

    prep_ticket = preparar_cierre(
        image_path=image_path,
        image_bytes=image_bytes,
        media_type=media_type,
        fecha=fecha,
        rollitos_override=rollitos_override,
        precierre=precierre,
        contexto=contexto,
    )
    if not prep_ticket["ok"]:
        return prep_ticket

    try:
        recetas = _ctx_get_recetas(contexto)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer recetas", e)}

    actualizacion = _preparar_actualizacion_ventas(
        fecha=fecha,
        ventas_finales=prep_ticket["ventas"],
        consumo_final=prep_ticket["consumo"],
        consumo_agrupado_final=prep_ticket["consumo_agrupado"],
        alertas_finales=prep_ticket["alertas"],
        requiere_aclaracion=prep_ticket["requiere_aclaracion"],
        recetas=recetas,
        origen="ticket_nuevo",
        descripcion="Actualización desde ticket",
        precierre=precierre,
        contexto=contexto,
    )
    if actualizacion.get("ok"):
        actualizacion["ventas_originales"] = prep_ticket.get("ventas_originales", prep_ticket["ventas"])
    return actualizacion


def confirmar_actualizacion_ticket(preparacion: dict, image_path: str = None,
                                   image_bytes: bytes = None,
                                   contexto: dict | None = None,
                                   on_progress=None) -> str:
    return _confirmar_actualizacion_ventas(
        preparacion,
        image_path=image_path,
        image_bytes=image_bytes,
        contexto=contexto,
        on_progress=on_progress,
    )


def preparar_ajuste_ventas(fecha: str = None, ajustes: list[dict] | None = None,
                           contexto: dict | None = None) -> dict:
    contexto = _normalizar_contexto(contexto)
    if not ajustes:
        return {"ok": False, "resumen": "❌ Debes indicar al menos un ajuste de ventas."}

    if not fecha:
        fecha = sugerir_fecha()[0]

    try:
        recetas = _ctx_get_recetas(contexto)
        ventas_actuales = _ctx_get_ventas_neola(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer datos", e)}

    try:
        ventas_finales = _aplicar_ajustes_ventas(ventas_actuales, ajustes, recetas=recetas)
    except ValueError as e:
        return {"ok": False, "resumen": f"❌ {str(e)}"}

    try:
        registros = _ctx_get_registros(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer registros", e)}

    datos_finales = _preparar_datos_cierre(
        ventas_finales,
        fecha,
        recetas,
        registros=registros,
    )

    preparacion = _preparar_actualizacion_ventas(
        fecha=fecha,
        ventas_finales=ventas_finales,
        consumo_final=datos_finales["consumo"],
        consumo_agrupado_final=datos_finales["consumo_agrupado"],
        alertas_finales=datos_finales["alertas"],
        requiere_aclaracion=datos_finales["requiere_aclaracion"],
        recetas=recetas,
        origen="ajuste_manual",
        descripcion="Ajuste manual de ventas",
        contexto=contexto,
    )
    if preparacion.get("ok"):
        preparacion["ajustes"] = ajustes
    return preparacion


def confirmar_ajuste_ventas(preparacion: dict, contexto: dict | None = None,
                            on_progress=None) -> str:
    return _confirmar_actualizacion_ventas(
        preparacion,
        contexto=contexto,
        on_progress=on_progress,
    )


def preparar_registro_corregido(fecha: str = None, avisos: list[dict] | None = None,
                                contexto: dict | None = None) -> dict:
    contexto = _normalizar_contexto(contexto)
    if not avisos:
        return {"ok": False, "resumen": "❌ Debes indicar al menos un aviso de registro corregido."}

    if not fecha:
        fecha = sugerir_fecha()[0]

    try:
        registros_actuales = _ctx_get_registros(contexto, fecha)
        insumos_registro = _ctx_get_insumos_registro(contexto, fecha)
        ventas_actuales = _ctx_get_ventas_neola(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer datos del día", e)}

    try:
        confirmados, diferencias_aviso = _verificar_avisos_registros(registros_actuales, avisos, insumos_disponibles=insumos_registro)
    except ValueError as e:
        return {"ok": False, "resumen": f"❌ {str(e)}"}

    if diferencias_aviso:
        lineas = [f"❌ Todavía no veo esa corrección en la hoja — {fecha}", "=" * 40]
        lineas.append("")
        lineas.append("Revisa la edición manual y vuelve a avisarme cuando ya esté guardada.")
        lineas.append("")
        lineas.append("Diferencias detectadas:")
        lineas.extend(_formatear_diferencias_aviso_registro(diferencias_aviso))
        return {"ok": False, "resumen": "\n".join(lineas)}

    modo_ventas = "final_ticket" if ventas_actuales else "provisional_registros"
    total_ventas = sum(venta.get("cantidad", 0) or 0 for venta in ventas_actuales)
    consumo_agrupado = {}
    alertas = []
    if ventas_actuales:
        try:
            recetas = leer_recetas()
            datos = _preparar_datos_cierre(
                ventas_actuales,
                fecha,
                recetas,
                registros=registros_actuales,
            )
            consumo_agrupado = datos["consumo_agrupado"]
            alertas = datos["alertas"]
        except Exception as e:
            return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al recalcular con los registros corregidos", e)}

    lineas = [f"📝 REGISTRO CORREGIDO MANUALMENTE — {fecha}", "=" * 40]
    lineas.append("")
    lineas.append("Correcciones confirmadas en la hoja:")
    lineas.extend(_formatear_confirmaciones_registro(confirmados))

    if ventas_actuales:
        lineas.append("")
        lineas.append(
            f"Después de esto, se resincronizará el inventario usando "
            f"las ventas ya cargadas ({total_ventas} unidades)."
        )
    else:
        lineas.append("")
        lineas.append("Todavía no hay ventas cargadas para ese día.")
        lineas.append("El inventario se volverá a calcular solo desde registros.")
        lineas.append("LINEA CALIENTE seguirá pendiente del ticket final.")

    if alertas:
        lineas.append("")
        lineas.append("⚠️ Alertas:")
        for alerta in alertas:
            lineas.append(f"   {alerta}")

    lineas.append("")
    lineas.append("=" * 40)
    lineas.append(_mensaje_cuidado_hojas())
    lineas.append("¿Todo correcto? ¿Releo el día y actualizo el inventario?")

    return {
        "ok": True,
        "fecha": fecha,
        "avisos": avisos,
        "ventas_actuales": ventas_actuales,
        "registros_actuales": registros_actuales,
        "insumos_registro": insumos_registro,
        "consumo_agrupado": consumo_agrupado,
        "modo_ventas": modo_ventas,
        "resumen": "\n".join(lineas),
    }


def confirmar_registro_corregido(preparacion: dict, contexto: dict | None = None,
                                 on_progress=None) -> str:
    fecha = preparacion["fecha"]
    contexto = _normalizar_contexto(contexto)
    sheets_cache = _ctx_sheets_cache(contexto)

    try:
        _emitir_progreso(on_progress, "Estoy releyendo los registros corregidos.")
        registros_actualizados = leer_registros_dia_completo(fecha)
        insumos_registro = leer_insumos_registro_dia_completo(fecha)
        ventas_actuales = leer_ventas_neola_dia(fecha)
        confirmados, diferencias_aviso = _verificar_avisos_registros(
            registros_actualizados,
            preparacion["avisos"],
            insumos_disponibles=insumos_registro,
        )
    except Exception as e:
        return _formatear_error_usuario("❌ Error al releer los registros corregidos", e)

    if diferencias_aviso:
        lineas = [f"❌ La hoja cambió y ya no coincide con el aviso — {fecha}", "=" * 40]
        lineas.append("")
        lineas.append("Antes de seguir, revisa otra vez la corrección manual en la hoja.")
        lineas.append("")
        lineas.append("Diferencias detectadas:")
        lineas.extend(_formatear_diferencias_aviso_registro(diferencias_aviso))
        return "\n".join(lineas)

    modo_ventas = "final_ticket" if ventas_actuales else "provisional_registros"
    consumo_agrupado = {}
    try:
        if ventas_actuales:
            recetas = leer_recetas()
            datos = _preparar_datos_cierre(
                ventas_actuales,
                fecha,
                recetas,
                registros=registros_actualizados,
            )
            consumo_agrupado = datos["consumo_agrupado"]

        _emitir_progreso(on_progress, "Estoy actualizando el inventario.")
        ubicaciones = leer_ubicacion_descuento()
        escribir_inventario_dia(
            fecha,
            consumo_agrupado,
            registros_actualizados,
            ubicaciones,
            modo_ventas=modo_ventas,
            cache=sheets_cache,
        )
        _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
    except Exception as e:
        return _formatear_error_usuario("❌ La corrección manual se leyó, pero falló la actualización del inventario", e)

    try:
        _emitir_progreso(on_progress, "Estoy verificando que todo haya quedado cuadrado.")
        diferencias = leer_diferencias_inventario_dia(fecha, cache=sheets_cache)
        advertencia = ""
    except Exception as e:
        diferencias = {"C1": [], "C2": [], "LINEA CALIENTE": []}
        advertencia = _formatear_error_usuario("⚠️ No se pudieron leer las diferencias finales del inventario", e)

    historial_previo = _leer_historial_cierre(fecha) or {}
    metadata_historial = dict(historial_previo.get("metadata") or {})
    metadata_historial["origen"] = "registro_corregido_manual"
    if ventas_actuales:
        _guardar_historial_cierre(
            fecha,
            ventas_actuales,
            consumo_agrupado,
            diferencias,
            metadata=metadata_historial,
        )

    lineas = [f"✅ REGISTRO REVISADO — {fecha}", "=" * 40]
    lineas.append("")
    lineas.append("Correcciones confirmadas:")
    lineas.extend(_formatear_confirmaciones_registro(confirmados))
    lineas.append("")
    if ventas_actuales:
        total_ventas = sum(venta.get("cantidad", 0) or 0 for venta in ventas_actuales)
        lineas.append(
            f"Inventario resincronizado con las ventas ya cargadas ({total_ventas} unidades)."
        )
    else:
        lineas.append("Inventario recalculado solo desde registros.")

    if advertencia:
        lineas.append("")
        lineas.append(advertencia)
    lineas.extend(_formatear_diferencias_inventario(diferencias))
    lineas.append("")
    lineas.append(_recordatorio_edicion_manual())
    return "\n".join(lineas)


# ============================================================
# Opción 2: Inventario solo desde registros (sin ticket)
# ============================================================

def preparar_inventario_registros(fecha: str = None, contexto: dict | None = None) -> dict:
    """
    Preview de inventario usando solo registros del día. Sin foto de ticket.
    No escribe nada en Sheets.
    """
    contexto = _normalizar_contexto(contexto)
    if not fecha:
        fecha = sugerir_fecha()[0]

    try:
        registros = _ctx_get_registros(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer registros", e)}

    total_movimientos = sum(len(r) for r in registros.values())
    if total_movimientos == 0:
        return {
            "ok": False,
            "resumen": f"❌ No hay registros para el {fecha}. Primero ingresa los registros del día.",
        }

    try:
        ubicaciones = _ctx_get_ubicaciones(contexto)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer ubicaciones", e)}

    lineas = [f"📋 INVENTARIO DESDE REGISTROS — {fecha}"]
    lineas.append("=" * 40)

    for ubicacion_key, hoja_nombre in [("C1", "C1"), ("C2", "C2"), ("LINEA", "LINEA CALIENTE")]:
        insumos = registros.get(ubicacion_key, {})
        if not insumos:
            continue
        lineas.append(f"\n📦 {hoja_nombre}:")
        for insumo, datos in insumos.items():
            partes = []
            if datos.get("ingreso", 0):
                partes.append(f"ingreso={datos['ingreso']}")
            if datos.get("salida", 0):
                partes.append(f"salida={datos['salida']}")
            if datos.get("conteo") is not None:
                partes.append(f"conteo={datos['conteo']}")
            if _motivos_ingreso_registro(datos):
                resumen = ", ".join(
                    f"{item.get('motivo')} ({int(item.get('cantidad', 0) or 0)})"
                    for item in _motivos_ingreso_registro(datos)
                )
                partes.append(f"ingresos especiales: {resumen}")
            if _motivos_salida_registro(datos):
                resumen = ", ".join(
                    f"{item.get('motivo')} ({int(item.get('cantidad', 0) or 0)})"
                    for item in _motivos_salida_registro(datos)
                )
                partes.append(f"salidas especiales: {resumen}")
            lineas.append(f"   • {insumo}: {', '.join(partes)}")

    lineas.append(f"\n{'=' * 40}")
    lineas.append(f"Se crearán las entradas del {fecha} en C1, C2 y LINEA CALIENTE.")
    lineas.append("En C1 y C2, VENTAS provisional se llenará desde SALIDA registrada.")
    lineas.append("Los movimientos especiales se leerán desde MOTIVOS ESPECIALES.")
    lineas.append("Si un insumo se descuenta en LINEA según UBICACION DESCUENTO, la salida especial solo reduce el INGRESO que pasa a línea.")
    lineas.append("LINEA CALIENTE quedará pendiente del ticket para completar VENTAS.")
    lineas.append(_mensaje_cuidado_hojas())
    lineas.append("\n¿Todo correcto? ¿Procedo?")

    return {
        "ok": True,
        "fecha": fecha,
        "registros": registros,
        "ubicaciones": ubicaciones,
        "resumen": "\n".join(lineas),
    }


def confirmar_inventario_registros(preparacion: dict, contexto: dict | None = None,
                                   on_progress=None) -> str:
    """Escribe el inventario del día usando solo registros con ventas provisionales."""
    fecha = preparacion["fecha"]
    registros = preparacion["registros"]
    ubicaciones = preparacion["ubicaciones"]
    contexto = _normalizar_contexto(contexto)
    sheets_cache = _ctx_sheets_cache(contexto)

    try:
        _emitir_progreso(on_progress, "Estoy creando las entradas del día con los registros.")
        escribir_inventario_dia(
            fecha,
            {},
            registros,
            ubicaciones,
            modo_ventas="provisional_registros",
            cache=sheets_cache,
        )
        _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
    except Exception as e:
        return _formatear_error_usuario("❌ Error al escribir inventario", e)

    try:
        _emitir_progreso(on_progress, "Estoy verificando que todo haya quedado cuadrado.")
        diferencias = leer_diferencias_inventario_dia(fecha, cache=sheets_cache)
        advertencia_diferencias = ""
    except Exception as e:
        diferencias = {"C1": [], "C2": [], "LINEA CALIENTE": []}
        advertencia_diferencias = _formatear_error_usuario("⚠️ No se pudieron leer las diferencias finales del inventario", e)

    lineas = [f"✅ INVENTARIO CREADO — {fecha}"]
    lineas.append("Entradas creadas en C1, C2 y LINEA CALIENTE.")
    lineas.append("C1 y C2 quedaron con VENTAS provisional desde SALIDA registrada.")
    lineas.append("Las transferencias a LINEA y las salidas directas a plato se seguirán distinguiendo según UBICACION DESCUENTO.")
    lineas.append("LINEA CALIENTE sigue pendiente del ticket para completar VENTAS.")
    if advertencia_diferencias:
        lineas.append(advertencia_diferencias)
    lineas.extend(_formatear_diferencias_inventario(diferencias))
    lineas.append("")
    lineas.append(_recordatorio_edicion_manual())
    return "\n".join(lineas)


# ============================================================
# Opción 1 Camino C: Solo cargar ventas a entrada existente
# ============================================================

def preparar_solo_ventas(image_path: str = None, image_bytes: bytes = None,
                         media_type: str = "image/jpeg", fecha: str = None,
                         rollitos_override: dict | None = None,
                         precierre: bool = False,
                         contexto: dict | None = None) -> dict:
    """
    Preview de ventas para cargar a una entrada de inventario ya existente.
    Requiere que la entrada del día ya exista (creada con solo-registros).
    """
    contexto = _normalizar_contexto(contexto)
    if not fecha:
        fecha = sugerir_fecha()[0]

    # Verificar que la entrada del día existe
    try:
        existencia = _ctx_get_existencia_inventario(contexto, fecha)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al verificar inventario", e)}

    hojas_faltantes = [hoja for hoja, existe in existencia.items() if not existe]
    if hojas_faltantes:
        return {
            "ok": False,
            "resumen": (
                f"❌ No existe la entrada del {fecha} en: {', '.join(hojas_faltantes)}.\n"
                "Primero crea la entrada del día con los registros."
            ),
        }

    # Preparar cierre normal (parsear ticket + calcular consumo)
    prep = preparar_cierre(
        image_path=image_path,
        image_bytes=image_bytes,
        media_type=media_type,
        fecha=fecha,
        rollitos_override=rollitos_override,
        precierre=precierre,
        contexto=contexto,
    )
    if not prep["ok"]:
        return prep

    try:
        recetas = _ctx_get_recetas(contexto)
    except Exception as e:
        return {"ok": False, "resumen": _formatear_error_usuario("❌ Error al leer recetas", e)}

    actualizacion = _preparar_actualizacion_ventas(
        fecha=fecha,
        ventas_finales=prep["ventas"],
        consumo_final=prep["consumo"],
        consumo_agrupado_final=prep["consumo_agrupado"],
        alertas_finales=prep["alertas"],
        requiere_aclaracion=prep["requiere_aclaracion"],
        recetas=recetas,
        origen="solo_ventas",
        descripcion="Cargar ventas",
        precierre=precierre,
        contexto=contexto,
    )
    if actualizacion.get("ok"):
        lineas = actualizacion["resumen"].splitlines()
        lineas.insert(2, "La entrada del día ya existe. Solo se actualizarán las VENTAS.")
        actualizacion["resumen"] = "\n".join(lineas)
        actualizacion["solo_ventas"] = True
        actualizacion["ventas_originales"] = prep.get("ventas_originales", prep["ventas"])
    return actualizacion


def confirmar_solo_ventas(preparacion: dict, image_path: str = None,
                          image_bytes: bytes = None,
                          on_progress=None) -> str:
    """Carga las ventas del ticket a una entrada de inventario ya existente."""
    return _confirmar_actualizacion_ventas(
        preparacion,
        image_path=image_path,
        image_bytes=image_bytes,
        on_progress=on_progress,
    )


# ============================================================
# Funciones legacy (para uso directo desde terminal)
# ============================================================

def ejecutar_cierre(image_path: str = None, image_bytes: bytes = None,
                     media_type: str = "image/jpeg", fecha: str = None,
                     rollitos_override: dict | None = None,
                     precierre: bool = False,
                     on_progress=None) -> str:
    """Cierre completo sin confirmación (para terminal/testing)."""
    contexto = {}
    _emitir_progreso(on_progress, "Estoy revisando la imagen del ticket.")
    prep = preparar_cierre(image_path=image_path, image_bytes=image_bytes,
                            media_type=media_type, fecha=fecha,
                            rollitos_override=rollitos_override,
                            precierre=precierre,
                            contexto=contexto)
    if not prep["ok"]:
        return prep["resumen"]
    return confirmar_cierre(
        prep,
        image_path=image_path,
        image_bytes=image_bytes,
        rollitos_override=rollitos_override,
        contexto=contexto,
        on_progress=on_progress,
    )


def corregir_inventario_por_insumos(image_path: str = None, image_bytes: bytes = None,
                                    media_type: str = "image/jpeg", fecha: str = None,
                                    insumos: list[str] | None = None,
                                    rollitos_override: dict | None = None,
                                    on_progress=None) -> str:
    if not insumos:
        return "❌ Debes indicar al menos un insumo para corregir."

    contexto = {}
    if not fecha:
        fecha = sugerir_fecha()[0]

    _emitir_progreso(on_progress, "Estoy revisando la imagen del ticket.")
    prep = preparar_cierre(
        image_path=image_path,
        image_bytes=image_bytes,
        media_type=media_type,
        fecha=fecha,
        rollitos_override=rollitos_override,
        contexto=contexto,
    )
    if not prep["ok"]:
        return prep["resumen"]

    try:
        registros = _ctx_get_registros(contexto, fecha)
        ubicaciones = _ctx_get_ubicaciones(contexto)
        _emitir_progreso(on_progress, "Estoy corrigiendo los insumos indicados.")
        actualizados = corregir_inventario_insumos(
            fecha,
            insumos,
            prep["consumo_agrupado"],
            registros,
            ubicaciones,
            cache=_ctx_sheets_cache(contexto),
        )
        _emitir_progreso(on_progress, "Estoy terminando de guardar el inventario.")
        _emitir_progreso(on_progress, "Estoy verificando que todo haya quedado cuadrado.")
        diferencias = leer_diferencias_inventario_dia(fecha, cache=_ctx_sheets_cache(contexto))
    except Exception as e:
        return _formatear_error_usuario("❌ Error al corregir inventario puntual", e)

    lineas = [f"🛠️ CORRECCIÓN PUNTUAL DE INVENTARIO — {fecha}"]
    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        insumos_hoja = actualizados.get(hoja, [])
        if insumos_hoja:
            lineas.append(f"{hoja}: {', '.join(insumos_hoja)}")

    lineas.extend(_formatear_diferencias_inventario(diferencias))
    return "\n".join(lineas)


def solo_parsear_ticket(image_path: str = None, image_bytes: bytes = None,
                         media_type: str = "image/jpeg") -> str:
    try:
        ventas, diagnostico_lectura = _parsear_ticket_desde_imagen(
            image_path=image_path,
            image_bytes=image_bytes,
            media_type=media_type,
        )
    except Exception as e:
        return _formatear_error_usuario("❌ Error al leer el ticket", e)

    lineas = []
    alertas_lectura = _alertas_lectura_para_cliente(diagnostico_lectura)
    if alertas_lectura:
        for alerta in alertas_lectura:
            lineas.append(alerta)
        for sugerencia in _sugerencias_lectura_para_cliente(diagnostico_lectura):
            lineas.append(sugerencia)
        lineas.append("")

    lineas.append(f"🔍 Platos detectados ({sum(v['cantidad'] for v in ventas)} unidades):\n")
    for v in ventas:
        lineas.append(f"   • {v['plato']} x{v['cantidad']} — ${v['precio_total']:.2f}")
    lineas.append(f"\nTotal: ${sum(v['precio_total'] for v in ventas):.2f}")
    return "\n".join(lineas)


def solo_consumo_teorico(image_path: str = None, image_bytes: bytes = None,
                          media_type: str = "image/jpeg", fecha: str = None,
                          rollitos_override: dict | None = None,
                          usar_registros_rollitos: bool = False,
                          contexto: dict | None = None) -> str:
    contexto = _normalizar_contexto(contexto)
    try:
        ventas, diagnostico_lectura = _parsear_ticket_desde_imagen(
            image_path=image_path,
            image_bytes=image_bytes,
            media_type=media_type,
        )
    except Exception as e:
        return _formatear_error_usuario("❌ Error al leer el ticket", e)

    try:
        recetas = _ctx_get_recetas(contexto)
    except Exception as e:
        return _formatear_error_usuario("❌ Error al leer recetas", e)

    fecha_base = fecha or sugerir_fecha()[0]
    registros = None
    if usar_registros_rollitos and _cantidad_rollitos_ambiguos(ventas) and not rollitos_override:
        try:
            registros = _ctx_get_registros(contexto, fecha_base)
        except Exception:
            registros = None

    datos = _preparar_datos_cierre(
        ventas,
        fecha_base,
        recetas,
        rollitos_override=rollitos_override,
        registros=registros,
        permitir_pendiente_rollitos=True,
    )
    consumo = datos["consumo"]
    agrupado = datos["consumo_agrupado"]
    alertas = _alertas_lectura_para_cliente(diagnostico_lectura) + datos["alertas"]
    ventas = datos["ventas"]

    lineas = [f"📦 CONSUMO TEÓRICO DE INSUMOS"]
    lineas.append("=" * 40)
    lineas.append(f"Basado en {sum(v['cantidad'] for v in ventas)} platos vendidos\n")
    lineas.append("🍽️ Por plato:")
    lineas.extend(_formatear_desglose_por_plato(ventas, consumo, recetas))
    lineas.append(_titulo_total_insumos(agrupado))
    lineas.extend(_formatear_totales_por_insumo(agrupado))

    if alertas:
        lineas.append(f"\n⚠️ Alertas:")
        for a in alertas:
            lineas.append(f"   {a}")

    if _cantidad_rollitos_ambiguos(ventas):
        lineas.append("\n❓ Tipo de rollitos vendidos por confirmar.")
        lineas.append("¿Cuántos fueron de pollo y cuántos de queso?")

    sugerencias_lectura = _sugerencias_lectura_para_cliente(diagnostico_lectura)
    if sugerencias_lectura:
        lineas.append("")
        lineas.extend(sugerencias_lectura)

    return "\n".join(lineas)
