---
name: motor-inventario-sambo
description: Procesa cierres diarios de Sambó a partir de una foto del cierre o preventa de Neola: hace preview de platos e insumos, confirma fecha, escribe ventas en VENTAS NEOLA y actualiza el inventario diario de C1, C2 y LINEA CALIENTE usando recetas, registros, transferencias a línea y reglas especiales de panes, reutilizando el bloque del día si ya existe. Al final reporta las diferencias reales del bloque escrito y permite correcciones puntuales por insumo sin reescribir todo el inventario diario.
---

# Motor de Inventario — Restaurante Sambó

Usa este skill cuando el usuario quiera procesar una foto de cierre/preventa de Neola, revisar platos vendidos, calcular insumos consumidos, aplicar el cierre a Google Sheets, rehacer un día ya existente o corregir la lógica del motor.

## Qué leer
- Este archivo para el flujo normal del skill.
- `references/flujo-operativo.md` antes de tocar reglas de negocio, inventario o descuadres.
- `references/google-sheets.md` antes de modificar cómo se ubica o escribe una nueva entrada en Google Sheets.

## Flujo de conversación

### Paso 1: preparar preview
Siempre empezar con preview. No escribir en Sheets todavía.

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --preparar
python3 main.py /ruta/a/imagen.jpg --preparar --fecha 2026-03-11
python3 main.py /ruta/a/imagen.jpg --preparar --fecha 2026-03-11 --rollitos-pollo 1 --rollitos-queso 1
```

El preview debe mostrar:
- fecha sugerida o fecha pedida por el usuario
- platos vendidos
- debajo de cada plato, sus insumos consumidos con sangría
- si un plato no genera insumos: `Motivo: ...`
- al final, total simple por insumo

**Después de correr `--preparar`, SIEMPRE enviar el preview completo y formateado al usuario antes de pedir confirmación.** No asumir que el usuario ya lo vio en el output del comando. Mostrar:
1. Fecha del cierre
2. Cada plato con sus insumos (o motivo si no genera insumos)
3. Total por insumo
4. Alertas si las hay
5. Solo después de mostrar todo esto, preguntar si confirma

### Paso 2: esperar confirmación
- Si el usuario dice `si`, `confirmar` o equivalente: ejecutar el cierre.
- Si responde `fecha YYYY-MM-DD`: confirmar usando esa fecha.
- Si responde `no` o `cancelar`: no escribir nada.

### Paso 3: confirmar cierre
```bash
python3 main.py /ruta/a/imagen.jpg --fecha 2026-03-11
```

Sin `--preparar`, el comando ejecuta el cierre completo.

### Paso 4: revisar diferencias finales
Después de escribir, el output por defecto debe mostrar las diferencias reales finales del bloque del día agrupadas por:
- `C1`
- `C2`
- `LINEA CALIENTE`

Cada diferencia debe salir con:
- `INICIO`
- `INGRESO`
- `SALIDA`
- `DIF`
- `VENTAS`
- `CIERRE`

Ese reporte final es el que el usuario usa para decidir si hay errores de registro que corregir.

### Lógica de fecha automática:
Los cierres de caja siempre corresponden al día que inició a las 19:00. Por eso:
- **Antes de las 19:00** → fecha de **ayer** (el cierre pertenece al día anterior que arrancó a las 19:00)
- **A partir de las 19:00** → fecha de **hoy** (el cierre pertenece al día que acaba de empezar)
- Si el usuario indica fecha, se usa esa sin importar la hora
- **Siempre mostrar la fecha sugerida al usuario y esperar confirmación antes de proceder**, incluso si se calculó automáticamente.

### Otros comandos:
```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --solo-leer

python3 main.py /ruta/a/imagen.jpg --consumo
python3 main.py /ruta/a/imagen.jpg --consumo --usar-registros-rollitos
python3 main.py /ruta/a/imagen.jpg --fecha 2026-03-13 --corregir-insumos "POLLO 200 gr,CERDO TABLA DE CARNE BBQ 180 gr"
```

Si el usuario pide solo consumo y hay `ROLLITOS RELLENO` ambiguo:
- mostrar el resto de insumos normalmente
- dejar `ROLLITOS RELLENO` como pendiente de confirmar
- al final pedir cuántos fueron de pollo y cuántos de queso
- no consultar registros de entradas o salidas para resolverlo
- cuando el usuario lo aclare, volver a correr con `--rollitos-pollo N --rollitos-queso N`

Solo si el usuario pide explícitamente revisar registros para resolver rollitos:
- usar `--usar-registros-rollitos`
- consultar `REGISTRO C2`
- si cuadra, convertirlos a pollo/queso y agregarlos al consumo
- si no cuadra, dejar la advertencia correspondiente

Si aparece `ENSALADA CAESAR` sin proteína explícita:
- tomarla por defecto como `ENSALADA CÉSAR (POLLO)`
- reflejar ese supuesto en el preview y en el consumo
- solo usar otra proteína si el usuario la indica explícitamente

Si el plato del ticket no tiene match exacto en el nombre corto de recetas:
- no aplicar una receta por prefijo o fragmento
- buscar la receta más similar y mostrársela al usuario como confirmación
- usar un mensaje del estilo:
  `en recetas existe 'TABLA QUESOS EMB', pero en el ticket sale 'TABLA DE QUESOS'. ¿Cambio el nombre de la receta a 'TABLA DE QUESOS' y aplico esa receta?`
- bloquear el cierre hasta que esa receta quede confirmada

## Reglas críticas
- Si el usuario dio una fecha exacta, usar esa fecha.
- Si la fecha ya existe en `VENTAS NEOLA` o en el inventario diario, reutilizar ese bloque y sobrescribirlo. No crear una segunda entrada del mismo día.
- Si la fecha no existe, crear la siguiente entrada dentro del mismo bloque mensual.
- Los platos ignorados por configuración no descuentan inventario, pero sí deben aparecer en el preview con `Motivo: Plato ignorado por configuración`.
- `ROLLITOS RELLENO` no se descuenta como ambiguo. Primero hay que resolver si fue pollo o queso.
- `ENSALADA CAESAR` sin proteína se toma por defecto como pollo, y eso debe quedar explícito en el preview.
- El match de recetas debe ser exacto sobre el nombre corto normalizado; no usar `startswith` ni fragmentos para aplicar recetas automáticamente.
- Si varias recetas comparten el mismo nombre corto y su firma inventariable es idéntica, tratarlas como una sola receta.
- Ejemplo: `HAMBURGUESA GOLDEN` y `HAMBURGUESA GOLDEN PLUS` descuentan la misma receta y no deben duplicar insumos.
- Si no hay match exacto, proponer la receta más similar y pedir confirmación antes de seguir.
- Para resolver `ROLLITOS RELLENO`, revisar `REGISTRO C2`:
  - `CREPE POLLO 2 unid`
  - `CREPE QUESO 2 unid`
- Si la suma de salidas coincide con la venta de rollitos, convertir la venta a `ROLLITOS RELLENO POLLO` y/o `ROLLITOS RELLENO QUESO`.
- Si no coincide o no hay registro suficiente, bloquear el cierre y pedir aclaración al usuario.
- Si el usuario aclara manualmente, usar `--rollitos-pollo N --rollitos-queso N`.
- Los panes en `LINEA CALIENTE` no se cuentan diariamente. Si no hay conteo, su cierre se calcula desde el cierre anterior, ingresos, ventas y salidas extraordinarias.
- Las salidas de `C1` o `C2` cuyo `DESCUENTO POR DEFECTO` sea `LINEA` deben entrar como `INGRESO` en `LINEA CALIENTE`.
- Al final del cierre, revisar el bloque del día trabajado y confirmar que `DIF` quede en `0` en `C1`, `C2` y `LINEA CALIENTE`, salvo que el usuario quiera conservar una diferencia real.

## Checklist operativo

### Antes de escribir
- Confirmar la fecha objetivo.
- Confirmar que el mes encontrado en la hoja corresponde a esa fecha.
- Buscar si la fecha exacta ya existe.
- Si ya existe, reutilizar ese bloque.
- Si no existe, usar el siguiente bloque vacío del mismo mes.
- Confirmar que no se va a crear una segunda entrada del mismo día.
- Revisar el preview de platos e insumos antes de confirmar.
- Revisar registros del día en `C1`, `C2` y `LINEA CALIENTE`.
- Si hay `ROLLITOS RELLENO`, revisar primero `CREPE POLLO 2 unid` y `CREPE QUESO 2 unid` en `REGISTRO C2`.
- Revisar transferencias a línea según `UBICACION DESCUENTO`.
- Para panes en línea, confirmar si el conteo está vacío. Si está vacío, tratarlos como no contados.

### Después de escribir
- Confirmar que `VENTAS NEOLA` quedó en la fecha correcta.
- Leer de vuelta el bloque escrito en `VENTAS NEOLA` y verificar que cada plato tenga la cantidad vendida correcta y los insumos correctos según receta.
- Si la verificación de `VENTAS NEOLA` falla, reescribir automáticamente el bloque exacto desde preview/recetas y volver a verificar.
- Solo si después de varios intentos el bloque sigue mal, detenerse antes de tocar inventario diario.
- Confirmar que `C1`, `C2` y `LINEA CALIENTE` quedaron en la fecha correcta.
- Confirmar que no se duplicó el día.
- Verificar que `INICIO` del día sale del `CIERRE` anterior.
- Verificar que ingresos transferidos a línea sí se registraron en `INGRESO`.
- Verificar que panes sin conteo no quedaron con `conteo = 0`.
- Verificar que `DIF` del bloque recién trabajado quede en `0`, o reportar explícitamente cualquier diferencia real restante.

### Correcciones posteriores
- Si el usuario pide corregir diferencias o errores de registro después del cierre, no reescribir todo el inventario diario.
- Actualizar solo los insumos pedidos en las hojas afectadas.
- No volver a tocar `VENTAS NEOLA` salvo que el usuario pida corregir ventas.
- Después de una corrección puntual, volver a leer las diferencias reales del bloque y reportarlas otra vez por `C1`, `C2` y `LINEA CALIENTE`.

## Archivos importantes
- `main.py`: CLI y flujo interactivo.
- `motor.py`: preview, confirmación, fecha y resumen.
- `parser_neola.py`: extracción de ventas desde la foto.
- `recetas.py`: matching plato-receta y consumo teórico.
- `sheets_connector.py`: lectura/escritura de Google Sheets.
- `config.py`: credenciales, hojas, platos ignorados y modelos.
