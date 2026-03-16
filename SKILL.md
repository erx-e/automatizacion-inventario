---
name: motor-inventario-sambo
description: Usa este skill cuando el usuario de Sambó quiera procesar una foto de cierre, precierre o preventa de Neola; crear el inventario diario solo con registros; cargar o actualizar ventas de un día ya existente; o ajustar ventas manualmente por texto. El flujo siempre hace preview, pide confirmación humana y luego actualiza Google Sheets.
---

# Motor de Inventario — Sambó

## Objetivo

Operar el cierre diario de Sambó desde Telegram. El usuario habla en lenguaje natural; tú eliges el flujo correcto y lo traduces al comando CLI adecuado.

## Lee solo lo necesario

- Este archivo: siempre.
- `references/flujo-operativo.md`: antes de tocar reglas de negocio, recetas, ventas, inventario o diferencias.
- `references/google-sheets.md`: solo si vas a cambiar cómo se ubica o escribe un bloque en Google Sheets.

## Usa este skill cuando

- el usuario manda una foto de ticket, cierre, precierre o preventa de Neola
- pide "solo registros", "crear entradas del día", "subir ventas", "actualizar cierre", "faltó vender", "súmale" o "réstale"
- quiere corregir ventas o inventario de un día ya escrito

## Contrato de conversación

- Habla como bot de Telegram: corto, claro y sin jerga técnica.
- No menciones flags, archivos ni nombres de hojas internas salvo que el usuario lo pida.
- No pidas formatos técnicos. Acepta fechas como "hoy", "ayer" o "11 de marzo" y conviértelas tú.
- Nunca escribas en Sheets sin preview y confirmación humana.
- Si el comando ya imprimió un resumen, igual reescríbelo de forma legible para el usuario.
- Si algo bloquea el flujo, explica solo el problema operativo y la siguiente acción.

## Árbol de decisión

1. Si hay foto y el usuario quiere revisar antes de escribir: usa la Opción 1A.
2. Si hay foto y el usuario quiere cierre completo del día: usa la Opción 1A y luego confirma.
3. Si hay foto y el inventario del día ya existe pero faltan cargar ventas: usa la Opción 1C.
4. Si hay foto, el día ya tiene ventas y llegó un ticket más completo: usa la Opción 1D.
5. Si no hay foto y el usuario quiere crear el día desde registros: usa la Opción 2.
6. Si no hay foto y el usuario describe cambios puntuales de ventas: usa la Opción 3.
7. Si el usuario solo quiere leer el ticket o ver consumo teórico: usa los comandos auxiliares.

## Cómo detectar `precierre`

Marca `precierre` si el usuario dice cosas como:

- "es un precierre"
- "faltan mesas por cerrar"
- "aún no han cerrado algunas cuentas"
- "todavía hay clientes abiertos"
- "este ticket está incompleto por ahora"

Si el usuario no lo menciona, no lo preguntes por defecto.

## Mapa de comandos

### Opción 1A. Foto -> preview o cierre completo

Usa este flujo cuando llega una foto y todavía no has escrito el día.

Preparación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --preparar
python3 main.py /ruta/a/imagen.jpg --preparar --fecha 2026-03-11
python3 main.py /ruta/a/imagen.jpg --preparar --precierre
python3 main.py /ruta/a/imagen.jpg --preparar --rollitos-pollo 1 --rollitos-queso 1
```

Confirmación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --fecha 2026-03-11
```

### Opción 1B. Solo leer ticket o ver consumo

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --solo-leer
python3 main.py /ruta/a/imagen.jpg --consumo
python3 main.py /ruta/a/imagen.jpg --consumo --usar-registros-rollitos
```

### Opción 1C. Cargar ventas sobre un día ya creado

Usa este flujo cuando el inventario del día ya fue creado con registros y ahora llegó el ticket.

Preparación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --solo-ventas
python3 main.py /ruta/a/imagen.jpg --solo-ventas --fecha 2026-03-11
python3 main.py /ruta/a/imagen.jpg --solo-ventas --precierre
python3 main.py /ruta/a/imagen.jpg --solo-ventas --rollitos-pollo 1 --rollitos-queso 1
```

Confirmación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --solo-ventas --confirmar
```

Si el día no existe todavía, no intentes forzarlo: primero usa la Opción 2.

### Opción 1D. Actualizar con ticket nuevo

Usa este flujo cuando ya había ventas cargadas y llega un ticket más completo del mismo día.

Preparación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --actualizar-ticket
python3 main.py /ruta/a/imagen.jpg --actualizar-ticket --fecha 2026-03-11
python3 main.py /ruta/a/imagen.jpg --actualizar-ticket --precierre
```

Confirmación:

```bash
cd {baseDir}
python3 main.py /ruta/a/imagen.jpg --actualizar-ticket --confirmar
```

Regla de uso:

- úsalo para comparar un ticket nuevo contra ventas ya existentes
- no lo uses para reescribir todo el día si solo cambió una parte
- si el día estaba marcado antes como `precierre`, recuérdalo en el preview

### Opción 2. Inventario solo desde registros

Usa este flujo cuando todavía no hay ticket final, pero sí existen los registros del día.

Preparación:

```bash
cd {baseDir}
python3 main.py --solo-registros
python3 main.py --solo-registros --fecha 2026-03-11
```

Confirmación:

```bash
cd {baseDir}
python3 main.py --solo-registros --confirmar
python3 main.py --solo-registros --fecha 2026-03-11 --confirmar
```

Flujo típico:

- primero Opción 2 para crear el día con registros
- después Opción 1C para cargar las ventas del ticket

### Opción 3. Ajuste manual de ventas

Usa este flujo cuando el usuario no manda foto y describe cambios de ventas por texto.

Preparación:

```bash
cd {baseDir}
python3 main.py --ajustar-ventas "NACHOS:+1,LOMO:-2" --fecha 2026-03-11
```

Confirmación:

```bash
cd {baseDir}
python3 main.py --ajustar-ventas "NACHOS:+1,LOMO:-2" --fecha 2026-03-11 --confirmar
```

Convierte lenguaje natural a deltas firmados:

- "faltó 1 nachos" -> `NACHOS:+1`
- "súmale 2 lomos" -> `LOMO:+2`
- "réstale 1 ensalada" -> `ENSALADA:-1`
- "se contó de más 1 hamburguesa" -> `HAMBURGUESA:-1`

Si una frase no deja claro si se suma o se resta, haz una sola pregunta corta antes de continuar.

## Preview obligatorio

### Para Opción 1A y Opción 1C

Siempre muestra, en este orden:

1. fecha del cierre
2. si aplica, aviso de `precierre`
3. cada plato vendido
4. debajo de cada plato, sus insumos o el motivo por el que no descuenta
5. total por insumo
6. alertas
7. una pregunta simple de confirmación

Si es Opción 1C, aclara que solo se actualizarán las ventas del día.

### Para Opción 1D y Opción 3

Siempre muestra, en este orden:

1. fecha del día
2. si el día ya estaba marcado como `precierre`
3. si el nuevo cambio también quedará marcado como `precierre`
4. solo el delta detectado o pedido
5. cómo quedarán las ventas finales del día
6. qué insumos se recalcularán
7. alertas
8. una pregunta simple de confirmación

Regla clave:

- el preview incremental debe enseñar solo lo que cambia
- no vuelvas a listar como novedad ventas que ya estaban correctas

### Para Opción 2

Siempre muestra:

1. fecha
2. movimientos del día por ubicación
3. aviso de que las ventas todavía quedarán vacías
4. pregunta simple de confirmación

## Confirmación

- Si el usuario confirma, ejecuta el comando de confirmación.
- Si cambia la fecha, vuelve a preparar o confirma con la nueva fecha según el flujo.
- Si cancela, no escribas nada.
- Después de escribir, siempre devuelve un resumen claro de lo que se hizo y de las diferencias finales.

## Reglas críticas

- Si el usuario dio una fecha exacta, usa esa fecha.
- Si la fecha ya existe, reutiliza el bloque del día. Nunca crees un duplicado del mismo día.
- Si el usuario indica `precierre`, guárdalo como recordatorio del estado del día.
- Si llega un ticket nuevo o un ajuste manual, compara contra las ventas actuales del día y modifica solo lo que cambió.
- Después de un ticket nuevo o un ajuste manual, recalcula solo los insumos afectados del inventario.
- Los platos ignorados por configuración sí deben aparecer en el preview con su motivo, pero no descuentan inventario.
- Los items con `$0.00` sí cuentan como ventas reales y deben consumir inventario.
- `ENSALADA CAESAR` sin proteína se toma como pollo y eso debe quedar explícito.
- El match de recetas es exacto sobre el nombre corto normalizado. No uses `startswith` ni coincidencias parciales automáticas.
- Si varias recetas comparten el mismo nombre corto y descuentan exactamente lo mismo, trátalas como una sola.
- Si no hay match exacto, propone la receta más similar y bloquea hasta que el usuario confirme.
- `ROLLITOS RELLENO` es ambiguo: resuélvelo por registros o por aclaración manual antes de cerrar.
- Los panes en `LINEA CALIENTE` no se cuentan diariamente; conteo vacío no significa cero.
- Las transferencias desde `C1` o `C2` hacia línea deben entrar como ingreso en `LINEA CALIENTE`.

## Cuándo bloquear y pedir aclaración

Bloquea el flujo y pide una aclaración corta si ocurre cualquiera de estos casos:

- el plato no tiene receta confirmada
- `ROLLITOS RELLENO` sigue ambiguo
- el usuario quiere Opción 1C, Opción 1D u Opción 3 pero el día todavía no existe
- el usuario quiere Opción 2 y no hay registros del día
- el ajuste manual no deja claro si una venta se suma o se resta

## Después de escribir

- Si no hay diferencias, responde: `✅ Todo cuadra, sin diferencias.`
- Si hay diferencias, lista solo los insumos con su descuadre, salvo que el usuario pida más detalle.
- Si falla la validación de `VENTAS NEOLA`, corrige ese bloque y vuelve a validar antes de tocar inventario.
- Si el usuario pide una corrección puntual posterior, no reescribas todo el día: toca solo ventas o insumos afectados.
- Si llega un ticket más completo después de un `precierre`, muestra solo la diferencia contra lo ya cargado y confirma antes de aplicar.

## Archivos importantes

- `main.py`: CLI y selección de flujo.
- `motor.py`: previews, confirmaciones y lógica incremental.
- `parser_neola.py`: lectura del ticket.
- `recetas.py`: match plato-receta y consumo teórico.
- `sheets_connector.py`: lectura y escritura en Google Sheets.
- `config.py`: credenciales, constantes y platos ignorados.
