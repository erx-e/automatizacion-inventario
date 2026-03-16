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
            ["PLATO", "MENU", "SKU", "INSUMO", "CANT", "UND", "UBIC", "CONFIRMADO", "NOTAS"],
            ["nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota"],
            ["HAMBURGUESA GOLD", "GOLD", "SKU-1", "CARNE 180G", "1", "UND", "C1", "✅", ""],
            ["", "", "SKU-2", "PAN BRIOCHE", "1", "UND", "LINEA", "✅", ""],
            ["", "", "SKU-3", "QUESO", "2", "LONJA", "LINEA", "✅", ""],
            ["PAPAS CHEDDAR", "PAPAS", "", "", "", "", "", "✅", "sin inventario"],
        ]

        recetas = sheets_connector._parsear_recetas_rows(rows)

        self.assertEqual(len(recetas), 4)
        self.assertEqual(
            [receta["insumo"] for receta in recetas[:3]],
            ["CARNE 180G", "PAN BRIOCHE", "QUESO"],
        )
        self.assertTrue(all(receta["plato"] == "HAMBURGUESA GOLD" for receta in recetas[:3]))
        self.assertEqual(recetas[3]["plato"], "PAPAS CHEDDAR")
        self.assertEqual(recetas[3]["sku"], "")
        self.assertEqual(recetas[3]["insumo"], "")

    def test_ignora_filas_de_categoria_expandidas_por_celdas_combinadas(self):
        rows = [
            ["PLATO", "MENU", "SKU", "INSUMO", "CANT", "UND", "UBIC", "CONFIRMADO", "NOTAS"],
            ["nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota", "nota"],
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
            ],
            ["HAMBURGUESA GOLD", "GOLD", "SKU-1", "CARNE 180G", "1", "UND", "C1", "✅", ""],
        ]

        recetas = sheets_connector._parsear_recetas_rows(rows)

        self.assertEqual(len(recetas), 1)
        self.assertEqual(recetas[0]["plato"], "HAMBURGUESA GOLD")


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


class VentasEsperadasTests(unittest.TestCase):
    def test_congelador_usa_salida_registrada_como_ventas(self):
        consumo_por_hoja = {"C1": {}, "C2": {"CREPE POLLO 2 unid": 0}, "LINEA": {}}
        registros = {"C1": {}, "C2": {"CREPE POLLO 2 unid": {"salida": 2}}, "LINEA": {}}
        tabla = {"CREPE POLLO 2 unid": {"descuento": "C2"}}

        ventas = sheets_connector._ventas_esperadas_para_hoja(
            "C2", "CREPE POLLO 2 unid", consumo_por_hoja, registros, tabla
        )

        self.assertEqual(ventas, 2)


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


if __name__ == "__main__":
    unittest.main()
