# -*- coding: utf-8 -*-
import os, io, csv, re, unicodedata
from urllib.parse import urlparse
import requests

BASE = "https://apiv2.infortisa.com"
CSV_EXT_URL = f"{BASE}/api/Tarifa/GetFileV5EXT"

# ---------- util ----------
def _strip_accents(s):
    if s is None: return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 128)

def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower().strip())

def _to_float_es(val):
    if val is None:
        return 0.0
    s = str(val).replace("\u00a0"," ").strip()
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s) if s else 0.0
    except Exception:
        m = re.search(r"[-+]?\d*\.?\d+", s or "")
        return float(m.group()) if m else 0.0

def _to_int(val):
    try:
        return int(round(float(str(val).replace(",", ".").strip())))
    except Exception:
        return 0

def _peso_from_row(row):
    """Devuelve el peso en **kg** leyendo la columna PESO (o variantes)."""
    raw = (row.get('PESO') or row.get('Peso') or row.get('peso') or
           row.get('WEIGHT') or row.get('Weight') or row.get('weight') or '')
    raw = str(raw).strip()
    if not raw:
        return 0.0
    txt = raw.replace(",", ".").lower()
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)', txt)
    if not m:
        try:
            return float(txt)
        except Exception:
            return 0.0
    val = float(m.group(1))
    unit = m.group(2)
    if unit.startswith('g'):
        val /= 1000.0
    return round(val, 6)

def _col(row, candidates):
    """Busca la primera columna existente (ignorando tildes/mayús/espacios)."""
    keys = {_norm_key(k): k for k in row.keys()}
    for c in candidates:
        k = keys.get(_norm_key(c))
        if k is not None:
            v = row.get(k)
            if v not in (None, "", "null", "None"):
                return str(v).strip()
    return ""

def _sum_stock(r):
    # Suma los 3 campos clásicos y cae a otros nombres si no existen
    total = 0
    for k in ("STOCKCENTRAL","STOCKPALMA","STOCKEXTERNO","STOCK","STOCKWEB","UNIDADES","DISPONIBLE","DISPONIBLES"):
        total += _to_int(r.get(k))
    if total:
        return total
    # última oportunidad: cualquier campo que huela a "stock" o "dispon"
    for k, v in r.items():
        nk = _norm_key(k)
        if "stock" in nk or "dispon" in nk:
            total += _to_int(v)
    return total

def _normalize_image_url(url: str):
    if not url: return None
    s = str(url).strip()
    try: urlparse(s)
    except Exception: pass
    return s or None

def _desc_to_html(raw: str) -> str:
    if not raw: return ""
    txt = str(raw).replace("\r\n","\n").replace("\r","\n").strip()
    if "<" in txt and ">" in txt:
        return txt
    parts = [p.strip() for p in re.split(r"\s*[•\-]\s+|\n+", txt) if p.strip()]
    return "<br/>".join(parts) if parts else txt

def _read_csv_sniff(text: str) -> csv.DictReader:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except Exception:
        class _D: delimiter = ';'
        dialect = _D()
    return csv.DictReader(io.StringIO(text), dialect=dialect)

# ---------- fetch ----------
def _fetch_csv(app_key: str, mode="query", header_name: str = "X-Api-Key"):
    params, headers = {}, {}
    if mode == "query": params["user"] = app_key
    elif mode == "header": headers[header_name] = app_key
    elif mode == "bearer": headers["Authorization"] = f"Bearer {app_key}"
    r = requests.get(CSV_EXT_URL, params=params, headers=headers, timeout=120)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text

# ---------- entrypoint ----------
def get_items(app_key=None, auth_mode="query", header_name="X-Api-Key",
              min_stock=1, limit=None, vendor_name=None, **kwargs):
    app_key = (app_key or os.getenv("INFORTISA_APP_KEY") or "").strip()
    if not app_key:
        raise RuntimeError("Falta APP KEY (INFORTISA_APP_KEY o parámetro).")

    try: min_stock = int(min_stock or 0)
    except Exception: min_stock = 0
    lim = int(limit) if (isinstance(limit, (int, str)) and str(limit).isdigit()) else None

    csv_text = _fetch_csv(app_key, mode=auth_mode, header_name=header_name)
    reader = _read_csv_sniff(csv_text)

    NAME = ['titulo','título','name','title']
    CODE = ['codigointerno','codigo','código','sku','ref','referencia','productcode','itemcode']
    BAR  = ['ean/upc','ean','barcode','código barras','codigo barras']
    IMG  = ['imagen','image','image_url','urlimagen','foto']
    DESC = ['ficha','descripcion','descripción','description','desc']
    FAM1 = ['titulofamilia','familia']
    FAM2 = ['titulosubfamilia','subfamilia']
    FAM3 = ['tituloseccion','seccion','sección']
    PRICE = ['precio','pvd','pvp','precio venta','precioventa','precio_base','precio unitario','coste']

    URLP = ['product_url','url','web_url','link','enlace','urlproducto','url producto','pagina','página']

    def _c(r, cands): return _col(r, cands)

    items = []
    for row in reader:
        try:
            name = _c(row, NAME)
            if not name:
                continue

            price = _to_float_es(_c(row, PRICE) or row.get("PRECIO"))
            stock_total = _sum_stock(row)
            if price <= 0 or stock_total < min_stock:
                continue

            sku = _c(row, CODE)
            barcode = _c(row, BAR)
            image_url = _normalize_image_url(_c(row, IMG))
            if not image_url:
                # mantener tu política: sin imagen => descartar
                continue

            desc_html = _desc_to_html(_c(row, DESC))
            category = (_c(row, FAM2) or _c(row, FAM1) or _c(row, FAM3)) or None

            # extra: URLs adicionales si existieran
            raw_extra = (row.get('IMAGENES_ADICIONALES') or row.get('Imagenes_Adicionales') or row.get('imagenes_adicionales') or '')
            extra_urls = []
            if raw_extra:
                parts = re.split(r'[|\n;, \t]+', str(raw_extra).strip())
                for u in parts:
                    u = u.strip()
                    if not u: continue
                    if u.startswith('//'): u = 'https:' + u
                    if u.startswith(('http://','https://')) and u != image_url and u not in extra_urls:
                        extra_urls.append(u)

            item = {
                "name": name,
                "sku": sku or barcode,
                "cost": float(f"{price:.2f}"),
                "list_price": None,
                "barcode": barcode or None,
                "image_url": image_url,
                "category": category,
                "vendor_code": sku or None,
                "vendor_name": (vendor_name or "Infortisa").strip(),
                "vendor_stock": int(stock_total),
                "weight": _peso_from_row(row)
        }
            prod_url = _c(row, URLP)
            if prod_url:
                item['product_url'] = prod_url.strip()

            if desc_html:
                item["description_ecommerce"] = desc_html
                item["website_description"] = desc_html
            if extra_urls:
                item["images"] = extra_urls
                item["gallery"] = extra_urls
                item["image_urls"] = extra_urls

            items.append(item)
            if lim and len(items) >= lim:
                break
        except Exception:
            continue
    return items

def get_items_mapped(**kwargs):
    return get_items(**kwargs)

# ===================== [PATCH] Category mapping helpers =====================
import io as _mm_io, csv as _mm_csv, re as _mm_re, os as _mm_os, unicodedata as _mm_unic

def _mm_norm_key(s: str) -> str:
    if s is None:
        return ""
    nfkd = _mm_unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in nfkd if not _mm_unicodedata.combining(ch) and ord(ch) < 128)
    s = s.lower().strip()
    return _mm_re.sub(r"\s+", " ", s)

def _mm_read_file(path: str) -> str:
    last = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception as e:
            last = e
    raise last

def _mm_csv_reader(text: str) -> _mm_csv.DictReader:
    sample = text[:4096]
    try:
        dialect = _mm_csv.Sniffer().sniff(sample, delimiters=";,|\t")
    except Exception:
        class _D: delimiter = ';'
        dialect = _D()
    return _mm_csv.DictReader(_mm_io.StringIO(text), dialect=dialect)

def _mm_load_cat_map(path: str) -> dict:
    if not path:
        return {}
    try:
        txt = _mm_read_file(path)
        rdr = _mm_csv_reader(txt)
        fl = { (c or "").strip().lower(): c for c in (rdr.fieldnames or []) }
        src = fl.get("categoría de producto") or fl.get("categoria de producto") or fl.get("categoria") or fl.get("origen")
        dst = fl.get("categoría final") or fl.get("categoria final") or fl.get("final")
        if not (src and dst):
            return {}
        mapping = {}
        for row in rdr:
            s = (row.get(src) or "").strip()
            d = (row.get(dst) or "").strip()
            if s:
                mapping[_mm_norm_key(s)] = d or s
        return mapping
    except Exception:
        return {}

# ------------------ [PATCH] Redefinir get_items_mapped(**kwargs) ------------------
def get_items_mapped(**kwargs):
    """
    Igual que get_items, pero si se pasa:
      - category_map_path (preferido) o
      - category_map_csv
    aplicamos el mapeo y ponemos también 'public_category' para eCommerce.
    """
    # Aceptar ambos nombres de parámetro (tu config usa category_map_path)
    cat_path = kwargs.pop("category_map_path", None) or kwargs.pop("category_map_csv", None)
    items = get_items(**kwargs)
    mapping = _mm_load_cat_map(cat_path) if cat_path else {}
    if not mapping:
        return items

    for it in items:
        src = it.get("category") or ""
        key = _mm_norm_key(src)
        dst = mapping.get(key)
        if dst:
            it["category"] = dst
            it["public_category"] = dst  # para que el módulo lo pueble en product.public.category
    return items
# ===================== [/PATCH] =====================
# ===================== [PATCH v2] category-map robusto =====================
import logging as _mm_log
_mm_logger = _mm_log.getLogger(__name__)

# Corrige el typo y mejora la normalización: acentos, & -> y, espacios
def _mm_norm_key(s: str) -> str:
    if s is None:
        return ""
    nfkd = _mm_unic.normalize("NFKD", str(s))
    s = "".join(ch for ch in nfkd if not _mm_unic.combining(ch) and ord(ch) < 128)
    s = s.lower().strip()
    s = s.replace("&", " y ")          # “&” vale como “y”
    s = s.replace("/", " / ")          # evita pegar palabras con slashes
    return _mm_re.sub(r"\s+", " ", s)

def _mm_pick_headers(fieldnames_low: dict):
    # Acepta tus cabeceras reales y sinónimos
    src_cands = [
        "categoría infortisa","categoria infortisa","infortisa",
        "categoría de producto","categoria de producto",
        "categoría","categoria","origen"
    ]
    dst_cands = [
        "categoría final","categoria final","final","destino",
        "mi categoría","mi categoria"
    ]
    src = next((fieldnames_low.get(k) for k in src_cands if fieldnames_low.get(k)), None)
    dst = next((fieldnames_low.get(k) for k in dst_cands if fieldnames_low.get(k)), None)
    # Fallback: si tenemos destino, usa la primera columna distinta como origen
    if not src and dst:
        for orig in fieldnames_low.values():
            if orig and orig != dst:
                src = orig; break
    return src, dst

def _mm_load_cat_map(path: str) -> dict:
    if not path:
        return {}
    try:
        txt = _mm_read_file(path)
        rdr = _mm_csv_reader(txt)
        fl = { (c or "").strip().lower(): c for c in (rdr.fieldnames or []) }
        src, dst = _mm_pick_headers(fl)
        if not (src and dst):
            if _mm_logger: _mm_logger.info("Category map: sin columnas reconocibles en %s", path)
            return {}
        mapping = {}
        for row in rdr:
            s = (row.get(src) or "").strip()
            d = (row.get(dst) or "").strip()
            if s:
                mapping[_mm_norm_key(s)] = d or s
        if _mm_logger:
            _mm_logger.info("Category map: %d pares desde %s (src=%s, dst=%s)", len(mapping), path, src, dst)
        return mapping
    except Exception as e:
        if _mm_logger:
            _mm_logger.info("Category map: fallo al leer %s (%s)", path, e)
        return {}

def get_items_mapped(**kwargs):
    cat_path = kwargs.pop("category_map_path", None) or kwargs.pop("category_map_csv", None)
    items = get_items(**kwargs)
    mapping = _mm_load_cat_map(cat_path) if cat_path else {}
    if not mapping:
        return items
    hits = 0
    for it in items:
        key = _mm_norm_key(it.get("category") or "")
        dst = mapping.get(key)
        if dst:
            it["category"] = dst
            it["public_category"] = dst
            hits += 1
    if _mm_logger:
        _mm_logger.info("Category map aplicado: %d/%d items mapeados.", hits, len(items))
    return items
# ===================== [/PATCH v2] =====================
# ===================== [PATCH v3] normalización + fuzzy =====================
import difflib as _mm_diff

def _mm_norm_key(s: str) -> str:
    """
    Normaliza para clave de mapeo:
      - Reemplaza NBSP y “espacios raros” por espacio normal.
      - Quita acentos (NFKD) pero CONSERVA espacios.
      - Pasa a ASCII (si no es ASCII lo descarta).
      - Convierte & -> " y ", / -> " / " y colapsa espacios.
    """
    if s is None:
        return ""
    # 1) str + normalización básica de espacios (incluye NBSP \u00a0)
    t = str(s).replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    # 2) quitar acentos pero mantener espacios
    nfkd = _mm_unic.normalize("NFKD", t)
    t = "".join(ch for ch in nfkd if not _mm_unic.combining(ch))
    # 3) a minúsculas y ASCII (descarta símbolos no ASCII pero deja espacios)
    out = []
    for ch in t.lower():
        if ch == "&":
            out.append(" y "); continue
        if ch == "/":
            out.append(" / "); continue
        # deja letras, números, espacios y guiones básicos
        if ord(ch) < 128 and (ch.isalnum() or ch in " -_()/"):
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        # todo lo demás: lo ignoramos
    t = "".join(out)
    # 4) compactar espacios
    return _mm_re.sub(r"\s+", " ", t).strip()

def _mm_find_map(mapping: dict, src_text: str):
    """
    Devuelve mapping[norm(src)] si existe; si no, intenta:
      - match por prefijo/sufijo con las claves del mapping
      - difflib.get_close_matches (cutoff=0.92)
    """
    if not mapping:
        return None
    key = _mm_norm_key(src_text)
    if key in mapping:
        return mapping[key]
    # prefijo/sufijo
    for k in mapping.keys():
        if key.startswith(k) or key.endswith(k) or k.startswith(key):
            return mapping[k]
    # fuzzy (plural/singular, guiones, etc.)
    cands = _mm_diff.get_close_matches(key, mapping.keys(), n=1, cutoff=0.92)
    if cands:
        return mapping[cands[0]]
    return None

def get_items_mapped(**kwargs):
    cat_path = kwargs.pop("category_map_path", None) or kwargs.pop("category_map_csv", None)
    items = get_items(**kwargs)
    mapping = _mm_load_cat_map(cat_path) if cat_path else {}
    if not mapping:
        return items

    hits, misses, miss_examples = 0, 0, {}
    for it in items:
        src = it.get("category") or ""
        dst = _mm_find_map(mapping, src)
        if dst:
            it["category"] = dst
            it["public_category"] = dst
            hits += 1
        else:
            misses += 1
            nk = _mm_norm_key(src)
            if nk not in miss_examples and len(miss_examples) < 5:
                miss_examples[nk] = src

    if _mm_logger:
        _mm_logger.info("Category map aplicado: %d/%d items mapeados; sin mapa: %d",
                        hits, len(items), misses)
        if miss_examples:
            _mm_logger.info("Ejemplos sin mapa (normalizado -> original): %s",
                            "; ".join(f"{k} -> {v}" for k,v in miss_examples.items()))
    return items
# ===================== [/PATCH v3] =====================
