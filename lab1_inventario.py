#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab1_inventario.py
Laboratorio 1 - ITIEL-13 Redes Programables (UTN)
Inventario YAML -> SSH con Netmiko -> estado estructurado -> reporte JSON
+ cambio idempotente en MikroTik (interfaz lo-ssh-<N> con IP 10.253.0.<N>/32).

Uso:
    python lab1_inventario.py --n 7 --nombre "Juan Perez"
    python lab1_inventario.py --n 7 --nombre "Juan Perez"            # 2.a vez: SIN CAMBIOS
    python lab1_inventario.py --n 7 --nombre "Juan Perez" --limpiar
"""
import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

# ---------------------------------------------------------------------------
# Comandos por device_type (agregar un fabricante = agregar una entrada)
# ---------------------------------------------------------------------------
COMANDOS = {
    "mikrotik_routeros": {
        "identidad": "/system identity print",
        "recursos": "/system resource print",
        "ips": "/ip address print terse",
        "ruta": "/ip route print terse where dst-address=0.0.0.0/0",
    },
    "cisco_ios": {
        "identidad": "show running-config | include hostname",
        "recursos": "show version | include uptime",
        "ips": "show ip interface brief",
        "ruta": "show ip route 0.0.0.0",
    },
}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def texto_a_dict(salida):
    """'clave: valor' por línea -> diccionario."""
    datos = {}
    for linea in salida.splitlines():
        if ":" in linea:
            clave, valor = linea.split(":", 1)
            datos[clave.strip()] = valor.strip()
    return datos


def terse_a_lista(salida):
    """Cada línea 'terse' de RouterOS (clave=valor ...) -> dict. Devuelve lista."""
    registros = []
    for linea in salida.splitlines():
        pares = re.findall(r'([\w-]+)=("[^"]*"|\S*)', linea)
        if pares:
            registros.append({k: v.strip('"') for k, v in pares})
    return registros


def parse_mikrotik(crudo):
    # RouterOS parte el nombre en una letra por línea (C / H / R): se unen todas
    m = re.search(r"name:\s*(.*)", crudo["identidad"], re.S)
    nombre_equipo = "".join(m.group(1).split()) if m else None
    rec = texto_a_dict(crudo["recursos"])
    ips = [{"interfaz": r.get("interface", ""), "ip": r.get("address", "")}
           for r in terse_a_lista(crudo["ips"])]
    rutas = terse_a_lista(crudo["ruta"])
    gw = rutas[0].get("gateway") if rutas else None
    return {"nombre_equipo": nombre_equipo, "uptime": rec.get("uptime"),
            "version": rec.get("version"), "interfaces": ips, "gateway_defecto": gw}

def parse_cisco(crudo):
    m = re.search(r"hostname\s+(\S+)", crudo["identidad"])
    u = re.search(r"uptime is (.+)", crudo["recursos"])
    ips = []
    for linea in crudo["ips"].splitlines()[1:]:
        c = linea.split()
        if len(c) >= 2:
            ips.append({"interfaz": c[0], "ip": c[1]})
    g = re.search(r"via (\d+\.\d+\.\d+\.\d+)", crudo["ruta"])
    return {"nombre_equipo": m.group(1) if m else None,
            "uptime": u.group(1).strip() if u else None, "version": None,
            "interfaces": ips, "gateway_defecto": g.group(1) if g else None}


PARSERS = {"mikrotik_routeros": parse_mikrotik, "cisco_ios": parse_cisco}


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------
def obtener_clave(eq):
    var = "LAB_PASS_" + eq["nombre"].upper().replace("-", "_")
    return os.getenv(var) or getpass.getpass(f"Clave de {eq['usuario']}@{eq['host']} ({var}): ")


# ---------------------------------------------------------------------------
# Cambio idempotente en MikroTik (R6)
# ---------------------------------------------------------------------------
def hay_error(salida):
    s = salida.lower()
    return any(p in s for p in ("failure", "invalid", "expected", "bad command", "no such"))


def existe(conn, ruta, filtro):
    return conn.send_command(f":put [{ruta} find where {filtro}]").strip() != ""


def asegurar(conn, etiqueta, ruta, filtro, cmd_add):
    if existe(conn, ruta, filtro):
        print(f"    [SIN CAMBIOS] {etiqueta} ya existe")
        return False
    salida = conn.send_command(cmd_add)
    if hay_error(salida):
        raise RuntimeError(f"{etiqueta}: {salida}")
    print(f"    [CREADO] {etiqueta}")
    return True


def configurar_mikrotik(conn, n, nombre):
    iface, ip = f"lo-ssh-{n}", f"10.253.0.{n}/32"
    cambios = asegurar(conn, f"bridge {iface}", "/interface bridge", f'name="{iface}"',
                       f'/interface bridge add name={iface} comment="{nombre}"')
    cambios |= asegurar(conn, f"IP {ip} en {iface}", "/ip address",
                        f'interface="{iface}" address="{ip}"',
                        f'/ip address add address={ip} interface={iface} comment="{nombre}"')
    verif = conn.send_command(f'/ip address print terse where interface="{iface}"')
    ok = ip.split("/")[0] in verif
    print(f"    [VERIFICACION] {'OK' if ok else 'FALLO'}: {verif.strip()}")
    return {"cambios": cambios, "verificado": ok,
            "resultado": "CAMBIOS APLICADOS" if cambios else "sin cambios"}


def limpiar_mikrotik(conn, n):
    iface, ip = f"lo-ssh-{n}", f"10.253.0.{n}/32"
    for etiqueta, ruta, filtro in (
        (f"IP {ip}", "/ip address", f'interface="{iface}" address="{ip}"'),
        (f"bridge {iface}", "/interface bridge", f'name="{iface}"'),
    ):
        if existe(conn, ruta, filtro):
            conn.send_command(f"{ruta} remove [find where {filtro}]")
            print(f"    [ELIMINADO] {etiqueta}")
        else:
            print(f"    [SIN CAMBIOS] {etiqueta} no existe")
    return {"resultado": "limpieza hecha"}


# ---------------------------------------------------------------------------
# Procesamiento por equipo
# ---------------------------------------------------------------------------
def procesar_equipo(eq, args):
    resultado = {"nombre": eq["nombre"], "host": eq["host"], "device_type": eq["device_type"],
                 "fecha": datetime.now().isoformat(timespec="seconds"),
                 "estado": "ok", "datos": None, "configuracion": None}
    dt = eq["device_type"]
    if dt not in COMANDOS:
        resultado["estado"] = f"device_type no soportado: {dt}"
        return resultado
    dispositivo = {
        "device_type": dt, "host": eq["host"], "username": eq["usuario"],
        "password": obtener_clave(eq), "conn_timeout": 20, "fast_cli": False,
        "session_log": str(Path("logs") / f"{eq['nombre']}.log"),
    }
    try:
        with ConnectHandler(**dispositivo) as conn:
            print(f"[OK] {eq['nombre']} ({eq['host']}) prompt: {conn.find_prompt()}")
            if args.limpiar and eq.get("rol") == "escritura" and dt == "mikrotik_routeros":
                resultado["configuracion"] = limpiar_mikrotik(conn, args.n)
            else:
                crudo = {k: conn.send_command(c, read_timeout=20) for k, c in COMANDOS[dt].items()}
                resultado["datos"] = PARSERS[dt](crudo)
                if eq.get("rol") == "escritura" and dt == "mikrotik_routeros":
                    resultado["configuracion"] = configurar_mikrotik(conn, args.n, args.nombre)
    except NetmikoTimeoutException:
        resultado["estado"] = "error: sin conexión (IP, SSH, firewall o red)"
    except NetmikoAuthenticationException:
        resultado["estado"] = "error: usuario o clave incorrectos"
    except Exception as e:  # noqa: BLE001
        resultado["estado"] = f"error: {e}"
    if resultado["estado"] != "ok":
        print(f"[ERROR] {eq['nombre']}: {resultado['estado']}")
    return resultado


def imprimir_tabla(resultados):
    cols = ["Equipo", "IP", "Estado", "Interfaces", "Gateway"]
    filas = []
    for r in resultados:
        d = r["datos"] or {}
        filas.append([r["nombre"], r["host"], r["estado"][:40],
                      str(len(d.get("interfaces", []))) if d else "-",
                      str(d.get("gateway_defecto") or "-")])
    anchos = [max(len(c), *(len(f[i]) for f in filas)) for i, c in enumerate(cols)]
    linea = "+" + "+".join("-" * (a + 2) for a in anchos) + "+"
    fmt = lambda f: "| " + " | ".join(x.ljust(a) for x, a in zip(f, anchos)) + " |"
    print("\n" + linea, fmt(cols), linea, *map(fmt, filas), linea, sep="\n")


def main():
    ap = argparse.ArgumentParser(description="Lab 1: inventario, estado y config segura")
    ap.add_argument("--inventario", default="inventario.yaml")
    ap.add_argument("--n", type=int, default=int(os.getenv("LAB_N", "0")), help="número de lista")
    ap.add_argument("--nombre", default=os.getenv("LAB_NOMBRE", ""), help="su nombre (comentario)")
    ap.add_argument("--limpiar", action="store_true", help="elimina solo sus objetos")
    args = ap.parse_args()
    if not (1 <= args.n <= 254) or not args.nombre:
        sys.exit("[ERROR] Indique --n (1-254) y --nombre (o LAB_N / LAB_NOMBRE).")

    with open(args.inventario, encoding="utf-8") as f:
        equipos = yaml.safe_load(f)["equipos"]
    Path("logs").mkdir(exist_ok=True)

    resultados = [procesar_equipo(eq, args) for eq in equipos]  # un fallo no detiene a los demás

    Path("reporte_estado.json").write_text(
        json.dumps({"generado": datetime.now().isoformat(timespec="seconds"),
                    "equipos": resultados}, indent=2, ensure_ascii=False), encoding="utf-8")
    imprimir_tabla(resultados)
    print("\nReporte: reporte_estado.json | Logs: logs/")


if __name__ == "__main__":
    main()
