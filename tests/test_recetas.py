import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


config_module = sys.modules.get("config")
if config_module is None:
    config_module = types.ModuleType("config")
    sys.modules["config"] = config_module

config_module.PLATOS_IGNORADOS = [
    "FISH & CHIPS GOL",
    "ROLLITOS RELLENO",
    "DEDOS DE MASA MA",
]

import recetas  # noqa: E402


class PlatosIgnoradosTests(unittest.TestCase):
    def test_omite_platos_configurados_del_consumo(self):
        ventas = [
            {"plato": "FISH & CHIPS GOL", "cantidad": 2, "precio_total": 30.64},
            {"plato": "HAMBURGUESA GOLD", "cantidad": 1, "precio_total": 9.99},
        ]
        recetas_data = [
            {"plato": "FISH & CHIPS 1P", "nombre_menu": "", "sku": "MAR-002", "insumo": "PESCADO", "cantidad": 1, "unidad": "UND", "ubicacion": "C2"},
            {"plato": "HAMBURGUESA GOLD", "nombre_menu": "", "sku": "RES-010", "insumo": "HAMBURGUESA (180gr)", "cantidad": 1, "unidad": "UND", "ubicacion": "C1"},
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(len(consumo), 1)
        self.assertEqual(consumo[0]["plato"], "HAMBURGUESA GOLD")
        self.assertEqual(alertas, [])

    def test_rollitos_resueltos_no_se_ignoran(self):
        ventas = [
            {"plato": "ROLLITOS RELLENO POLLO", "cantidad": 2, "precio_total": 10.00},
        ]
        recetas_data = [
            {"plato": "ROLLITOS RELLENO POLLO", "nombre_menu": "", "sku": "CRE-002", "insumo": "CREPE POLLO 2 unid", "cantidad": 1, "unidad": "PAX", "ubicacion": "C2"},
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(len(consumo), 1)
        self.assertEqual(consumo[0]["plato"], "ROLLITOS RELLENO POLLO")
        self.assertEqual(consumo[0]["insumo"], "CREPE POLLO 2 unid")
        self.assertEqual(consumo[0]["cantidad_total"], 2)
        self.assertEqual(alertas, [])

    def test_ensalada_caesar_sin_proteina_toma_pollo_por_defecto(self):
        ventas = [
            {"plato": "ENSALADA CAESAR", "cantidad": 2, "precio_total": 17.99},
        ]
        recetas_data = [
            {"plato": "ENSALADA CESAR", "nombre_menu": "ENSALADA CÉSAR (POLLO)", "sku": "POL-005", "insumo": "POLLO 160 gr CECAR", "cantidad": 1, "unidad": "UND", "ubicacion": "C1"},
            {"plato": "ENSALADA CESAR", "nombre_menu": "ENSALADA CÉSAR (CAMARÓN)", "sku": "MAR-006", "insumo": "CAMARON PYD (6U/PAX)", "cantidad": 1, "unidad": "UND", "ubicacion": "C2"},
            {"plato": "ENSALADA CESAR", "nombre_menu": "ENSALADA CÉSAR (FALAFEL)", "sku": "RES-011", "insumo": "HAMBURGUESA FALAFEL (150 gr)", "cantidad": 1, "unidad": "UND", "ubicacion": "C1"},
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(len(consumo), 1)
        self.assertEqual(consumo[0]["insumo"], "POLLO 160 gr CECAR")
        self.assertEqual(consumo[0]["cantidad_total"], 2)
        self.assertIn("ENSALADA CÉSAR (POLLO)", alertas[0])

    def test_sugiere_receta_similar_en_lugar_de_aplicar_match_peligroso(self):
        ventas = [
            {"plato": "TABLA DE QUESOS", "cantidad": 1, "precio_total": 14.99},
        ]
        recetas_data = [
            {"plato": "TABLA QUESOS EMB", "nombre_menu": "TABLA DE QUESOS Y EMBUTIDOS", "sku": "EMB-008", "insumo": "JAMON", "cantidad": 1, "unidad": "PAX", "ubicacion": "C1"},
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(consumo, [])
        self.assertEqual(len(alertas), 1)
        self.assertIn("Posible receta similar", alertas[0])
        self.assertIn("TABLA QUESOS EMB", alertas[0])

    def test_no_aplica_match_por_prefijo_sin_confirmacion(self):
        ventas = [
            {"plato": "HAMBURGUESA ARTE PULLED PORK", "cantidad": 1, "precio_total": 10.43},
        ]
        recetas_data = [
            {
                "plato": "HAMBURGUESA ARTE",
                "nombre_menu": "HAMBURGUESA ARTESANAL PULLED PORK",
                "sku": "RES-010",
                "insumo": "HAMBURGUESA (180gr)",
                "cantidad": 1,
                "unidad": "UND",
                "ubicacion": "C1",
            },
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(consumo, [])
        self.assertEqual(len(alertas), 1)
        self.assertIn("Posible receta similar", alertas[0])
        self.assertIn("HAMBURGUESA ARTE", alertas[0])

    def test_resuelve_nombre_corto_duplicado_con_la_variante_mas_cercana(self):
        ventas = [
            {"plato": "HAMBURGUESA GOLD", "cantidad": 7, "precio_total": 70.28},
        ]
        recetas_data = [
            {
                "plato": "HAMBURGUESA GOLD",
                "nombre_menu": "HAMBURGUESA GOLDEN",
                "sku": "RES-010",
                "insumo": "HAMBURGUESA (180gr)",
                "cantidad": 1,
                "unidad": "UND",
                "ubicacion": "C1",
            },
            {
                "plato": "HAMBURGUESA GOLD",
                "nombre_menu": "HAMBURGUESA GOLDEN",
                "sku": "PAN-002",
                "insumo": "PAN DE SEMILLA NEGRA",
                "cantidad": 1,
                "unidad": "UND",
                "ubicacion": "LINEA",
            },
            {
                "plato": "HAMBURGUESA GOLD",
                "nombre_menu": "HAMBURGUESA GOLDEN PLUS",
                "sku": "RES-010",
                "insumo": "HAMBURGUESA (180gr)",
                "cantidad": 1,
                "unidad": "UND",
                "ubicacion": "C1",
            },
            {
                "plato": "HAMBURGUESA GOLD",
                "nombre_menu": "HAMBURGUESA GOLDEN PLUS",
                "sku": "PAN-002",
                "insumo": "PAN DE SEMILLA NEGRA",
                "cantidad": 1,
                "unidad": "UND",
                "ubicacion": "LINEA",
            },
        ]

        consumo, alertas = recetas.calcular_consumo_teorico(ventas, recetas_data)

        self.assertEqual(alertas, [])
        self.assertEqual(
            consumo,
            [
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
                {
                    "plato": "HAMBURGUESA GOLD",
                    "cantidad_platos": 7,
                    "insumo": "PAN DE SEMILLA NEGRA",
                    "sku": "PAN-002",
                    "cantidad_por_plato": 1,
                    "cantidad_total": 7,
                    "unidad": "UND",
                    "ubicacion": "LINEA",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
