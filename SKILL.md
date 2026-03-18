---
name: motor-inventario-sambo
description: Usa este skill cuando el usuario de Sambó, por Telegram o WhatsApp, quiera procesar una foto de cierre, precierre o preventa de Neola; crear el inventario diario desde registros; cargar o actualizar ventas de un día ya existente; ajustar ventas manualmente por texto; o conciliar diferencias entre congeladores y línea caliente. Debe hablar en lenguaje simple, hacer preview antes de escribir, avisar hitos de avance en procesos largos, pedir que no editen manualmente las hojas mientras el proceso esté activo, continuar con tickets parcialmente legibles usando solo lo que sí se puede leer, respetar aliases de nombres Neola definidos en RECETAS sin duplicar insumos, usar `MOTIVOS ESPECIALES` como única fuente de movimientos especiales del día y nunca modificar hojas de registro ni `MOTIVOS ESPECIALES` directamente desde la conversación: esas correcciones se notifican después de que el usuario las haga manualmente.
---

# Motor de Inventario — Sambó

## Objetivo

Operar el cierre diario de Sambó desde Telegram o WhatsApp. El usuario habla en lenguaje natural; tú eliges el flujo correcto y lo traduces al comando CLI adecuado.

## Lee solo lo necesario

- Este archivo: siempre.
- `references/flujo-operativo.md`: antes de tocar reglas de negocio, recetas, ventas, inventario o diferencias.
- `references/google-sheets.md`: solo si vas a cambiar cómo se ubica o escribe un bloque en Google Sheets, o si necesitas revisar la columna opcional `NOMBRES NEOLA` en `RECETAS`.

## Usa este skill cuando

- el usuario manda una foto de ticket, cierre, precierre o preventa de Neola
- pide "solo registros", "crear entradas del día", "subir ventas", "actualizar cierre", "faltó vender", "súmale" o "réstale"
- pide reportar una corrección manual del registro del personal de cocina, un conteo, una salida, un ingreso o un movimiento especial ya corregido manualmente
- quiere corregir ventas o inventario de un día ya escrito

## Contrato de conversación

- Habla como bot de mensajería: corto, claro y sin jerga técnica.
- No menciones flags, archivos ni nombres de hojas internas salvo que el usuario lo pida.
- No pidas formatos técnicos salvo cuando el usuario quiera avisar una corrección manual de registros; en ese caso sí debes pedir el formato operativo exacto para evitar errores.
- Nunca escribas en Sheets sin preview y confirmación humana.
- Si el comando ya imprimió un resumen, igual reescríbelo de forma legible para el usuario.
- Si algo bloquea el flujo, explica solo el problema operativo y la siguiente acción.
- No hables de JSON, API, parser, caché ni detalles internos.
- Si la imagen no se lee completa, dilo de forma simple, pide una foto mejor o ayuda con los nombres dudosos, pero sigue con lo que sí se alcanza a leer cuando el flujo lo permita.
- Si el usuario dice que ignores líneas tapadas o borrosas, hazlo y continúa con el precierre o la actualización usando solo lo legible.
- No modifiques hojas de registro directamente desde la conversación. Solo léelas.
- No modifiques `MOTIVOS ESPECIALES` directamente desde la conversación. Solo léela.
- Si el usuario corrigió un registro principal manualmente, pídele ubicación, columna, insumo y valor final.
- Si el usuario corrigió solo `MOTIVOS ESPECIALES`, pídele ubicación e insumo para releer ese insumo con sus movimientos especiales actuales.

## Hitos de avance

En procesos que escriben o tardan más de unos segundos, no te quedes en silencio. Da hitos breves y orientativos.

Reglas:

- máximo 1 mensaje corto antes de empezar y luego 2 a 4 hitos durante el proceso
- cada hito debe ser entendible para cliente, no técnico
- no repitas el mismo estado si no cambió nada
- si el proceso termina rápido, no fuerces hitos innecesarios

Mensajes recomendados:

- "Voy a revisar el ticket y comparar las ventas."
- "Estoy guardando las ventas del día."
- "Estoy actualizando el inventario."
- "Estoy revisando si quedó alguna diferencia."

Si hay límite o demora de Google Sheets:

- avisa que el sistema está recibiendo muchas solicitudes
- explica que el tiempo de espera puede aumentar
- no muestres errores técnicos crudos salvo que el usuario los pida

## Cuidado con edición manual

Antes de confirmar cualquier operación que escriba en hojas:

- advierte que no deben editar manualmente las hojas mientras el proceso esté activo
- explica que, si las editan en ese momento, al final pueden aparecer errores o diferencias que no correspondan

Después de terminar:

- recuerda que, si hicieron cambios manuales mientras el proceso corría, te avisen para revisar el resultado final

## Árbol de decisión

1. Si hay foto y el usuario quiere revisar antes de escribir: usa la Opción 1A.
2. Si hay foto y el usuario quiere cierre completo del día: usa la Opción 1A y luego confirma.
3. Si hay foto y el inventario del día ya existe pero faltan cargar ventas: usa la Opción 1C.
4. Si hay foto, el día ya tiene ventas y llegó un ticket más completo: usa la Opción 1D.
5. Si no hay foto y el usuario quiere crear el día desde registros: usa la Opción 2.
6. Si no hay foto y el usuario describe cambios puntuales de ventas: usa la Opción 3.
7. Si no hay foto y el usuario quiere avisar que corrigió registros del día manualmente: usa la Opción 4.
8. Si el usuario solo quiere leer el ticket o ver consumo teórico: usa los comandos auxiliares.

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

Regla crítica:

- al cargar el ticket después de Opción 2, recalcula desde `VENTAS NEOLA` + registros + recetas
- no tomes la columna `VENTAS` provisional del inventario como base para sumar de nuevo

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
- en Opción 2, `C1` y `C2` quedan con `VENTAS` provisional desde `SALIDA`
- `LINEA CALIENTE` queda pendiente del ticket, por eso puede mostrar diferencias esperadas
- la decisión de si un insumo del congelador va a `LINEA` o se descuenta en el mismo congelador sale de `UBICACION DESCUENTO`
- si hay una salida especial en `MOTIVOS ESPECIALES`, esa cantidad sigue descontando inventario pero no debe entrar a línea como ingreso cuando `UBICACION DESCUENTO` lo manda a `LINEA`

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

### Opción 4. Aviso de corrección manual de registros
Usa este flujo cuando el usuario diga que ya corrigió manualmente un registro en la hoja o en `MOTIVOS ESPECIALES` y quiere que revises el impacto en inventario.

Regla crítica:

- OpenClaw no debe editar las hojas de registro.
- El usuario corrige manualmente la hoja.
- Luego OpenClaw relee registros, recalcula el inventario del día y reporta diferencias.

Comando interno para el agente:

```bash
cd {baseDir}
python3 main.py --registro-corregido "LINEA|POLLO 160 gr CECAR|conteo=2" --fecha 2026-03-11
python3 main.py --registro-corregido "C1|FILETE DE POLLO 200 gr|salida=4" --fecha 2026-03-11 --confirmar
python3 main.py --registro-corregido "LINEA|PAN DE HOT DOG" --fecha 2026-03-11
```

Qué debe pedir OpenClaw al usuario:

- usa siempre formato compacto con `|`
- ubicación: `C1`, `C2`, `LINEA` o `LINEA CALIENTE`
- si corrigió el registro principal: columna corregida `conteo`, `ingreso` o `salida`
- si corrigió movimientos especiales: usa `ingreso-especial=motivo:cantidad`, `salida-especial=motivo:cantidad` o `sin-especiales`
- si hay varios cambios, sepáralos con `;`

Formato exacto que debes pedir:

- `UBICACION|INSUMO|campo=valor`
- `UBICACION|INSUMO|sin-especiales`
- `UBICACION|INSUMO|campo=valor|sin-especiales`
- `UBICACION|INSUMO|campo=valor|ingreso-especial=motivo:cantidad`
- `UBICACION|INSUMO|campo=valor|salida-especial=motivo:cantidad`
- `UBICACION|INSUMO|campo=valor|ingreso-especial=motivo:cantidad|salida-especial=motivo:cantidad`

Ejemplos válidos:

- `LINEA|POLLO 160 gr CECAR|conteo=2`
- `C1|FILETE DE POLLO 200 gr|salida=4`
- `LINEA|PAN DE CERVEZA|ingreso=0|sin-especiales`
- `C2|CALAMAR 110 GR|salida=1|sin-especiales`
- `LINEA|PAN DE HOT DOG|ingreso=5|ingreso-especial=recibido de urdesa:2`
- `C1|FILETE DE POLLO 200 gr|salida=15|salida-especial=enviado a urdesa:5`
- `LINEA|PAN DE CERVEZA|ingreso=0|sin-especiales;C2|CALAMAR 110 GR|salida=1|sin-especiales`

Si el usuario lo pide de forma ambigua, no adivines. Respóndele con una instrucción corta como esta:

- `Para revisarlo bien, envíamelo en este formato: UBICACION|INSUMO|campo=valor.`
- `Si cambiaste movimientos especiales, usa este formato: UBICACION|INSUMO|sin-especiales o UBICACION|INSUMO|campo=valor|ingreso-especial=motivo:cantidad.`

Reglas:

- `C1` y `C2` aceptan `ingreso` y `salida`
- `LINEA` o `LINEA CALIENTE` acepta `conteo`, `ingreso` y `salida`
- los movimientos especiales ya no viven en los registros principales; se leen desde `MOTIVOS ESPECIALES`
- en `MOTIVOS ESPECIALES`, `TIPO=INGRESO` o `TIPO=SALIDA` y la `CANTIDAD` siempre es parte del ingreso o salida total del registro principal
- OpenClaw debe asumir que `MOTIVOS ESPECIALES` se usa filtrando por la fecha de trabajo actual; no debe mezclar movimientos de otros días
- la suma de ingresos especiales nunca puede ser mayor que el `INGRESO` total del registro principal
- la suma de salidas especiales nunca puede ser mayor que la salida total del insumo
- `sin-especiales` significa eliminación completa de todos los movimientos especiales de ese insumo para la fecha actual
- si el usuario menciona `congelador`, pide que especifique si es `C1` o `C2`
- si falta ubicación, columna, insumo o valor final, pide solo ese dato faltante
- después del aviso del usuario, relee registros del día
- al releer registros del día, OpenClaw también debe releer `MOTIVOS ESPECIALES`
- si ya hay ventas cargadas ese día, resincroniza el inventario con esas ventas
- si todavía no hay ventas cargadas, recalcula el inventario solo desde registros
- al final, informa si quedó alguna diferencia o si todo quedó cuadrado
- si el usuario hizo varios cambios manuales, puede avisarlos en mensajes separados o en una sola lista clara

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
7. aviso de no editar manualmente las hojas mientras el proceso esté activo
8. una pregunta simple de confirmación

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
8. aviso de no editar manualmente las hojas mientras el proceso esté activo
9. una pregunta simple de confirmación

Regla clave:

- el preview incremental debe enseñar solo lo que cambia
- no vuelvas a listar como novedad ventas que ya estaban correctas

### Para Opción 2

Siempre muestra:

1. fecha
2. movimientos del día por ubicación
3. aviso de que `C1` y `C2` quedarán con `VENTAS` provisional desde `SALIDA`
4. aviso de que `LINEA CALIENTE` seguirá pendiente del ticket
5. aviso de no editar manualmente las hojas mientras el proceso esté activo
6. pregunta simple de confirmación

## Confirmación

- Si el usuario confirma, ejecuta el comando de confirmación.
- Si cambia la fecha, vuelve a preparar o confirma con la nueva fecha según el flujo.
- Si cancela, no escribas nada.
- Después de escribir, siempre devuelve un resumen claro de lo que se hizo y de las diferencias finales.
- Si el entorno lo permite, emite hitos de avance mientras el proceso corre.

## Reglas críticas

- Si el usuario dio una fecha exacta, usa esa fecha.
- Si la fecha ya existe, reutiliza el bloque del día. Nunca crees un duplicado del mismo día.
- Si el usuario indica `precierre`, guárdalo como recordatorio del estado del día.
- Si llega un ticket nuevo o un ajuste manual, compara contra las ventas actuales del día y modifica solo lo que cambió.
- Después de un ticket nuevo o un ajuste manual, recalcula solo los insumos afectados del inventario.
- En `solo-registros`, deja `SALIDA` y `DIF` como fórmulas visibles; si aparecen diferencias, son esperadas hasta que llegue el ticket.
- Los platos ignorados por configuración sí deben aparecer en el preview con su motivo, pero no descuentan inventario.
- Los items con `$0.00` sí cuentan como ventas reales y deben consumir inventario.
- `ENSALADA CAESAR` sin proteína se toma como pollo y eso debe quedar explícito.
- El match de recetas es exacto sobre el nombre corto normalizado, pero también debe aceptar cualquier alias listado en `RECETAS -> NOMBRES NEOLA`.
- Si un alias existe, trátalo como match exacto; no lo mandes a "posible receta similar".
- Ejemplo: si la receta canónica es `SANDWICH DE PEPE` y en aliases existe `SANDWICH DE PEPP`, ambos deben entrar como el mismo plato canónico `SANDWICH DE PEPE`.
- Si llega algo muy parecido pero no exacto, como `SANDWICH DE PEP`, no inventes la receta ni lo cambies solo: dile al usuario que no encontraste una receta exacta, cuál es la más parecida y pregúntale si es ese mismo plato o si corresponde a otro distinto.
- Si varias recetas comparten el mismo nombre corto y descuentan exactamente lo mismo, trátalas como una sola.
- Si no hay match exacto, propone la receta más similar y bloquea hasta que el usuario confirme.
- Un problema de legibilidad de imagen por sí solo no debe bloquear si todavía se puede seguir con lo legible.
- `ROLLITOS RELLENO` es ambiguo: resuélvelo por registros o por aclaración manual antes de cerrar.
- Los panes en `LINEA CALIENTE` no se cuentan diariamente; conteo vacío no significa cero.
- Las transferencias desde `C1` o `C2` hacia línea deben entrar como ingreso en `LINEA CALIENTE`.
- Si un insumo tiene movimientos especiales en `MOTIVOS ESPECIALES`, esa cantidad sigue afectando el cierre del inventario, pero su efecto operativo depende de `UBICACION DESCUENTO`.
- `MOTIVOS ESPECIALES` solo afecta el día que coincide con la fecha del proceso; si el usuario corrige esa hoja para otro día, primero confirma la fecha exacta.
- En congeladores, `VENTAS` final puede representar venta de Neola, transferencia a línea o salida directa para preparación.
- Si en `UBICACION DESCUENTO` el insumo va de congelador a línea, la `cantidad` especial reduce el `INGRESO` a línea, no el `CIERRE` del congelador ni la `VENTAS` provisional del congelador.
- Si en `UBICACION DESCUENTO` el insumo se usa directo para un plato, la `cantidad` especial se suma a la parte esperada en `VENTAS` del congelador.
- Si un insumo ya salió del congelador y luego llega un ticket con ese mismo consumo, no dupliques la cantidad: el recálculo final debe conciliar ambas fuentes.

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
- Si hay diferencias, explica la causa más probable usando los datos dados: ticket faltante, salida manual, transferencia a línea, error de conteo, receta incompleta o problema humano.
- Si falla la validación de `VENTAS NEOLA`, corrige ese bloque y vuelve a validar antes de tocar inventario.
- Si el usuario pide una corrección puntual posterior, no reescribas todo el día: toca solo ventas o insumos afectados.
- Si llega un ticket más completo después de un `precierre`, muestra solo la diferencia contra lo ya cargado y confirma antes de aplicar.
- Siempre cierra recordando que, si editaron manualmente las hojas mientras el proceso estaba activo, te avisen para revisar.

## Archivos importantes

- `main.py`: CLI y selección de flujo.
- `motor.py`: previews, confirmaciones y lógica incremental.
- `parser_neola.py`: lectura del ticket.
- `recetas.py`: match plato-receta y consumo teórico.
- `sheets_connector.py`: lectura y escritura en Google Sheets.
- `config.py`: credenciales, constantes y platos ignorados.
