#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUTOGRADE - Laboratorio #6 - Desarrollo Web (HTML5 + CSS3)
Docente: SarmientoEdu

Califica la rúbrica (100 pts), aplica controles de integridad (antiplagio / anti-IA)
y sube el resultado a Supabase mediante la función RPC `submit_grade`.

Uso local:   python autograde/autograde.py --dry-run     (no sube nada)
Uso en CI:   lo ejecuta .github/workflows/autograde.yml en cada push
Docente:     python autograde/autograde.py --hash        (hash a registrar en lab_config)

Solo usa la biblioteca estándar de Python (sin pip install).
"""
import colorsys
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DEL DOCENTE  (edite SOLO esta sección)
# ════════════════════════════════════════════════════════════════════════
SUPABASE_URL = "https://ikusdplwwvcbxgvevkvw.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlrdXNkcGx3d3ZjYnhndmV2a3Z3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5ODgyMjYsImV4cCI6MjEwNDU2NDIyNn0.TEA3ZOmZxv7zedc2hDZUaDoB0oGDVMXM2yh1M6D4YBc"

LAB = 6
SAL = "SarmientoEdu-lab6-V9S3"   # cambie la sal cada semestre
CANARIOS = ["lab6-ia-8842", "verificado-por-ia"]  # marcas trampa (ver guía docente)

REVISAR_GIT = False      # True = activa el control del historial de commits (Lab #7 en adelante)
MIN_COMMITS = 1          # commits mínimos del estudiante (sin contar el del template)
MIN_MINUTOS = 1         # tiempo mínimo entre el primer y el último commit
MAX_COMMIT_SHARE = 1.60  # un solo commit no debe aportar más de este % de líneas
# ════════════════════════════════════════════════════════════════════════

RAIZ = Path(__file__).resolve().parent.parent

TEMAS = [  # (nombre del tema, palabra clave sin tildes que debe aparecer en <title> y <h1>)
    ("Cafetería universitaria", "cafeteria"),
    ("Biblioteca de la facultad", "biblioteca"),
    ("Gimnasio del campus", "gimnasio"),
    ("Clínica veterinaria", "veterinaria"),
    ("Agencia de viajes", "viaje"),
    ("Club de robótica", "robotica"),
    ("Estación meteorológica", "meteorolog"),
    ("Tienda de componentes electrónicos", "electronic"),
    ("Festival de música", "musica"),
    ("Laboratorio de circuitos", "circuito"),
]


# ───────────────────────── Personalización por estudiante ─────────────────────────
def normalizar_ced(c):
    return re.sub(r"[^A-Za-z0-9]", "", c or "").upper()


def perfil(cedula, codigo):
    """Parámetros únicos y reproducibles para cada (cédula, código de sesión)."""
    h = hashlib.sha256(f"{SAL}|{normalizar_ced(cedula)}|{(codigo or '').strip().upper()}".encode()).hexdigest()
    hue = int(h[8:12], 16) % 360
    r, g, b = colorsys.hls_to_rgb(hue / 360, 0.36, 0.65)
    color = "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    tema = TEMAS[int(h[0:8], 16) % len(TEMAS)]
    return {
        "tema": tema[0],
        "clave": tema[1],
        "color": color,
        "filas": 4 + int(h[14:16], 16) % 3,               # 4, 5 o 6 filas en <tbody>
        "id_main": "ficha-" + normalizar_ced(cedula)[-4:].lower(),
        "clase_main": "k-" + h[16:22],
    }


def sin_tildes(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


# ───────────────────────── Parser HTML mínimo (stdlib) ─────────────────────────
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr", "param"}


class Nodo:
    def __init__(self, tag, attrs, padre=None):
        self.tag = tag
        self.attrs = {k.lower(): (v if v is not None else "") for k, v in attrs}
        self.hijos, self.padre, self.texto = [], padre, []

    def get(self, k, d=None):
        return self.attrs.get(k, d)

    def desc(self):
        for h in self.hijos:
            yield h
            yield from h.desc()

    def find(self, tag):
        return [n for n in self.desc() if n.tag == tag]

    def text(self):
        return " ".join(["".join(self.texto)] + [h.text() for h in self.hijos]).strip()


class Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.raiz = Nodo("#root", [])
        self.act = self.raiz
        self.comentarios, self.doctype = [], None

    def handle_decl(self, d):
        self.doctype = d.strip().lower()

    def handle_comment(self, d):
        self.comentarios.append(d)

    def handle_starttag(self, tag, attrs):
        n = Nodo(tag, attrs, self.act)
        self.act.hijos.append(n)
        if tag not in VOID:
            self.act = n

    def handle_startendtag(self, tag, attrs):
        self.act.hijos.append(Nodo(tag, attrs, self.act))

    def handle_endtag(self, tag):
        n = self.act
        while n is not None and n.tag != tag:
            n = n.padre
        if n is not None and n is not self.raiz:
            self.act = n.padre

    def handle_data(self, d):
        self.act.texto.append(d)


def leer(ruta):
    try:
        return Path(ruta).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ───────────────────────── Parser CSS mínimo ─────────────────────────
def parsear_css(txt):
    comentarios = re.findall(r"/\*(.*?)\*/", txt, re.S)
    limpio = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    balanceado = limpio.count("{") == limpio.count("}")
    reglas, errores = [], 0
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", limpio):
        sel, cuerpo = m.group(1).strip(), m.group(2)
        if sel.startswith("@"):
            continue
        decls = [d for d in cuerpo.split(";") if d.strip()]
        if cuerpo.strip() and not cuerpo.strip().endswith(";"):
            errores += 1
        props = {}
        for d in decls:
            if re.match(r"^\s*-{0,2}[a-zA-Z][\w-]*\s*:\s*\S", d):
                k, v = d.split(":", 1)
                props[k.strip().lower()] = v.strip()
            else:
                errores += 1
        reglas.append({"sel": sel, "partes": [p.strip() for p in sel.split(",") if p.strip()], "props": props})
    return {"reglas": reglas, "comentarios": comentarios, "balanceado": balanceado, "errores": errores}


# ───────────────────────── Rúbrica ─────────────────────────
class Crit:
    def __init__(self, cid, nombre, maximo):
        self.id, self.nombre, self.maximo, self.items = cid, nombre, maximo, []

    def add(self, ok, pts, msg):
        self.items.append((bool(ok), pts, msg))

    @property
    def pts(self):
        return round(sum(p for ok, p, _ in self.items if ok), 2)


def evaluar(est, p):
    """Devuelve (criterios, contexto) evaluando index.html, css/, js/ e img/."""
    crits = []
    html_txt = leer(RAIZ / "index.html") or ""
    doc = Doc()
    doc.feed(html_txt)
    R = doc.raiz
    todos = list(R.desc())
    ids = {n.get("id") for n in todos if n.get("id")}
    clases = {c for n in todos for c in n.get("class", "").split()}

    css_ext_txt = leer(RAIZ / "css" / "estilos.css") or ""
    css_int_txt = "\n".join(n.text() for n in R.find("style"))
    css_ext, css_int = parsear_css(css_ext_txt), parsear_css(css_int_txt)
    reglas = css_ext["reglas"] + css_int["reglas"]
    props_all = {k for r in reglas for k in r["props"]}
    css_todo = css_ext_txt + "\n" + css_int_txt + "\n" + "\n".join(n.get("style", "") for n in todos)

    html = R.find("html")
    head, body = R.find("head"), R.find("body")
    metas = R.find("meta")

    def meta(nombre):
        for m in metas:
            if m.get("name", "").lower() == nombre:
                return m.get("content", "").strip()
        return None

    titulo = " ".join(t.text() for t in R.find("title"))
    nombre_completo = sin_tildes(f"{est['nombre']} {est['apellido']}")

    # A ── Estructura, raíz y metadatos (10)
    c = Crit("A", "Estructura, elemento raíz y metadatos (2.1, 2.2.1, 2.2.2)", 10)
    c.add(doc.doctype == "doctype html", 1, "Declaración <!DOCTYPE html>")
    c.add(html and html[0].get("lang", "").lower().startswith("es"), 1, '<html lang="es">')
    c.add(len(head) == 1 and len(body) == 1, 1, "Un único <head> y un único <body>")
    c.add(any(m.get("charset", "").lower() == "utf-8" for m in metas), 1, 'meta charset="UTF-8"')
    c.add(meta("viewport") and "width=device-width" in meta("viewport"), 1, "meta viewport")
    c.add(p["clave"] in sin_tildes(titulo), 2, f"<title> menciona el tema ({p['tema']})")
    c.add(len(meta("description") or "") >= 30, 1, "meta description (mín. 30 caracteres)")
    c.add(sin_tildes(meta("author") or "") == nombre_completo, 1, "meta author = Nombre Apellido")
    c.add((meta("codigo-sesion") or "").upper() == est["codigo_sesion"].upper(), 1, "meta codigo-sesion")
    crits.append(c)

    # B ── Scripting (5)
    c = Crit("B", "Scripting (2.2.3)", 5)
    scripts = [s for s in R.find("script") if s.get("src", "").endswith("app.js")]
    js_txt = leer(RAIZ / "js" / "app.js") or ""
    js_limpio = re.sub(r"//.*|/\*.*?\*/", "", js_txt, flags=re.S).strip()
    c.add(scripts and ("defer" in scripts[0].attrs or scripts[0].get("type") == "module"), 2, "<script src='js/app.js' defer>")
    c.add(len(js_limpio) >= 1, 1, "js/app.js contiene código")
    c.add(any(len(n.text()) >= 10 for n in R.find("noscript")), 2, "<noscript> con mensaje alternativo")
    crits.append(c)

    # C ── Secciones, agrupación y diseño (14)
    c = Crit("C", "Secciones, agrupación de contenido y elementos de diseño (2.2.4, 2.2.5, 2.3)", 14)
    c.add(R.find("header"), 1, "<header>")
    anclas = [a for nav in R.find("nav") for a in nav.find("a") if a.get("href", "").startswith("#")]
    ok_anc = [a for a in anclas if a.get("href")[1:] in ids]
    c.add(len(ok_anc) >= 4, 2, "<nav> con 4+ enlaces internos (#id) que apuntan a ids existentes")
    c.add(len(R.find("main")) == 1, 1, "Exactamente un <main>")
    secs = [s for s in R.find("section") if any(s.find(f"h{i}") for i in range(1, 7))]
    c.add(len(secs) >= 3, 2, "3+ <section>, cada una con encabezado")
    c.add(len(R.find("article")) >= 2, 1, "2+ <article>")
    c.add(R.find("aside"), 1, "<aside>")
    c.add(R.find("footer"), 1, "<footer>")
    niveles = [int(n.tag[1]) for n in todos if re.fullmatch(r"h[1-6]", n.tag)]
    jerarquia = niveles.count(1) == 1 and niveles[:1] == [1] and all(b - a <= 1 for a, b in zip(niveles, niveles[1:]))
    c.add(jerarquia, 1, "Un solo <h1> y jerarquía de encabezados sin saltos")
    c.add(any(d.find("summary") for d in R.find("details")), 1, "<details> con <summary>")
    c.add(any(len(l.find("li")) >= 3 for l in R.find("ul") + R.find("ol")), 1, "Lista <ul>/<ol> con 3+ <li>")
    c.add(any(d.find("dt") and d.find("dd") for d in R.find("dl")), 1, "Lista de descripción <dl><dt><dd>")
    c.add(any(f.find("figcaption") for f in R.find("figure")), 1, "<figure> con <figcaption>")
    crits.append(c)

    # D ── Tablas (8)
    c = Crit("D", "Tablas (2.2.6)", 8)
    tabla = R.find("table")[0] if R.find("table") else None
    c.add(tabla, 1, "<table>")
    c.add(tabla and tabla.find("caption"), 1, "<caption>")
    c.add(tabla and tabla.find("thead") and tabla.find("tbody"), 1, "<thead> y <tbody>")
    c.add(tabla and tabla.find("tfoot"), 1, "<tfoot>")
    c.add(tabla and any(t.get("scope") for t in tabla.find("th")), 1, "<th scope=...>")
    filas = 0
    if tabla:
        tb = tabla.find("tbody")
        trs = tb[0].find("tr") if tb else tabla.find("tr")
        filas = len([t for t in trs if len(t.find("td")) + len(t.find("th")) >= 3])
    c.add(tabla and filas == p["filas"], 2, f"<tbody> con exactamente {p['filas']} filas de 3+ celdas (tu valor personal)")
    c.add(tabla and any(x.get("colspan") or x.get("rowspan") for x in tabla.find("td") + tabla.find("th")), 1, "colspan o rowspan")
    crits.append(c)

    # E ── Semántica de texto (6)
    c = Crit("E", "Semántica a nivel de texto (2.2.7)", 6)
    sem = {"strong", "em", "mark", "abbr", "time", "code", "small", "cite", "q", "sub", "sup", "kbd", "del", "ins", "dfn", "var", "samp"}
    usados = {n.tag for n in body[0].desc()} & sem if body else set()
    c.add(len(usados) >= 4, 2, "4+ etiquetas semánticas de texto distintas")
    c.add(len(usados) >= 6, 2, "6+ etiquetas semánticas de texto distintas")
    c.add(any(a.get("title") for a in R.find("abbr")), 1, "<abbr title=...>")
    c.add(any(t.get("datetime") for t in R.find("time")), 1, "<time datetime=...>")
    crits.append(c)

    # F ── Contenido incrustado (6)
    c = Crit("F", "Contenido incrustado (2.2.8)", 6)
    imgs = R.find("img")
    local = [i for i in imgs if i.get("src") and not re.match(r"^(https?:)?//", i.get("src")) and (RAIZ / i.get("src")).is_file()]
    c.add(any(i.get("alt", "").strip() for i in imgs), 1, "<img> con alt descriptivo")
    c.add(local, 1, "<img> cuyo archivo existe dentro del repositorio (img/)")
    media = R.find("video") + R.find("audio")
    c.add(any("controls" in m.attrs for m in media), 1, "<video>/<audio> con controls")
    c.add(any(m.find("source") or m.get("src") for m in media), 1, "<video>/<audio> con <source> o src")
    c.add(any(f.get("title") and f.get("src") for f in R.find("iframe")), 1, "<iframe> con src y title")
    c.add(any(n.get("loading") == "lazy" for n in imgs + R.find("iframe")), 1, 'loading="lazy"')
    crits.append(c)

    # G ── Formularios (14)
    c = Crit("G", "Formularios HTML (2.4)", 14)
    forms = R.find("form")
    f0 = forms[0] if forms else None
    c.add(f0 and f0.get("action") is not None and f0.get("method", "").lower() in ("get", "post"), 1, "<form action method>")
    controles = (f0.find("input") + f0.find("select") + f0.find("textarea")) if f0 else []
    cids = {x.get("id") for x in controles if x.get("id")}
    labels_ok = [l for l in (f0.find("label") if f0 else []) if l.get("for") in cids]
    c.add(len(labels_ok) >= 6, 2, "6+ <label for> asociados a controles con ese id")
    c.add(f0 and f0.find("fieldset") and f0.find("legend"), 1, "<fieldset> con <legend>")
    tipos = {x.get("type", "text").lower() for x in (f0.find("input") if f0 else [])} - {"hidden"}
    c.add(len(tipos) >= 6, 2, "6+ tipos de <input> distintos")
    c.add(len(tipos) >= 8, 1, "8+ tipos de <input> distintos")
    radios = [x.get("name") for x in (f0.find("input") if f0 else []) if x.get("type") == "radio" and x.get("name")]
    c.add(any(radios.count(n) >= 2 for n in set(radios)), 1, "Grupo de 2+ radios con el mismo name")
    c.add(f0 and any(len(s.find("option")) >= 3 for s in f0.find("select")), 1, "<select> con 3+ <option>")
    c.add(f0 and f0.find("textarea") and any(t.get("rows") for t in f0.find("textarea")), 1, "<textarea rows>")
    listas = {d.get("id") for d in R.find("datalist")}
    c.add(any(x.get("list") in listas for x in controles if x.get("list")), 1, "<input list> + <datalist>")
    c.add(f0 and any(b.get("type", "submit") == "submit" for b in f0.find("button")), 1, '<button type="submit">')
    attrs_ok = {"required", "placeholder", "min", "max", "maxlength", "minlength", "pattern", "autocomplete",
                "readonly", "disabled", "step", "value", "autofocus", "multiple"}
    usados_a = {k for x in controles for k in x.attrs} & attrs_ok
    c.add(len(usados_a) >= 6, 1, "6+ atributos de entrada distintos (required, placeholder, min, ...)")
    c.add(len(usados_a) >= 9, 1, "9+ atributos de entrada distintos")
    crits.append(c)

    # H ── CSS: sintaxis y selectores (14)
    c = Crit("H", "CSS: sintaxis y selectores (2.5, 2.6)", 14)
    partes = [x for r in reglas for x in r["partes"]]
    c.add(len(reglas) >= 12, 2, "12+ reglas CSS")
    c.add(len(css_ext["comentarios"]) + len(css_int["comentarios"]) >= 3, 1, "3+ comentarios /* */")
    sintaxis = css_ext["balanceado"] and css_int["balanceado"] and (css_ext["errores"] + css_int["errores"]) == 0 and len(reglas) > 0
    c.add(sintaxis, 2, "Sintaxis correcta (llaves balanceadas, prop: valor; )")
    c.add(any(re.match(r"^\*(\s|$|::|:|\.|\[)", x) for x in partes), 2, "Selector universal *")
    c.add(any(x.startswith("#") and re.split(r"[:\s.>+~\[]", x[1:])[0] in ids for x in partes), 2, "Selector de id (#) que existe en el HTML")
    c.add(any(x.startswith(".") and re.split(r"[:\s.>+~\[]", x[1:])[0] in clases for x in partes), 2, "Selector de clase (.) que existe en el HTML")
    c.add(any(len(r["partes"]) >= 2 for r in reglas), 2, "Selector de agrupación (a, b { })")
    c.add(any(re.match(r"^(body|html|h[1-6]|p|a|table|th|td|ul|ol|li|footer|header|nav|main|section|article|form|label|input|button|img)\b", x) for x in partes), 1, "Selector de elemento")
    crits.append(c)

    # I ── CSS: propiedades (8)
    c = Crit("I", "CSS: propiedades/atributos (2.7)", 8)
    FAM = {
        "color y fondo": r"^(color|background.*|opacity)$",
        "tipografía": r"^(font.*|line-height|letter-spacing|text-.*)$",
        "modelo de caja": r"^(margin.*|padding.*|box-sizing)$",
        "bordes": r"^(border.*|outline.*|box-shadow)$",
        "dimensiones": r"^(width|height|(max|min)-(width|height))$",
        "disposición": r"^(display|flex.*|grid.*|gap|position|float|justify-.*|align-.*|overflow.*)$",
    }
    fams = {n for n, rx in FAM.items() if any(re.match(rx, k) for k in props_all)}
    unidades = set(re.findall(r"\d(px|%|rem|em|vw|vh)\b", css_todo))
    c.add(len(props_all) >= 8, 2, "8+ propiedades CSS distintas")
    c.add(len(props_all) >= 12, 1, "12+ propiedades CSS distintas")
    c.add(len(fams) >= 4, 2, "4+ familias de propiedades (color, tipografía, caja, bordes, dimensiones, disposición)")
    c.add(len(fams) >= 6, 1, "Las 6 familias de propiedades")
    c.add(len(unidades) >= 2, 2, "2+ unidades distintas (px, %, rem, em, vw, vh)")
    crits.append(c)

    # J ── Integración CSS-HTML (6)
    c = Crit("J", "Integración de CSS con HTML (2.8)", 6)
    links = [l for l in R.find("link") if l.get("rel", "").lower() == "stylesheet" and l.get("href", "").endswith("estilos.css")]
    c.add(links and css_ext["reglas"], 2, "CSS externo: <link rel='stylesheet' href='css/estilos.css'> con reglas")
    c.add(css_int["reglas"], 2, "CSS interno: <style> con al menos una regla")
    c.add(any(n.get("style", "").strip() for n in body[0].desc()) if body else False, 2, "CSS en línea: atributo style=''")
    crits.append(c)

    # K ── Personalización (9)
    c = Crit("K", "Personalización de tu entrega", 9)
    c.add(p["id_main"] in ids, 2, f"id personal '{p['id_main']}' presente")
    c.add(p["clase_main"] in clases, 2, f"clase personal '{p['clase_main']}' presente")
    c.add(p["color"].lower() in css_todo.lower(), 2, f"color personal {p['color']} usado en el CSS")
    c.add(any(p["clave"] in sin_tildes(h.text()) for h in R.find("h1")), 1, "<h1> menciona el tema asignado")
    nom1 = sin_tildes(est["nombre"]).split()[0] if est["nombre"].split() else "?"
    ape1 = sin_tildes(est["apellido"]).split()[0] if est["apellido"].split() else "?"
    c.add(any(nom1 in sin_tildes(f.text()) and ape1 in sin_tildes(f.text()) for f in R.find("footer")), 2, "<footer> con tu nombre y apellido")
    crits.append(c)

    ctx = {"html": html_txt, "css": css_ext_txt + "\n" + css_int_txt, "js": js_txt, "doc": doc,
           "comentarios": doc.comentarios + css_ext["comentarios"] + css_int["comentarios"]}
    return crits, ctx


# ───────────────────────── Integridad (antiplagio / anti-IA) ─────────────────────────
NIVEL = {"bajo": 0, "medio": 1, "alto": 2}
ING = {"the", "and", "this", "with", "your", "here", "that", "are", "is", "of"}
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def analizar_git():
    root = set(git("rev-list", "--max-parents=0", "HEAD").split())
    filas = [l.split("|") for l in git("log", "--reverse", "--format=%H|%at|%ae").splitlines() if l.count("|") == 2]
    mios = [f for f in filas if f[0] not in root]
    info = {"commits": len(mios), "minutos": 0, "share": 0.0, "autores": len({f[2] for f in mios})}
    if len(mios) >= 2:
        info["minutos"] = round((int(mios[-1][1]) - int(mios[0][1])) / 60)
    add, cur = {}, None
    for l in git("log", "--numstat", "--format=@@%H").splitlines():
        if l.startswith("@@"):
            cur = l[2:]
            add[cur] = 0
        elif cur and "\t" in l:
            a = l.split("\t")[0]
            add[cur] += int(a) if a.isdigit() else 0
    aportes = [add.get(f[0], 0) for f in mios]
    if sum(aportes) > 0:
        info["share"] = round(max(aportes) / sum(aportes), 2)
    return info


def integridad(est, p, ctx):
    obs, riesgo = [], "bajo"

    def marca(nivel, msg):
        nonlocal riesgo
        obs.append(msg)
        if NIVEL[nivel] > NIVEL[riesgo]:
            riesgo = nivel

    todo = (ctx["html"] + "\n" + ctx["css"] + "\n" + ctx["js"]).lower()
    for cn in CANARIOS:
        if cn.lower() in todo:
            marca("alto", f"Contiene la marca trampa de IA '{cn}' (texto oculto del enunciado copiado a una IA)")
    ajenos = ({t for t in re.findall(r"\bk-[0-9a-f]{6}\b", todo)} - {p["clase_main"]}) | \
             ({t for t in re.findall(r"\bficha-[a-z0-9]{4}\b", todo)} - {p["id_main"]})
    if ajenos:
        marca("alto", f"Contiene marcas de personalización de OTRO estudiante: {', '.join(sorted(ajenos))}")

    g = analizar_git() if REVISAR_GIT else {"commits": 0, "minutos": 0, "share": 0.0, "autores": 0}
    if not REVISAR_GIT:
        pass  # control de Git desactivado en este laboratorio
    elif g["commits"] <= 1:
        marca("alto", f"Historial Git: solo {g['commits']} commit(s) del estudiante (entrega pegada de una vez)")
    elif g["commits"] < MIN_COMMITS:
        marca("medio", f"Historial Git: {g['commits']} commits (mínimo esperado {MIN_COMMITS})")
    if REVISAR_GIT and g["commits"] >= 2 and g["minutos"] < MIN_MINUTOS:
        marca("medio", f"Historial Git: solo {g['minutos']} min entre primer y último commit")
    if REVISAR_GIT and g["share"] > MAX_COMMIT_SHARE:
        marca("medio", f"Historial Git: un solo commit aporta el {int(g['share'] * 100)}% de las líneas")

    debiles = []
    if EMOJI.search(todo):
        debiles.append("emojis en el código")
    ing = sum(1 for c in ctx["comentarios"] if len(set(re.findall(r"[a-z]+", c.lower())) & ING) >= 2)
    if ing >= 3:
        debiles.append(f"{ing} comentarios en inglés")
    if g["autores"] > 1:
        debiles.append(f"{g['autores']} correos de autor distintos en los commits")
    if len(debiles) >= 2:
        marca("medio", "Indicios de estilo: " + "; ".join(debiles))
    elif debiles:
        obs.append("Indicio débil: " + debiles[0])
    return riesgo, obs, g


# ───────────────────────── Huellas para comparar entre estudiantes ─────────────────────────
def h52(s):
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:13], 16)


def huella(tokens, n, k=200):
    if not tokens:
        return []
    sh = {h52(" ".join(tokens[i:i + n])) for i in range(max(1, len(tokens) - n + 1))}
    return sorted(sh)[:k]


def huellas(ctx):
    doc = ctx["doc"]
    texto = []
    for n in doc.raiz.desc():
        if n.tag not in ("script", "style"):
            texto += re.findall(r"\w+", " ".join(n.texto).lower())
    for c in ctx["comentarios"]:
        texto += re.findall(r"\w+", c.lower())
    estructura = []
    for n in doc.raiz.desc():
        estructura.append(n.tag)
        estructura += sorted(n.attrs)
    css = re.sub(r"/\*.*?\*/", " ", ctx["css"], flags=re.S)
    estructura += re.findall(r"[a-z-]+(?=\s*:)", css)
    return huella(texto, 3), huella(estructura, 6)


# ───────────────────────── Subida a Supabase ─────────────────────────
def hash_grader():
    data = Path(__file__).resolve().read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def subir(payload):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/submit_grade",
        data=json.dumps({"p": payload}).encode("utf-8"),
        headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY,
                 "Authorization": f"Bearer {SUPABASE_ANON_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "msg": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {"ok": False, "msg": f"Error de red: {e}"}


def main():
    if "--hash" in sys.argv:
        print(hash_grader())
        return 0
    dry = "--dry-run" in sys.argv

    try:
        est = json.loads((RAIZ / "estudiante.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("ERROR: estudiante.json no existe o no es JSON válido.")
        return 1
    for k in ("nombre", "apellido", "cedula", "codigo_sesion"):
        est[k] = str(est.get(k, "")).strip()
        if not est[k]:
            print(f"ERROR: falta completar '{k}' en estudiante.json")
            return 1
    if not re.fullmatch(r"[A-Za-z0-9-]{5,20}", est["cedula"]):
        print("ERROR: la cédula tiene un formato inválido (ej. 8-123-456).")
        return 1

    p = perfil(est["cedula"], est["codigo_sesion"])
    crits, ctx = evaluar(est, p)
    total = round(sum(c.pts for c in crits), 2)

    print(f"\n=== Laboratorio #{LAB} · {est['nombre']} {est['apellido']} ===\n")
    for c in crits:
        print(f"[{c.id}] {c.nombre}: {c.pts}/{c.maximo}")
        for ok, pts, msg in c.items:
            print(f"     {'[OK]' if ok else '[--]'} ({pts} pt) {msg}")
    print(f"\nNOTA AUTOMÁTICA: {total}/100")

    riesgo, obs, g = integridad(est, p, ctx)
    sk_texto, sk_estructura = huellas(ctx)
    run_id = os.environ.get("GITHUB_RUN_ID")
    payload = {
        "lab": LAB, "codigo_sesion": est["codigo_sesion"].upper(), "cedula": est["cedula"],
        "nombre": est["nombre"], "apellido": est["apellido"],
        "github_user": os.environ.get("GITHUB_ACTOR", "local"),
        "repo": os.environ.get("GITHUB_REPOSITORY", "local"),
        "commit_sha": os.environ.get("GITHUB_SHA", git("rev-parse", "HEAD").strip()),
        "run_url": (f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
                    f"/actions/runs/{run_id}") if run_id else None,
        "detalle": [{"id": c.id, "nombre": c.nombre, "max": c.maximo, "pts": c.pts} for c in crits],
        "riesgo_local": riesgo, "obs_local": obs, "git": g,
        "sketch_text": sk_texto, "sketch_struct": sk_estructura,
        "grader_sha256": hash_grader(),
    }

    if dry:
        print("\n[dry-run] No se sube nada. Integridad local:", riesgo, obs, g)
        return 0

    res = subir(payload)
    if res.get("ok"):
        print(f"\nEntrega REGISTRADA (intento #{res.get('intentos')}). Nota registrada: {res.get('nota_final')}/100")
        print("La revisión de integridad queda a cargo del docente.")
        resumen = os.environ.get("GITHUB_STEP_SUMMARY")
        if resumen:
            with open(resumen, "a", encoding="utf-8") as f:
                f.write(f"## Laboratorio {LAB}: {total}/100\n\n" + "\n".join(f"- **{c.id}** {c.nombre}: {c.pts}/{c.maximo}" for c in crits) + "\n")
        return 0
    print(f"\nLa entrega NO fue registrada: {res.get('msg')}")
    print("Revisa tu código de sesión, la ventana de entrega o avisa al docente.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
