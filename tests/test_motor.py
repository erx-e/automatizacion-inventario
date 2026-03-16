import importlib
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def cargar_motor():
    sys.modules.pop("motor", None)

    config_module = types.ModuleType("config")
    config_module.UMBRAL_DESCUADRE = 1
    config_module.PLATOS_IGNORADOS = [
        "FISH & CHIPS GOL",
        "ROLLITOS RELLENO",
        "DEDOS DE MASA MA",
    ]
    sys.modules["config"] = config_module

    parser_module = types.ModuleType("parser_neola")
    state = {
        "ventas": [],
        "ventas_excel": [],
        "recetas": [],
        "consumo": [],
        "alertas": [],
        "ubicaciones": {},
        "registros": {"C1": {}, "C2": {}, "LINEA": {}},
        "ventas_writes": [],
        "inventario_writes": [],
        "inventario_corrections": [],
    }

    parser_module.parsear_foto_ticket = lambda image_path: state["ventas"]
    parser_module.parsear_foto_bytes = lambda image_bytes, media_type="image/jpeg": state["ventas"]
    sys.modules["parser_neola"] = parser_module

    recetas_module = types.ModuleType("recetas")
    recetas_module.calcular_consumo_teorico = lambda ventas, recetas: (state["consumo"], state["alertas"])

    def normalizar(nombre):
        return nombre.upper().strip().replace("  ", " ")

    recetas_module.normalizar_nombre = normalizar
    recetas_module.plato_ignorado = lambda plato: normalizar(plato) in {
        normalizar(item) for item in config_module.PLATOS_IGNORADOS
    }

    def buscar_receta_stub(plato_neola, recetas):
        plato_norm = normalizar(plato_neola)
        exactos = [r for r in recetas if normalizar(r["plato"]) == plato_norm]
        return exactos

    recetas_module.buscar_receta = buscar_receta_stub
    recetas_module.resolver_variantes_receta = lambda plato, recetas_plato: (recetas_plato, [])
    recetas_module.sugerir_receta_similar = lambda plato, recetas: None
    recetas_module.es_ensalada_cesar_sin_proteina = (
        lambda plato: plato.upper().strip() in {"ENSALADA CAESAR", "ENSALADA CESAR"}
    )

    def agrupar_consumo_stub(consumo):
        agrupado = {}
        for item in consumo:
            insumo = item["insumo"]
            if insumo not in agrupado:
                agrupado[insumo] = {
                    "total": 0,
                    "unidad": item["unidad"],
                    "ubicacion": item["ubicacion"],
                    "sku": item["sku"],
                    "detalle": [],
                }
            agrupado[insumo]["total"] += item["cantidad_total"]
            agrupado[insumo]["detalle"].append({
                "plato": item["plato"],
                "cant_platos": item["cantidad_platos"],
                "cant_insumo": item["cantidad_total"],
            })
        return agrupado

    recetas_module.agrupar_consumo_por_insumo = agrupar_consumo_stub
    sys.modules["recetas"] = recetas_module

    sheets_module = types.ModuleType("sheets_connector")
    sheets_module.leer_recetas = lambda: state["recetas"]
    sheets_module.leer_ubicacion_descuento = lambda: state["ubicaciones"]
    sheets_module.leer_registros_dia_completo = lambda fecha: state["registros"]
    sheets_module.escribir_ventas_neola = lambda fecha, ventas, consumo: state["ventas_writes"].append(
        (fecha, ventas, consumo)
    )
    sheets_module.escribir_inventario_dia = (
        lambda fecha, consumo_agrupado, registros, ubicaciones: state["inventario_writes"].append(
            (fecha, consumo_agrupado, registros, ubicaciones)
        )
    )
    sheets_module.corregir_inventario_insumos = lambda fecha, insumos, consumo_agrupado, registros, ubicaciones: (
        state["inventario_corrections"].append(
            (fecha, insumos, consumo_agrupado, registros, ubicaciones)
        ) or {
            "C1": [],
            "C2": [],
            "LINEA CALIENTE": [],
        }
    )
    sheets_module.leer_diferencias_inventario_dia = lambda fecha: {
        "C1": [],
        "C2": [],
        "LINEA CALIENTE": [],
    }
    sheets_module.verificar_inventario_dia_existe = lambda fecha: {
        "C1": True,
        "C2": True,
        "LINEA CALIENTE": True,
    }
    sheets_module.leer_ventas_neola_dia = lambda fecha: state["ventas_excel"]
    sys.modules["sheets_connector"] = sheets_module

    motor = importlib.import_module("motor")
    return motor, state


class FakeDateTime(datetime):
    current = datetime(2026, 3, 13, 10, 30)

    @classmethod
    def now(cls, tz=None):
        return cls.current.replace(tzinfo=tz)


class SugerirFechaTests(unittest.TestCase):
    def test_horario_diurno_sugiere_hoy(self):
        motor, _ = cargar_motor()
        FakeDateTime.current = datetime(2026, 3, 13, 10, 30)

        with mock.patch.object(motor, "datetime", FakeDateTime):
            fecha, motivo = motor.sugerir_fecha()

        self.assertEqual(fecha, "2026-03-13")
        self.assertIn("fecha de hoy", motivo)

    def test_madrugada_sugiere_dia_anterior(self):
        motor, _ = cargar_motor()
        FakeDateTime.current = datetime(2026, 3, 13, 2, 15)

        with mock.patch.object(motor, "datetime", FakeDateTime):
            fecha, motivo = motor.sugerir_fecha()

        self.assertEqual(fecha, "2026-03-12")
        self.assertIn("día anterior", motivo)

    def test_horario_nocturno_sugiere_hoy(self):
        motor, _ = cargar_motor()
        FakeDateTime.current = datetime(2026, 3, 13, 21, 0)

        with mock.patch.object(motor, "datetime", FakeDateTime):
            fecha, motivo = motor.sugerir_fecha()

        self.assertEqual(fecha, "2026-03-13")
        self.assertIn("cierre de hoy", motivo)

    def test_limite_exacto_19h_sugiere_hoy(self):
        motor, _ = cargar_motor()
        FakeDateTime.current = datetime(2026, 3, 13, 19, 0)

        with mock.patch.object(motor, "datetime", FakeDateTime):
            fecha, motivo = motor.sugerir_fecha()

        self.assertEqual(fecha, "2026-03-13")
        self.assertIn("cierre de hoy", motivo)


class ConfirmarCierreTests(unittest.TestCase):
    def test_reporte_descuadre_usa_salida_de_la_ubicacion_conciliada(self):
        motor, state = cargar_motor()
        state["ubicaciones"] = {"PAN": "C1"}
        state["registros"] = {
            "C1": {"PAN": {"ingreso": 0, "salida": 2, "motivo": ""}},
            "C2": {"PAN": {"ingreso": 0, "salida": 5, "motivo": ""}},
            "LINEA": {},
        }

        preparacion = {
            "fecha": "2026-03-12",
            "ventas": [{"plato": "HAMBURGUESA", "cantidad": 1, "precio_total": 9.99}],
            "consumo": [],
            "consumo_agrupado": {
                "PAN": {
                    "total": 4,
                    "unidad": "UND",
                    "ubicacion": "C1",
                    "sku": "SKU-1",
                    "detalle": [],
                }
            },
            "alertas": [],
        }
        motor.calcular_consumo_teorico = lambda ventas, recetas: ([
            {
                "plato": "HAMBURGUESA",
                "cantidad_platos": 1,
                "insumo": "PAN",
                "sku": "SKU-1",
                "cantidad_por_plato": 4,
                "cantidad_total": 4,
                "unidad": "UND",
                "ubicacion": "C1",
            }
        ], [])
        motor.leer_diferencias_inventario_dia = lambda fecha: (_ for _ in ()).throw(
            ValueError("sin lectura final")
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            motor.CIERRES_DIR = Path(tmp_dir) / "cierres"
            reporte = motor.confirmar_cierre(preparacion)

        self.assertIn("registrado=2, DIF=-2 (C1)", reporte)
        self.assertNotIn("registrado=7", reporte)

    def test_no_escribe_inventario_si_falla_verificacion_de_ventas(self):
        motor, state = cargar_motor()
        state["ubicaciones"] = {}
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        motor.escribir_ventas_neola = lambda fecha, ventas, consumo: (_ for _ in ()).throw(
            ValueError("No se pudo corregir automaticamente VENTAS NEOLA")
        )

        preparacion = {
            "fecha": "2026-03-13",
            "ventas": [{"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28}],
            "ventas_originales": [{"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28}],
            "consumo": [],
            "consumo_agrupado": {},
            "alertas": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            motor.CIERRES_DIR = Path(tmp_dir) / "cierres"
            reporte = motor.confirmar_cierre(preparacion)

        self.assertIn("Error al guardar ventas", reporte)
        self.assertIn("Inventario no actualizado porque no se pudo corregir automaticamente VENTAS NEOLA", reporte)
        self.assertEqual(state["inventario_writes"], [])

    def test_reporte_final_agrupa_diferencias_por_hoja(self):
        motor, state = cargar_motor()
        state["ubicaciones"] = {}
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        motor.leer_diferencias_inventario_dia = lambda fecha: {
            "C1": [{"insumo": "POLLO", "inicio": 0, "ingreso": 2, "salida": 1, "dif": -1, "ventas": 2, "cierre": 1}],
            "C2": [],
            "LINEA CALIENTE": [{"insumo": "CERDO", "inicio": 3, "ingreso": 10, "salida": 7, "dif": 0, "ventas": 7, "cierre": 6}],
        }

        preparacion = {
            "fecha": "2026-03-13",
            "ventas": [{"plato": "X", "cantidad": 1, "precio_total": 1}],
            "ventas_originales": [{"plato": "X", "cantidad": 1, "precio_total": 1}],
            "consumo": [],
            "consumo_agrupado": {},
            "alertas": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            motor.CIERRES_DIR = Path(tmp_dir) / "cierres"
            reporte = motor.confirmar_cierre(preparacion)

        self.assertIn("DIFERENCIAS FINALES", reporte)
        self.assertIn("C1:", reporte)
        self.assertIn("C2:", reporte)
        self.assertIn("LINEA CALIENTE:", reporte)


class RollitosRellenosTests(unittest.TestCase):
    def test_resuelve_rollitos_desde_registro_c2(self):
        motor, _ = cargar_motor()
        ventas = [
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        registros = {
            "C1": {},
            "C2": {
                "CREPE POLLO 2 unid": {"salida": 1},
                "CREPE QUESO 2 unid": {"salida": 1},
            },
            "LINEA": {},
        }

        resueltas, alertas, requiere = motor._resolver_rollitos_rellenos(
            ventas,
            "2026-03-10",
            registros=registros,
        )

        self.assertFalse(requiere)
        self.assertEqual(
            resueltas,
            [
                {"plato": "ROLLITOS RELLENO POLLO", "cantidad": 1, "precio_total": 5.0},
                {"plato": "ROLLITOS RELLENO QUESO", "cantidad": 1, "precio_total": 5.0},
            ],
        )
        self.assertIn("REGISTRO C2", alertas[0])

    def test_bloquea_rollitos_si_registro_no_cuadra(self):
        motor, _ = cargar_motor()
        ventas = [
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        registros = {
            "C1": {},
            "C2": {
                "CREPE POLLO 2 unid": {"salida": 1},
            },
            "LINEA": {},
        }

        resueltas, alertas, requiere = motor._resolver_rollitos_rellenos(
            ventas,
            "2026-03-10",
            registros=registros,
        )

        self.assertTrue(requiere)
        self.assertEqual(resueltas, ventas)
        self.assertIn("Indica cuántos fueron de pollo y cuántos de queso", alertas[0])

    def test_preparar_cierre_bloquea_si_rollitos_no_se_pueden_resolver(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        state["registros"] = {
            "C1": {},
            "C2": {
                "CREPE POLLO 2 unid": {"ingreso": 0, "salida": 1, "motivo": ""},
            },
            "LINEA": {},
        }

        preparacion = motor.preparar_cierre(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertFalse(preparacion["ok"])
        self.assertTrue(preparacion["requiere_aclaracion"])
        self.assertIn("No se puede continuar hasta aclarar los rollitos rellenos", preparacion["resumen"])
        self.assertIn("Pendiente definir si es de pollo o queso", preparacion["resumen"])

    def test_solo_consumo_muestra_el_resto_y_deja_rollitos_por_confirmar(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "SALCHIPRAGA", "cantidad": 4, "precio_total": 25.96},
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        state["consumo"] = [
            {
                "plato": "SALCHIPRAGA",
                "cantidad_platos": 4,
                "insumo": "SALCHICHA VIENESA H-D",
                "sku": "EMB-003",
                "cantidad_por_plato": 2,
                "cantidad_total": 8,
                "unidad": "UNID",
                "ubicacion": "C1",
            },
        ]
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}

        salida = motor.solo_consumo_teorico(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertIn("   • SALCHIPRAGA x4", salida)
        self.assertIn("      - SALCHICHA VIENESA H-D: 8 UNID", salida)
        self.assertIn("   • ROLLITOS RELLENO x2", salida)
        self.assertIn("      - Motivo: Pendiente definir si es de pollo o queso", salida)
        self.assertIn("Tipo de rollitos vendidos por confirmar", salida)
        self.assertIn("Cuántos fueron de pollo y cuántos de queso", salida)
        self.assertNotIn("REGISTRO C2", salida)

    def test_solo_consumo_no_consulta_registros_para_rollitos(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        motor.leer_registros_dia_completo = lambda fecha: (_ for _ in ()).throw(
            AssertionError("solo_consumo_teorico no debe consultar registros")
        )

        salida = motor.solo_consumo_teorico(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertIn("Tipo de rollitos vendidos por confirmar", salida)
        self.assertNotIn("REGISTRO C2", salida)

    def test_solo_consumo_consulta_registros_solo_si_se_pide(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "ROLLITOS RELLENO", "cantidad": 2, "precio_total": 10.00},
        ]
        state["recetas"] = [
            {"plato": "ROLLITOS RELLENO POLLO", "sku": "CRE-002", "insumo": "CREPE POLLO 2 unid", "cantidad": 1, "unidad": "PAX", "ubicacion": "C2"},
            {"plato": "ROLLITOS RELLENO QUESO", "sku": "CRE-004", "insumo": "CREPE QUESO 2 unid", "cantidad": 1, "unidad": "PAX", "ubicacion": "C2"},
        ]
        state["registros"] = {
            "C1": {},
            "C2": {
                "CREPE POLLO 2 unid": {"ingreso": 0, "salida": 1, "motivo": ""},
                "CREPE QUESO 2 unid": {"ingreso": 0, "salida": 1, "motivo": ""},
            },
            "LINEA": {},
        }
        motor.calcular_consumo_teorico = lambda ventas, recetas: ([
            {
                "plato": "ROLLITOS RELLENO POLLO",
                "cantidad_platos": 1,
                "insumo": "CREPE POLLO 2 unid",
                "sku": "CRE-002",
                "cantidad_por_plato": 1,
                "cantidad_total": 1,
                "unidad": "PAX",
                "ubicacion": "C2",
            },
            {
                "plato": "ROLLITOS RELLENO QUESO",
                "cantidad_platos": 1,
                "insumo": "CREPE QUESO 2 unid",
                "sku": "CRE-004",
                "cantidad_por_plato": 1,
                "cantidad_total": 1,
                "unidad": "PAX",
                "ubicacion": "C2",
            },
        ], [])

        salida = motor.solo_consumo_teorico(
            image_path="ticket.jpg",
            fecha="2026-03-10",
            usar_registros_rollitos=True,
        )

        self.assertIn("ROLLITOS RELLENO POLLO x1", salida)
        self.assertIn("ROLLITOS RELLENO QUESO x1", salida)
        self.assertIn("CREPE POLLO 2 unid: 1 PAX", salida)
        self.assertIn("CREPE QUESO 2 unid: 1 PAX", salida)
        self.assertIn("resueltos desde REGISTRO C2", salida)
        self.assertNotIn("Tipo de rollitos vendidos por confirmar", salida)


class FormatoPreviewTests(unittest.TestCase):
    def test_preparar_cierre_muestra_insumos_indentados_y_total_simple(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 9.99},
            {"plato": "DEDOS DE MASA MA", "cantidad": 2, "precio_total": 11.98},
            {"plato": "PLATO SIN RECETA", "cantidad": 1, "precio_total": 7.50},
            {"plato": "PAPAS CHEDDAR", "cantidad": 1, "precio_total": 8.00},
        ]
        state["recetas"] = [
            {"plato": "HAMBURGUESA GOLD", "sku": "RES-010", "insumo": "HAMBURGUESA (180gr)", "cantidad": 1, "unidad": "UND", "ubicacion": "C1"},
            {"plato": "HAMBURGUESA GOLD", "sku": "PAN-002", "insumo": "PAN DE SEMILLA NEGRA", "cantidad": 1, "unidad": "UND", "ubicacion": "LINEA"},
            {"plato": "PAPAS CHEDDAR", "sku": "", "insumo": "", "cantidad": 0, "unidad": "", "ubicacion": ""},
        ]
        state["consumo"] = [
            {
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 1,
                "insumo": "HAMBURGUESA (180gr)",
                "sku": "RES-010",
                "cantidad_por_plato": 1,
                "cantidad_total": 1,
                "unidad": "UND",
                "ubicacion": "C1",
            },
            {
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 1,
                "insumo": "PAN DE SEMILLA NEGRA",
                "sku": "PAN-002",
                "cantidad_por_plato": 1,
                "cantidad_total": 1,
                "unidad": "UND",
                "ubicacion": "LINEA",
            },
        ]

        preparacion = motor.preparar_cierre(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertTrue(preparacion["ok"])
        self.assertIn("   • HAMBURGUESA GOLD x1", preparacion["resumen"])
        self.assertIn("      - HAMBURGUESA (180gr): 1 UND", preparacion["resumen"])
        self.assertIn("      - PAN DE SEMILLA NEGRA: 1 UND", preparacion["resumen"])
        self.assertIn("   • DEDOS DE MASA MA x2", preparacion["resumen"])
        self.assertIn("      - Motivo: Plato ignorado por configuración", preparacion["resumen"])
        self.assertIn("   • PLATO SIN RECETA x1", preparacion["resumen"])
        self.assertIn("      - Motivo: Sin receta encontrada", preparacion["resumen"])
        self.assertIn("   • PAPAS CHEDDAR x1", preparacion["resumen"])
        self.assertIn("      - Motivo: Receta sin insumos inventariables", preparacion["resumen"])
        self.assertIn("📦 Total por insumo (2 insumos):", preparacion["resumen"])
        self.assertNotIn("📍 C1", preparacion["resumen"])

    def test_preparar_cierre_aclara_ensalada_caesar_por_defecto(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "ENSALADA CAESAR", "cantidad": 2, "precio_total": 17.99},
        ]
        state["consumo"] = [
            {
                "plato": "ENSALADA CAESAR",
                "cantidad_platos": 2,
                "insumo": "POLLO 160 gr CECAR",
                "sku": "POL-005",
                "cantidad_por_plato": 1,
                "cantidad_total": 2,
                "unidad": "UND",
                "ubicacion": "C1",
            },
        ]
        state["alertas"] = ["ℹ️ ENSALADA CAESAR se toma por defecto como ENSALADA CÉSAR (POLLO)."]

        preparacion = motor.preparar_cierre(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertTrue(preparacion["ok"])
        self.assertIn("Nota: Por defecto se toma como ENSALADA CÉSAR (POLLO)", preparacion["resumen"])
        self.assertIn("ENSALADA CAESAR se toma por defecto", preparacion["resumen"])

    def test_preparar_cierre_marca_precierre_si_se_indica(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 9.99},
        ]

        preparacion = motor.preparar_cierre(
            image_path="ticket.jpg",
            fecha="2026-03-10",
            precierre=True,
        )

        self.assertTrue(preparacion["ok"])
        self.assertTrue(preparacion["precierre"])
        self.assertIn("Ticket marcado como precierre", preparacion["resumen"])

    def test_preparar_cierre_bloquea_receta_similar_por_confirmar(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "TABLA DE QUESOS", "cantidad": 1, "precio_total": 14.99},
        ]
        state["alertas"] = [
            "❓ Posible receta similar: en recetas existe 'TABLA QUESOS EMB', pero en el ticket sale 'TABLA DE QUESOS'. ¿Cambio el nombre de la receta a 'TABLA DE QUESOS' y aplico esa receta?"
        ]

        preparacion = motor.preparar_cierre(image_path="ticket.jpg", fecha="2026-03-10")

        self.assertFalse(preparacion["ok"])
        self.assertTrue(preparacion["requiere_aclaracion"])
        self.assertIn("no coincide exactamente con las recetas", preparacion["resumen"])
        self.assertIn("TABLA QUESOS EMB", preparacion["resumen"])

    def test_solo_consumo_teorico_usa_el_mismo_formato_para_mensajeria(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "SALCHIPRAGA", "cantidad": 4, "precio_total": 25.96},
        ]
        state["consumo"] = [
            {
                "plato": "SALCHIPRAGA",
                "cantidad_platos": 4,
                "insumo": "SALCHICHA VIENESA H-D",
                "sku": "EMB-003",
                "cantidad_por_plato": 2,
                "cantidad_total": 8,
                "unidad": "UNID",
                "ubicacion": "C1",
            },
        ]

        salida = motor.solo_consumo_teorico(image_path="ticket.jpg")

        self.assertIn("🍽️ Por plato:", salida)
        self.assertIn("   • SALCHIPRAGA x4", salida)
        self.assertIn("      - SALCHICHA VIENESA H-D: 8 UNID", salida)
        self.assertIn("📦 Total por insumo (1 insumo):", salida)


class HistorialCierreTests(unittest.TestCase):
    def test_confirmar_cierre_guarda_historial_json(self):
        motor, state = cargar_motor()
        state["ubicaciones"] = {}
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        state["consumo"] = [
            {
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 7,
                "insumo": "HAMBURGUESA (180gr)",
                "sku": "RES-010",
                "cantidad_por_plato": 1,
                "cantidad_total": 7,
                "unidad": "UND",
                "ubicacion": "C1",
            },
        ]

        preparacion = {
            "fecha": "2026-03-13",
            "ventas": [{"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28}],
            "ventas_originales": [{"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28}],
            "consumo": [
                {
                    "plato": "HAMBURGUESA GOLD",
                    "cantidad_platos": 7,
                    "insumo": "HAMBURGUESA (180gr)",
                    "sku": "RES-010",
                    "cantidad_por_plato": 1,
                    "cantidad_total": 7,
                    "unidad": "UND",
                    "ubicacion": "C1",
                },
            ],
            "consumo_agrupado": {
                "HAMBURGUESA (180gr)": {
                    "total": 7, "unidad": "UND", "ubicacion": "C1", "sku": "RES-010", "detalle": [],
                },
            },
            "alertas": [],
        }

        import json as json_mod
        with tempfile.TemporaryDirectory() as tmp_dir:
            motor.CIERRES_DIR = Path(tmp_dir) / "cierres"
            motor.confirmar_cierre(preparacion)

            json_path = Path(tmp_dir) / "cierres" / "13-03-2026" / "13-03-2026.json"
            self.assertTrue(json_path.exists())

            historial = json_mod.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(historial["fecha"], "2026-03-13")
            self.assertIn("HAMBURGUESA (180gr)", historial["consumo_agrupado"])
            self.assertEqual(historial["consumo_agrupado"]["HAMBURGUESA (180gr)"]["total"], 7)


class InventarioRegistrosTests(unittest.TestCase):
    def test_preparar_inventario_registros_muestra_preview(self):
        motor, state = cargar_motor()
        state["registros"] = {
            "C1": {"POLLO 200 gr": {"ingreso": 5, "salida": 2, "motivo": ""}},
            "C2": {"CREPE POLLO 2 unid": {"ingreso": 0, "salida": 3, "motivo": "venta"}},
            "LINEA": {},
        }

        prep = motor.preparar_inventario_registros(fecha="2026-03-14")

        self.assertTrue(prep["ok"])
        self.assertEqual(prep["fecha"], "2026-03-14")
        self.assertIn("INVENTARIO DESDE REGISTROS", prep["resumen"])
        self.assertIn("POLLO 200 gr", prep["resumen"])
        self.assertIn("ingreso=5", prep["resumen"])
        self.assertIn("salida=2", prep["resumen"])
        self.assertIn("CREPE POLLO 2 unid", prep["resumen"])
        self.assertIn("¿Todo correcto?", prep["resumen"])

    def test_preparar_inventario_registros_falla_sin_registros(self):
        motor, state = cargar_motor()
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}

        prep = motor.preparar_inventario_registros(fecha="2026-03-14")

        self.assertFalse(prep["ok"])
        self.assertIn("No hay registros", prep["resumen"])

    def test_confirmar_inventario_registros_escribe_con_ventas_vacias(self):
        motor, state = cargar_motor()
        state["registros"] = {
            "C1": {"POLLO 200 gr": {"ingreso": 5, "salida": 2, "motivo": ""}},
            "C2": {},
            "LINEA": {},
        }
        state["ubicaciones"] = {"POLLO 200 gr": "C1"}

        preparacion = {
            "fecha": "2026-03-14",
            "registros": state["registros"],
            "ubicaciones": state["ubicaciones"],
        }
        resultado = motor.confirmar_inventario_registros(preparacion)

        self.assertIn("INVENTARIO CREADO", resultado)
        self.assertIn("2026-03-14", resultado)
        self.assertIn("VENTAS está vacío", resultado)
        self.assertEqual(len(state["inventario_writes"]), 1)
        fecha, consumo, registros, ubicaciones = state["inventario_writes"][0]
        self.assertEqual(fecha, "2026-03-14")
        self.assertEqual(consumo, {})  # Ventas vacías

    def test_preparar_inventario_registros_usa_fecha_sugerida(self):
        motor, state = cargar_motor()
        state["registros"] = {
            "C1": {"POLLO 200 gr": {"ingreso": 1, "salida": 0, "motivo": ""}},
            "C2": {},
            "LINEA": {},
        }

        FakeDateTime.current = datetime(2026, 3, 14, 10, 30)
        with mock.patch.object(motor, "datetime", FakeDateTime):
            prep = motor.preparar_inventario_registros()

        self.assertTrue(prep["ok"])
        self.assertEqual(prep["fecha"], "2026-03-14")


class SoloVentasTests(unittest.TestCase):
    def test_preparar_solo_ventas_falla_si_entrada_no_existe(self):
        motor, state = cargar_motor()
        motor.verificar_inventario_dia_existe = lambda fecha: {
            "C1": True,
            "C2": False,
            "LINEA CALIENTE": True,
        }

        prep = motor.preparar_solo_ventas(image_path="ticket.jpg", fecha="2026-03-14")

        self.assertFalse(prep["ok"])
        self.assertIn("No existe la entrada", prep["resumen"])
        self.assertIn("C2", prep["resumen"])

    def test_preparar_solo_ventas_muestra_preview_si_entrada_existe(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 2, "precio_total": 19.98},
        ]
        state["recetas"] = [
            {"plato": "HAMBURGUESA GOLD", "sku": "RES-010", "insumo": "HAMBURGUESA (180gr)", "cantidad": 1, "unidad": "UND", "ubicacion": "C1"},
        ]
        state["consumo"] = [
            {
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 2,
                "insumo": "HAMBURGUESA (180gr)",
                "sku": "RES-010",
                "cantidad_por_plato": 1,
                "cantidad_total": 2,
                "unidad": "UND",
                "ubicacion": "C1",
            },
        ]
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}

        prep = motor.preparar_solo_ventas(image_path="ticket.jpg", fecha="2026-03-14")

        self.assertTrue(prep["ok"])
        self.assertTrue(prep.get("solo_ventas"))
        self.assertIn("CARGAR VENTAS", prep["resumen"])
        self.assertIn("Solo se actualizarán las VENTAS", prep["resumen"])
        self.assertIn("HAMBURGUESA GOLD", prep["resumen"])
        self.assertIn("¿Aplico la actualización?", prep["resumen"])

    def test_preparar_solo_ventas_falla_si_todas_las_hojas_faltan(self):
        motor, state = cargar_motor()
        motor.verificar_inventario_dia_existe = lambda fecha: {
            "C1": False,
            "C2": False,
            "LINEA CALIENTE": False,
        }

        prep = motor.preparar_solo_ventas(image_path="ticket.jpg", fecha="2026-03-14")

        self.assertFalse(prep["ok"])
        self.assertIn("C1", prep["resumen"])
        self.assertIn("C2", prep["resumen"])
        self.assertIn("LINEA CALIENTE", prep["resumen"])
        self.assertIn("Primero crea la entrada", prep["resumen"])

    def test_confirmar_solo_ventas_corrige_solo_insumos_afectados(self):
        motor, state = cargar_motor()
        state["ubicaciones"] = {}
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        motor.calcular_consumo_teorico = lambda ventas, recetas: ([
            {
                "plato": venta["plato"],
                "cantidad_platos": venta["cantidad"],
                "insumo": "PAN",
                "sku": "SKU-1",
                "cantidad_por_plato": 1,
                "cantidad_total": venta["cantidad"],
                "unidad": "UND",
                "ubicacion": "C1",
            }
            for venta in ventas
        ], [])

        preparacion = {
            "fecha": "2026-03-14",
            "ventas_finales": [{"plato": "HAMBURGUESA GOLD", "cantidad": 2, "precio_total": 19.98}],
            "consumo_final": [{
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 2,
                "insumo": "PAN",
                "sku": "SKU-1",
                "cantidad_por_plato": 1,
                "cantidad_total": 2,
                "unidad": "UND",
                "ubicacion": "C1",
            }],
            "consumo_agrupado_final": {
                "PAN": {"total": 2, "unidad": "UND", "ubicacion": "C1", "sku": "SKU-1", "detalle": []},
            },
            "registros": state["registros"],
            "ubicaciones": state["ubicaciones"],
            "cambios": [{"plato": "HAMBURGUESA GOLD", "cantidad_actual": 0, "cantidad_nueva": 2, "delta": 2}],
            "insumos_afectados": ["PAN"],
            "origen_actualizacion": "solo_ventas",
            "solo_ventas": True,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            motor.CIERRES_DIR = Path(tmp_dir) / "cierres"
            resultado = motor.confirmar_solo_ventas(preparacion)

        self.assertIsInstance(resultado, str)
        self.assertEqual(len(state["ventas_writes"]), 1)
        self.assertEqual(len(state["inventario_corrections"]), 1)


class ActualizacionTicketTests(unittest.TestCase):
    def test_preparar_actualizacion_ticket_detecta_delta(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "NACHOS", "cantidad": 3, "precio_total": 18.0},
            {"plato": "LOMO", "cantidad": 2, "precio_total": 20.0},
        ]
        state["ventas_excel"] = [
            {"plato": "NACHOS", "cantidad": 2, "precio_total": 0.0},
            {"plato": "LOMO", "cantidad": 2, "precio_total": 0.0},
        ]
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        motor.calcular_consumo_teorico = lambda ventas, recetas: ([
            {
                "plato": venta["plato"],
                "cantidad_platos": venta["cantidad"],
                "insumo": f"INS-{venta['plato']}",
                "sku": "SKU",
                "cantidad_por_plato": 1,
                "cantidad_total": venta["cantidad"],
                "unidad": "UND",
                "ubicacion": "C1",
            }
            for venta in ventas
        ], [])

        prep = motor.preparar_actualizacion_ticket(
            image_path="ticket.jpg",
            fecha="2026-03-14",
        )

        self.assertTrue(prep["ok"])
        self.assertIn("Cambios detectados", prep["resumen"])
        self.assertIn("NACHOS: 2 → 3 (+1)", prep["resumen"])
        self.assertEqual(prep["insumos_afectados"], ["INS-NACHOS"])

    def test_confirmar_actualizacion_ticket_actualiza_ventas_y_corrige_inventario(self):
        motor, state = cargar_motor()
        preparacion = {
            "fecha": "2026-03-14",
            "ventas_finales": [{"plato": "NACHOS", "cantidad": 3, "precio_total": 18.0}],
            "consumo_final": [{
                "plato": "NACHOS",
                "cantidad_platos": 3,
                "insumo": "INS-NACHOS",
                "sku": "SKU",
                "cantidad_por_plato": 1,
                "cantidad_total": 3,
                "unidad": "UND",
                "ubicacion": "C1",
            }],
            "consumo_agrupado_final": {
                "INS-NACHOS": {"total": 3, "unidad": "UND", "ubicacion": "C1", "sku": "SKU", "detalle": []},
            },
            "registros": {"C1": {}, "C2": {}, "LINEA": {}},
            "ubicaciones": {},
            "cambios": [{"plato": "NACHOS", "cantidad_actual": 2, "cantidad_nueva": 3, "delta": 1}],
            "insumos_afectados": ["INS-NACHOS"],
            "origen_actualizacion": "ticket_nuevo",
        }

        resultado = motor.confirmar_actualizacion_ticket(preparacion)

        self.assertIn("ACTUALIZACIÓN APLICADA", resultado)
        self.assertEqual(len(state["ventas_writes"]), 1)
        self.assertEqual(len(state["inventario_corrections"]), 1)


class AjusteVentasTests(unittest.TestCase):
    def test_preparar_ajuste_ventas_suma_y_resta(self):
        motor, state = cargar_motor()
        state["ventas_excel"] = [
            {"plato": "NACHOS", "cantidad": 2, "precio_total": 0.0},
            {"plato": "LOMO", "cantidad": 3, "precio_total": 0.0},
        ]
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}
        motor.calcular_consumo_teorico = lambda ventas, recetas: ([
            {
                "plato": venta["plato"],
                "cantidad_platos": venta["cantidad"],
                "insumo": f"INS-{venta['plato']}",
                "sku": "SKU",
                "cantidad_por_plato": 1,
                "cantidad_total": venta["cantidad"],
                "unidad": "UND",
                "ubicacion": "C1",
            }
            for venta in ventas
        ], [])

        prep = motor.preparar_ajuste_ventas(
            fecha="2026-03-14",
            ajustes=[
                {"plato": "NACHOS", "delta": 1},
                {"plato": "LOMO", "delta": -2},
            ],
        )

        self.assertTrue(prep["ok"])
        self.assertIn("NACHOS: 2 → 3 (+1)", prep["resumen"])
        self.assertIn("LOMO: 3 → 1 (-2)", prep["resumen"])

    def test_preparar_ajuste_ventas_bloquea_cantidad_negativa(self):
        motor, state = cargar_motor()
        state["ventas_excel"] = [
            {"plato": "NACHOS", "cantidad": 1, "precio_total": 0.0},
        ]

        prep = motor.preparar_ajuste_ventas(
            fecha="2026-03-14",
            ajustes=[{"plato": "NACHOS", "delta": -2}],
        )

        self.assertFalse(prep["ok"])
        self.assertIn("cantidad negativa", prep["resumen"])


class PrepararCorreccionTests(unittest.TestCase):
    def test_preparar_correccion_muestra_preview_sin_escribir(self):
        motor, state = cargar_motor()
        state["ventas"] = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 9.99},
        ]
        state["consumo"] = [
            {
                "plato": "HAMBURGUESA GOLD",
                "cantidad_platos": 1,
                "insumo": "HAMBURGUESA (180gr)",
                "sku": "RES-010",
                "cantidad_por_plato": 1,
                "cantidad_total": 1,
                "unidad": "UND",
                "ubicacion": "C1",
            },
        ]
        state["ubicaciones"] = {"HAMBURGUESA (180gr)": "C1"}
        state["registros"] = {"C1": {}, "C2": {}, "LINEA": {}}

        prep = motor.preparar_correccion(
            image_path="ticket.jpg",
            fecha="2026-03-13",
            insumos=["HAMBURGUESA (180gr)"],
        )

        self.assertTrue(prep["ok"])
        self.assertIn("PREVIEW DE CORRECCIÓN", prep["resumen"])
        self.assertIn("HAMBURGUESA (180gr)", prep["resumen"])
        self.assertEqual(state["inventario_writes"], [])

    def test_preparar_correccion_falla_sin_insumos(self):
        motor, _ = cargar_motor()
        prep = motor.preparar_correccion(
            image_path="ticket.jpg", fecha="2026-03-13", insumos=[],
        )
        self.assertFalse(prep["ok"])
        self.assertIn("al menos un insumo", prep["resumen"])


if __name__ == "__main__":
    unittest.main()
