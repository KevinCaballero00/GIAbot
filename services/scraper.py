import requests
from bs4 import BeautifulSoup

URLS = [
    "https://gia.ufps.edu.co/index/",
    "https://gia.ufps.edu.co/team/",
    "https://gia.ufps.edu.co/semilleros/",
    "https://gia.ufps.edu.co/servicios/",
    "https://gia.ufps.edu.co/proyectos/",
    "https://gia.ufps.edu.co/contacto/",
    "https://gia.ufps.edu.co/about/",
]

def scrape_pagina(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # Elimina scripts, estilos y nav
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return f"Error al obtener {url}: {e}"

def obtener_contexto_web():
    textos = []
    for url in URLS:
        contenido = scrape_pagina(url)
        textos.append(f"--- Página: {url} ---\n{contenido}\n")
    return "\n".join(textos)

# Se ejecuta una sola vez al importar
CONTEXTO_WEB = obtener_contexto_web()
#________________________________________________________________________#

import requests
from bs4 import BeautifulSoup
import re

def scrapInv(url="https://gia.ufps.edu.co/team/"):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; scrapInv/1.0; +https://example.com)"
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    # Estrategia 1: buscar bloques típicos de perfiles
    candidates = soup.select("article, .team, .team-member, .member, .card, .profile, li, div")

    seen = set()
    for node in candidates:
        text = " ".join(node.get_text(" ", strip=True).split())
        if len(text) < 20:
            continue

        links = []
        for a in node.select("a[href]"):
            href = a.get("href", "").strip()
            if href:
                links.append(href)

        emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)

        data = {
            "nombre": None,
            "titulo": None,
            "rol": None,
            "biografia": None,
            "contacto": emails[0] if emails else None,
            "enlaces": list(dict.fromkeys(links)),
            "html_id": node.get("id"),
            "data_attrs": {k: v for k, v in node.attrs.items() if k.startswith("data-")},
            "texto": text
        }

        # Heurísticas simples para nombre/rol/título
        name_tag = node.select_one("h1, h2, h3, h4, .name, .title, .member-name")
        if name_tag:
            data["nombre"] = " ".join(name_tag.get_text(" ", strip=True).split())

        role_tag = node.select_one(".role, .position, .job, .member-role")
        if role_tag:
            data["rol"] = " ".join(role_tag.get_text(" ", strip=True).split())

        bio_tag = node.select_one("p, .bio, .description, .excerpt")
        if bio_tag:
            data["biografia"] = " ".join(bio_tag.get_text(" ", strip=True).split())

        # Filtrar duplicados vacíos
        key = (data["nombre"], data["rol"], tuple(data["enlaces"]))
        if key not in seen and (data["nombre"] or data["rol"] or data["enlaces"]):
            seen.add(key)
            items.append(data)

    return items