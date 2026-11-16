import json, re, os, sys, html
from pathlib import Path
import requests

NEWS_URL = "https://cinea.ec.europa.eu/news-events/news_en"
TOPIC = os.environ.get("NTFY_TOPIC", "")
KEYWORDS = ["cef energy", "cb res", "cb-res", "pci", "pmi", "renewable", "hydrogen", "energy infrastructure"]
DATA, SEEN, PAGE = Path("fondos.json"), Path("vistas.json"), Path("docs/index.html")

def fetch_news():
    r = requests.get(NEWS_URL, timeout=60, headers={"User-Agent": "fondos-monitor/1.0"})
    r.raise_for_status()
    items = {}
    pattern = r'<a[^>]+href="([^"]*/news-events/news/[^"]+)"[^>]*>(.*?)</a>'
    for href, inner in re.findall(pattern, r.text, flags=re.S | re.I):
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if not title:
            continue
        url = href if href.startswith("http") else "https://cinea.ec.europa.eu" + href
        items[url] = title
    return items

def notify(msg):
    print(msg)
    if TOPIC:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode("utf8"),
                      headers={"Title": "Monitor fondos CEF", "Click": NEWS_URL}, timeout=30)

def build_page():
    d = json.loads(DATA.read_text(encoding="utf8"))
    items = d["items"]
    def total(prog, tipo):
        return round(sum(i["importe"] for i in items if i["programa"] == prog and i["tipo"] == tipo), 2)
    conv = round(total("PCI/PMI", "convocado") + total("CB-RES", "convocado"), 2)
    adj = round(total("PCI/PMI", "adjudicado") + total("CB-RES", "adjudicado"), 2)
    base = d["presupuesto_total"]
    rows = "".join(
        f"<tr><td>{i['programa']}</td><td>{i['tipo']}</td><td>{i['año']}</td>"
        f"<td>{i['importe']}</td><td>{i['fuente']}</td><td>{'Sí' if i['verificado'] else 'No'}</td></tr>"
        for i in items)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Fondos CEF Energía</title>
<style>body{{font-family:sans-serif;margin:2em}}table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 8px}}</style>
</head><body><h1>Fondos CEF Energía (M€)</h1>
<p>Presupuesto total: {base} · Total convocado: {conv} · Total adjudicado: {adj}</p>
<p><b>Pendiente de convocar:</b> {round(base - conv, 2)} · <b>Pendiente de conceder:</b> {round(base - adj, 2)}</p>
<table><tr><th>Programa</th><th>Tipo</th><th>Año</th><th>Importe</th><th>Fuente</th><th>Verificado</th></tr>{rows}</table>
<p>Actualizado: ejecución automática. Última comprobación de noticias: ver registro de GitHub Actions.</p>
</body></html>"""
    PAGE.parent.mkdir(exist_ok=True)
    PAGE.write_text(page, encoding="utf8")

def main():
    news = fetch_news()
    print(f"Noticias encontradas: {len(news)}")
    if not news:
        notify("Monitor fondos: no se encontraron noticias. Revisar check.py.")
        sys.exit(1)
    seen = set(json.loads(SEEN.read_text(encoding="utf8"))) if SEEN.exists() else set()
    new = {u: t for u, t in news.items() if u not in seen}
    relevant = {u: t for u, t in new.items() if any(k in t.lower() for k in KEYWORDS)}
    # En la primera ejecución (lista vacía) solo se guarda la base, sin avisar.
    if seen and relevant:
        lines = [f"- {t}\n  {u}" for u, t in relevant.items()]
        notify("Nuevas noticias relevantes:\n" + "\n".join(lines)[:3500])
    SEEN.write_text(json.dumps(sorted(seen | set(news)), ensure_ascii=False, indent=1), encoding="utf8")
    build_page()

if __name__ == "__main__":
    main()
