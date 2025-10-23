# -*- coding: utf-8 -*-
"""
Adaptador Infortisa -> lista de items para Odoo.
Usa tu APP KEY desde INFORTISA_APP_KEY (entorno) o pásala como parámetro.
"""

import os, io, csv, re, unicodedata
from urllib.parse import urlparse
import requests

BASE = "https://apiv2.infortisa.com"
CSV_EXT_URL = f"{BASE}/api/Tarifa/GetFileV5EXT"

def _strip_accents(s):
    if s is None:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 128)

def _to_float_es(val):
    if val is None:
        return 0.0
    s = (str(val).replace("\u00a0", " ").strip()
         .replace(" ", "").replace(".", "").replace(",", "."))
    try:
        return float(s) if s else 0.0
    except Exception:
        m = re.search(r"[-+]?\d*\.?\d+", s)
        return float(m.group()) if m else 0.0

def _to_int(val):
    try:
        return int(round(float(str(val).replace(",", ".").strip())))
    except Exception:
        return 0

def _g(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""

def _sum_stock(r):
    return _to_int(r.get("STOCKCENTRAL")) + _to_int(r.get("STOCKPALMA")) + _to_int(r.get("STOCKEXTERNO"))

def _normalize_image_url(url: str):
    if not url:
        return None
    s = str(url).strip()
    try:
        urlparse(s)  # valida superficialmente
    except Exception:
        pass
    return s or None

def _fetch_csv(app_key: str, mode="query", header_name="X-Api-Key") -> str:
    params = {}; headers = {}
    if mode == "query":
        params["user"] = app_key
    elif mode == "header":
        headers[header_name] = app_key
    elif mode == "bearer":
        headers["Authorization"] = f"Bearer {app_key}"
    r = requests.get(CSV_EXT_URL, params=params, headers=headers, timeout=120)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def get_items(app_key=None, auth_mode="query", header_name="X-Api-Key",
              min_stock=1, limit=None, vendor_name=None, **kwargs):
    """
    Retorna list[dict] con:
      name, sku, cost(=precio CSV), list_price(opcional), barcode,
      image_url(obligatoria), category, vendor_code, vendor_name.
    """
    try:
        min_stock = int(min_stock or 0)
    except Exception:
        min_stock = 0
    try:
        lim = int(limit) if limit is not None else None
    except Exception:
        lim = None

    app_key = (app_key or os.getenv("INFORTISA_APP_KEY") or "").strip()
    if not app_key:
        raise RuntimeError("Falta APP KEY (INFORTISA_APP_KEY o parámetro).")

    csv_text = _fetch_csv(app_key, mode=auth_mode, header_name=header_name)
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")

    items = []
    for row in reader:
        try:
            name = _g(row, "TITULO")
            if not name:
                continue

            price = _to_float_es(row.get("PRECIO"))
            stock_total = _sum_stock(row)
            if price <= 0 or stock_total < min_stock:
                continue

            sku = _g(row, "CODIGOINTERNO", "CODIGO")
            barcode = _g(row, "EAN/UPC", "EAN")

            img = _g(row, "IMAGEN")
            image_url = _normalize_image_url(img)
            if not image_url:
                continue  # descartar sin imagen

            category = _strip_accents(_g(row, "TITULOSUBFAMILIA")) or None

            items.append({
                "name": name,
                "sku": sku or barcode,
                "cost": float(f"{price:.2f}"),  # PRECIO CSV -> COSTE Odoo
                "list_price": None,            # opcional: no tocamos PV si None
                "barcode": barcode or None,
                "image_url": image_url,
                "category": category,
                "vendor_code": sku or None,
                "vendor_name": (vendor_name or "").strip() or None,
            })

            if lim and len(items) >= lim:
                break
        except Exception:
            continue

    return items

# ====== [AUTO-INJECT v2] category map inside get_items ======================
import io as _io, csv as _csv, re as _re, unicodedata as _ud, os as _os

# Guardamos la función get_items ORIGINAL antes de redefinirla
try:
    _ORIG_GET_ITEMS_CM2 = get_items  # referencia a la función previa
except Exception:
    _ORIG_GET_ITEMS_CM2 = None

def _cm2_strip(_s):
    if _s is None: return ""
    _s = str(_s)
    _s = _ud.normalize("NFKD", _s)
    return "".join(c for c in _s if not _ud.combining(c))

def _cm2_norm(_s):
    return _re.sub(r"\s+", " ", _cm2_strip(_s).lower().strip())

def _cm2_read(path: str) -> str:
    last = None
    for enc in ("utf-8","utf-8-sig","cp1252","latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception as e:
            last = e
            continue
    if last: raise last
    return ""

def _cm2_reader(text: str):
    sample = text[:4096]
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=";,|\t,")
    except Exception:
        class _D: delimiter = ';'
        dialect = _D()
    return _csv.DictReader(_io.StringIO(text), dialect=dialect)

def _cm2_load_map(*, text: str = None, path: str = None):
    """Devuelve dict { norm('Categoría de producto') : 'Categoría final' }."""
    mapping = {}
    try:
        if not (text or path): return mapping
        if path and not text and _os.path.exists(path):
            text = _cm2_read(path)
        if not text: return mapping
        r = _cm2_reader(text)
        fns = [ (c or "").strip() for c in (r.fieldnames or []) ]
        low = { c.lower(): c for c in fns }
        src = low.get("categoría de producto") or low.get("categoria de producto") or low.get("categoria") or next(iter(low.values()), None)
        dst = low.get("categoría final") or low.get("categoria final") or low.get("final")
        if not (src and dst): return mapping
        for row in r:
            s = (row.get(src) or "").strip()
            d = (row.get(dst) or "").strip()
            if s:
                mapping[_cm2_norm(s)] = d or s
    except Exception:
        return {}
    return mapping

def get_items(app_key=None, auth_mode="query", header_name="X-Api-Key",
              min_stock=1, limit=None, vendor_name=None,
              category_map_csv=None, category_map_path=None, **kwargs):
    """
    MISMA firma que get_items original + parámetros opcionales de mapeo.
    """
    orig = _ORIG_GET_ITEMS_CM2
    if not callable(orig):
        return []  # si no encontramos la original, devolvemos vacío

    # 1) Traer items originales
    try:
        items = orig(app_key=app_key, auth_mode=auth_mode, header_name=header_name,
                     min_stock=min_stock, limit=limit, vendor_name=vendor_name, **kwargs) or []
    except Exception:
        return []

    # 2) Cargar mapeo
    cmap = _cm2_load_map(text=category_map_csv, path=category_map_path)
    if not cmap:
        try:
            base_dir = _os.path.dirname(_os.path.dirname(__file__))  # .../vendor_catalog_import
            default_csv = _os.path.join(base_dir, "data", "infortisa_odoo_agrupado__mapping.csv")
            cmap = _cm2_load_map(path=default_csv)
        except Exception:
            cmap = {}

    # 3) Aplicar mapeo
    if cmap:
        for it in items:
            try:
                orig_cat = it.get("category") or ""
                new_cat  = cmap.get(_cm2_norm(orig_cat))
                if new_cat:
                    it["category"] = new_cat
            except Exception:
                continue
    return items
# ====== [END AUTO-INJECT] ====================================================
