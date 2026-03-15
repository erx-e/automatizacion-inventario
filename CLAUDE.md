# Motor de Inventario Sambó

Sistema de cierre diario de inventario para el Restaurante Sambó (Ecuador). Procesa fotos de tickets POS (Neola), calcula consumo de ingredientes por recetas, y actualiza Google Sheets (ventas + inventario C1/C2/Línea Caliente).

## Quick reference

```bash
# Tests (sin pytest — usa unittest)
python3 -m unittest discover -s tests -v

# CLI — Opción 1 (con foto de ticket)
python3 main.py <imagen> --preparar                         # Preview cierre
python3 main.py <imagen> --preparar --fecha 2026-03-11      # Preview con fecha
python3 main.py <imagen> --solo-ventas                      # Solo cargar ventas a entrada existente
python3 main.py <imagen> --solo-ventas --confirmar           # Confirmar carga de ventas
python3 main.py <imagen> --solo-leer                        # Solo parsear ticket
python3 main.py <imagen> --consumo                          # Solo consumo teórico
python3 main.py <imagen> --fecha YYYY-MM-DD                 # Cierre directo
python3 main.py <imagen> --corregir-insumos "POLLO 200 gr,CERDO 180 gr"
python3 main.py <imagen> --preparar-correccion "POLLO 200 gr"   # Preview corrección

# CLI — Opción 2 (sin foto, solo registros)
python3 main.py --solo-registros                            # Preview inventario desde registros
python3 main.py --solo-registros --fecha 2026-03-11         # Con fecha
python3 main.py --solo-registros --confirmar                # Confirmar escritura
```

## Stack

- **Python 3** — sin framework web, todo es CLI y funciones invocables
- **anthropic** — Claude Vision para parsear fotos de tickets
- **gspread + google-auth** — lectura/escritura en Google Sheets
- **Dependencias:** `requirements.txt` (anthropic, gspread, google-auth)
- **Zona horaria:** Ecuador UTC-5 (hardcoded en `motor.py`)

## Architecture

```
main.py              → CLI entry point, parseo de args
motor.py             → Lógica central: preparar/confirmar cierre, fecha, rollitos, reportes
parser_neola.py      → Claude Vision: foto → JSON de platos vendidos
recetas.py           → Match plato↔receta, consumo teórico, normalización, similitud
sheets_connector.py  → Todo Google Sheets: leer recetas/registros, escribir ventas/inventario
config.py            → Variables de entorno (.env), constantes, platos ignorados
```

### Data flow

```
Foto ticket → [parser_neola + Claude] → ventas JSON
    → [recetas] → consumo teórico por insumo
    → [motor] → preview → confirmación usuario
    → [sheets_connector] → Google Sheets (VENTAS NEOLA + C1/C2/LINEA CALIENTE)
    → reporte de diferencias
```

## Key conventions

### Idioma
- Todo el código, variables, funciones, comentarios y mensajes al usuario están en **español**.
- Los nombres de funciones y variables usan snake_case en español (`preparar_cierre`, `leer_recetas`, `consumo_agrupado`).

### Patterns
- Las funciones públicas de `motor.py` devuelven `dict` con campo `ok` (bool) + `resumen` (str) para el flujo de preview, o `str` directamente para reportes.
- Los tests mockean módulos completos (`config`, `parser_neola`, `recetas`, `sheets_connector`) vía `sys.modules` — no usan fixtures ni pytest.
- Google Sheets tiene delays obligatorios entre writes (`SHEETS_WRITE_DELAY_SECONDS = 10`) para evitar rate limits.
- Escritura de VENTAS NEOLA incluye validación post-write con hasta 3 reintentos automáticos.

### Business rules (critical — read references/ before changing)
- **Fecha automática:** 19:00-23:59 → hoy (cierre del día); 00:00-03:59 → ayer (cierre tardío); 04:00-18:59 → hoy (caso inusual, asumimos hoy).
- **ROLLITOS RELLENO:** ambiguo, requiere resolución pollo/queso antes del cierre. Se resuelve vía REGISTRO C2 o override manual.
- **ENSALADA CAESAR sin proteína:** default a pollo, siempre explicitado en preview.
- **Match de recetas:** solo exacto sobre nombre corto normalizado. Si no hay match, sugerir la más similar y bloquear hasta confirmar. Nunca `startswith` automático.
- **Panes en LINEA CALIENTE:** no se cuentan diariamente — conteo vacío ≠ 0.
- **Transferencias a línea:** salidas de C1/C2 con `DESCUENTO POR DEFECTO = LINEA` → INGRESO en LINEA CALIENTE.
- **Inventario C1/C2:** CIERRE = INICIO + INGRESO - SALIDA; DIF = SALIDA - VENTAS.
- **Inventario LINEA con conteo:** CIERRE = conteo; SALIDA = INICIO + INGRESO - CIERRE.
- **Inventario LINEA sin conteo:** CIERRE = INICIO + INGRESO - SALIDA - VENTAS; DIF = 0.

## Config (.env)

Variables requeridas:
- `ANTHROPIC_API_KEY` — API key de Anthropic
- `GOOGLE_CREDENTIALS_PATH` — ruta al JSON de service account
- `SHEET_REGISTROS`, `SHEET_RECETAS`, `SHEET_INVENTARIO` — IDs de Google Sheets
- `CLAUDE_MODEL` — modelo a usar (default: `claude-sonnet-4-6`)
- `UMBRAL_DESCUADRE` — unidades de tolerancia en diferencias (default: 1)
- `SHEETS_WRITE_DELAY_SECONDS` — pausa entre writes (default: 10)

## Testing

```bash
python3 -m unittest discover -s tests -v
```

- Todos unitarios con mocks de módulos
- No requieren conexión a Google Sheets ni API de Anthropic
- Los tests de `motor.py` reemplazan completamente `parser_neola`, `recetas`, `sheets_connector` y `config` vía `sys.modules`
- Tests de `recetas.py` y `sheets_connector.py` solo mockean dependencias externas (anthropic, gspread, google-auth)

## Important files to read first

1. `references/flujo-operativo.md` — todas las reglas de negocio de inventario
2. `references/google-sheets.md` — estructura de hojas y cómo ubicar bloques
3. `SKILL.md` — flujo de conversación y reglas críticas para el skill de OpenClaw
