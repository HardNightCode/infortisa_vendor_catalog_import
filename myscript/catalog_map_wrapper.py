# -*- coding: utf-8 -*-
"""
Wrapper que aplica un mapeo de categorías (CSV) a los items producidos por
`vendor_catalog_import.myscript.catalog.get_items`.

Uso desde Configuración:
- Python module:    vendor_catalog_import.myscript.catalog_map_wrapper
- Python callable:  get_items_mapped
- Parámetros JSON:  {"category_map_path": "/opt/odoo/custom/addons/vendor_catalog_import/data/infortisa_odoo_agrupado__mapping.csv", ...}
"""
import os, io, csv, re, unicodedata

# importar el adaptador base
try:
    from . import catalog as base
except Exception:  # fallback si no estuviera bien el paquete
    import importlib.util as _iu
    _p = os.path.join(os.path.dirname(__file__), "catalog.py")
    spec = _iu.spec_from_file_location("vendor_catalog_import.myscript.catalog", _p)
    base = _iu.module_from_spec(spec)
    spec.loader.exec_module(base)  # type: ignore

def _strip_accents(s):
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))

def _norm_key(s):
    return re.sub(r"\s+", " ", _strip_accents(s).lower().strip())

def _read_file_robust(path: str) -> str:
    last = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception as e:
            last = e
            continue
    if last:
        raise last
    return ""

def _sniff_reader(text: str):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
    except Exception:
        class _D: delimiter = ';'
        dialect = _D()
    return csv.DictReader(io.StringIO(text), dialect=dialect)

def _load_category_mapping(text: str = None, path: str = None):
    """
    Devuelve dict { norm(origen) : destino }.
    Encabezados tolerantes:
      - 'Categoría de producto' / 'Categoria de producto' / 'Categoria'
      - 'Categoría final' / 'Categoria final' / 'Final'
    """
    mapping = {}
    try:
        if not (text or path):
            return mapping
        if path and not text and os.path.exists(path):
            text = _read_file_robust(path)
        if not text:
            return mapping
        reader = _sniff_reader(text)
        fns = [ (c or "").strip() for c in (reader.fieldnames or []) ]
        low = { c.lower(): c for c in fns }
        src = low.get("categoría de producto") or low.get("categoria de producto") or low.get("categoria") or next(iter(low.values()), None)
        dst = low.get("categoría final") or low.get("categoria final") or low.get("final")
        if not (src and dst):
            return mapping
        for row in reader:
            s = (row.get(src) or "").strip()
            d = (row.get(dst) or "").strip()
            if s:
                mapping[_norm_key(s)] = d or s
    except Exception:
        return {}
    return mapping

def _default_map_path():
    # .../vendor_catalog_import
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "data", "infortisa_odoo_agrupado__mapping.csv")

def get_items_mapped(category_map_path=None, category_map_csv=None, **kwargs):
    """
    Extra:
      - category_map_path: ruta CSV mapeo
      - category_map_csv:  contenido CSV (texto)
    """
    try:
        items = base.get_items(**kwargs) or []
    except Exception:
        return []

    cmap = _load_category_mapping(text=category_map_csv, path=category_map_path)
    if not cmap:
        cmap = _load_category_mapping(path=_default_map_path())
    if not cmap:
        # Enforce fallback for unknown categories
        FALLBACK_CATEGORY = "Otros"
        for it in items:
            src_cat = (it.get("category") or "")
            it["category"] = cat_map.get(_norm(src_cat)) or FALLBACK_CATEGORY
        return items

    for it in items:
        try:
            orig = it.get("category") or ""
            new = cmap.get(_norm_key(orig))
            if new:
                it["category"] = new
        except Exception:
            continue
    return items
