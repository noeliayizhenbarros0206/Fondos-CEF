"""Monitor de noticias CINEA para CEF Energy (PCI/PMI y CB-RES).

Cada ejecución:
1. Lee la portada de noticias de CINEA.
2. Para cada noticia nueva, abre el artículo, la clasifica (línea y tipo) y extrae el importe.
3. Guarda las novedades en noticias.json como "pendientes de validar".
4. Genera docs/index.html con las cifras verificadas (fondos.json) y las novedades.

Las cifras verificadas solo cambian cuando tú editas fondos.json.
"""
import html, json, os, re, sys
from datetime import date
from pathlib import Path
import requests

NEWS_URL = "https://cinea.ec.europa.eu/news-events/news_en"
BASE = "https://cinea.ec.europa.eu"
TOPIC = os.environ.get("NTFY_TOPIC", "")
DATA, SEEN, NEWS, PAGE = Path("fondos.json"), Path("vistas.json"), Path("noticias.json"), Path("docs/index.html")
UA = {"User-Agent": "fondos-monitor/2.0"}

# Clasificación. Más adelante se pueden añadir CEF Transport, Horizon Europe, LIFE...
LINEAS = [
    ("CB-RES", ["cb res", "cb-res", "cross-border renewable", "cross border renewable"]),
    ("PCI/PMI", ["pci", "pmi", "projects of common interest", "projects of mutual interest",
                 "cross-border energy infrastructure", "energy infrastructure"]),
]
PROGRAMA = ["cef energy", "connecting europe facility for energy", "cb res", "pci", "pmi",
            "cross-border renewable", "energy infrastructure"]
TIPOS = [  # el orden importa: se aplica el primero que coincide
    ("estatus (no suma)", ["status", "union list", "project list"]),
    ("firma de acuerdos (no suma)", ["grant agreement", "signed", "signature"]),
    ("solicitudes (no suma)", ["applications", "requested", "proposals received", "attracts"]),
    ("convocatoria", ["call", "launch", "opens", "available"]),
    ("adjudicación", ["allocated", "invests", "awarded", "selected", "grants for", "of grants", "to support"]),
]
IMPORTE = re.compile(r"(?:€|EUR)\s?([\d][\d.,]*)\s?(billion|million|bn|m)\b", re.I)


def get(url):
    r = requests.get(url, timeout=60, headers=UA)
    r.raise_for_status()
    return r.text


def texto(html_txt):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_txt)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def portada():
    items = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"]*/news-events/news/[^"]+)"[^>]*>(.*?)</a>',
                                  get(NEWS_URL), flags=re.S | re.I):
        titulo = texto(inner)
        if titulo:
            items[href if href.startswith("http") else BASE + href] = titulo
    return items


def a_millones(num, unidad):
    n = num.replace(" ", "")
    if "," in n and "." in n:
        n = n.replace(",", "")                 # 18,755,000.5 -> 18755000.5
    elif n.count(",") > 1 or re.fullmatch(r"\d{1,3}(,\d{3})+", n):
        n = n.replace(",", "")                 # 18,755,000
    else:
        n = n.replace(",", ".")                # 22,1 -> 22.1
    if n.count(".") > 1:
        n = n.replace(".", "")
    v = float(n)
    return round(v * 1000, 2) if unidad.lower() in ("billion", "bn") else round(v, 4)


def clasificar(titulo, cuerpo):
    t = (titulo + " " + cuerpo[:1500]).lower()
    if not any(k in t for k in PROGRAMA):
        return None
    linea = next((n for n, ks in LINEAS if any(k in t for k in ks)), "CEF Energy (sin línea)")
    tt = titulo.lower()
    tipo = next((n for n, ks in TIPOS if any(k in tt for k in ks)), None) \
        or next((n for n, ks in TIPOS if any(k in t for k in ks)), "otra")
    return linea, tipo


def importe(cuerpo):
    """Primer importe del texto, y si el texto lo marca como aproximado."""
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
                      headers={"Title": "Fondos CEF Energy", "Click": NEWS_URL}, timeout=30)


def fmt(x, d=4):
    s = f"{x:,.{d}f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pagina(hist):
    d = json.loads(DATA.read_text(encoding="utf8"))
    it, base = d["items"], d["presupuesto_total"]
    tot = lambda l, t: sum(i["importe"] for i in it if (l is None or i["linea"] == l) and i["tipo"] == t)
    conv, adj = tot(None, "convocado"), tot(None, "adjudicado")
    nuevas_adj = [n for n in hist if n["tipo"] == "adjudicación" and n["importe"] and n["estado"] == "pendiente de validar"]
    prov = adj + sum(n["importe"] for n in nuevas_adj)

    def tabla(linea, tipo):
        filas = "".join(f"<tr><td>{i['anio']}</td><td class=n>{fmt(i['importe'])}</td><td>{html.escape(i['fuente'])}</td>"
                        f"<td>{html.escape(i.get('nota',''))}</td></tr>"
                        for i in it if i["linea"] == linea and i["tipo"] == tipo)
        return (f"<table><tr><th>Año</th><th>M€</th><th>Fuente</th><th>Nota</th></tr>{filas}"
                f"<tr class=t><td>Total</td><td class=n>{fmt(tot(linea, tipo))}</td><td></td><td></td></tr></table>")

    filas_n = "".join(
        f"<tr><td>{n['fecha']}</td><td>{n['linea']}</td><td>{n['tipo']}</td>"
        f"<td class=n>{fmt(n['importe']) if n['importe'] else '—'}{' (aprox.)' if n.get('aprox') else ''}</td>"
        f"<td><a href='{n['url']}'>{html.escape(n['titulo'])}</a></td><td>{n['estado']}</td></tr>" for n in hist
    ) or "<tr><td colspan=6>Sin novedades de CEF Energy desde la última revisión.</td></tr>"

    aviso = (f"<p class=warn>Hay {len(nuevas_adj)} adjudicación(es) nueva(s) sin validar. Si se confirman, "
             f"el pendiente de conceder pasaría a <b>{fmt(base - prov, 2)} M€</b>.</p>") if nuevas_adj else ""

    PAGE.parent.mkdir(exist_ok=True)
    PAGE.write_text(f"""<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Fondos CEF Energy</title>
<style>body{{font-family:system-ui,sans-serif;margin:0 auto;padding:1.5em;max-width:1100px;color:#222}}
h1{{margin-bottom:.2em}}.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.8em;margin:1em 0}}
.k{{border:1px solid #ddd;border-radius:8px;padding:.7em}}.k b{{display:block;font-size:1.4em}}
table{{border-collapse:collapse;width:100%;margin:.5em 0 1.5em}}td,th{{border-bottom:1px solid #e3e3e3;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#f4f6f8}}.n{{text-align:right;white-space:nowrap}}.t td{{font-weight:bold}}
.warn{{background:#fff4e5;border-left:4px solid #f0a020;padding:.6em}}small{{color:#666}}</style></head><body>
<h1>Fondos CEF Energy 2021-2027</h1><small>Cifras verificadas actualizadas el {d['actualizado']}. Revisión de noticias: {date.today().isoformat()}. Importes en M€.</small>
<div class=kpis>
<div class=k>Presupuesto<b>{fmt(base, 2)}</b></div>
<div class=k>Convocado<b>{fmt(conv, 2)}</b><small>PCI/PMI {fmt(tot('PCI/PMI','convocado'), 2)} · CB-RES {fmt(tot('CB-RES','convocado'), 2)}</small></div>
<div class=k>Adjudicado<b>{fmt(adj, 2)}</b><small>PCI/PMI {fmt(tot('PCI/PMI','adjudicado'), 2)} · CB-RES {fmt(tot('CB-RES','adjudicado'), 2)}</small></div>
<div class=k>Pendiente de convocar<b>{fmt(base - conv, 2)}</b></div>
<div class=k>Pendiente de conceder<b>{fmt(base - adj, 2)}</b></div></div>
{aviso}
<h2>Novedades detectadas en CINEA</h2>
<table><tr><th>Detectada</th><th>Línea</th><th>Tipo</th><th>Importe</th><th>Noticia</th><th>Estado</th></tr>{filas_n}</table>
<h2>PCI/PMI</h2><h3>Convocado</h3>{tabla('PCI/PMI','convocado')}<h3>Adjudicado</h3>{tabla('PCI/PMI','adjudicado')}
<h2>CB-RES</h2><h3>Convocado</h3>{tabla('CB-RES','convocado')}<h3>Adjudicado</h3>{tabla('CB-RES','adjudicado')}
<p><small>Criterio: solo importes del texto de noticias oficiales de CINEA o la Comisión; si el texto es aproximado, se usa la suma de proyectos.
Pendiente de convocar = presupuesto − convocado. Pendiente de conceder = presupuesto − adjudicado. Las novedades se detectan automáticamente
y no cambian las cifras hasta validarlas en fondos.json.</small></p></body></html>""", encoding="utf8")


def main():
    noticias = portada()
    print(f"Noticias en portada: {len(noticias)}")
    if not noticias:
        notificar("Monitor CEF: no se han podido leer las noticias de CINEA. Revisar check.py.")
        sys.exit(1)
    vistas = set(json.loads(SEEN.read_text(encoding="utf8"))) if SEEN.exists() else set()
    hist = json.loads(NEWS.read_text(encoding="utf8")) if NEWS.exists() else []
    primera = not vistas
    nuevas = []
    for url, titulo in noticias.items():
        if url in vistas:
            continue
        try:
            cuerpo = texto(get(url))
        except Exception as e:
            print("No se pudo abrir", url, e); cuerpo = ""
        c = clasificar(titulo, cuerpo)
        if not c:
            continue
        imp, aprox = importe(cuerpo)
        n = {"fecha": date.today().isoformat(), "titulo": titulo, "url": url, "linea": c[0], "tipo": c[1],
             "importe": imp, "aprox": aprox,
             "estado": "pendiente de validar" if "no suma" not in c[1] else "informativa"}
        nuevas.append(n)
        print("CEF Energy:", n)
    if nuevas and not primera:
        notificar("Novedades CEF Energy:\n" + "\n".join(
            f"- [{n['linea']} · {n['tipo']}{' · ' + fmt(n['importe']) + ' M€' if n['importe'] else ''}] {n['titulo']}"
            for n in nuevas)[:3500])
    hist = (nuevas + hist)[:100]
    NEWS.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf8")
    SEEN.write_text(json.dumps(sorted(vistas | set(noticias)), ensure_ascii=False, indent=1), encoding="utf8")
    pagina(hist)


if __name__ == "__main__":
    main()
