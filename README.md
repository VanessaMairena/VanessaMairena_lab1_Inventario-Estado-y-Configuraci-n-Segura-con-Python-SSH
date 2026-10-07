# Lab 1 - Inventario, estado y configuración segura (Netmiko)

ITIEL-13 Redes Programables - UTN. Entorno: MikroTik CHR en VirtualBox (host-only 192.168.56.0/24).

## Instalación
```bash
python -m venv env
source env/bin/activate        # Windows: env\Scripts\activate
pip install -r requirements.txt
```

## Variables de entorno (clave nunca en el código)
```bash
export LAB_PASS_MT_LAB='su_clave'      # Windows PowerShell: $env:LAB_PASS_MT_LAB='su_clave'
```
Si no existe, el script la pide con getpass.

## Ejecución
```bash
python lab1_inventario.py --n 18 --nombre "Vanessa Mairena"             # estado + config
python lab1_inventario.py --n 18 --nombre "Vanessa Mairena"             # 2.a vez: sin cambios
python lab1_inventario.py --n 18 --nombre "Vanessa Mairena" --limpiar   # borra solo lo suyo
```
Genera `reporte_estado.json` y `logs/<equipo>.log`.

## Declaración de uso de IA
Se usó un asistente de IA (Claude) como apoyo para estructurar el script y la guía de VirtualBox.
Todo el código fue revisado y entendido por el estudiante. 

## Extra: REST vs SSH
Además de SSH, el script obtiene el estado del MikroTik por la API REST
(`https://<host>/rest`, librería `requests`) y compara ambos resultados en
`reporte_estado.json` (campo `comparacion`) y en la columna `SSH=REST` de la tabla.
Requiere `www-ssl` habilitado en el MikroTik (`/ip service enable www-ssl`)
y el paquete `requests` (incluido en `requirements.txt`).
