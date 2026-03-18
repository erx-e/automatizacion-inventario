import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


gspread_stub = types.ModuleType("gspread")
gspread_stub.authorize = lambda creds: None
gspread_stub.exceptions = types.SimpleNamespace(WorksheetNotFound=Exception)
gspread_stub.utils = types.SimpleNamespace(rowcol_to_a1=lambda row, col: f"R{row}C{col}")
sys.modules.setdefault("gspread", gspread_stub)

google_module = sys.modules.setdefault("google", types.ModuleType("google"))
oauth2_module = sys.modules.setdefault("google.oauth2", types.ModuleType("google.oauth2"))
service_account_module = types.ModuleType("google.oauth2.service_account")


class Credentials:
    @staticmethod
    def from_service_account_file(path, scopes=None):
        return {"path": path, "scopes": scopes}


service_account_module.Credentials = Credentials
sys.modules["google.oauth2.service_account"] = service_account_module
google_module.oauth2 = oauth2_module
oauth2_module.service_account = service_account_module

config_stub = sys.modules.get("config")
if config_stub is None:
    config_stub = types.ModuleType("config")
    sys.modules["config"] = config_stub

config_stub.GOOGLE_CREDENTIALS_PATH = "/tmp/credentials.json"
config_stub.SHEET_REGISTROS = "registros"
config_stub.SHEET_RECETAS = "recetas"
config_stub.SHEET_INVENTARIO = "inventario"
config_stub.HOJA_REGISTRO_C1 = "REGISTRO C1"
config_stub.HOJA_REGISTRO_C2 = "REGISTRO C2"
config_stub.HOJA_REGISTRO_LINEA = "REGISTRO LINEA CALIENTE"
config_stub.HOJA_VENTAS_NEOLA = "VENTAS NEOLA"
config_stub.HOJA_UBICACION = "UBICACION DESCUENTO"
config_stub.HOJA_RECETAS = "RECETAS"
config_stub.SHEETS_WRITE_DELAY_SECONDS = 0
config_stub.ANTHROPIC_API_KEY = getattr(config_stub, "ANTHROPIC_API_KEY", "")
config_stub.CLAUDE_MODEL = getattr(config_stub, "CLAUDE_MODEL", "claude-sonnet-4-6")

import sheets_connector  # noqa: E402


class ParsearRecetasRowsTests(unittest.TestCase):
    def test_parsea_todos_los_insumos_de_un_plato_con_celdas_combinadas(self):
        rows = [
            ["PLATO", "MENU", "SKU", "INSUMO", "CANT", "UND", "UBIC", "CONFIRMADO", "NOTAS", "NOMBRES NEOLA"],
            ["nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota"],
            ["HAMBURGUESA GOLD", "GOLD", "SKU-1", "CARNE 180G", "1", "UND", "C1", "✅", "", "HAMBURGUESA GOLD|HAMBURGUESA G"],
            ["", "", "SKU-2", "PAN BRIOCHE", "1", "UND", "LINEA", "✅", "", ""],
            ["", "", "SKU-3", "QUESO", "2", "LONJA", "LINEA", "✅", "", ""],
            ["PAPAS CHEDDAR", "PAPAS", "", "", "", "", "", "✅", "sin inventario", ""],
        ]

        recetas = sheets_connector._parsear_recetas_rows(rows)

        self.assertEqual(len(recetas), 4)
        self.assertEqual(
            [receta["insumo"] for receta in recetas[:3]],
            ["CARNE 180G", "PAN BRIOCHE", "QUESO"],
        )
        self.assertTrue(all(receta["plato"] == "HAMBURGUESA GOLD" for receta in recetas[:3]))
        self.assertEqual(recetas[0]["nombres_neola"], ["HAMBURGUESA GOLD", "HAMBURGUESA G"])
        self.assertEqual(recetas[1]["nombres_neola"], ["HAMBURGUESA GOLD", "HAMBURGUESA G"])
        self.assertEqual(recetas[3]["plato"], "PAPAS CHEDDAR")
        self.assertEqual(recetas[3]["sku"], "")
        self.assertEqual(recetas[3]["insumo"], "")
        self.assertEqual(recetas[3]["nombres_neola"], [])

    def test_ignora_filas_de_categoria_expandidas_por_celdas_combinadas(self):
        rows = [
            ["PLATO", "MENU", "SKU", "INSUMO", "CANT", "UND", "UBIC", "CONFIRMADO", "NOTAS", "NOMBRES NEOLA"],
            ["nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota"],
            [
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "HAMBURGUESAS Y SANDWICHES",
                "",
                "",
            ],
            ["HAMBURGUESA GOLD", "GOLD", "SKU-1", "CARNE 180G", "1", "UND", "C1", "✅", "", ""],
        ]

        recetas = sheets_connector._parsear_recetas_rows(rows)

        self.assertEqual(len(recetas), 1)
        self.assertEqual(recetas[0]["plato"], "HAMBURGUESA GOLD")

    def test_hereda_aliases_neola_en_filas_siguientes_del_mismo_bloque(self):
        rows = [
            ["PLATO", "MENU", "SKU", "INSUMO", "CANT", "UND", "UBIC", "CONFIRMADO", "NOTAS", "NOMBRES NEOLA"],
            ["nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota"],
            ["SANDWICH DE PEPP", "SANDWICH DE PEPERONI", "SKU-1", "PEPERONI", "1", "PAX", "C1", "✅", "", "SANDWICH DE PEPE|SANDWICH DE PEPP"],
            ["", "", "SKU-2", "PANINI", "1", "UND", "LINEA", "✅", "", ""],
        ]

        recetas = sheets_connector._parsear_recetas_rows(rows)

        self.assertEqual(len(recetas), 2)
        self.assertEqual(recetas[0]["nombres_neola"], ["SANDWICH DE PEPE", "SANDWICH DE PEPP"])
        self.assertEqual(recetas[1]["nombres_neola"], ["SANDWICH DE PEPE", "SANDWICH DE PEPP"])


class LeerValoresHojaTests(unittest.TestCase):
    def test_pide_expandir_celdas_combinadas_si_la_api_lo_soporta(self):
        class Worksheet:
            def __init__(self):
                self.kwargs = None

            def get_values(self, **kwargs):
                self.kwargs = kwargs
                return [["ok"]]

        ws = Worksheet()
        values = sheets_connector._leer_valores_hoja(ws)

        self.assertEqual(values, [["ok"]])
        self.assertEqual(
            ws.kwargs,
            {"combine_merged_cells": True, "pad_values": True},
        )

    def test_hace_fallback_al_metodo_legacy_si_no_acepta_los_parametros(self):
        class Worksheet:
            def __init__(self):
                self.legacy_called = False

            def get_values(self, **kwargs):
                raise TypeError("unsupported kwargs")

            def get_all_values(self):
                self.legacy_called = True
                return [["legacy"]]

        ws = Worksheet()
        values = sheets_connector._leer_valores_hoja(ws)

        self.assertEqual(values, [["legacy"]])
        self.assertTrue(ws.legacy_called)


class ParsearRegistroRowsTests(unittest.TestCase):
    def test_parsea_registro_c1_con_bloques_de_ingreso_salida_motivo(self):
        rows = [
            ["MARZO"],
            ["REGISTRO C1", "", "10-03-2026", "", "", "11-03-2026"],
            ["INSUMO", "UND", "INGRESO", "SALIDA", "MOTIVO", "INGRESO"],
            ["  RES"],
            ["POLLO 200 gr", "UND", "", "5", ""],
            ["ALITAS 4 unid", "UND", "2", "1", "ajuste"],
        ]

        registros = sheets_connector._parsear_registro_rows(rows, "2026-03-10")

        self.assertEqual(registros["POLLO 200 gr"]["ingreso"], 0)
        self.assertEqual(registros["POLLO 200 gr"]["salida"], 5)
        self.assertEqual(registros["POLLO 200 gr"]["conteo"], None)
        self.assertEqual(registros["ALITAS 4 unid"]["ingreso"], 2)
        self.assertEqual(registros["ALITAS 4 unid"]["motivo"], "ajuste")

    def test_parsea_registro_linea_con_conteo(self):
        rows = [
            ["MARZO"],
            ["REGISTRO LINEA CALIENTE", "", "10-03-2026", "", "", "", "11-03-2026"],
            ["INSUMO", "UND", "CONTEO", "INGRESO", "SALIDA", "MOTIVO", "CONTEO"],
            ["PAN DE SEMILLA NEGRA", "UND", "9", "", "", ""],
            ["PAN DE HOT DOG", "UND", "0", "10", "", ""],
        ]

        registros = sheets_connector._parsear_registro_rows(rows, "2026-03-10")

        self.assertEqual(registros["PAN DE SEMILLA NEGRA"]["conteo"], 9)
        self.assertEqual(registros["PAN DE HOT DOG"]["conteo"], 0)
        self.assertEqual(registros["PAN DE HOT DOG"]["ingreso"], 10)

    def test_deja_conteo_en_none_cuando_linea_no_fue_contada(self):
        rows = [
            ["MARZO"],
            ["REGISTRO LINEA CALIENTE", "", "10-03-2026"],
            ["INSUMO", "UND", "CONTEO", "INGRESO", "SALIDA", "MOTIVO"],
            ["PAN DE SEMILLA NEGRA", "UND", "", "12", "", ""],
        ]

        registros = sheets_connector._parsear_registro_rows(rows, "2026-03-10")

        self.assertIsNone(registros["PAN DE SEMILLA NEGRA"]["conteo"])
        self.assertEqual(registros["PAN DE SEMILLA NEGRA"]["ingreso"], 12)


class LeerRegistroDiaTests(unittest.TestCase):
    def test_lee_hasta_el_ancho_real_de_la_hoja(self):
        ws = types.SimpleNamespace(row_count=200, col_count=42)
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: ws)
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            return_value=[],
        ) as leer_mock:
            sheets_connector.leer_registro_dia("REGISTRO C1", "2026-03-10")

        self.assertEqual(
            leer_mock.call_args.args,
            (ws, f"A1:{gspread_stub.utils.rowcol_to_a1(200, 42)}"),
        )
        self.assertEqual(leer_mock.call_args.kwargs, {})


class ActualizarRegistrosDiaTests(unittest.TestCase):
    def test_actualiza_salida_y_motivo_en_c1(self):
        class Worksheet:
            def __init__(self):
                self.row_count = 10
                self.col_count = 8
                self.title = "REGISTRO C1"

        ws = Worksheet()
        rows = [
            ["MARZO"],
            ["REGISTRO C1", "", "10-03-2026"],
            ["INSUMO", "UND", "INGRESO", "SALIDA", "MOTIVO"],
            ["FILETE DE POLLO 200 gr", "UND", "", "5", ""],
        ]

        with mock.patch.object(
            sheets_connector,
            "_obtener_worksheet",
            return_value=ws,
        ), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            return_value=rows,
        ), mock.patch.object(
            sheets_connector,
            "_batch_update_user_entered",
        ) as batch_mock:
            actualizados = sheets_connector.actualizar_registros_dia(
                "2026-03-10",
                [{
                    "ubicacion": "C1",
                    "insumo": "FILETE DE POLLO 200 gr",
                    "cambios": {"salida": 4, "motivo": "correccion"},
                }],
            )

        self.assertEqual(actualizados, {"C1": ["FILETE DE POLLO 200 gr"], "C2": [], "LINEA": []})
        self.assertEqual(
            batch_mock.call_args.args[1],
            [
                {"range": "R4C4:R4C4", "values": [[4]]},
                {"range": "R4C5:R4C5", "values": [["correccion"]]},
            ],
        )

    def test_actualiza_conteo_en_linea(self):
        class Worksheet:
            def __init__(self):
                self.row_count = 10
                self.col_count = 8
                self.title = "REGISTRO LINEA CALIENTE"

        ws = Worksheet()
        rows = [
            ["MARZO"],
            ["REGISTRO LINEA CALIENTE", "", "10-03-2026"],
            ["INSUMO", "UND", "CONTEO", "INGRESO", "SALIDA", "MOTIVO"],
            ["POLLO 160 gr CECAR", "UND", "3", "", "", ""],
        ]

        with mock.patch.object(
            sheets_connector,
            "_obtener_worksheet",
            return_value=ws,
        ), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            return_value=rows,
        ), mock.patch.object(
            sheets_connector,
            "_batch_update_user_entered",
        ) as batch_mock:
            actualizados = sheets_connector.actualizar_registros_dia(
                "2026-03-10",
                [{
                    "ubicacion": "LINEA",
                    "insumo": "POLLO 160 gr CECAR",
                    "cambios": {"conteo": 2},
                }],
            )

        self.assertEqual(actualizados, {"C1": [], "C2": [], "LINEA": ["POLLO 160 gr CECAR"]})
        self.assertEqual(
            batch_mock.call_args.args[1],
            [{"range": "R4C3:R4C3", "values": [[2]]}],
        )


class ScopedCacheSheetsTests(unittest.TestCase):
    def setUp(self):
        sheets_connector._spreadsheet_cache.clear()
        sheets_connector._worksheet_cache.clear()
        sheets_connector._master_cache.clear()

    def test_leer_registros_dia_completo_reutiliza_lecturas_en_misma_operacion(self):
        hojas = {
            "REGISTRO C1": types.SimpleNamespace(row_count=20, col_count=8, title="REGISTRO C1"),
            "REGISTRO C2": types.SimpleNamespace(row_count=20, col_count=8, title="REGISTRO C2"),
            "REGISTRO LINEA CALIENTE": types.SimpleNamespace(row_count=20, col_count=8, title="REGISTRO LINEA CALIENTE"),
        }
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: hojas[hoja_nombre])
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)
        rows = [
            ["MARZO"],
            ["REGISTRO", "", "10-03-2026"],
            ["INSUMO", "UND", "INGRESO", "SALIDA", "MOTIVO"],
            ["POLLO 200 gr", "UND", "", "2", ""],
        ]

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            return_value=rows,
        ) as leer_mock:
            cache = {}
            primero = sheets_connector.leer_registros_dia_completo("2026-03-10", cache=cache)
            segundo = sheets_connector.leer_registros_dia_completo("2026-03-10", cache=cache)

        self.assertEqual(primero, segundo)
        self.assertEqual(leer_mock.call_count, 3)

    def test_escribir_ventas_invalida_cache_del_dia(self):
        ventas = [{"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 9.99}]
        consumo = [
            {"plato": "HAMBURGUESA GOLD", "insumo": "HAMBURGUESA (180gr)", "cantidad_total": 1},
        ]
        rows_escritos = sheets_connector._construir_filas_ventas_neola("2026-03-13", ventas, consumo)

        class Worksheet:
            def __init__(self):
                self.row_count = 10
                self.col_count = sheets_connector.COLUMNAS_VENTAS_NEOLA
                self.id = 123

            def update(self, range_name, values):
                return None

        ws = Worksheet()
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: ws, batch_update=lambda payload: None)
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)
        hoja_vacia = [[""] * sheets_connector.COLUMNAS_VENTAS_NEOLA for _ in range(ws.row_count)]
        hoja_actualizada = [list(row) for row in hoja_vacia]
        hoja_actualizada[0] = rows_escritos[0]
        hoja_actualizada[1] = rows_escritos[1]

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            side_effect=[
                hoja_vacia,
                hoja_vacia,
                rows_escritos,
                hoja_actualizada,
            ],
        ):
            cache = {}
            antes = sheets_connector.leer_ventas_neola_dia("2026-03-13", cache=cache)
            sheets_connector.escribir_ventas_neola("2026-03-13", ventas, consumo, cache=cache)
            despues = sheets_connector.leer_ventas_neola_dia("2026-03-13", cache=cache)

        self.assertEqual(antes, [])
        self.assertEqual(
            despues,
            [{"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 0.0}],
        )


class VentasEsperadasTests(unittest.TestCase):
    def test_solo_registros_congelador_usa_salida_registrada_como_ventas_provisionales(self):
        consumo_por_hoja = {"C1": {}, "C2": {"CREPE POLLO 2 unid": 2}, "LINEA": {}}
        registros = {"C1": {}, "C2": {"CREPE POLLO 2 unid": {"salida": 2}}, "LINEA": {}}
        tabla = {"CREPE POLLO 2 unid": {"descuento": "C2"}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C2",
            "CREPE POLLO 2 unid",
            consumo_por_hoja,
            registros,
            tabla,
            modo_ventas="provisional_registros",
        )

        self.assertEqual(ventas, 2)

    def test_linea_usa_consumo_teorico_como_ventas_aun_si_hay_salida_registrada(self):
        consumo_por_hoja = {"C1": {}, "C2": {}, "LINEA": {"JALAPENOS": 3}}
        registros = {"C1": {}, "C2": {}, "LINEA": {"JALAPENOS": {"salida": 5}}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "LINEA", "JALAPENOS", consumo_por_hoja, registros, {}
        )

        self.assertEqual(ventas, 3)

    def test_congelador_en_final_toma_maximo_entre_ticket_y_salida_para_no_duplicar(self):
        tabla = {"HAMBURGUESA FALAFEL (150 gr)": {"descuento": "C1"}}
        registros = {
            "C1": {"HAMBURGUESA FALAFEL (150 gr)": {"salida": 2}},
            "C2": {},
            "LINEA": {},
        }
        consumo_por_hoja = {"C1": {"HAMBURGUESA FALAFEL (150 gr)": 2}, "C2": {}, "LINEA": {}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C1", "HAMBURGUESA FALAFEL (150 gr)", consumo_por_hoja, registros, tabla
        )

        self.assertEqual(ventas, 2)

    def test_congelador_en_final_detecta_venta_faltante_si_ticket_supera_salida(self):
        tabla = {"HAMBURGUESA FALAFEL (150 gr)": {"descuento": "C1"}}
        registros = {
            "C1": {"HAMBURGUESA FALAFEL (150 gr)": {"salida": 1}},
            "C2": {},
            "LINEA": {},
        }
        consumo_por_hoja = {"C1": {"HAMBURGUESA FALAFEL (150 gr)": 2}, "C2": {}, "LINEA": {}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C1", "HAMBURGUESA FALAFEL (150 gr)", consumo_por_hoja, registros, tabla
        )

        self.assertEqual(ventas, 2)

    def test_salida_de_congelador_hacia_linea_se_mantiene_como_ventas_del_congelador(self):
        tabla = {"PEPERONI SANDUCHE": {"descuento": "LINEA"}}
        registros = {
            "C1": {"PEPERONI SANDUCHE 2 unid": {"salida": 2}},
            "C2": {},
            "LINEA": {},
        }
        consumo_por_hoja = {"C1": {}, "C2": {}, "LINEA": {"PEPERONI SANDUCHE 2 unid": 2}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C1", "PEPERONI SANDUCHE", consumo_por_hoja, registros, tabla
        )
        transferidos = sheets_connector._ingresos_transferidos_a_linea(registros, tabla)

        self.assertEqual(ventas, 2)
        self.assertEqual(transferidos, {"PEPERONI SANDUCHE 2 unid": 2})

    def test_congelador_con_descuento_en_linea_no_duplica_con_ticket(self):
        tabla = {"FILETE DE POLLO 200 gr": {"descuento": "LINEA"}}
        registros = {
            "C1": {"FILETE DE POLLO 200 gr": {"salida": 5}},
            "C2": {},
            "LINEA": {},
        }
        consumo_por_hoja = {
            "C1": {},
            "C2": {},
            "LINEA": {"FILETE DE POLLO 200 gr": 3},
        }

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C1", "FILETE DE POLLO 200 gr", consumo_por_hoja, registros, tabla
        )

        self.assertEqual(ventas, 5)

    def test_no_confunde_variantes_distintas_por_gramaje(self):
        clave = sheets_connector._resolver_clave_mapa(
            {"FILETE DE POLLO 200 gr": {"salida": 3}},
            "FILETE DE POLLO 100 gr",
        )

        self.assertIsNone(clave)

    def test_agrupar_consumo_por_hoja_usa_ubicacion_por_nombre_normalizado(self):
        consumo_agrupado = {
            "PEPERONI SANDUCHE 2 unid": {
                "total": 2,
                "unidad": "UND",
                "ubicacion": "C1",
            }
        }
        ubicaciones = {"PEPERONI SANDUCHE": "LINEA"}

        agrupado = sheets_connector._agrupar_consumo_por_hoja(consumo_agrupado, ubicaciones)

        self.assertEqual(agrupado["LINEA"]["PEPERONI SANDUCHE 2 unid"], 2)


class VentasNeolaHelpersTests(unittest.TestCase):
    def test_agrupa_ventas_repetidas_por_plato(self):
        ventas = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 6, "precio_total": 59.29},
            {"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 10.99},
            {"plato": "SALCHIPRAGA", "cantidad": 8, "precio_total": 51.92},
        ]

        agrupadas = sheets_connector._agrupar_ventas_neola(ventas)

        self.assertEqual(
            agrupadas,
            [
                {"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28},
                {"plato": "SALCHIPRAGA", "cantidad": 8, "precio_total": 51.92},
            ],
        )

    def test_detecta_longitud_de_bloque_existente(self):
        rows = [
            ["10-03-2026"],
            ["HAMBURGUESA GOLD", "1"],
            ["SALCHIPRAGA", "4"],
            ["11-03-2026"],
            ["ALITAS", "1"],
        ]

        longitud = sheets_connector._longitud_bloque_existente_ventas(rows, 1)

        self.assertEqual(longitud, 3)

    def test_agrupa_consumo_repetido_por_plato_e_insumo(self):
        consumo = [
            {"plato": "HAMBURGUESA GOLD", "insumo": "HAMBURGUESA (180gr)", "cantidad_total": 6},
            {"plato": "HAMBURGUESA GOLD", "insumo": "PAN DE SEMILLA NEGRA", "cantidad_total": 6},
            {"plato": "HAMBURGUESA GOLD", "insumo": "HAMBURGUESA (180gr)", "cantidad_total": 1},
            {"plato": "HAMBURGUESA GOLD", "insumo": "PAN DE SEMILLA NEGRA", "cantidad_total": 1},
        ]

        agrupado = sheets_connector._agrupar_consumo_para_neola(consumo)

        self.assertEqual(
            agrupado,
            {
                "HAMBURGUESA GOLD": [
                    {"insumo": "HAMBURGUESA (180gr)", "cantidad_total": 7},
                    {"insumo": "PAN DE SEMILLA NEGRA", "cantidad_total": 7},
                ]
            },
        )

    def test_detecta_mismatch_entre_ventas_y_bloque_escrito(self):
        rows_bloque = [
            ["13-03-2026", "", "", "", "", "", "", "", "", "", ""],
            ["HAMBURGUESA GOLD", "7", "", "HAMBURGUESA (180gr)", "14", "PAN DE SEMILLA NEGRA", "14", "", "", "", ""],
        ]
        ventas = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28},
        ]
        consumo = [
            {"plato": "HAMBURGUESA GOLD", "insumo": "HAMBURGUESA (180gr)", "cantidad_total": 7},
            {"plato": "HAMBURGUESA GOLD", "insumo": "PAN DE SEMILLA NEGRA", "cantidad_total": 7},
        ]

        with self.assertRaisesRegex(ValueError, "HAMBURGUESA GOLD / HAMBURGUESA \\(180gr\\)"):
            sheets_connector._validar_bloque_ventas_neola(rows_bloque, ventas, consumo)

    def test_construye_fila_con_hasta_seis_insumos(self):
        ventas = [
            {"plato": "TABLA MIXTA", "cantidad": 1, "precio_total": 20.00},
        ]
        consumo = [
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 1", "cantidad_total": 1},
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 2", "cantidad_total": 2},
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 3", "cantidad_total": 3},
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 4", "cantidad_total": 4},
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 5", "cantidad_total": 5},
            {"plato": "TABLA MIXTA", "insumo": "INSUMO 6", "cantidad_total": 6},
        ]

        rows = sheets_connector._construir_filas_ventas_neola("2026-03-13", ventas, consumo)

        self.assertEqual(len(rows[0]), sheets_connector.COLUMNAS_VENTAS_NEOLA)
        self.assertEqual(rows[1][13], "INSUMO 6")
        self.assertEqual(rows[1][14], 6)

    def test_falla_si_un_plato_supera_seis_insumos(self):
        ventas = [
            {"plato": "TABLA MIXTA", "cantidad": 1, "precio_total": 20.00},
        ]
        consumo = [
            {"plato": "TABLA MIXTA", "insumo": f"INSUMO {idx}", "cantidad_total": idx}
            for idx in range(1, 8)
        ]

        with self.assertRaisesRegex(ValueError, "soporta hasta 6 por plato"):
            sheets_connector._construir_filas_ventas_neola("2026-03-13", ventas, consumo)

    def test_agrega_columnas_si_ventas_neola_aun_esta_en_a_k(self):
        ventas = [
            {"plato": "TABLA MIXTA", "cantidad": 1, "precio_total": 20.00},
        ]
        consumo = [
            {"plato": "TABLA MIXTA", "insumo": f"INSUMO {idx}", "cantidad_total": idx}
            for idx in range(1, 7)
        ]
        rows_to_write = sheets_connector._construir_filas_ventas_neola("2026-03-13", ventas, consumo)

        class Worksheet:
            def __init__(self):
                self.row_count = 10
                self.col_count = 11
                self.id = 123
                self.add_cols_calls = []

            def add_cols(self, amount):
                self.add_cols_calls.append(amount)
                self.col_count += amount

            def update(self, range_name, values):
                return None

        ws = Worksheet()
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: ws, batch_update=lambda payload: None)
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "_leer_valores_hoja",
            side_effect=[
                [[""] * sheets_connector.COLUMNAS_VENTAS_NEOLA for _ in range(ws.row_count)],
                rows_to_write,
            ],
        ):
            sheets_connector.escribir_ventas_neola("2026-03-13", ventas, consumo)

        self.assertEqual(ws.add_cols_calls, [4])


class InventarioHelpersTests(unittest.TestCase):
    def test_valores_inventario_para_insumo_linea_con_transferencia_y_conteo(self):
        consumo_por_hoja = {"C1": {}, "C2": {}, "LINEA": {"POLLO 200 gr": 10}}
        registros = {
            "C1": {"POLLO 200 gr": {"salida": 10}},
            "C2": {},
            "LINEA": {"POLLO 200 gr": {"conteo": 6, "ingreso": 0, "salida": 0, "motivo": ""}},
        }
        tabla = {"POLLO 200 gr": {"descuento": "LINEA"}}
        transferidos = {"POLLO 200 gr": 10}

        valores = sheets_connector._valores_inventario_para_insumo(
            "LINEA",
            "POLLO 200 gr",
            6,
            registros["LINEA"]["POLLO 200 gr"],
            consumo_por_hoja,
            registros,
            tabla,
            transferidos,
        )

        self.assertEqual(valores, [6, 10, 10, 0, 10, 6])

    def test_fila_inventario_para_insumo_c1_escribe_formulas_en_salida_y_dif(self):
        consumo_por_hoja = {"C1": {"POLLO 200 gr": 3}, "C2": {}, "LINEA": {}}
        registros = {"C1": {"POLLO 200 gr": {"ingreso": 0, "salida": 3, "motivo": ""}}, "C2": {}, "LINEA": {}}
        tabla = {"POLLO 200 gr": {"descuento": "C1"}}

        fila = sheets_connector._fila_inventario_para_insumo(
            ubicacion_key="C1",
            row=12,
            col_destino=7,
            insumo="POLLO 200 gr",
            cierre_previo=10,
            registro=registros["C1"]["POLLO 200 gr"],
            consumo_por_hoja=consumo_por_hoja,
            registros=registros,
            tabla_ubicaciones=tabla,
            ingresos_linea_transferidos={},
        )

        self.assertEqual(
            fila,
            [10, "", "=R12C7+R12C8-R12C12", "=R12C9-R12C11", 3, 7],
        )

    def test_fila_inventario_para_insumo_linea_sin_conteo_usa_formula_especial(self):
        consumo_por_hoja = {"C1": {}, "C2": {}, "LINEA": {"PAN BRIOCHE": 7}}
        registros = {
            "C1": {},
            "C2": {},
            "LINEA": {"PAN BRIOCHE": {"conteo": None, "ingreso": 12, "salida": 2, "motivo": ""}},
        }

        fila = sheets_connector._fila_inventario_para_insumo(
            ubicacion_key="LINEA",
            row=5,
            col_destino=1,
            insumo="PAN BRIOCHE",
            cierre_previo=6,
            registro=registros["LINEA"]["PAN BRIOCHE"],
            consumo_por_hoja=consumo_por_hoja,
            registros=registros,
            tabla_ubicaciones={},
            ingresos_linea_transferidos={},
        )

        self.assertEqual(
            fila,
            [6, 12, "=R5C1+R5C2-R5C6-R5C5", "=0", 7, 9],
        )


class UserEnteredWriteTests(unittest.TestCase):
    def test_batch_update_user_entered_usa_raw_false(self):
        class Worksheet:
            def __init__(self):
                self.calls = []

            def batch_update(self, data, raw=True):
                self.calls.append({"data": data, "raw": raw})

        ws = Worksheet()
        payload = [{"range": "R1C1:R1C6", "values": [["", "", "=R1C1-R1C2", "=0", "", ""]]}]

        sheets_connector._batch_update_user_entered(ws, payload)

        self.assertEqual(ws.calls, [{"data": payload, "raw": False}])


class EscribirInventarioDiaTests(unittest.TestCase):
    def test_crea_dia_nuevo_sin_exigir_fecha_existente_antes_del_encabezado(self):
        class Worksheet:
            def __init__(self, title):
                self.title = title
                self.id = 1

            def col_values(self, col):
                if col == 1:
                    return ["", "", "", "POLLO 200 gr", ""]
                if col == 6:
                    return ["", "", "", "10", ""]
                return []

            def batch_update(self, data):
                return None

        hojas = {
            "C1": Worksheet("C1"),
            "C2": Worksheet("C2"),
            "LINEA CALIENTE": Worksheet("LINEA CALIENTE"),
        }
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: hojas[hoja_nombre])
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)
        writes = []

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "leer_tabla_ubicacion_descuento",
            return_value={},
        ), mock.patch.object(
            sheets_connector,
            "_buscar_seccion_mes_inventario",
            return_value=(1, 2, 3, 4, 4),
        ), mock.patch.object(
            sheets_connector,
            "_buscar_bloque_fecha_inventario",
            return_value=None,
        ), mock.patch.object(
            sheets_connector,
            "_buscar_bloque_siguiente_inventario",
            return_value=(1, 7),
        ), mock.patch.object(
            sheets_connector,
            "_copiar_bloque_inventario",
            return_value=None,
        ), mock.patch.object(
            sheets_connector,
            "_batch_update_user_entered",
            side_effect=lambda ws, data: writes.append((ws.title, data)),
        ):
            sheets_connector.escribir_inventario_dia(
                "2026-03-14",
                {},
                {"C1": {}, "C2": {}, "LINEA": {}},
                {},
                cache={},
            )

        self.assertEqual(len(writes), 3)


class CorregirInventarioInsumosTests(unittest.TestCase):
    def test_corrige_insumos_objetivo_con_sufijos_en_filas_de_inventario(self):
        class Worksheet:
            def __init__(self, title):
                self.title = title
                self.id = 1

        hojas = {
            "C1": Worksheet("C1"),
            "C2": Worksheet("C2"),
            "LINEA CALIENTE": Worksheet("LINEA CALIENTE"),
        }
        sh = types.SimpleNamespace(worksheet=lambda hoja_nombre: hojas[hoja_nombre])
        gc = types.SimpleNamespace(open_by_key=lambda key: sh)
        productos = [
            "INSUMO",
            "KLOBASA DE PRAGA (6 unidad, 500 gr)",
            "SALCHICHA JALAPENO (5 unidad, 500 gr)",
            "OTRO INSUMO",
        ]
        cierres = ["", "10", "8", "5"]
        writes = []

        def contexto(ws, fecha, cache=None):
            return 7, 2, 4, 1, productos, cierres

        def batch_update(ws, data):
            writes.append({
                "hoja": ws.title,
                "ranges": [item["range"] for item in data],
            })

        with mock.patch.object(sheets_connector, "get_client", return_value=gc), mock.patch.object(
            sheets_connector,
            "leer_tabla_ubicacion_descuento",
            return_value={},
        ), mock.patch.object(
            sheets_connector,
            "_contexto_bloque_inventario",
            side_effect=contexto,
        ), mock.patch.object(
            sheets_connector,
            "_fila_inventario_para_insumo",
            side_effect=lambda **kwargs: [kwargs["insumo"], "", "", "", "", ""],
        ), mock.patch.object(
            sheets_connector,
            "_batch_update_user_entered",
            side_effect=batch_update,
        ):
            actualizados = sheets_connector.corregir_inventario_insumos(
                "2026-03-14",
                ["KLOBASA DE PRAGA", "SALCHICHA JALAPENO"],
                {},
                {"C1": {}, "C2": {}, "LINEA": {}},
                {},
            )

        for hoja in ("C1", "C2", "LINEA CALIENTE"):
            self.assertEqual(
                actualizados[hoja],
                [
                    "KLOBASA DE PRAGA (6 unidad, 500 gr)",
                    "SALCHICHA JALAPENO (5 unidad, 500 gr)",
                ],
            )
        self.assertEqual(
            writes,
            [
                {"hoja": "C1", "ranges": ["R2C7:R2C12", "R3C7:R3C12"]},
                {"hoja": "C2", "ranges": ["R2C7:R2C12", "R3C7:R3C12"]},
                {"hoja": "LINEA CALIENTE", "ranges": ["R2C7:R2C12", "R3C7:R3C12"]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
