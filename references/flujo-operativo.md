# Flujo operativo real

## Objetivo

Procesar el cierre diario de Sambó a partir de un ticket de Neola o de los registros del día, con preview obligatorio, confirmación humana y escritura segura en Google Sheets.

## Entradas

- Foto del cierre, precierre o preventa de Neola.
- Hoja de recetas.
- Hojas de registros diarios:
  - `REGISTRO C1`
  - `REGISTRO C2`
  - `REGISTRO LINEA CALIENTE`
- Hoja `UBICACION DESCUENTO`.
- Historial local en `cierres-diarios/{dd-mm-yyyy}/{dd-mm-yyyy}.json` para recordar el estado del día.

## Salidas

- `VENTAS NEOLA`
- Inventario diario:
  - `C1`
  - `C2`
  - `LINEA CALIENTE`
- Historial local del cierre, con ventas finales, consumo, diferencias y metadata del ticket.

## Flujos soportados

### 1. Cierre completo desde ticket

Usar cuando llega una foto y todavía no se ha cerrado el día o se quiere rehacer el día completo.

Secuencia:

1. Parsear el ticket.
2. Calcular consumo teórico.
3. Mostrar preview.
4. Esperar confirmación.
5. Escribir `VENTAS NEOLA`.
6. Si `VENTAS NEOLA` quedó validado, escribir inventario diario.
7. Leer diferencias reales finales del día y guardarlas en historial.

### 2. Inventario solo desde registros

Usar cuando todavía no hay ticket final, pero sí existen los registros del día.

Secuencia:

1. Leer registros del día.
2. Mostrar preview de movimientos por ubicación.
3. Esperar confirmación.
4. Crear la entrada del día en `C1`, `C2` y `LINEA CALIENTE`.
5. Dejar `VENTAS` en cero hasta que llegue el ticket.

Este flujo prepara el día para luego usar "solo ventas".

### 3. Cargar ventas sobre un día ya creado

Usar cuando el día ya fue creado con registros y ahora llegó el ticket.

Secuencia:

1. Verificar que el día ya exista en `C1`, `C2` y `LINEA CALIENTE`.
2. Parsear ticket y calcular ventas finales.
3. Leer ventas actuales del día en `VENTAS NEOLA`.
4. Comparar ventas actuales vs nuevas ventas.
5. Mostrar preview incremental.
6. Esperar confirmación.
7. Actualizar `VENTAS NEOLA`.
8. Recalcular solo los insumos afectados en inventario.

Si el día no existe, bloquear y pedir crear primero la entrada desde registros.

### 4. Actualizar con ticket nuevo

Usar cuando ya se habían cargado ventas y llegó un ticket más completo del mismo día.

Secuencia:

1. Parsear ticket nuevo.
2. Calcular ventas finales y consumo final.
3. Leer ventas actuales del día.
4. Calcular solo el delta entre ventas actuales y ventas finales.
5. Mostrar preview incremental.
6. Esperar confirmación.
7. Actualizar `VENTAS NEOLA`.
8. Recalcular solo los insumos afectados.

Si el ticket nuevo no cambia nada, responder que no hay cambios y no escribir nada.

### 5. Ajuste manual de ventas

Usar cuando el usuario no manda foto y describe correcciones textuales como "faltó 1 nachos" o "réstale 2 lomos".

Secuencia:

1. Leer ventas actuales del día.
2. Convertir el texto a deltas firmados por plato.
3. Aplicar deltas sobre las ventas actuales.
4. Bloquear si alguna cantidad final queda negativa.
5. Calcular consumo final.
6. Mostrar preview incremental.
7. Esperar confirmación.
8. Actualizar `VENTAS NEOLA`.
9. Recalcular solo los insumos afectados.

### 6. Corrección puntual por insumo

Usar cuando el usuario quiere corregir diferencias de inventario ya escritas, sin tocar ventas.

Regla:

- no reescribir todo el día
- recalcular solo los insumos pedidos
- volver a leer diferencias reales al final

## Regla de fecha

- Si el usuario da una fecha exacta, usar esa fecha.
- Si no la da:
  - `19:00` a `23:59` -> usar hoy
  - `00:00` a `03:59` -> usar ayer
  - `04:00` a `18:59` -> usar hoy
- Siempre mostrar la fecha elegida en el preview.

## Parseo del ticket

1. Parsear la foto con Claude y extraer solo `VENTAS_ALIMENTOS`.
2. Ignorar postres.
3. Los items con `$0.00` sí se incluyen: son ventas reales que consumen insumos.
4. Agrupar ventas por plato.
5. Buscar recetas.
6. Calcular consumo teórico.

## Preview obligatorio

### En cierre completo

El preview debe mostrar:

1. fecha
2. si aplica, aviso de `precierre`
3. platos vendidos
4. debajo de cada plato, sus insumos o el motivo por el que no descuenta
5. total por insumo
6. alertas
7. pregunta simple de confirmación

### En flujos incrementales

El preview debe mostrar:

1. fecha
2. si el día ya estaba marcado como `precierre`
3. si el nuevo ticket también quedará marcado como `precierre`
4. solo los cambios detectados
5. ventas finales del día
6. insumos afectados que se recalcularán
7. alertas
8. pregunta simple de confirmación

Regla:

- el preview incremental debe enseñar solo lo que cambia
- no repetir como novedad ventas que ya estaban correctas

## `precierre`

Si el usuario indica que el ticket es un precierre:

- marcar el cierre como `precierre`
- recordarlo en el historial local del día
- mostrarlo en el preview
- si luego llega un ticket más completo o un ajuste manual, comparar contra lo ya cargado

Si el usuario no menciona `precierre`, no asumirlo.

## Matching de recetas

- Buscar match exacto sobre el nombre corto de Neola normalizado.
- No aplicar recetas por `startswith`, fragmentos o coincidencias parciales automáticas.
- Si varias recetas comparten el mismo nombre corto y tienen la misma firma inventariable, colapsarlas y tratarlas como una sola receta.
- Ejemplo: `HAMBURGUESA GOLDEN` y `HAMBURGUESA GOLDEN PLUS` no deben duplicar consumo si descuentan lo mismo.
- Si no hay match exacto, sugerir la receta más similar y bloquear hasta confirmación del usuario.
- Si el plato está en `PLATOS_IGNORADOS`, no descuenta inventario, pero sí debe aparecer en el preview con su motivo.

## Regla especial de `ENSALADA CAESAR`

- Si el ticket dice `ENSALADA CAESAR` sin proteína explícita, tomarla por defecto como pollo.
- En preview y consumo debe quedar explícito que se está usando `ENSALADA CÉSAR (POLLO)`.
- Solo usar otra proteína si el usuario la indicó explícitamente.

## Regla especial de `ROLLITOS RELLENO`

- `ROLLITOS RELLENO` es ambiguo y no debe descontarse directamente.
- Antes de cerrar, revisar `REGISTRO C2`:
  - `CREPE POLLO 2 unid`
  - `CREPE QUESO 2 unid`
- Si la suma de salidas coincide con la cantidad vendida, convertir la venta a:
  - `ROLLITOS RELLENO POLLO`
  - `ROLLITOS RELLENO QUESO`
- Si no existe una combinación exacta, bloquear y pedir aclaración manual.
- El override manual usa cantidades separadas para pollo y queso.

Excepción:

- si el usuario pidió solo consumo y no cierre, no bloquear la respuesta
- en ese caso, devolver el resto del consumo y dejar rollitos como pendiente
- solo consultar registros en modo consumo si el usuario lo pidió explícitamente

## Reglas de inventario

### Congeladores `C1` y `C2`

- `INICIO` = `CIERRE` del bloque anterior.
- `INGRESO` = ingreso registrado del día.
- `SALIDA` = salida registrada del día.
- `VENTAS`:
  - usar la salida registrada si existe
  - si no existe, usar el consumo teórico de recetas
- `CIERRE` = `INICIO + INGRESO - SALIDA`
- `DIF` = `SALIDA - VENTAS`

En la hoja diaria:

- `SALIDA` se deja escrita como fórmula visible
- `DIF` se deja escrita como fórmula visible

Esto evita perder ventas registradas manualmente, como `CREPE POLLO 2 unid`.

### `LINEA CALIENTE`

- `INGRESO` = ingreso registrado en línea + transferencias desde `C1/C2`.
- Una transferencia existe cuando un insumo salió de `C1/C2` y en `UBICACION DESCUENTO` su `DESCUENTO POR DEFECTO` es `LINEA`.
- `VENTAS`:
  - usar la salida registrada en línea si existe
  - si no existe, usar el consumo teórico que corresponde a línea

#### Cuando sí hay conteo

- `CIERRE` = conteo registrado.
- `SALIDA` = `INICIO + INGRESO - CIERRE`
- `DIF` = `SALIDA - VENTAS`

En la hoja diaria:

- `SALIDA` y `DIF` se escriben como fórmulas visibles

#### Cuando no hay conteo

- se usa para items no contados diariamente, como panes
- `SALIDA` = solo la salida extraordinaria registrada manualmente
- `CIERRE` = `INICIO + INGRESO - SALIDA - VENTAS`
- `DIF` = `0`

En la hoja diaria:

- `SALIDA` se escribe como fórmula visible a partir de `INICIO`, `INGRESO`, `CIERRE` y `VENTAS`
- `DIF` se deja en fórmula constante `0`

## Regla especial de panes

- Los panes en `LINEA CALIENTE` no se cuentan diariamente.
- Si el conteo está vacío, no convertirlo a `0`.
- Deben reflejar solo:
  - `INGRESO`
  - `SALIDA` extraordinaria, si existe
  - `VENTAS` teóricas del día
  - `CIERRE` calculado desde el cierre anterior

## Validación y seguridad

- Nunca crear una segunda entrada del mismo día.
- Si el día ya existe, reutilizar ese bloque.
- Validar `VENTAS NEOLA` después de escribir.
- Si `VENTAS NEOLA` no coincide con ventas y receta, reescribir y volver a validar.
- Si después de varios intentos `VENTAS NEOLA` sigue mal, detenerse antes de tocar inventario diario.
- En flujos incrementales, primero actualizar `VENTAS NEOLA` y después recalcular inventario.
- En flujos incrementales, si falla `VENTAS NEOLA`, no tocar inventario.
- En correcciones puntuales, no reescribir todo el inventario diario.
- Al cruzar recetas, registros y `UBICACION DESCUENTO`, intentar primero match exacto y luego un match normalizado para tolerar tildes, mayúsculas y sufijos como `2 unid`.

## Cuándo bloquear

Bloquear y pedir aclaración si ocurre cualquiera de estos casos:

- el plato no tiene receta confirmada
- `ROLLITOS RELLENO` sigue ambiguo
- el usuario quiere cargar o actualizar ventas, pero el día no existe en inventario
- el usuario quiere crear inventario desde registros y no hay registros del día
- un ajuste manual deja una cantidad final negativa
- el ajuste manual no deja claro si una venta se suma o se resta

## Resultado final esperado

- `VENTAS NEOLA` actualizado para el día correcto
- `C1`, `C2` y `LINEA CALIENTE` actualizados o corregidos según el flujo
- diferencias reales finales leídas desde el bloque recién trabajado
- historial local guardado con:
  - ventas finales
  - consumo agrupado
  - diferencias
  - metadata del ticket, incluyendo `ticket_tipo` y origen de la actualización

## Checklist sugerido para el agente

### Antes de escribir

1. Confirmar fecha objetivo.
2. Confirmar si el flujo correcto es cierre completo, solo registros, solo ventas, ticket nuevo o ajuste manual.
3. Confirmar si el día debe quedar marcado como `precierre`.
4. Buscar si la fecha ya existe.
5. Si existe, reutilizar bloque.
6. Si no existe, usar el siguiente bloque libre del mes.
7. Confirmar preview antes de cualquier escritura.
8. Leer registros del día si el flujo toca inventario.
9. Detectar panes sin conteo y tratarlos como no contados.

### Después de escribir

1. Confirmar que la fecha quedó en el bloque correcto.
2. Confirmar que no se creó un duplicado del día.
3. Verificar `VENTAS NEOLA` contra ventas y receta esperada.
4. Si el flujo fue incremental, confirmar que solo se recalcularon los insumos afectados.
5. Leer diferencias reales del bloque recién trabajado.
6. Reportar solo los descuadres restantes.
