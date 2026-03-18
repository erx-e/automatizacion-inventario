import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


anthropic_stub = types.ModuleType("anthropic")


class Anthropic:
    def __init__(self, api_key=None):
        self.api_key = api_key


anthropic_stub.Anthropic = Anthropic
sys.modules.setdefault("anthropic", anthropic_stub)

config_module = sys.modules.get("config")
if config_module is None:
    config_module = types.ModuleType("config")
    sys.modules["config"] = config_module

config_module.ANTHROPIC_API_KEY = getattr(config_module, "ANTHROPIC_API_KEY", "")
config_module.CLAUDE_MODEL = getattr(config_module, "CLAUDE_MODEL", "claude-sonnet-4-6")

import parser_neola  # noqa: E402


class ParsearRespuestaTests(unittest.TestCase):
    def test_parsea_json_dentro_de_code_fence(self):
        text = '```json\n[{"plato":"HAMBURGUESA","cantidad":2,"precio_total":19.98}]\n```'

        parsed = parser_neola._parsear_respuesta(text)

        self.assertEqual(parsed[0]["plato"], "HAMBURGUESA")
        self.assertEqual(parsed[0]["cantidad"], 2)

    def test_extrae_json_array_embebido_en_texto(self):
        text = (
            "Aquí está el análisis del ticket.\n"
            '[{"plato":"HAMBURGUESA","cantidad":2,"precio_total":19.98}]'
        )

        parsed = parser_neola._parsear_respuesta(text)

        self.assertEqual(parsed[0]["plato"], "HAMBURGUESA")
        self.assertEqual(parsed[0]["cantidad"], 2)


class ObservacionesLecturaTests(unittest.TestCase):
    def test_extrae_diagnostico_con_observacion_y_nombres_dudosos(self):
        text = (
            "OBSERVACION: La foto está borrosa en una parte y podría faltar algún plato.\n"
            "DUDOSOS: SANDWICH DE PEPE | MILANESA GOLDE\n"
            '[{"plato":"HAMBURGUESA","cantidad":2,"precio_total":19.98}]'
        )

        diagnostico = parser_neola._extraer_diagnostico_lectura(text)

        self.assertEqual(diagnostico["problema"], "La foto está borrosa en una parte y podría faltar algún plato.")
        self.assertEqual(diagnostico["platos_dudosos"], ["SANDWICH DE PEPE", "MILANESA GOLDE"])
        self.assertTrue(diagnostico["requiere_aclaracion"])
        self.assertEqual(diagnostico["mensajes"], ["La foto está borrosa en una parte y podría faltar algún plato."])

    def test_extrae_observacion_legacy_si_hay_texto_de_lectura_insegura(self):
        text = (
            "Aquí está el análisis del ticket. "
            "Noto que algunas líneas están parcialmente cubiertas por las marcas azules, "
            "pero puedo identificar lo siguiente:\n"
            '[{"plato":"HAMBURGUESA","cantidad":2,"precio_total":19.98}]'
        )

        diagnostico = parser_neola._extraer_diagnostico_lectura(text)

        self.assertIn("parcialmente cubiertas", diagnostico["problema"])
        self.assertEqual(diagnostico["platos_dudosos"], [])
        self.assertFalse(diagnostico["requiere_aclaracion"])

    def test_ignora_prefijo_si_no_indica_problemas_de_lectura(self):
        text = (
            "Aquí está el análisis del ticket.\n"
            '[{"plato":"HAMBURGUESA","cantidad":2,"precio_total":19.98}]'
        )

        diagnostico = parser_neola._extraer_diagnostico_lectura(text)

        self.assertEqual(diagnostico["mensajes"], [])
        self.assertEqual(diagnostico["platos_dudosos"], [])
        self.assertFalse(diagnostico["requiere_aclaracion"])


class ExtraerTextoRespuestaTests(unittest.TestCase):
    def test_consolida_bloques_texto_de_anthropic(self):
        response = types.SimpleNamespace(
            content=[
                types.SimpleNamespace(type="text", text='[{"plato":"PIZZA",'),
                types.SimpleNamespace(type="text", text='"cantidad":1,"precio_total":12.5}]'),
            ]
        )

        text = parser_neola._extraer_texto_respuesta(response)

        self.assertEqual(text, '[{"plato":"PIZZA","cantidad":1,"precio_total":12.5}]')

    def test_falla_si_anthropic_no_devuelve_texto(self):
        response = types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", text="")])

        with self.assertRaises(ValueError):
            parser_neola._extraer_texto_respuesta(response)


if __name__ == "__main__":
    unittest.main()
