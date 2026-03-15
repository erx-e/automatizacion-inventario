from datetime import datetime
import time
import gspread
from google.oauth2.service_account import Credentials
from config import (
    GOOGLE_CREDENTIALS_PATH, SHEET_REGISTROS, SHEET_RECETAS, SHEET_INVENTARIO,
    HOJA_REGISTRO_C1, HOJA_REGISTRO_C2, HOJA_REGISTRO_LINEA,
    HOJA_VENTAS_NEOLA, HOJA_UBICACION, HOJA_RECETAS,
    SHEETS_WRITE_DELAY_SECONDS,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

_client = None
VALORES_CONFIRMACION_RECETA = {"✅", "❓"}
MAX_INSUMOS_POR_PLATO_VENTAS = 6
COLUMNAS_VENTAS_NEOLA = 3 + (MAX_INSUMOS_POR_PLATO_VENTAS * 2)
MESES_ES = {
    1: "ENERO",
    2: "FEBRERO",
    3: "MARZO",
    4: "ABRIL",
    5: "MAYO",
    6: "JUNIO",
    7: "JULIO",
    8: "AGOSTO",
    9: "SEPTIEMBRE",
    10: "OCTUBRE",
    11: "NOVIEMBRE",
    12: "DICIEMBRE",
}


def _esperar_despues_de_write():
    if SHEETS_WRITE_DELAY_SECONDS > 0:
        time.sleep(SHEETS_WRITE_DELAY_SECONDS)

def get_client():
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


# ============================================================
# LECTURA DE TABLAS MAESTRAS
# ============================================================

def _leer_valores_hoja(ws, range_name: str | None = None) -> list[list[str]]:
    """
    Lee valores de una hoja expandiendo celdas combinadas cuando la versión
    de gspread lo soporta. Si no, cae al comportamiento legacy.
    """
    kwargs = {
        "combine_merged_cells": True,
        "pad_values": True,
    }
    if range_name:
        kwargs["range_name"] = range_name

    if hasattr(ws, "get_values"):
        try:
            return ws.get_values(**kwargs)
        except TypeError:
            pass

    if hasattr(ws, "get_all_values"):
        try:
            return ws.get_all_values(**kwargs)
        except TypeError:
            if range_name:
                return ws.get_all_values(range_name)
            return ws.get_all_values()

    return []


def _parsear_recetas_rows(rows: list[list[str]]) -> list[dict]:
    """
    Convierte las filas crudas de Google Sheets en la lista de recetas.
    Mantiene el plato y nombre de menú actuales para soportar tablas donde
    esos campos solo aparecen en la primera fila del bloque.
    """
    recetas = []
    plato_actual = ""
    nombre_actual = ""

    for raw_row in rows[2:]:
        row = list(raw_row) + [""] * max(0, 9 - len(raw_row))
        confirmado = row[7].strip()

        # Las filas de categoría o notas quedan "rellenadas" por las celdas
        # combinadas; la columna CONFIRMADO permite distinguir recetas reales.
        if confirmado not in VALORES_CONFIRMACION_RECETA:
            continue

        plato = row[0].strip() or plato_actual
        nombre = row[1].strip() or nombre_actual

        if plato:
            plato_actual = plato
        if nombre:
            nombre_actual = nombre

        sku = row[2].strip()
        insumo = row[3].strip()
        cantidad_raw = row[4].strip()
        unidad = row[5].strip()
        ubicacion = row[6].strip()

        if not plato_actual:
            continue

        try:
            cantidad = int(float(cantidad_raw)) if cantidad_raw else 0
        except ValueError:
            cantidad = 0

        recetas.append({
            "plato": plato_actual,
            "nombre_menu": nombre_actual,
            "sku": sku,
            "insumo": insumo,
            "cantidad": cantidad,
            "unidad": unidad,
            "ubicacion": ubicacion
        })

    return recetas


def leer_recetas() -> list[dict]:
    gc = get_client()
    sh = gc.open_by_key(SHEET_RECETAS)
    ws = sh.worksheet(HOJA_RECETAS)
    rows = _leer_valores_hoja(ws, f"A1:I{ws.row_count}")
    return _parsear_recetas_rows(rows)


def leer_ubicacion_descuento() -> dict:
    return {
        insumo: datos["descuento"]
        for insumo, datos in leer_tabla_ubicacion_descuento().items()
    }


def leer_tabla_ubicacion_descuento() -> dict:
    gc = get_client()
    sh = gc.open_by_key(SHEET_REGISTROS)
    ws = sh.worksheet(HOJA_UBICACION)
    rows = ws.get_all_values()

    ubicaciones = {}
    for row in rows[1:]:
        insumo_raw = row[0] if row else ""
        if not insumo_raw or insumo_raw.startswith(" "):
            continue

        insumo = insumo_raw.strip()
        almacen = row[2].strip() if len(row) > 2 and row[2] else ""
        descuento = row[3].strip() if len(row) > 3 and row[3] else almacen

        if insumo:
            ubicaciones[insumo] = {
                "almacen": almacen,
                "descuento": descuento,
            }

    return ubicaciones


# ============================================================
# HELPERS DE FECHAS Y PARSEO
# ============================================================

def _fecha_a_display(fecha: str) -> str:
    if len(fecha) == 10 and fecha[4] == "-" and fecha[7] == "-":
        return datetime.strptime(fecha, "%Y-%m-%d").strftime("%d-%m-%Y")
    return fecha


def _es_fecha_display(valor: str) -> bool:
    try:
        datetime.strptime(valor.strip(), "%d-%m-%Y")
        return True
    except (ValueError, AttributeError):
        return False


def _nombre_mes_es(fecha: str) -> str:
    fecha_display = _fecha_a_display(fecha)
    dt = datetime.strptime(fecha_display, "%d-%m-%Y")
    return MESES_ES[dt.month]


def _parsear_numero(raw: str) -> int:
    try:
        return int(float(raw)) if str(raw).strip() else 0
    except (ValueError, TypeError):
        return 0


def _tiene_dato(raw: str) -> bool:
    return bool(str(raw).strip())


# ============================================================
# LECTURA DE REGISTROS DIARIOS
# ============================================================

def _parsear_registro_rows(rows: list[list[str]], fecha: str) -> dict:
    if len(rows) < 3:
        return {}

    fecha_display = _fecha_a_display(fecha)
    fechas_row = rows[1]
    headers_row = [cell.strip().upper() for cell in rows[2]]

    col_base = None
    for c in range(2, len(fechas_row)):
        if fechas_row[c].strip() == fecha_display:
            col_base = c
            break

    if col_base is None:
        return {}

    modo_linea = col_base < len(headers_row) and headers_row[col_base] == "CONTEO"
    registros = {}
    for raw_row in rows[3:]:
        row = list(raw_row)
        insumo_raw = row[0] if row and row[0] else ""
        if not insumo_raw or insumo_raw.startswith(" "):
            continue
        insumo = insumo_raw.strip()

        if modo_linea:
            conteo_raw = row[col_base] if col_base < len(row) else ""
            ingreso_raw = row[col_base + 1] if (col_base + 1) < len(row) else ""
            salida_raw = row[col_base + 2] if (col_base + 2) < len(row) else ""
            motivo = row[col_base + 3] if (col_base + 3) < len(row) else ""
        else:
            conteo_raw = ""
            ingreso_raw = row[col_base] if col_base < len(row) else ""
            salida_raw = row[col_base + 1] if (col_base + 1) < len(row) else ""
            motivo = row[col_base + 2] if (col_base + 2) < len(row) else ""

        tiene_movimiento = any([
            _tiene_dato(ingreso_raw),
            _tiene_dato(salida_raw),
            _tiene_dato(motivo),
            modo_linea and _tiene_dato(conteo_raw),
        ])
        if not tiene_movimiento:
            continue

        registros[insumo] = {
            "conteo": _parsear_numero(conteo_raw) if modo_linea and _tiene_dato(conteo_raw) else None,
            "ingreso": _parsear_numero(ingreso_raw),
            "salida": _parsear_numero(salida_raw),
            "motivo": str(motivo).strip(),
        }

    return registros


def leer_registro_dia(hoja_nombre: str, fecha: str) -> dict:
    gc = get_client()
    sh = gc.open_by_key(SHEET_REGISTROS)
    ws = sh.worksheet(hoja_nombre)
    rows = _leer_valores_hoja(ws)
    return _parsear_registro_rows(rows, fecha)


def leer_registros_dia_completo(fecha: str) -> dict:
    return {
        "C1": leer_registro_dia(HOJA_REGISTRO_C1, fecha),
        "C2": leer_registro_dia(HOJA_REGISTRO_C2, fecha),
        "LINEA": leer_registro_dia(HOJA_REGISTRO_LINEA, fecha)
    }


# ============================================================
# ESCRITURA EN VENTAS NEOLA
# ============================================================

def _ultima_fila_no_vacia(rows: list[list[str]]) -> int:
    last_nonempty = 0
    for i, row in enumerate(rows, start=1):
        if any(str(cell).strip() for cell in row):
            last_nonempty = i
    return last_nonempty


def _buscar_fila_fecha_ventas(rows: list[list[str]], fecha: str) -> int | None:
    fecha_display = _fecha_a_display(fecha)
    for i, row in enumerate(rows, start=1):
        if row and row[0].strip() == fecha_display:
            return i
    return None


def _agrupar_ventas_neola(ventas: list[dict]) -> list[dict]:
    agrupadas = {}
    orden = []
    for venta in ventas:
        plato = venta["plato"]
        if plato not in agrupadas:
            agrupadas[plato] = {
                "plato": plato,
                "cantidad": 0,
                "precio_total": 0,
            }
            orden.append(plato)
        agrupadas[plato]["cantidad"] += venta.get("cantidad", 0)
        agrupadas[plato]["precio_total"] += venta.get("precio_total", 0)

    return [agrupadas[plato] for plato in orden]


def _longitud_bloque_existente_ventas(rows: list[list[str]], fila_fecha: int) -> int:
    total = 1
    for row in rows[fila_fecha:]:
        if not any(str(cell).strip() for cell in row):
            break
        primera = row[0].strip() if row else ""
        if primera and _es_fecha_display(primera):
            break
        total += 1
    return total


def _agrupar_consumo_para_neola(consumo: list[dict]) -> dict[str, list[dict]]:
    consumo_por_plato = {}
    for item in consumo:
        plato = item["plato"]
        if plato not in consumo_por_plato:
            consumo_por_plato[plato] = {}

        insumo = item["insumo"]
        if insumo not in consumo_por_plato[plato]:
            consumo_por_plato[plato][insumo] = {
                "insumo": insumo,
                "cantidad_total": 0,
            }

        consumo_por_plato[plato][insumo]["cantidad_total"] += item["cantidad_total"]

    return {
        plato: list(insumos.values())
        for plato, insumos in consumo_por_plato.items()
    }


def _estructura_esperada_ventas_neola(ventas: list[dict], consumo: list[dict]) -> dict[str, dict]:
    ventas_agrupadas = _agrupar_ventas_neola(ventas)
    consumo_por_plato = _agrupar_consumo_para_neola(consumo)

    esperado = {}
    for venta in ventas_agrupadas:
        plato = venta["plato"]
        esperado[plato] = {
            "cantidad": int(venta.get("cantidad", 0) or 0),
            "insumos": {
                item["insumo"]: int(item.get("cantidad_total", 0) or 0)
                for item in consumo_por_plato.get(plato, [])
            },
        }
    return esperado


def _estructura_actual_ventas_neola(rows_bloque: list[list[str]]) -> dict[str, dict]:
    actual = {}
    for row in rows_bloque[1:]:
        plato = row[0].strip() if row else ""
        if not plato:
            continue

        cantidad = _parsear_numero(row[1] if len(row) > 1 else "")
        insumos = {}
        for idx in range(3, COLUMNAS_VENTAS_NEOLA, 2):
            if len(row) <= idx:
                break
            insumo = row[idx].strip()
            if not insumo:
                continue
            insumos[insumo] = _parsear_numero(row[idx + 1] if len(row) > idx + 1 else "")

        actual[plato] = {
            "cantidad": cantidad,
            "insumos": insumos,
        }

    return actual


def _validar_bloque_ventas_neola(rows_bloque: list[list[str]], ventas: list[dict], consumo: list[dict]):
    esperado = _estructura_esperada_ventas_neola(ventas, consumo)
    actual = _estructura_actual_ventas_neola(rows_bloque)

    errores = []
    platos = sorted(set(esperado) | set(actual))
    for plato in platos:
        if plato not in actual:
            errores.append(f"{plato}: falta la fila del plato")
            continue
        if plato not in esperado:
            errores.append(f"{plato}: existe una fila inesperada")
            continue

        if actual[plato]["cantidad"] != esperado[plato]["cantidad"]:
            errores.append(
                f"{plato}: ventas={actual[plato]['cantidad']} y debería ser {esperado[plato]['cantidad']}"
            )

        insumos = sorted(set(esperado[plato]["insumos"]) | set(actual[plato]["insumos"]))
        for insumo in insumos:
            actual_cantidad = actual[plato]["insumos"].get(insumo, 0)
            esperada_cantidad = esperado[plato]["insumos"].get(insumo, 0)
            if actual_cantidad != esperada_cantidad:
                errores.append(
                    f"{plato} / {insumo}: actual={actual_cantidad}, esperado={esperada_cantidad}"
                )

    if errores:
        raise ValueError(
            "VENTAS NEOLA no coincide con ventas y receta: " + "; ".join(errores[:5])
        )


def _escribir_bloque_ventas_neola(ws, next_row: int, rows_to_write: list[list[str]], filas_a_limpiar: int):
    ultima_columna = gspread.utils.rowcol_to_a1(1, COLUMNAS_VENTAS_NEOLA).rstrip("1")
    if filas_a_limpiar:
        ws.update(
            f"A{next_row}:{ultima_columna}{next_row + filas_a_limpiar - 1}",
            [[""] * COLUMNAS_VENTAS_NEOLA for _ in range(filas_a_limpiar)],
        )
        _esperar_despues_de_write()

    if rows_to_write:
        ws.update(f"A{next_row}:{ultima_columna}{next_row + len(rows_to_write) - 1}", rows_to_write)
        _esperar_despues_de_write()


def _construir_filas_ventas_neola(fecha: str, ventas: list[dict], consumo: list[dict]) -> list[list[str]]:
    fecha_display = _fecha_a_display(fecha)
    ventas_agrupadas = _agrupar_ventas_neola(ventas)
    rows_to_write = [[fecha_display] + [""] * (COLUMNAS_VENTAS_NEOLA - 1)]
    consumo_por_plato = _agrupar_consumo_para_neola(consumo)

    for venta in ventas_agrupadas:
        plato = venta["plato"]
        row = [plato, venta["cantidad"], ""]

        insumos_plato = consumo_por_plato.get(plato, [])
        if len(insumos_plato) > MAX_INSUMOS_POR_PLATO_VENTAS:
            raise ValueError(
                f"{plato} tiene {len(insumos_plato)} insumos y VENTAS NEOLA soporta "
                f"hasta {MAX_INSUMOS_POR_PLATO_VENTAS} por plato."
            )

        for ins in insumos_plato:
            row.append(ins["insumo"])
            row.append(ins["cantidad_total"])

        rows_to_write.append(row + [""] * (COLUMNAS_VENTAS_NEOLA - len(row)))

    return rows_to_write


def escribir_ventas_neola(fecha: str, ventas: list[dict], consumo: list[dict]):
    gc = get_client()
    sh = gc.open_by_key(SHEET_REGISTROS)
    ws = sh.worksheet(HOJA_VENTAS_NEOLA)
    ventas_agrupadas = _agrupar_ventas_neola(ventas)
    rows_to_write = _construir_filas_ventas_neola(fecha, ventas, consumo)

    all_values = _leer_valores_hoja(
        ws,
        f"A1:{gspread.utils.rowcol_to_a1(ws.row_count, COLUMNAS_VENTAS_NEOLA)}",
    )
    fila_existente = _buscar_fila_fecha_ventas(all_values, fecha)
    next_row = fila_existente or (_ultima_fila_no_vacia(all_values) + 1)
    total_rows = next_row + len(ventas_agrupadas)
    if total_rows > ws.row_count:
        ws.add_rows(total_rows - ws.row_count)
        _esperar_despues_de_write()

    if next_row > 4 and not fila_existente:
        requests = [
            {
                "copyPaste": {
                    "source": {
                        "sheetId": ws.id,
                        "startRowIndex": 3,
                        "endRowIndex": 4,
                        "startColumnIndex": 0,
                        "endColumnIndex": COLUMNAS_VENTAS_NEOLA,
                    },
                    "destination": {
                        "sheetId": ws.id,
                        "startRowIndex": next_row - 1,
                        "endRowIndex": next_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": COLUMNAS_VENTAS_NEOLA,
                    },
                    "pasteType": "PASTE_FORMAT",
                    "pasteOrientation": "NORMAL",
                }
            }
        ]
        for target_row in range(next_row + 1, next_row + len(ventas_agrupadas) + 1):
            requests.append({
                "copyPaste": {
                    "source": {
                        "sheetId": ws.id,
                        "startRowIndex": 4,
                        "endRowIndex": 5,
                        "startColumnIndex": 0,
                        "endColumnIndex": COLUMNAS_VENTAS_NEOLA,
                    },
                    "destination": {
                        "sheetId": ws.id,
                        "startRowIndex": target_row - 1,
                        "endRowIndex": target_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": COLUMNAS_VENTAS_NEOLA,
                    },
                    "pasteType": "PASTE_FORMAT",
                    "pasteOrientation": "NORMAL",
                }
            })
        sh.batch_update({"requests": requests})
        _esperar_despues_de_write()

    longitud_existente = _longitud_bloque_existente_ventas(all_values, fila_existente) if fila_existente else 0
    filas_a_limpiar = max(len(rows_to_write), longitud_existente)
    ultimo_error = None
    for _ in range(3):
        _escribir_bloque_ventas_neola(ws, next_row, rows_to_write, filas_a_limpiar)
        bloque_actual = _leer_valores_hoja(
            ws,
            f"A{next_row}:{gspread.utils.rowcol_to_a1(next_row + len(rows_to_write) - 1, COLUMNAS_VENTAS_NEOLA)}",
        )
        try:
            _validar_bloque_ventas_neola(bloque_actual, ventas, consumo)
            return
        except ValueError as exc:
            ultimo_error = exc

    raise ValueError(
        "No se pudo corregir automaticamente VENTAS NEOLA: "
        f"{ultimo_error}"
    )


# ============================================================
# ESCRITURA EN INVENTARIO DIARIO
# ============================================================

def _buscar_seccion_mes_inventario(ws, fecha: str) -> tuple[int, int, int, int, int]:
    mes = _nombre_mes_es(fecha)
    fecha_display = _fecha_a_display(fecha)
    sufijo_mes = fecha_display[2:]
    col_a = ws.col_values(1)

    candidatos = []
    for idx, valor in enumerate(col_a, start=1):
        if valor.strip().upper() != mes:
            continue
        fila_fechas = ws.row_values(idx + 1)
        if any(celda.strip().endswith(sufijo_mes) for celda in fila_fechas):
            candidatos.append(idx)

    if not candidatos:
        raise ValueError(f"No se encontró la sección {mes} en {ws.title}")

    fila_mes = candidatos[-1]
    siguiente_mes = None
    for idx, valor in enumerate(col_a[fila_mes:], start=fila_mes + 1):
        if valor.strip().upper() in MESES_ES.values():
            siguiente_mes = idx
            break

    if siguiente_mes:
        fila_fin = siguiente_mes - 1
    else:
        fila_fin = max(i for i, valor in enumerate(col_a, start=1) if valor.strip())

    return fila_mes, fila_mes + 1, fila_mes + 2, fila_mes + 3, fila_fin


def _buscar_bloque_siguiente_inventario(ws, fila_fechas: int) -> tuple[int, int]:
    valores = ws.row_values(fila_fechas)
    cols_fecha = [i + 1 for i, val in enumerate(valores) if _es_fecha_display(val)]
    if not cols_fecha:
        raise ValueError(f"No hay fechas en la fila {fila_fechas} de {ws.title}")
    ultima_col = max(cols_fecha)
    return ultima_col, ultima_col + 6


def _buscar_bloque_fecha_inventario(ws, fila_fechas: int, fecha: str) -> int | None:
    fecha_display = _fecha_a_display(fecha)
    valores = ws.row_values(fila_fechas)
    for i, val in enumerate(valores, start=1):
        if val.strip() == fecha_display:
            return i
    return None


def _rango_ya_merged(ws, row: int, start_col: int, end_col: int) -> bool:
    meta = ws.spreadsheet.fetch_sheet_metadata()
    sheet = next(s for s in meta["sheets"] if s["properties"]["title"] == ws.title)
    for merge in sheet.get("merges", []):
        if (
            merge["sheetId"] == ws.id
            and merge["startRowIndex"] == row - 1
            and merge["endRowIndex"] == row
            and merge["startColumnIndex"] == start_col - 1
            and merge["endColumnIndex"] == end_col
        ):
            return True
    return False


def _copiar_bloque_inventario(ws, fila_fechas: int, fila_headers: int, fila_inicio_datos: int,
                              fila_fin: int, col_origen: int, col_destino: int):
    if col_destino + 5 > ws.col_count:
        ws.add_cols(col_destino + 5 - ws.col_count)
        _esperar_despues_de_write()

    requests = []
    if not _rango_ya_merged(ws, fila_fechas, col_destino, col_destino + 5):
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": fila_fechas - 1,
                    "endRowIndex": fila_fechas,
                    "startColumnIndex": col_destino - 1,
                    "endColumnIndex": col_destino + 5,
                },
                "mergeType": "MERGE_ALL",
            }
        })

    requests.extend([
        {
            "copyPaste": {
                "source": {
                    "sheetId": ws.id,
                    "startRowIndex": fila_fechas - 1,
                    "endRowIndex": fila_fin,
                    "startColumnIndex": col_origen - 1,
                    "endColumnIndex": col_origen + 5,
                },
                "destination": {
                    "sheetId": ws.id,
                    "startRowIndex": fila_fechas - 1,
                    "endRowIndex": fila_fin,
                    "startColumnIndex": col_destino - 1,
                    "endColumnIndex": col_destino + 5,
                },
                "pasteType": "PASTE_FORMAT",
                "pasteOrientation": "NORMAL",
            }
        }
    ])
    ws.spreadsheet.batch_update({"requests": requests})
    _esperar_despues_de_write()


def _encontrar_fila_insumo(ws, insumo: str, col_nombre: int = 1) -> int | None:
    """
    Busca la fila donde está el insumo en la columna A (match exacto).
    Devuelve el índice de fila (1-based) o None.
    """
    col_values = ws.col_values(col_nombre)
    insumo_upper = insumo.upper().strip()
    for i, val in enumerate(col_values):
        if val.strip().upper() == insumo_upper:
            return i + 1
    return None


def _agrupar_consumo_por_hoja(consumo_agrupado: dict, ubicaciones_defecto: dict) -> dict[str, dict[str, int]]:
    agrupado = {"C1": {}, "C2": {}, "LINEA": {}}
    for insumo, datos in consumo_agrupado.items():
        ubicacion = ubicaciones_defecto.get(insumo, datos["ubicacion"])
        if ubicacion not in agrupado:
            ubicacion = "C1"
        agrupado[ubicacion][insumo] = datos["total"]
    return agrupado


def _ventas_esperadas_para_hoja(ubicacion_key: str, insumo: str, consumo_por_hoja: dict,
                                registros: dict, tabla_ubicaciones: dict) -> int:
    if ubicacion_key == "LINEA":
        return consumo_por_hoja["LINEA"].get(insumo, 0)

    salida_registrada = registros.get(ubicacion_key, {}).get(insumo, {}).get("salida", 0)
    if salida_registrada:
        return salida_registrada

    return consumo_por_hoja.get(ubicacion_key, {}).get(insumo, 0)


def _ingresos_transferidos_a_linea(registros: dict, tabla_ubicaciones: dict) -> dict[str, int]:
    transferidos = {}
    for origen in ("C1", "C2"):
        for insumo, mov in registros.get(origen, {}).items():
            if tabla_ubicaciones.get(insumo, {}).get("descuento") != "LINEA":
                continue
            salida = mov.get("salida", 0)
            if salida:
                transferidos[insumo] = transferidos.get(insumo, 0) + salida
    return transferidos


def _valores_inventario_para_insumo(
    ubicacion_key: str,
    insumo: str,
    cierre_previo: int,
    registro: dict,
    consumo_por_hoja: dict,
    registros: dict,
    tabla_ubicaciones: dict,
    ingresos_linea_transferidos: dict,
) -> list:
    ingreso = registro.get("ingreso", 0)
    if ubicacion_key == "LINEA":
        ingreso += ingresos_linea_transferidos.get(insumo, 0)

    ventas = _ventas_esperadas_para_hoja(
        ubicacion_key,
        insumo,
        consumo_por_hoja,
        registros,
        tabla_ubicaciones,
    )

    if ubicacion_key == "LINEA" and registro.get("conteo") is not None:
        cierre = registro["conteo"]
        salida = cierre_previo + ingreso - cierre
        dif = salida - ventas
    else:
        salida_real = registro.get("salida", 0)
        if ubicacion_key == "LINEA":
            cierre = cierre_previo + ingreso - salida_real - ventas
            salida = salida_real
            dif = 0
        else:
            cierre = cierre_previo + ingreso - salida_real
            salida = salida_real
            dif = salida - ventas

    return [
        cierre_previo if cierre_previo != "" else "",
        ingreso if ingreso else "",
        salida if salida or ingreso or ventas or cierre_previo or cierre else 0,
        dif if dif or salida or ventas else 0,
        ventas if ventas else "",
        cierre,
    ]


def _contexto_bloque_inventario(ws, fecha: str) -> tuple[int, int, int, int, list[str], list[str]]:
    fila_mes, fila_fechas, fila_headers, fila_datos, fila_fin = _buscar_seccion_mes_inventario(ws, fecha)
    col_destino = _buscar_bloque_fecha_inventario(ws, fila_fechas, fecha)
    if col_destino is None:
        raise ValueError(f"La fecha {fecha} no existe todavía en {ws.title}")
    col_origen = col_destino - 6
    productos = ws.col_values(1)
    cierres_previos = ws.col_values(col_origen + 5)
    return col_destino, fila_datos, fila_fin, col_origen, productos, cierres_previos


def escribir_inventario_dia(fecha: str, consumo_agrupado: dict, registros: dict, ubicaciones_defecto: dict):
    """
    Escribe los datos del cierre en las hojas de inventario:
    - VENTAS (consumo teórico) en la columna VENTAS del día
    - INGRESO/SALIDA desde los registros
    - Motivos extraordinarios como comentarios

    Estructura del inventario por día (6 columnas):
    INICIO | INGRESO | SALIDA | DIF | VENTAS | CIERRE
    """
    fecha_display = _fecha_a_display(fecha)
    gc = get_client()
    sh = gc.open_by_key(SHEET_INVENTARIO)
    tabla_ubicaciones = leer_tabla_ubicacion_descuento()
    consumo_por_hoja = _agrupar_consumo_por_hoja(consumo_agrupado, ubicaciones_defecto)
    ingresos_linea_transferidos = _ingresos_transferidos_a_linea(registros, tabla_ubicaciones)

    hojas_inv = {
        "C1": "C1",
        "C2": "C2",
        "LINEA": "LINEA CALIENTE",
    }

    for ubicacion_key, hoja_nombre in hojas_inv.items():
        ws = sh.worksheet(hoja_nombre)
        fila_mes, fila_fechas, fila_headers, fila_datos, fila_fin = _buscar_seccion_mes_inventario(ws, fecha)
        col_destino = _buscar_bloque_fecha_inventario(ws, fila_fechas, fecha)
        if col_destino is None:
            col_origen, col_destino = _buscar_bloque_siguiente_inventario(ws, fila_fechas)
        else:
            col_origen = col_destino - 6
        _copiar_bloque_inventario(ws, fila_fechas, fila_headers, fila_datos, fila_fin, col_origen, col_destino)

        productos = ws.col_values(1)
        cierres_previos = ws.col_values(col_origen + 5)
        rango_bloque = []

        for fila in range(fila_datos, fila_fin + 1):
            insumo = productos[fila - 1].strip() if fila - 1 < len(productos) else ""
            if not insumo or insumo.startswith(" "):
                rango_bloque.append(["", "", "", "", "", ""])
                continue

            cierre_previo = _parsear_numero(cierres_previos[fila - 1] if fila - 1 < len(cierres_previos) else "")
            registro = registros.get(ubicacion_key, {}).get(insumo, {})
            rango_bloque.append(
                _valores_inventario_para_insumo(
                    ubicacion_key,
                    insumo,
                    cierre_previo,
                    registro,
                    consumo_por_hoja,
                    registros,
                    tabla_ubicaciones,
                    ingresos_linea_transferidos,
                )
            )

        ws.batch_update([
            {
                "range": f"{gspread.utils.rowcol_to_a1(fila_headers, col_destino)}:{gspread.utils.rowcol_to_a1(fila_headers, col_destino + 5)}",
                "values": [["INICIO", "INGRESO", "SALIDA", "DIF", "VENTAS", "CIERRE"]],
            },
            {
                "range": f"{gspread.utils.rowcol_to_a1(fila_datos, col_destino)}:{gspread.utils.rowcol_to_a1(fila_fin, col_destino + 5)}",
                "values": rango_bloque,
            },
            {
                "range": gspread.utils.rowcol_to_a1(fila_fechas, col_destino),
                "values": [[fecha_display]],
            },
        ])
        _esperar_despues_de_write()


def corregir_inventario_insumos(fecha: str, insumos: list[str], consumo_agrupado: dict,
                                registros: dict, ubicaciones_defecto: dict) -> dict[str, list[str]]:
    gc = get_client()
    sh = gc.open_by_key(SHEET_INVENTARIO)
    tabla_ubicaciones = leer_tabla_ubicacion_descuento()
    consumo_por_hoja = _agrupar_consumo_por_hoja(consumo_agrupado, ubicaciones_defecto)
    ingresos_linea_transferidos = _ingresos_transferidos_a_linea(registros, tabla_ubicaciones)
    objetivos = {insumo.strip() for insumo in insumos if str(insumo).strip()}

    hojas_inv = {
        "C1": "C1",
        "C2": "C2",
        "LINEA": "LINEA CALIENTE",
    }
    actualizados = {hoja: [] for hoja in hojas_inv.values()}

    for ubicacion_key, hoja_nombre in hojas_inv.items():
        ws = sh.worksheet(hoja_nombre)
        col_destino, fila_datos, fila_fin, _, productos, cierres_previos = _contexto_bloque_inventario(ws, fecha)

        updates = []
        for fila in range(fila_datos, fila_fin + 1):
            insumo = productos[fila - 1].strip() if fila - 1 < len(productos) else ""
            if insumo not in objetivos:
                continue

            cierre_previo = _parsear_numero(cierres_previos[fila - 1] if fila - 1 < len(cierres_previos) else "")
            registro = registros.get(ubicacion_key, {}).get(insumo, {})
            values = _valores_inventario_para_insumo(
                ubicacion_key,
                insumo,
                cierre_previo,
                registro,
                consumo_por_hoja,
                registros,
                tabla_ubicaciones,
                ingresos_linea_transferidos,
            )
            updates.append({
                "range": (
                    f"{gspread.utils.rowcol_to_a1(fila, col_destino)}:"
                    f"{gspread.utils.rowcol_to_a1(fila, col_destino + 5)}"
                ),
                "values": [values],
            })
            actualizados[hoja_nombre].append(insumo)

        if updates:
            ws.batch_update(updates)
            _esperar_despues_de_write()

    return actualizados


def _indices_bloque_inventario(headers: list[str]) -> dict[str, int]:
    normalizados = [str(h).strip().upper() for h in headers]
    alias = {
        "INICIO": {"INCIO", "INICIO"},
        "INGRESO": {"INGRESO", "INGRESO "},
        "SALIDA": {"SALIDA"},
        "DIF": {"DIF"},
        "VENTAS": {"VENTAS"},
        "CIERRE": {"CIERRE"},
    }
    indices = {}
    for nombre, opciones in alias.items():
        for idx, header in enumerate(normalizados):
            if header in opciones:
                indices[nombre] = idx
                break
    return indices


def verificar_inventario_dia_existe(fecha: str) -> dict[str, bool]:
    """Verifica si la entrada del día existe en cada hoja de inventario."""
    gc = get_client()
    sh = gc.open_by_key(SHEET_INVENTARIO)
    resultado = {}
    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        ws = sh.worksheet(hoja)
        fila_mes, fila_fechas, fila_headers, fila_datos, fila_fin = _buscar_seccion_mes_inventario(ws, fecha)
        col = _buscar_bloque_fecha_inventario(ws, fila_fechas, fecha)
        resultado[hoja] = col is not None
    return resultado


def leer_diferencias_inventario_dia(fecha: str) -> dict[str, list[dict]]:
    gc = get_client()
    sh = gc.open_by_key(SHEET_INVENTARIO)
    diferencias = {}

    for hoja in ("C1", "C2", "LINEA CALIENTE"):
        ws = sh.worksheet(hoja)
        fila_mes, fila_fechas, fila_headers, fila_datos, fila_fin = _buscar_seccion_mes_inventario(ws, fecha)
        col = _buscar_bloque_fecha_inventario(ws, fila_fechas, fecha)
        if col is None:
            diferencias[hoja] = []
            continue

        rows = _leer_valores_hoja(ws, f"A1:{gspread.utils.rowcol_to_a1(fila_fin, col + 5)}")
        headers = rows[fila_headers - 1][col - 1:col + 5]
        indices = _indices_bloque_inventario(headers)
        difs_hoja = []

        for fila in range(fila_datos, fila_fin + 1):
            row = rows[fila - 1]
            insumo = row[0].strip() if row else ""
            if not insumo or insumo.startswith(" "):
                continue

            bloque = row[col - 1:col + 5]
            if len(bloque) < 6:
                bloque += [""] * (6 - len(bloque))

            dif = _parsear_numero(bloque[indices["DIF"]]) if "DIF" in indices else 0
            if dif == 0:
                continue

            difs_hoja.append({
                "insumo": insumo,
                "inicio": _parsear_numero(bloque[indices["INICIO"]]) if "INICIO" in indices else 0,
                "ingreso": _parsear_numero(bloque[indices["INGRESO"]]) if "INGRESO" in indices else 0,
                "salida": _parsear_numero(bloque[indices["SALIDA"]]) if "SALIDA" in indices else 0,
                "dif": dif,
                "ventas": _parsear_numero(bloque[indices["VENTAS"]]) if "VENTAS" in indices else 0,
                "cierre": _parsear_numero(bloque[indices["CIERRE"]]) if "CIERRE" in indices else 0,
            })

        diferencias[hoja] = difs_hoja

    return diferencias
