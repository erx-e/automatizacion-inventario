# config.py — Configuración del Motor de Inventario Sambó
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().with_name(".env"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"La variable {name} debe ser un entero, no {raw!r}") from exc


# ============================================================
# GOOGLE SHEETS
# ============================================================
DEFAULT_GOOGLE_CREDENTIALS_PATH = "/data/.openclaw/secrets/motor-sambo/golden-sambo-inventario-a26e523ffa6e.json"
GOOGLE_CREDENTIALS_PATH = _env("GOOGLE_CREDENTIALS_PATH", DEFAULT_GOOGLE_CREDENTIALS_PATH)

# IDs de los Google Sheets
SHEET_REGISTROS = _env("SHEET_REGISTROS")
SHEET_RECETAS = _env("SHEET_RECETAS")
SHEET_INVENTARIO = _env("SHEET_INVENTARIO")
# Nombres de las hojas
HOJA_REGISTRO_C1 = "REGISTRO C1"
HOJA_REGISTRO_C2 = "REGISTRO C2"
HOJA_REGISTRO_LINEA = "REGISTRO LINEA CALIENTE"
HOJA_MOTIVOS_ESPECIALES = _env("HOJA_MOTIVOS_ESPECIALES", "MOTIVOS ESPECIALES")
HOJA_VENTAS_NEOLA = "VENTAS NEOLA"
HOJA_UBICACION = "UBICACION DESCUENTO"
HOJA_RECETAS = "RECETAS"

# ============================================================
# CLAUDE API (Anthropic directa)
# ============================================================
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-4-6")

# ============================================================
# MOTOR
# ============================================================
UMBRAL_DESCUADRE = _env_int("UMBRAL_DESCUADRE", 1)
SHEETS_WRITE_DELAY_SECONDS = _env_int("SHEETS_WRITE_DELAY_SECONDS", 10)

# Platos a omitir del cálculo por ambigüedad o falta de receta.
PLATOS_IGNORADOS = [
    "FISH & CHIPS GOL",
    "ROLLITOS RELLENO",
    "DEDOS DE MASA MA",
]


def validar_configuracion(requiere_anthropic: bool = True):
    """Verifica que las variables de entorno críticas estén configuradas."""
    errores = []
    if requiere_anthropic and not ANTHROPIC_API_KEY:
        errores.append("ANTHROPIC_API_KEY no está configurada")
    if not Path(GOOGLE_CREDENTIALS_PATH).exists():
        errores.append(f"Archivo de credenciales no encontrado: {GOOGLE_CREDENTIALS_PATH}")
    for nombre, valor in [("SHEET_REGISTROS", SHEET_REGISTROS),
                           ("SHEET_RECETAS", SHEET_RECETAS),
                           ("SHEET_INVENTARIO", SHEET_INVENTARIO)]:
        if not valor:
            errores.append(f"{nombre} no está configurada")
    if errores:
        raise EnvironmentError("Configuración incompleta:\n  - " + "\n  - ".join(errores))
