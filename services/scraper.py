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