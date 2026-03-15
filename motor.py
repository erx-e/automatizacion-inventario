# motor.py — Motor de Cierre Diario de Inventario Sambó
import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import UMBRAL_DESCUADRE
from parser_neola import parsear_foto_ticket, parsear_foto_bytes
from recetas import (
    agrupar_consumo_por_insumo,
    buscar_receta,
    calcular_consumo_teorico,
    es_ensalada_cesar_sin_proteina,
    plato_ignorado,
    resolver_variantes_receta,
    sugerir_receta_similar,
)
from sheets_connector import (
    leer_recetas, leer_ubicacion_descuento,
    leer_registros_dia_completo, escribir_ventas_neola,
    escribir_inventario_dia, leer_diferencias_inventario_dia,
    corregir_inventario_insumos
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
                               diferencias: dict | None = None):
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
    }

    destino = carpeta / f"{nombre_carpeta}.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)


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
    ventas_resueltas, alertas_rollitos, requiere_aclaracion = _resolver_rollitos_rellenos(
        ventas_originales,
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
                     rollitos_override: dict | None = None) -> dict:
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
    resultado = {
        "ok": False,
        "fecha": "",
        "fecha_motivo": "",
        "fecha_origen": "",
        "ventas_originales": [],
        "ventas": [],
        "consumo": [],
        "consumo_agrupado": {},
        "alertas": [],
        "requiere_aclaracion": False,
        "rollitos_override": _normalizar_rollitos_override(rollitos_override),
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
        if image_bytes:
            ventas = parsear_foto_bytes(image_bytes, media_type)
        elif image_path:
            ventas = parsear_foto_ticket(image_path)
        else:
            resultado["resumen"] = "❌ No se recibió imagen del ticket"
            return resultado
    except Exception as e:
        resultado["resumen"] = f"❌ Error al leer el ticket: {str(e)}"
        return resultado

    resultado["ventas_originales"] = list(ventas)

    # Calcular consumo teórico
    try:
        recetas = leer_recetas()
    except Exception as e:
        resultado["resumen"] = f"❌ Error al leer recetas: {str(e)}"
        return resultado

    registros = None
    if _cantidad_rollitos_ambiguos(ventas):
        try:
            registros = leer_registros_dia_completo(resultado["fecha"])
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
    resultado["alertas"] = datos["alertas"]
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

    if resultado["requiere_aclaracion"]:
        lineas.append(f"\n{'=' * 40}")
        if any("Rollitos rellenos ambiguos" in alerta for alerta in resultado["alertas"]):
            lineas.append("❌ No se puede continuar hasta aclarar los rollitos rellenos.")
            lineas.append("Indica cuántos fueron de pollo y cuántos de queso.")
            lineas.append("Ejemplo: --rollitos-pollo 1 --rollitos-queso 1")
        elif requiere_receta:
            lineas.append("❌ No se puede continuar hasta confirmar la receta similar sugerida.")
            lineas.append("Si confirmas que la receta sugerida corresponde al plato del ticket, actualiza el nombre en recetas y vuelve a correr el cierre.")
        else:
            lineas.append("❌ No se puede continuar hasta aclarar la información pendiente.")
    else:
        # Pregunta de confirmación
        lineas.append(f"\n{'=' * 40}")
        lineas.append(f"¿Todo correcto? Responde:")
        lineas.append(f"   'si' → proceder con el cierre")
        lineas.append(f"   'fecha YYYY-MM-DD' → cambiar la fecha")
        lineas.append(f"   'no' → cancelar")

    resultado["resumen"] = "\n".join(lineas)
    return resultado


def confirmar_cierre(preparacion: dict, fecha_override: str = None,
                     image_path: str = None, image_bytes: bytes = None,
                     rollitos_override: dict | None = None) -> str:
    """
    PASO 2 del flujo: ejecuta el cierre con los datos ya preparados.
    Se llama después de que el usuario confirma.
    Guarda la imagen del ticket en cierres-diarios/{fecha}/{fecha}.ext
    """
    fecha = fecha_override or preparacion["fecha"]

    try:
        recetas = leer_recetas()
    except Exception as e:
        return f"❌ Error al leer recetas: {str(e)}"

    # Guardar imagen del cierre
    _guardar_imagen_cierre(fecha, image_path, image_bytes)

    reporte = []
    reporte.append(f"📊 CIERRE DE INVENTARIO — {fecha}")
    reporte.append("=" * 40)

    # Leer registros del día
    reporte.append("\n📦 Leyendo registros del día...")
    try:
        registros = leer_registros_dia_completo(fecha)
    except Exception as e:
        reporte.append(f"⚠️ No se pudieron leer registros: {str(e)}")
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
        ubicaciones_defecto = leer_ubicacion_descuento()
    except Exception as e:
        reporte.append(f"⚠️ No se pudo leer tabla de ubicaciones: {str(e)}")
        ubicaciones_defecto = {}

    # Conciliar
    reporte.append("\n🔄 Conciliando inventario...")

    descuadres = []
    movimientos_extraordinarios = []

    for insumo, datos in consumo_agrupado.items():
        consumo_teorico = datos["total"]
        ubicacion_defecto = ubicaciones_defecto.get(insumo, datos["ubicacion"])

        salida_c1 = registros["C1"].get(insumo, {}).get("salida", 0)
        salida_c2 = registros["C2"].get(insumo, {}).get("salida", 0)
        salida_total_cong = salida_c1 + salida_c2

        datos_linea = registros["LINEA"].get(insumo, {})
        salida_linea = datos_linea.get("salida", 0)

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
            if datos_reg["motivo"]:
                movimientos_extraordinarios.append({
                    "insumo": insumo,
                    "ubicacion": ubicacion,
                    "ingreso": datos_reg["ingreso"],
                    "salida": datos_reg["salida"],
                    "motivo": datos_reg["motivo"]
                })

    # Escribir en Google Sheets
    reporte.append("\n💾 Guardando en Google Sheets...")

    ventas_ok = False
    try:
        escribir_ventas_neola(fecha, ventas, consumo)
        ventas_ok = True
        reporte.append("✅ Ventas Neola actualizado")
    except Exception as e:
        reporte.append(f"⚠️ Error al guardar ventas: {str(e)}")

    if ventas_ok:
        try:
            escribir_inventario_dia(fecha, consumo_agrupado, registros, ubicaciones_defecto)
            reporte.append("✅ Inventario diario actualizado")
        except Exception as e:
            reporte.append(f"⚠️ Error al guardar inventario: {str(e)}")
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
        diferencias_finales = leer_diferencias_inventario_dia(fecha)
    except Exception as e:
        diferencias_finales = None
        reporte.append(f"\n⚠️ No se pudieron leer las diferencias finales del inventario: {str(e)}")

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
            tipo = "Ingreso" if m["ingreso"] else "Salida"
            cant = m["ingreso"] or m["salida"]
            reporte.append(
                f"   • {m['insumo']} ({m['ubicacion']}): {tipo} {cant} — {m['motivo']}"
            )

    if alertas_recetas:
        reporte.append(f"\n⚠️ Alertas:")
        for a in alertas_recetas:
            reporte.append(f"   {a}")

    # Guardar historial JSON del cierre
    _guardar_historial_cierre(fecha, ventas, consumo_agrupado, diferencias_finales)

    return "\n".join(reporte)


def preparar_correccion(image_path: str = None, image_bytes: bytes = None,
                        media_type: str = "image/jpeg", fecha: str = None,
                        insumos: list[str] | None = None,
                        rollitos_override: dict | None = None) -> dict:
    """Preview de corrección puntual sin escribir en Google Sheets."""
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
    )
    if not prep["ok"]:
        return prep

    try:
        registros = leer_registros_dia_completo(fecha)
        ubicaciones = leer_ubicacion_descuento()
    except Exception as e:
        return {"ok": False, "resumen": f"❌ Error al leer datos: {str(e)}"}

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


def confirmar_correccion(preparacion: dict) -> str:
    """Ejecuta la corrección puntual con los datos ya preparados."""
    fecha = preparacion["fecha"]
    insumos = preparacion["insumos"]

    try:
        actualizados = corregir_inventario_insumos(
            fecha, insumos, preparacion["consumo_agrupado"],
            preparacion["registros"], preparacion["ubicaciones"],
        )
        diferencias = leer_diferencias_inventario_dia(fecha)
    except Exception as e:
        return f"❌ Error al corregir inventario puntual: {str(e)}"

    lineas = [f"🛠️ CORRECCIÓN PUNTUAL DE INVENTARIO — {fecha}"]
    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        insumos_hoja = actualizados.get(hoja, [])
        if insumos_hoja:
            lineas.append(f"{hoja}: {', '.join(insumos_hoja)}")

    lineas.extend(_formatear_diferencias_inventario(diferencias))
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
    else:
        lineas.append("\nSin diferencias en C1, C2 y LINEA CALIENTE.")
    return lineas


# ============================================================
# Funciones legacy (para uso directo desde terminal)
# ============================================================

def ejecutar_cierre(image_path: str = None, image_bytes: bytes = None,
                     media_type: str = "image/jpeg", fecha: str = None,
                     rollitos_override: dict | None = None) -> str:
    """Cierre completo sin confirmación (para terminal/testing)."""
    prep = preparar_cierre(image_path=image_path, image_bytes=image_bytes,
                            media_type=media_type, fecha=fecha,
                            rollitos_override=rollitos_override)
    if not prep["ok"]:
        return prep["resumen"]
    return confirmar_cierre(
        prep,
        image_path=image_path,
        image_bytes=image_bytes,
        rollitos_override=rollitos_override,
    )


def corregir_inventario_por_insumos(image_path: str = None, image_bytes: bytes = None,
                                    media_type: str = "image/jpeg", fecha: str = None,
                                    insumos: list[str] | None = None,
                                    rollitos_override: dict | None = None) -> str:
    if not insumos:
        return "❌ Debes indicar al menos un insumo para corregir."

    if not fecha:
        fecha = sugerir_fecha()[0]

    prep = preparar_cierre(
        image_path=image_path,
        image_bytes=image_bytes,
        media_type=media_type,
        fecha=fecha,
        rollitos_override=rollitos_override,
    )
    if not prep["ok"]:
        return prep["resumen"]

    try:
        registros = leer_registros_dia_completo(fecha)
        ubicaciones = leer_ubicacion_descuento()
        actualizados = corregir_inventario_insumos(
            fecha,
            insumos,
            prep["consumo_agrupado"],
            registros,
            ubicaciones,
        )
        diferencias = leer_diferencias_inventario_dia(fecha)
    except Exception as e:
        return f"❌ Error al corregir inventario puntual: {str(e)}"

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
        if image_bytes:
            ventas = parsear_foto_bytes(image_bytes, media_type)
        elif image_path:
            ventas = parsear_foto_ticket(image_path)
        else:
            return "❌ No se recibió imagen"
    except Exception as e:
        return f"❌ Error: {str(e)}"

    lineas = [f"🔍 Platos detectados ({sum(v['cantidad'] for v in ventas)} unidades):\n"]
    for v in ventas:
        lineas.append(f"   • {v['plato']} x{v['cantidad']} — ${v['precio_total']:.2f}")
    lineas.append(f"\nTotal: ${sum(v['precio_total'] for v in ventas):.2f}")
    return "\n".join(lineas)


def solo_consumo_teorico(image_path: str = None, image_bytes: bytes = None,
                          media_type: str = "image/jpeg", fecha: str = None,
                          rollitos_override: dict | None = None,
                          usar_registros_rollitos: bool = False) -> str:
    try:
        if image_bytes:
            ventas = parsear_foto_bytes(image_bytes, media_type)
        elif image_path:
            ventas = parsear_foto_ticket(image_path)
        else:
            return "❌ No se recibió imagen"
    except Exception as e:
        return f"❌ Error: {str(e)}"

    try:
        recetas = leer_recetas()
    except Exception as e:
        return f"❌ Error al leer recetas: {str(e)}"

    fecha_base = fecha or sugerir_fecha()[0]
    registros = None
    if usar_registros_rollitos and _cantidad_rollitos_ambiguos(ventas) and not rollitos_override:
        try:
            registros = leer_registros_dia_completo(fecha_base)
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
    alertas = datos["alertas"]
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
        lineas.append("Indica cuántos fueron de pollo y cuántos de queso para agregar esos insumos al consumo final.")
        lineas.append("Ejemplo: --rollitos-pollo 1 --rollitos-queso 1")

    return "\n".join(lineas)
