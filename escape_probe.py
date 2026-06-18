#!/usr/bin/env python3
"""Probe live rapide : 1 GET homepage par enseigne, parallèle, court timeout,
sans retry. Reporte statut HTTP + plateforme détectée. Sert à savoir vite
"qui répond / qui bloque" sans le backoff lent de la vraie discovery."""
import concurrent.futures as cf
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import escape_extension_discover as d

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
brands = json.load(open("escape_extension_brands.json"))["brands"]
# dédoublonne par URL (plusieurs lieux partagent un site)
seen, urls = set(), []
for b in brands:
    if b["url"] not in seen:
        seen.add(b["url"]); urls.append(b)

def probe(b):
    url = b["url"]
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urlopen(req, timeout=8) as r:
            html = r.read(400_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
            plats = d.detect(html)
            return (url, r.status, plats, "")
    except HTTPError as e:
        return (url, e.code, [], "http")
    except URLError as e:
        return (url, None, [], f"url:{getattr(e,'reason',e)}")
    except Exception as e:
        return (url, None, [], f"err:{type(e).__name__}")

results = []
with cf.ThreadPoolExecutor(max_workers=12) as ex:
    for res in ex.map(probe, urls):
        results.append(res)
        u, code, plats, err = res
        tag = "OK " if code == 200 else "XX "
        print(f"{tag}{str(code):>4}  {','.join(plats) or '-':24}  {err:18}  {u}")

ok = [r for r in results if r[1] == 200]
print(f"\n== {len(ok)}/{len(results)} sites répondent 200 ==")
plat_hits = {}
for _, code, plats, _ in results:
    for p in plats:
        plat_hits[p] = plat_hits.get(p, 0) + 1
print("plateformes détectées homepage:", plat_hits or "aucune")
