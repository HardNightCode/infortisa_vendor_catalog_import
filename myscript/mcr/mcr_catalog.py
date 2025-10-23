
# -*- coding: utf-8 -*-
"""
Importador de catálogo MCR para el módulo vendor_catalog_import.
Lee un CSV público de OpenMCR y devuelve una lista de items con:
  name, sku, barcode, image_url, category, cost, vendor_stock
  (opcional) description_ecommerce  <-- SOLO esta descripción
  (opcional) weight (kg)            <-- SOLO si se detecta en Especificaciones
NO envía list_price (PVP) para no tocar el precio de venta en Odoo.
"""
from __future__ import annotations
import csv, io, re, unicodedata, urllib.request
from typing import Dict, List, Any

# ------------------------- util -------------------------

def _strip_accents(s: str) -> str:
    if s is None: return ""
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")

def _norm_key(s: str) -> str:
    return re.sub(r"\s+", "", _strip_accents(s or "").lower())

def _to_float_es(val) -> float:
    if val is None: return 0.0
    s = str(val).strip().replace("€","").replace(" ","")
    if "," in s and s.count(",")==1 and s.rfind(",")>s.rfind("."):
        s = s.replace(".","").replace(",",".")
    try:
        return float(s)
    except Exception:
        m = re.search(r"[-+]?\d*\.?\d+", s or "")
        return float(m.group()) if m else 0.0

def _to_int(val) -> int:
    try:
        return int(float(str(val).strip().replace(",", ".")))
    except Exception:
        return 0

def _download_text(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8-sig","utf-8","latin-1","cp1252"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")

def _read_csv_from_text(text: str) -> List[Dict[str, Any]]:
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";"
    rdr = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [{k.strip(): (v.strip() if isinstance(v,str) else v) for k,v in row.items()} for row in rdr]

def _col(row: Dict[str, Any], candidates: List[str]) -> Any:
    if not row: return None
    keys_norm = {_norm_key(k): k for k in row.keys()}
    for cand in candidates:
        k = keys_norm.get(_norm_key(cand))
        if k:
            v = row.get(k)
            if v not in (None, "", "null", "None"):
                return v
    return None

def _sum_stocks(row: Dict[str, Any], stock_keys: List[str]) -> int:
    total = 0
    keys_norm = {_norm_key(k): k for k in row.keys()}
    for cand in stock_keys:
        k = keys_norm.get(_norm_key(cand))
        if k:
            total += _to_int(row.get(k))
    return total

def _load_category_map(path: str) -> Dict[str, str]:
    if not path: return {}
    try:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig","utf-8","latin-1","cp1252"):
            try:
                text = raw.decode(enc); break
            except Exception:
                continue
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";")
            delim = dialect.delimiter
        except Exception:
            delim = ";"
        rdr = csv.reader(io.StringIO(text), delimiter=delim)
        mapping = {}
        for row in rdr:
            if not row: continue
            src = (row[0] or "").strip()
            dst = (row[1] or "").strip() if len(row) > 1 else ""
            if src and dst:
                mapping[_norm_key(src)] = dst
        return mapping
    except Exception:
        return {}

def _apply_category_map(category: str, mapping: Dict[str, str]) -> str:
    """Mapea categoría libre usando coincidencias exactas,
    prefijos y partes separadas por > / | ; , -"""
    if not category or not mapping:
        return category
    dst = mapping.get(_norm_key(category))
    if dst:
        return dst
    parts = re.split(r"[>/\\|;,-]+", category)
    parts = [p.strip() for p in parts if p.strip()]
    for i in range(len(parts), 0, -1):
        cand = " > ".join(parts[:i])
        dst = mapping.get(_norm_key(cand))
        if dst:
            return dst
    for p in parts:
        dst = mapping.get(_norm_key(p))
        if dst:
            return dst
    return category

def _fix_category_by_name(name: str, category: str) -> str:
    n = _norm_key(name)
    has_support = any(k in n for k in ("soporte","stand","holder","vesa","montaje","brazo","pared","techo"))
    is_cableish = any(k in n for k in ("cable","adaptador","hub","convertidor","conversor","extension","extensión","extensor","alargador"))
    if has_support and not is_cableish:
        return "Soportes & Montaje"
    return category

def _clean_name(s: str) -> str:
    if not isinstance(s, str): s = str(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\r"," ").replace("\n"," ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:180]

def _desc_to_html(desc_raw: str) -> str:
    if not desc_raw:
        return ""
    txt = str(desc_raw)
    txt = (txt.replace("\r\n","\n").replace("\r","\n")
              .replace("\\r\\n","\n").replace("\\n","\n")
              .replace("\\t"," ")).strip()
    if "<" in txt and ">" in txt:
        return txt
    txt2 = re.sub(r"[–—]", "-", txt)
    parts = None
    if "•" in txt2:
        parts = [t.strip(" •-\t") for t in txt2.split("•") if t.strip(" •-\t")]
    else:
        parts = re.split(r'(?:(?<=^)|(?<=\.))\s*-\s+|\n\s*-\s+', txt2)
        parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        if txt2.count(" - ") >= 2:
            parts = [t.strip() for t in re.split(r"\s-\s", txt2) if t.strip()]
        elif txt2.count(";") >= 2:
            parts = [t.strip(" ;\t") for t in txt2.split(";") if t.strip(" ;\t")]
        elif "\n" in txt2:
            parts = [t.strip() for t in txt2.split("\n") if t.strip()]
        else:
            parts = [txt2]
    if len(parts) == 1:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÜÑ0-9])", parts[0]) if s.strip()]
        if len(sentences) >= 2:
            parts = sentences
    if len(parts) == 1 and len(parts[0]) > 180:
        block = parts[0]; mid = len(block)//2
        left = block.rfind(" ", 80, mid); right = block.find(" ", mid, len(block)-40)
        cut = left if left!=-1 else (right if right!=-1 else -1)
        if cut != -1:
            parts = [block[:cut].strip(), block[cut:].lstrip()]
    return "<br/>".join(parts)

# ------- extracción de peso desde "Especificaciones" -------
_PESO_PAT = re.compile(
    r"\bpeso(?:\s*(?:neto|aprox(?:\.|imado)?|bruto))?\s*[:\-]?\s*"
    r"([0-9]+(?:[.,][0-9]+)?)\s*(kg|g)\b",
    flags=re.IGNORECASE
)

def _extract_weight_from_specs(spec_text: str | None) -> float:
    """Devuelve el peso en kg. Acepta 'Peso: 250 g', 'Peso 0,25 kg', etc."""
    if not spec_text:
        return 0.0
    txt = str(spec_text)
    m = _PESO_PAT.search(txt)
    if not m:
        return 0.0
    num_s, unit = m.group(1), (m.group(2) or "").lower()
    try:
        num = float(num_s.replace(",", "."))
    except Exception:
        return 0.0
    if unit.startswith("g"):
        num = num / 1000.0
    return round(num, 6)

# ------------------------- core -------------------------

def get_items(
    feed_url: str = None,
    min_stock: int = 1,
    limit: int | None = None,
    category_map_path: str | None = None,
    require_image: bool = True,
    placeholder_image_url: str | None = None,
    vendor_name: str | None = None,
    **kwargs
) -> List[Dict[str, Any]]:
    if not feed_url:
        raise ValueError("Falta feed_url (URL del CSV de MCR).")

    text = _download_text(feed_url)
    rows = _read_csv_from_text(text)
    cat_map = _load_category_map(category_map_path)

    NAME = ["nombre", "titulo", "título", "name", "title"]
    DESC = ["especificaciones","especificacion","especificación",
            "descripcion","descripción","desc",
            "descripcioncorta","caracteristicas","características","ficha"]
    CODE = ["codigo","código","cod","sku","referencia","ref","productcode","itemcode","code","id","mpn","part number","partnumber","pn","codigo proveedor","código proveedor","cod proveedor","vendorcode"]
    BAR  = ["ean","ean13","barcode","codigo barras","codigobarras"]
    COST = ["precio","precio coste","coste","cost","price"]
    IMG  = ["imagen","image","image_url","urlimagen","foto"]
    CAT1 = ["categoria","categoría","familia","family","grupo","seccion","sección","categoria1","categoria2","categoria3"]
    STK  = ["stock","stock total","stock_total","stocktotal","disponible","stockcentral","stockpalma","stockexterno","qty","cantidad"]

    items: List[Dict[str, Any]] = []
    for row in rows:
        name = _clean_name(_col(row, NAME))
        if not name:
            continue

        sku = _col(row, CODE)
        barcode = _col(row, BAR)
        sku = sku or _col(row, ["mpn","part number","partnumber","pn"]) or barcode
        image_url = _col(row, IMG) or placeholder_image_url

        # Categoría
        cat_parts = []
        for k in CAT1:
            v = _col(row, [k])
            if v: cat_parts.append(v)
        category = " > ".join([p for p in cat_parts if p])
        category = _apply_category_map(category, cat_map)
        category = _fix_category_by_name(name, category)

        cost = _to_float_es(_col(row, COST))
        if cost <= 0:
            continue

        stock_total = _sum_stocks(row, STK)
        if stock_total < int(min_stock or 0):
            continue

        if require_image and not image_url:
            continue

        # Especificaciones -> descripción web y (opcional) peso
        spec_raw = _col(row, DESC) or ""
        desc_html = _desc_to_html(spec_raw)
        weight = _extract_weight_from_specs(spec_raw)  # kg (0.0 si no hay)

        item = {
            "name": name,
            "sku": sku or None,
            "barcode": barcode or None,
            "image_url": image_url or None,
            "category": category or None,
            "cost": cost,
            "vendor_stock": int(stock_total),
        }
        # SOLO enviamos 'weight' si lo hemos detectado (>0) para NO sobrescribir
        # pesos ya fijados manualmente en Odoo cuando MCR no lo aporta.
        if weight and weight > 0:
            item["weight"] = float(weight)

        if desc_html:
            item["description_ecommerce"] = desc_html
        if vendor_name:
            item["vendor_name"] = vendor_name

        items.append(item)
        if limit and len(items) >= int(limit):
            break

    return items

def get_items_mapped(**kwargs) -> List[Dict[str, Any]]:
    return get_items(**kwargs)
