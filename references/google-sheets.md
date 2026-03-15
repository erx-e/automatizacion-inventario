# Reglas de Google Sheets

## Hojas relevantes
- `VENTAS NEOLA`
- `C1`
- `C2`
- `LINEA CALIENTE`
- `REGISTRO C1`
- `REGISTRO C2`
- `REGISTRO LINEA CALIENTE`
- `UBICACION DESCUENTO`

## Cómo ubicar el día a trabajar

### Regla general
1. Determinar el mes de la fecha objetivo.
2. Buscar ese mes en la hoja.
3. En la fila inmediatamente inferior al nombre del mes, escanear hacia la derecha las fechas ya existentes.
4. Si la fecha exacta ya existe, trabajar sobre ese bloque.
5. Si no existe, usar el siguiente bloque vacío a la derecha del último día usado del mismo mes.

Nunca crear una segunda entrada del mismo día.

## `VENTAS NEOLA`
- La fecha debe escribirse como una nueva cabecera del día.
- Si el día ya existe, reutilizar esa cabecera y sobrescribir los registros.
- Si es un día nuevo:
  - crear el bloque siguiente
  - combinar las celdas de la cabecera del día
  - copiar formato y bordes del bloque anterior
  - aplicar color y fecha
  - luego escribir los platos vendidos.

## Inventario diario
- Cada día ocupa un bloque de 6 columnas:
  - `INCIO`
  - `INGRESO`
  - `SALIDA`
  - `DIF`
  - `VENTAS`
  - `CIERRE`
- Si el día ya existe, reutilizar ese bloque.
- Si no existe, copiar formato del bloque anterior y escribir sobre el bloque nuevo.
- `INICIO` siempre se basa en el `CIERRE` del bloque anterior.

## Registros diarios
- `REGISTRO C1` y `REGISTRO C2` usan bloques:
  - `INGRESO`
  - `SALIDA`
  - `MOTIVO`
- `REGISTRO LINEA CALIENTE` usa bloques:
  - `CONTEO`
  - `INGRESO`
  - `SALIDA`
  - `MOTIVO`
- Si el conteo de línea está vacío, significa "no contado", no `0`.

## Criterio de seguridad
- Antes de escribir, confirmar que el bloque mensual encontrado corresponde al mes correcto.
- Después de escribir, verificar que no se haya creado un bloque duplicado para la misma fecha.
- Al rehacer un día anterior, buscar primero la fecha exacta y trabajar ahí mismo.
