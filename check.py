"""Monitor de noticias de CINEA para varios programas (CEF Energy, CEF Transport...).

Cada ejecución:
1. Lee la portada de noticias de CINEA.
2. Abre cada noticia nueva, detecta programa, línea y tipo, y extrae el importe del texto.
3. Guarda las novedades en noticias.json como "pendientes de validar".
4. Genera docs/data.json con las cifras verificadas (carpeta data/) y las novedades.
   La web (docs/index.html) lee ese archivo y permite filtrar y agrupar.

Para añadir un programa: añade un bloque en programas.json y su archivo en data/.
Las cifras verificadas solo cambian cuando editas los archivos de data/.
"""
import html, json, os, re, sys
from datetime import date
from pathlib import Path
import requests

NEWS_URL = "https://cinea.ec.europa.eu/news-events/news_en"
BASE = "https://cinea.ec.europa.eu"
TOPIC = os.environ.get("NTFY_TOPIC", "")
CONFIG, SEEN, NEWS, OUT = Path("programas.json"), Path("vistas.json"), Path("noticias.json"), Path("docs/data.json")
UA = {"User-Agent": "fondos-monitor/3.0"}

TIPOS = [  # el orden importa: se aplica el primero que coincide
    ("estatus (no suma)", ["status", "union list", "project list"]),
    ("firma de acuerdos (no suma)", ["grant agreement", "signed", "signature"]),
    ("solicitudes (no suma)", ["applications", "requested", "proposals received", "attracts"]),
    ("convocatoria", ["call", "launch", "opens", "available"]),
    ("adjudicación", ["allocated", "invests", "awarded", "selected", "grants for", "of grants", "to support"]),
]
IMPORTE = re.compile(r"(?:€|EUR)\s?(\d[\d.,]*)\s?(billion|million|bn|m)\b", re.I)


def get(url):
    r = requests.get(url, timeout=60, headers=UA)
    r.raise_for_status()
    return r.text


def texto(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def portada():
    items = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"]*/news-events/news/[^"]+)"[^>]*>(.*?)</a>',
                                  get(NEWS_URL), flags=re.S | re.I):
        t = texto(inner)
        if t:
            items[href if href.startswith("http") else BASE + href] = t
    return items


def a_millones(num, unidad):
    n = num.replace(" ", "")
    if "," in n and "." in n:
        n = n.replace(",", "")
    elif n.count(",") > 1 or re.fullmatch(r"\d{1,3}(,\d{3})+", n):
        n = n.replace(",", "")
    else:
        n = n.replace(",", ".")
    if n.count(".") > 1:
        n = n.replace(".", "")
    v = float(n)
    return round(v * 1000, 2) if unidad.lower() in ("billion", "bn") else round(v, 4)


def puntos(t, claves):
    return sum(t.count(k) for k in claves)


def clasificar(titulo, cuerpo, programas):
    """Devuelve (programa_id, linea, tipo) o None si la noticia no es de ningún programa."""
    tt, t = titulo.lower(), (titulo + " " + cuerpo[:2000]).lower()
    mejor = max(programas, key=lambda p: (puntos(tt, p["palabras_clave"]) * 3 + puntos(t, p["palabras_clave"])))
    if puntos(t, mejor["palabras_clave"]) == 0:
        return None
    linea = next((l["nombre"] for l in mejor["lineas"] if any(k in t for k in l["palabras_clave"])),
                 "Sin línea")
    tipo = next((n for n, ks in TIPOS if any(k in tt for k in ks)), None) \
        or next((n for n, ks in TIPOS if any(k in t for k in ks)), "otra")
    return mejor["id"], linea, tipo


def importe(cuerpo):
    m = IMPORTE.search(cuerpo)
    if not m:
        return None, False
    antes = cuerpo[max(0, m.start() - 40):m.start()].lower()
    aprox = any(w in antes for w in ["approximately", "around", "almost", "nearly", "over", "more than"])
    return a_millones(m.group(1), m.group(2)), aprox


def notificar(msg):
    print(msg)
    if TOPIC:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode("utf8"),
                      headers={"Title": "Fondos UE - CINEA", "Click": NEWS_URL}, timeout=30)


def exportar(programas, hist):
    salida = {"generado": date.today().isoformat(), "programas": [], "items": [], "novedades": hist}
    for p in programas:
        d = json.loads(Path(p["datos"]).read_text(encoding="utf8"))
        salida["programas"].append({"id": p["id"], "nombre": p["nombre"],
                                    "presupuesto_total": d.get("presupuesto_total"),
                                    "fuente_presupuesto": d.get("fuente_presupuesto"),
                                    "actualizado": d.get("actualizado"),
                                    "lineas": [l["nombre"] for l in p["lineas"]]})
        for i in d["items"]:
            salida["items"].append({**i, "programa": p["id"]})
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf8")


def main():
    programas = json.loads(CONFIG.read_text(encoding="utf8"))["programas"]
    noticias = portada()
    print(f"Noticias en portada: {len(noticias)}")
    if not noticias:
        notificar("Monitor CINEA: no se han podido leer las noticias. Revisar check.py.")
        sys.exit(1)
    vistas = set(json.loads(SEEN.read_text(encoding="utf8"))) if SEEN.exists() else set()
    hist = json.loads(NEWS.read_text(encoding="utf8")) if NEWS.exists() else []
    for n in hist:  # novedades guardadas con la versión anterior
        n.setdefault("programa", "cef-energy"); n.setdefault("linea", "Sin línea"); n.setdefault("tipo", "otra")
        n.setdefault("importe", None); n.setdefault("estado", "pendiente de validar")
    primera, nuevas = not vistas, []
    for url, titulo in noticias.items():
        if url in vistas:
            continue
        try:
            cuerpo = texto(get(url))
        except Exception as e:
            print("No se pudo abrir", url, e); cuerpo = ""
        c = clasificar(titulo, cuerpo, programas)
        if not c:
            continue
        imp, aprox = importe(cuerpo)
        n = {"fecha": date.today().isoformat(), "titulo": titulo, "url": url,
             "programa": c[0], "linea": c[1], "tipo": c[2], "importe": imp, "aprox": aprox,
             "estado": "informativa" if "no suma" in c[2] else "pendiente de validar"}
        nuevas.append(n)
        print("Detectada:", n)
    if nuevas and not primera:
        nombres = {p["id"]: p["nombre"] for p in programas}
        notificar("Novedades CINEA:\n" + "\n".join(
            f"- [{nombres[n['programa']]} · {n['linea']} · {n['tipo']}"
            f"{' · ' + str(n['importe']) + ' M€' if n['importe'] else ''}] {n['titulo']}" for n in nuevas)[:3500])
    hist = (nuevas + hist)[:200]
    NEWS.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf8")
    SEEN.write_text(json.dumps(sorted(vistas | set(noticias)), ensure_ascii=False, indent=1), encoding="utf8")
    exportar(programas, hist)


if __name__ == "__main__":
    main()
