# Flujo operativo real

## Entradas
- Foto del cierre o preventa de Neola.
- Hoja de recetas.
- Hojas de registros diarios:
  - `REGISTRO C1`
  - `REGISTRO C2`
  - `REGISTRO LINEA CALIENTE`
- Hoja `UBICACION DESCUENTO`.

## Salidas
- `VENTAS NEOLA`
- Inventario diario:
  - `C1`
  - `C2`
  - `LINEA CALIENTE`

## Flujo del motor
1. Parsear la foto con Claude y extraer solo `VENTAS_ALIMENTOS`.
2. Ignorar postres e items con `$0.00`.
3. Buscar cada plato en recetas.
4. Calcular consumo teórico por insumo.
5. Mostrar preview y pedir confirmación.
6. Si se confirma:
   - escribir ventas en `VENTAS NEOLA`
   - leer registros del día
   - escribir inventario diario
   - reportar diferencias reales finales del bloque por `C1`, `C2` y `LINEA CALIENTE`.

## Matching de recetas
- Buscar match exacto sobre el nombre corto de Neola normalizado.
- Si varias recetas comparten ese nombre corto pero tienen exactamente la misma firma inventariable, colapsarlas y usar una sola receta.
- Ejemplo: `HAMBURGUESA GOLDEN` y `HAMBURGUESA GOLDEN PLUS` no deben duplicar consumo si descuentan lo mismo.
- No aplicar recetas por `startswith`, fragmentos o coincidencias parciales automáticas.
- Si no hay match exacto, buscar la receta más similar y pedir confirmación explícita al usuario.
- La confirmación debe servir para corregir gradualmente el nombre corto de Neola en la hoja de recetas.
- Si el plato está en `PLATOS_IGNORADOS`, no descontar inventario.

## Regla especial de `ENSALADA CAESAR`
- Si el ticket dice `ENSALADA CAESAR` sin proteína explícita, tomarla por defecto como pollo.
- En preview y consumo debe quedar explícito que se está usando `ENSALADA CÉSAR (POLLO)` por defecto.
- Solo usar camarón, falafel u otra proteína si el usuario la indicó explícitamente.

## Regla especial de `ROLLITOS RELLENO`
- `ROLLITOS RELLENO` es ambiguo y no debe descontarse directamente.
- Antes de calcular consumo, revisar `REGISTRO C2`:
  - `CREPE POLLO 2 unid`
  - `CREPE QUESO 2 unid`
- Si la suma de salidas coincide exactamente con la cantidad vendida de `ROLLITOS RELLENO`, convertir la venta a:
  - `ROLLITOS RELLENO POLLO`
  - `ROLLITOS RELLENO QUESO`
- Esa conversión debe respetar la cantidad registrada en `C2`.
- Si no existe una combinación exacta, bloquear el cierre y pedir al usuario cuántos fueron de pollo y cuántos de queso.
- El override manual se pasa como:
  - `--rollitos-pollo N`
  - `--rollitos-queso N`
- Excepción: si el usuario pidió solo consumo y no cierre, no bloquear la respuesta.
- En ese caso, no consultar registros.
- Devolver los demás insumos y dejar `ROLLITOS RELLENO` como pendiente de confirmar al final.
- Solo si el usuario pide explícitamente revisar registros en modo consumo, consultar `REGISTRO C2`.
- Para eso usar `--usar-registros-rollitos`.

## Reglas de inventario

### Congeladores `C1` y `C2`
- `INICIO` = `CIERRE` del bloque anterior.
- `INGRESO` = ingreso registrado del día.
- `SALIDA` = salida registrada del día.
- `VENTAS`:
  - usar la `salida` registrada si existe
  - si no existe, usar el consumo teórico de recetas.
- `CIERRE` = `INICIO + INGRESO - SALIDA`
- `DIF` = `SALIDA - VENTAS`

Esto evita perder ventas registradas manualmente, como `CREPE POLLO 2 unid`.

### `LINEA CALIENTE`
- `INGRESO` = ingreso registrado en línea + transferencias desde `C1/C2`.
- Una transferencia existe cuando un insumo salió de `C1/C2` y en `UBICACION DESCUENTO` su `DESCUENTO POR DEFECTO` es `LINEA`.
- `VENTAS` = consumo teórico que corresponde a línea.

#### Cuando sí hay conteo
- `CIERRE` = conteo registrado.
- `SALIDA` = `INICIO + INGRESO - CIERRE`
- `DIF` = `SALIDA - VENTAS`

#### Cuando no hay conteo
- Se usa para items no contados diariamente, como panes.
- `SALIDA` = solo la salida extraordinaria registrada manualmente.
- `CIERRE` = `INICIO + INGRESO - SALIDA - VENTAS`
- `DIF` = `0`

## Regla especial de panes
- Los panes en `LINEA CALIENTE` no se cuentan diariamente.
- Si el conteo está vacío, no convertirlo a `0`.
- Solo deben reflejar:
  - `INGRESO`
  - `SALIDA` extraordinaria, si existe
  - `VENTAS` teóricas del día
  - `CIERRE` calculado desde el cierre anterior.

## Validación final
- Después de escribir el día, revisar la columna `DIF` del bloque del día en:
  - `C1`
  - `C2`
  - `LINEA CALIENTE`
- Esta validación es sobre el bloque del día recién escrito, no sobre el día anterior.
- Antes de escribir todavía no existe `DIF` para ese día, así que no hay nada que validar ahí.
- El cierre correcto debe terminar con `0` diferencias o, si hay diferencias, reportarlas explícitamente.
- El output por defecto al finalizar debe listar esas diferencias reales agrupadas por hoja, con `INICIO`, `INGRESO`, `SALIDA`, `DIF`, `VENTAS` y `CIERRE`.

## Correcciones posteriores
- Si después del cierre el usuario corrige un registro o pide ajustar una diferencia puntual, no reescribir todo el bloque del inventario diario.
- Recalcular y actualizar solo las filas de los insumos afectados.
- Aplicar esos cambios sobre la fecha ya existente.
- Volver a leer las diferencias reales al final para confirmar qué sigue pendiente.
- Esa corrección puntual no debe volver a tocar `VENTAS NEOLA`, salvo que el problema del usuario sea específicamente de ventas.

## Checklist sugerido para el agente

### Pre-escritura
1. Confirmar fecha objetivo.
2. Confirmar mes correcto en la hoja destino.
3. Buscar si la fecha ya existe.
4. Si existe, reutilizar bloque.
5. Si no existe, usar el siguiente bloque libre del mes.
6. Confirmar preview de platos e insumos.
7. Leer registros del día.
8. Calcular transferencias a `LINEA`.
9. Detectar panes sin conteo y tratarlos como no contados.

### Post-escritura
1. Confirmar que la fecha quedó en el bloque correcto.
2. Confirmar que no se creó un duplicado del día.
3. Leer el bloque recién escrito en `VENTAS NEOLA` y comparar ventas e insumos contra la receta esperada.
4. Si `VENTAS NEOLA` no coincide con ventas y receta, reescribir el bloque exacto desde preview/recetas y volver a validar.
5. Solo si sigue mal después de reintentos, detenerse y no tocar inventario diario.
6. Verificar `INICIO` contra el `CIERRE` anterior.
7. Verificar ingresos transferidos a línea.
8. Verificar panes sin conteo.
9. Revisar `DIF` del bloque del día recién escrito.
