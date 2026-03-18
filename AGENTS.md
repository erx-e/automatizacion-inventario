# Motor de Inventario Sambó

Sistema CLI para cierres diarios de Sambó. Lee tickets de Neola, calcula consumo por receta, escribe ventas en Google Sheets y actualiza inventario en `C1`, `C2` y `LINEA CALIENTE`.

## Quick reference

```bash
# Tests
python3 -m unittest discover -s tests -v

# Cierre con foto
python3 main.py <imagen> --preparar
python3 main.py <imagen> --preparar --precierre
python3 main.py <imagen> --solo-ventas --fecha 2026-03-14
python3 main.py <imagen> --actualizar-ticket --fecha 2026-03-14
python3 main.py <imagen> --solo-leer
python3 main.py <imagen> --consumo
python3 main.py <imagen> --fecha 2026-03-14

# Inventario desde registros
python3 main.py --solo-registros --fecha 2026-03-14
python3 main.py --solo-registros --fecha 2026-03-14 --confirmar

# Ajuste manual de ventas
python3 main.py --ajustar-ventas "NACHOS:+1,LOMO:-2" --fecha 2026-03-14
python3 main.py --ajustar-ventas "NACHOS:+1,LOMO:-2" --fecha 2026-03-14 --confirmar

# Releer correcciones manuales
python3 main.py --registro-corregido "LINEA|POLLO 160 gr CECAR|conteo=2" --fecha 2026-03-14
python3 main.py --registro-corregido "C1|FILETE DE POLLO 200 gr|salida=4" --fecha 2026-03-14
python3 main.py --registro-corregido "LINEA|PAN DE HOT DOG" --fecha 2026-03-14
```

## Stack

- Python 3
- `anthropic` para lectura de tickets
- `gspread` + `google-auth` para Google Sheets
- `.env` para configuración local
- tests con `unittest`, sin `pytest`

## Módulos

```text
main.py             CLI y parseo de argumentos
motor.py            previews, confirmaciones, flujos incrementales, reportes
parser_neola.py     foto -> ventas detectadas
recetas.py          match plato-receta, aliases de Neola, consumo teórico
sheets_connector.py lectura/escritura de Google Sheets
config.py           variables de entorno y constantes
references/         reglas operativas y estructura de hojas
SKILL.md            skill de OpenClaw para Telegram/WhatsApp
```

## Flujo general

```text
Ticket / ajuste manual
  -> parser_neola.py / texto
  -> recetas.py
  -> motor.py (preview + confirmación)
  -> sheets_connector.py
  -> VENTAS NEOLA + inventario diario + diferencias finales
```

## Hojas relevantes

- `REGISTRO C1`
- `REGISTRO C2`
- `REGISTRO LINEA CALIENTE`
- `MOTIVOS ESPECIALES`
- `VENTAS NEOLA`
- `UBICACION DESCUENTO`
- `RECETAS`

## Reglas operativas críticas

- Fecha automática:
  - `19:00` a `23:59` -> hoy
  - `00:00` a `03:59` -> ayer
  - `04:00` a `18:59` -> hoy
- `ROLLITOS RELLENO` se resuelve por registros de `C2` o por override manual.
- `ENSALADA CAESAR` sin proteína se toma como pollo.
- Los items con `$0.00` sí consumen inventario.
- El match de recetas acepta aliases definidos en `RECETAS -> NOMBRES NEOLA`.
- Si un alias existe, se canoniza al nombre principal; no debe quedar como plato separado.
- `precierre` permite cargar un ticket incompleto y luego aplicar solo el delta faltante.
- OpenClaw no debe editar registros ni `MOTIVOS ESPECIALES`; solo releerlos.

## Registros y movimientos especiales

### Registros principales

- `REGISTRO C1` y `REGISTRO C2`: `INGRESO`, `SALIDA`
- `REGISTRO LINEA CALIENTE`: `CONTEO`, `INGRESO`, `SALIDA`

### `MOTIVOS ESPECIALES`

Columnas:
- `FECHA`
- `UBICACION`
- `INSUMO`
- `TIPO`
- `MOTIVO`
- `CANTIDAD`
- `OBSERVACION`

Reglas:
- `TIPO` solo puede ser `INGRESO` o `SALIDA`
- los movimientos especiales se filtran por la fecha actual del proceso
- la suma de ingresos especiales nunca puede superar el `INGRESO` total del registro principal
- la suma de salidas especiales nunca puede superar la salida total del insumo
- `INGRESO` y `SALIDA` del registro principal ya son totales; los movimientos especiales son subconjuntos de esos totales

## Inventario

### Congeladores `C1` / `C2`

- En `solo-registros`, `VENTAS` provisional = `SALIDA` registrada.
- En cierre final:
  - si `UBICACION DESCUENTO` manda a `LINEA`, la salida del congelador puede representar transferencia a línea
  - si el insumo se descuenta en el mismo congelador, `VENTAS` final usa ventas teóricas + salida especial
- `SALIDA` y `DIF` se escriben como fórmulas visibles.

### `LINEA CALIENTE`

- Si hay conteo:
  - `CIERRE` = conteo
  - `SALIDA = INICIO + INGRESO - CIERRE`
  - `DIF` compara salida operativa vs ventas
- Si no hay conteo:
  - se usa para panes y otros no contados
  - `SALIDA` y `DIF` siguen como fórmulas visibles estándar
- Las transferencias desde `C1/C2` a línea se determinan con `UBICACION DESCUENTO`.
- Una salida especial de congelador reduce el ingreso que pasa a línea.

## Correcciones manuales

### Ventas

- Se ajustan con `--ajustar-ventas`.
- El motor compara contra ventas actuales del día y recalcula solo insumos afectados.

### Registros

- Ruta read-only: `--registro-corregido`
- Si se corrigió el registro principal:
  - formato interno: `UBICACION|INSUMO|campo=valor`
- Si se corrigieron movimientos especiales:
  - eliminación completa: `UBICACION|INSUMO|sin-especiales`
  - cambio explícito: `UBICACION|INSUMO|campo=valor|ingreso-especial=motivo:cantidad`
  - también admite `salida-especial=motivo:cantidad`
  - varios cambios: separar con `;`
- El motor relee el día, relee `MOTIVOS ESPECIALES`, resincroniza inventario y reporta diferencias.

## Google Sheets y performance

- Hay retry con backoff para `429`, `RESOURCE_EXHAUSTED`, `502/503/504`.
- Hay caché por ejecución, no persistente entre corridas.
- Después de cualquier escritura, el caché se invalida.
- `MOTIVOS ESPECIALES` ya no se lee completa:
  - primero se revisa la columna `FECHA`
  - luego solo se leen las filas del día actual

## Pruebas

- Ejecutar siempre:

```bash
python3 -m unittest discover -s tests -v
```

- Los tests mockean módulos completos en `motor.py`.
- `test_sheets_connector.py` cubre layout de registros, `MOTIVOS ESPECIALES`, fórmulas y helpers de inventario.

## Archivos que conviene leer antes de tocar lógica

1. `references/flujo-operativo.md`
2. `references/google-sheets.md`
3. `SKILL.md`

## Notas para futuros cambios

- No reintroducir columnas `MOTIVO` o `CANTIDAD` en los registros principales.
- No usar la columna `VENTAS` del inventario como fuente de verdad para recalcular tickets futuros.
- Si cambias la semántica de `MOTIVOS ESPECIALES`, actualiza al mismo tiempo:
  - `sheets_connector.py`
  - `motor.py`
  - `SKILL.md`
  - `references/flujo-operativo.md`
  - `references/google-sheets.md`
  - tests
