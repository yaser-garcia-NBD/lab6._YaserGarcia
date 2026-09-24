#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Muestra TUS parámetros personales del Lab #6 (dependen de tu cédula y del código de sesión)."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "autograde"))
import autograde as a  # noqa: E402

try:
    est = json.loads((RAIZ / "estudiante.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    sys.exit("Primero completa estudiante.json")
if not all(str(est.get(k, "")).strip() for k in ("nombre", "apellido", "cedula", "codigo_sesion")):
    sys.exit("Completa TODOS los campos de estudiante.json (nombre, apellido, cedula, codigo_sesion)")

p = a.perfil(est["cedula"], est["codigo_sesion"])
print(f"""
╔════════════════════════════════════════════════════════════╗
  TUS PARÁMETROS PERSONALES · {est['nombre']} {est['apellido']}
╚════════════════════════════════════════════════════════════╝
 1. Tema de tu página ............ {p['tema']}
    (debe aparecer en el <title> y en el <h1>)
 2. id del elemento <main> ........ {p['id_main']}
 3. class del elemento <main> ..... {p['clase_main']}
 4. Color principal ............... {p['color']}
    (úsalo en el CSS: títulos, bordes o fondos)
 5. Filas de datos en <tbody> ..... {p['filas']}  (cada fila con 3 o más celdas)
 6. <meta name="author"> .......... {est['nombre']} {est['apellido']}
 7. <meta name="codigo-sesion"> ... {est['codigo_sesion'].upper()}
 8. <footer> ...................... debe incluir tu nombre y apellido

Estos valores son SOLO tuyos. Un compañero tendrá otros distintos.
""")
