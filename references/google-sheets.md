# Reglas de Google Sheets

## Objetivo

Documentar cómo el motor ubica, crea, reutiliza, valida y corrige bloques en Google Sheets para que no haya duplicados, truncamientos silenciosos ni escrituras parciales peligrosas.

## Spreadsheets involucrados

### Spreadsheet de registros

- `VENTAS NEOLA`
- `REGISTRO C1`
- `REGISTRO C2`
- `REGISTRO LINEA CALIENTE`
- `UBICACION DESCUENTO`

### Spreadsheet de inventario

- `C1`
- `C2`
- `LINEA CALIENTE`

## Regla general para ubicar un día

1. Determinar la fecha objetivo.
2. Identificar el mes de esa fecha.
3. Buscar la sección de ese mes en la hoja destino.
4. Escanear las fechas ya existentes dentro del bloque del mes.
5. Si la fecha exacta ya existe, reutilizar ese bloque.
6. Si no existe, usar el siguiente bloque vacío a la derecha del último día usado del mismo mes.

Regla crítica:

- nunca crear una segunda entrada del mismo día

## `VENTAS NEOLA`

### Estructura real del bloque

Cada día ocupa un bloque vertical:

1. una fila de cabecera con la fecha
2. una fila por plato vendido

Cada fila de plato usa estas columnas:

- `A`: plato
- `B`: cantidad vendida
- `C`: reservada, se deja vacía
- `D:E`: insumo 1 y cantidad
- `F:G`: insumo 2 y cantidad
- `H:I`: insumo 3 y cantidad
- `J:K`: insumo 4 y cantidad
- `L:M`: insumo 5 y cantidad
- `N:O`: insumo 6 y cantidad

Capacidad actual:

- máximo `6` insumos por plato
- si un plato necesita `7` o más, el motor falla con error explícito
- no se permite truncar insumos en silencio

### Escritura de un día

Cuando se escribe `VENTAS NEOLA`:

1. agrupar ventas por plato
2. agrupar consumo por plato
3. construir el bloque esperado del día
4. buscar si la fecha ya existe
5. si existe, reutilizar la misma fila de fecha
6. si no existe, escribir el bloque al final del bloque usado

### Robustez al escribir

Antes de escribir:

- si la hoja tiene menos de `15` columnas, agregar las columnas faltantes automáticamente
- si el bloque existente del día tiene menos filas que el bloque nuevo, insertar filas nuevas con formato heredado
- si el día es nuevo, copiar formato de la plantilla previa

Durante la escritura:

- limpiar primero el bloque a usar
- escribir después el bloque completo

Después de escribir:

- leer de vuelta el bloque recién escrito
- comparar contra ventas y receta esperadas
- si no coincide, reescribir automáticamente
- reintentar hasta `3` veces
- si sigue mal, fallar y no continuar con inventario

### Lectura de un día

Para leer ventas ya existentes del día:

1. leer hasta el ancho real de la hoja, o al menos hasta `A:O`
2. ubicar la fila exacta de la fecha
3. detectar cuántas filas pertenecen a ese bloque hasta la siguiente fecha o una fila vacía
4. reconstruir la estructura actual del día desde ese bloque

Esto permite comparar ventas actuales vs ventas nuevas sin depender de rangos fijos cortos.

## `RECETAS`

### Columna opcional para nombres de Neola

La hoja `RECETAS` ahora admite una columna opcional adicional al final:

- `NOMBRES NEOLA`

Objetivo:

- permitir que una misma receta responda a varios nombres equivalentes usados por Neola
- absorber problemas de estandarización como abreviaciones, recortes o variantes históricas

Ejemplo:

- receta canónica: `SANDWICH DE PEPE`
- `NOMBRES NEOLA`: `SANDWICH DE PEPE | SANDWICH DE PEPP`

Reglas:

- el motor sigue usando `PLATO` como nombre canónico interno de la receta
- `NOMBRES NEOLA` se usa solo para reconocer nombres alternativos al leer el ticket
- se pueden separar aliases con `|`
- también se aceptan `;` o saltos de línea
- si la columna no existe o está vacía, el comportamiento sigue igual que antes
- si el bloque de una receta ocupa varias filas, el alias escrito en la primera fila aplica a todas las filas del bloque

Regla crítica:

- si el ticket usa cualquiera de los nombres listados en `NOMBRES NEOLA`, el motor debe aplicar la misma receta sin pedir confirmación adicional

## Hojas de inventario diario

### Estructura del bloque

Cada día ocupa un bloque horizontal de `6` columnas:

- `INICIO`
- `INGRESO`
- `SALIDA`
- `DIF`
- `VENTAS`
- `CIERRE`

Reglas:

- si el día ya existe, reutilizar ese bloque
- si no existe, crear el siguiente bloque libre del mes
- `INICIO` siempre se basa en el `CIERRE` del bloque anterior
- `SALIDA` y `DIF` se escriben como fórmulas de Google Sheets, no como valores fijos

### Fórmulas visibles en el bloque

Objetivo:

- permitir ajustes manuales rápidos en la hoja sin tener que reescribir fórmulas a mano

Reglas por ubicación:

- `C1` y `C2`
  - `SALIDA = INICIO + INGRESO - CIERRE`
  - `DIF = SALIDA - VENTAS`
- `LINEA CALIENTE` con conteo
  - `SALIDA = INICIO + INGRESO - CIERRE`
  - `DIF = SALIDA - VENTAS`
- `LINEA CALIENTE` sin conteo
  - `SALIDA = INICIO + INGRESO - CIERRE - VENTAS`
  - `DIF = 0`

Regla crítica:

- las fórmulas se escriben con `USER_ENTERED` para que Sheets las interprete como fórmulas reales

### Escritura normal

En un cierre completo:

1. escribir `VENTAS NEOLA`
2. si `VENTAS NEOLA` quedó validado, escribir inventario diario completo
3. leer diferencias finales del día

### Escritura desde "solo registros"

Cuando se crea el día sin ticket final:

1. escribir el bloque del día en `C1`, `C2` y `LINEA CALIENTE`
2. dejar `SALIDA` y `DIF` como fórmulas visibles
3. en `C1` y `C2`, escribir `VENTAS` provisional desde `SALIDA`
4. en `LINEA CALIENTE`, dejar `VENTAS` pendiente del ticket

Regla crítica:

- esta `VENTAS` provisional no es fuente de verdad para futuras sumas
- cuando llegue el ticket, el motor debe recalcular desde `VENTAS NEOLA` + registros, no desde la columna `VENTAS` ya escrita

### Escritura incremental

En "solo ventas", "ticket nuevo" o "ajuste manual":

1. verificar que el día ya exista en `C1`, `C2` y `LINEA CALIENTE`
2. actualizar `VENTAS NEOLA`
3. recalcular solo los insumos afectados
4. actualizar únicamente las filas de esos insumos
5. releer diferencias finales del día

Regla crítica:

- si falla `VENTAS NEOLA`, no tocar inventario

## Registros diarios

### `REGISTRO C1` y `REGISTRO C2`

Usan bloques con:

- `INGRESO`
- `SALIDA`
- `MOTIVO`

### `REGISTRO LINEA CALIENTE`

Usa bloques con:

- `CONTEO`
- `INGRESO`
- `SALIDA`
- `MOTIVO`

Regla:

- si el conteo de línea está vacío, significa "no contado", no `0`

### Lectura robusta de registros

Los registros del día se leen usando:

- el total real de filas de la hoja
- el ancho real de la hoja (`ws.col_count`)
- coincidencia exacta de insumo cuando existe
- fallback a coincidencia normalizada cuando solo cambian tildes, mayúsculas o sufijos como `2 unid`

Esto evita perder fechas que queden más a la derecha del rango fijo y elimina el problema de truncar meses largos por un límite tipo `A:Q`.

## `UBICACION DESCUENTO`

La tabla se usa para decidir:

- ubicación de descuento por defecto de cada insumo
- qué salidas de `C1` o `C2` deben entrar como ingresos en `LINEA CALIENTE`
- y también admite fallback por nombre normalizado cuando el texto no coincide exactamente

## Verificaciones operativas

### Antes de escribir

- confirmar que la fecha apunta al mes correcto
- confirmar si la fecha ya existe o si se debe crear un bloque nuevo
- confirmar que no se generará un duplicado del día
- en flujos incrementales, confirmar que el día ya existe en inventario

### Después de escribir

- verificar que la fecha quedó en el bloque correcto
- verificar que no se duplicó el día
- verificar `VENTAS NEOLA` contra ventas y receta esperadas
- en inventario, releer las diferencias del bloque recién trabajado

## Criterios de seguridad

- no truncar insumos silenciosamente
- no asumir que la hoja ya tiene todas las columnas necesarias
- no asumir que el bloque existente del día tiene suficiente altura
- no usar rangos fijos que puedan dejar fuera días del mes
- no tocar inventario si `VENTAS NEOLA` no pudo quedar consistente
- en correcciones puntuales, actualizar solo las filas afectadas
