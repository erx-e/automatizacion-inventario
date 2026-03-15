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

## Canal y tono

El usuario final es personal del Restaurante Sambó que interactúa a través de un bot de Telegram.

### Reglas de comunicación
- **Lenguaje natural y directo.** No usar jerga técnica, flags de CLI ni nombres de archivos. El usuario no sabe qué es `--rollitos-pollo` ni `VENTAS NEOLA`.
- **Mensajes cortos y claros.** Telegram es chat — no mandar párrafos largos. Separar en mensajes si es necesario.
- **Emojis moderados** para distinguir secciones, pero sin exceso.
- **No mencionar** nombres de hojas internas (`C1`, `C2`, `LINEA CALIENTE`) salvo en el reporte final de diferencias.
- **No pedir formatos técnicos.** En vez de "fecha YYYY-MM-DD", preguntar "¿Para qué día es este cierre?" y aceptar respuestas como "hoy", "ayer", "11 de marzo".
- **Confirmaciones simples.** "¿Todo bien? ¿Procedo?" — no instrucciones con comillas ni flechas.

## Opciones disponibles

El motor tiene tres caminos principales:

| Opción | Qué hace | Requiere foto | Requisito previo |
|--------|----------|---------------|------------------|
| **1A — Cierre completo** | Parsea ticket + preview + escribe ventas e inventario | Sí | Ninguno |
| **1B — Solo preview** | Parsea ticket + preview (sin escribir) | Sí | Ninguno |
| **1C — Solo cargar ventas** | Parsea ticket + carga ventas a entrada existente | Sí | Entrada creada con Opción 2 |
| **2 — Inventario desde registros** | Crea entradas del día usando solo registros | No | Registros del día deben existir |

El flujo típico de dos pasos es: primero Opción 2 (crear entradas desde registros), luego Opción 1C (cargar ventas del ticket).

---

## Opción 1: Cierre desde ticket (foto)

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

**Después de correr `--preparar`, SIEMPRE enviar el preview completo y formateado al usuario antes de pedir confirmación.** No asumir que el usuario ya lo vio en el output del comando. Reformatear el output del comando para que sea legible en Telegram:
1. Fecha del cierre
2. Cada plato con sus insumos (o motivo si no genera insumos)
3. Total por insumo
4. Alertas si las hay
5. Solo después de mostrar todo esto, preguntar si confirma con una pregunta simple ("¿Todo bien? ¿Procedo con el cierre?")

### Paso 2: esperar confirmación
- Si el usuario dice `si`, `dale`, `confirmar`, `ok` o equivalente: ejecutar el cierre.
- Si el usuario indica otra fecha (en cualquier formato): confirmar usando esa fecha.
- Si responde `no`, `cancelar` o equivalente: no escribir nada.

### Paso 3: confirmar cierre
```bash
python3 main.py /ruta/a/imagen.jpg --fecha 2026-03-11
```

Sin `--preparar`, el comando ejecuta el cierre completo.

### Paso 4: revisar diferencias finales
Después de escribir, el output por defecto muestra las diferencias reales finales del bloque del día agrupadas por C1, C2 y LINEA CALIENTE.

Reformatear el reporte para Telegram de forma clara:
- Si no hay diferencias: "✅ Todo cuadra, sin diferencias."
- Si hay diferencias: listar cada insumo con su descuadre de forma legible, sin la línea completa de INICIO/INGRESO/SALIDA/etc. salvo que el usuario lo pida.
- Preguntar si quiere corregir algo.

### Lógica de fecha automática:
Los cierres de caja normalmente se procesan de noche. La lógica es:
- **19:00 a 23:59** → fecha de **hoy** (cierre del día que acaba de terminar)
- **00:00 a 03:59** → fecha de **ayer** (cierre tardío, corresponde al día anterior)
- **04:00 a 18:59** → fecha de **hoy** (caso inusual/atrasado, asumimos el día actual)
- Si el usuario indica fecha, se usa esa sin importar la hora
- **Siempre mostrar la fecha sugerida al usuario y esperar confirmación antes de proceder**, incluso si se calculó automáticamente.

### Paso 1C: Solo cargar ventas (entrada ya existe)

Si el usuario ya creó la entrada del día con la Opción 2 y ahora quiere cargar las ventas del ticket:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --solo-ventas
python3 main.py /ruta/a/imagen.jpg --solo-ventas --fecha 2026-03-11
python3 main.py /ruta/a/imagen.jpg --solo-ventas --rollitos-pollo 1 --rollitos-queso 1
```

Si la entrada del día **no existe** en alguna hoja, el comando lo indica y pide crear la entrada primero (Opción 2).

El preview muestra los mismos platos e insumos que el cierre completo, pero aclara que **solo se actualizarán las VENTAS** (la entrada ya tiene INICIO, INGRESO y SALIDA de los registros).

Después de mostrar el preview, preguntar: "¿Cargo las ventas?"

Para confirmar:
```bash
python3 main.py /ruta/a/imagen.jpg --solo-ventas --confirmar
```

---

## Opción 2: Inventario solo desde registros (sin foto)

El usuario pide crear las entradas del día usando únicamente los registros de la hoja de registros. No se necesita foto de ticket.

```bash
cd {baseDir}
python3 main.py --solo-registros
python3 main.py --solo-registros --fecha 2026-03-11
```

**Requisito:** los registros del día deben existir. Si no hay movimientos registrados, el comando lo indica y pide que se completen primero.

El preview muestra:
- Fecha
- Movimientos por ubicación (C1, C2, LINEA CALIENTE): ingresos, salidas, conteos
- Aviso de que VENTAS quedará vacío hasta que se procese el ticket

Después de mostrar el preview, preguntar: "¿Todo correcto? ¿Procedo?"

Para confirmar:
```bash
python3 main.py --solo-registros --confirmar
python3 main.py --solo-registros --fecha 2026-03-11 --confirmar
```

Después de confirmar, el resultado muestra:
- Confirmación de que las entradas se crearon
- Aviso de que VENTAS está vacío y debe procesarse el ticket cuando esté listo
- Diferencias actuales (que serán altas porque no hay ventas aún)

**Flujo natural en Telegram:**
- Usuario: "Crea las entradas del día con los registros" o "Solo registros de hoy"
- Bot: muestra el preview con los movimientos del día
- Usuario: "Dale" / "Sí" / "Procede"
- Bot: confirma y muestra resultado

---

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
- preguntar al usuario de forma natural: "Los rollitos, ¿cuántos fueron de pollo y cuántos de queso?"
- no consultar registros de entradas o salidas para resolverlo
- cuando el usuario lo aclare (ej: "2 de pollo y 1 de queso"), volver a correr con `--rollitos-pollo N --rollitos-queso N`

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
- preguntar de forma natural, por ejemplo:
  "En las recetas tenemos 'TABLA QUESOS EMB' pero en el ticket sale 'TABLA DE QUESOS'. ¿Es el mismo plato? Si confirmas, actualizo el nombre."
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
