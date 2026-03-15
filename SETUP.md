# Guía de Instalación — Motor de Inventario Sambó

## Paso 1: Subir archivos al VPS

Desde tu computadora:
```bash
scp motor-sambo-v2.tar.gz root@<IP_VPS>:/root/
```

Desde el VPS, copiar al contenedor:
```bash
docker cp /root/motor-sambo-v2.tar.gz <contenedor>:/root/.openclaw/skills/
```

Dentro del contenedor:
```bash
cd ~/.openclaw/skills/
tar -xzf motor-sambo-v2.tar.gz
ls motor-sambo/
```

## Paso 2: Instalar dependencias

```bash
cd ~/.openclaw/skills/motor-sambo
pip install -r requirements.txt --break-system-packages
```

## Paso 3: Configurar credentials.json de Google Sheets

Subir el archivo JSON de Google Cloud al contenedor:
```bash
# Desde tu computadora → VPS
scp credentials.json root@<IP_VPS>:/root/
# Desde VPS → contenedor
docker cp /root/credentials.json <contenedor>:/root/.openclaw/skills/motor-sambo/
```

## Paso 4: Editar config.py

```bash
cd ~/.openclaw/skills/motor-sambo
nano config.py
```

Completar:
- `NEXOS_API_KEY`: tu API key de Nexos.ai (está en hPanel → OpenClaw)
- `SHEET_REGISTROS`: ID del Google Sheet de registros
- `SHEET_RECETAS`: ID del Google Sheet de recetas
- `SHEET_INVENTARIO`: ID del Google Sheet de inventario

Los IDs se copian de la URL: `docs.google.com/spreadsheets/d/ESTE_ES_EL_ID/edit`

## Paso 5: Probar

```bash
cd ~/.openclaw/skills/motor-sambo

# Test conexión a Google Sheets:
python3 -c "from sheets_connector import leer_recetas; print(f'Recetas: {len(leer_recetas())}')"

# Test parseo de imagen:
python3 main.py /ruta/a/foto.jpg --solo-leer

# Test cierre completo:
python3 main.py /ruta/a/foto.jpg
```
