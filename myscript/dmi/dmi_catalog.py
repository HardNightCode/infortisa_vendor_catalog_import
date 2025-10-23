# -*- coding: utf-8 -*-
"""
Conector DMI para vendor_catalog_import: catálogo + precio + stock
con login automático (username/password) y refresco de token.

- Si NO pasas token ni DMI_TOKEN, hace POST a /users/authenticate con
  DMI_USERNAME/DMI_PASSWORD (o parámetros username=, password=) y pone el token.
- Si el token caduca (401), se reautentica y reintenta la petición.
- App-Key se toma de DMI_APP_KEY o parámetro app_key= (requerido).

Salida por item:
  name, sku, barcode, image_url, category, cost, vendor_code, vendor_name,
  vendor_stock, weight, (opcional) description_ecommerce / website_description
"""

import os, re, csv, time
from typing import Any, Dict, List, Optional, Tuple
import requests

BASE = "https://api.dmi.es"
API  = "/api/v2"
AUTH_URL      = f"{BASE}{API}/users/authenticate"
EP_PRODUCTS   = f"{BASE}{API}/products/getallproducts"
EP_STOCKPRICE = f"{BASE}{API}/products/stockprice"
EP_PRICE      = f"{BASE}{API}/price/getprice"
EP_STOCK      = f"{BASE}{API}/stock/getstock"

# ======================= Utilidades =======================

def _debug(msg: str) -> None:
    try:
        import sys
        sys.stderr.write(f"[dmi] {msg}\n")
    except Exception:
        pass

def _items_from_payload(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list): return data
    if isinstance(data, dict):
        for k in ("items","data","results","Items"):
            v = data.get(k)
            if isinstance(v, list): return v
    return []

def _chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

# ======================= Autenticación =======================

class _TokenBox:
    """Caja mutable para compartir el token entre llamadas."""
    def __init__(self, token: Optional[str] = None):
        self.token = token or ""

def _build_auth_headers(app_key: str) -> Dict[str, str]:
    # Algunos tenants aceptan variantes del nombre del header
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "App-Key": app_key,
        "X-Api-Key": app_key,
        "x-app-key": app_key,
        "User-Agent": "vendor-catalog-import/dmi/1.1",
        "Connection": "close",
    }

def _login(session: requests.Session, app_key: str, username: str, password: str) -> str:
    headers = _build_auth_headers(app_key)
    payload = {"username": username, "password": password}
    r = session.post(AUTH_URL, headers=headers, json=payload, timeout=(10, 30))
    if r.status_code != 200:
        raise RuntimeError(f"Login DMI falló: {r.status_code} {r.text[:200]}")
    data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    token = (data.get("token") or data.get("accessToken") or "").strip()
    if not token:
        raise RuntimeError("Login DMI: respuesta sin 'token'.")
    _debug("Token DMI obtenido.")
    return token

def _ensure_token(session: requests.Session,
                  token_box: _TokenBox,
                  app_key: str,
                  username: Optional[str],
                  password: Optional[str]) -> str:
    """Obtiene token si no hay, usando env o parámetros."""
    if token_box.token:
        return token_box.token
    if not username:
        username = os.getenv("DMI_USERNAME") or ""
    if not password:
        password = os.getenv("DMI_PASSWORD") or ""
    if not username or not password:
        raise RuntimeError("Falta token y credenciales (DMI_USERNAME/DMI_PASSWORD).")
    token_box.token = _login(session, app_key, username, password)
    return token_box.token

def _authz_headers(app_key: str, token: str) -> Dict[str, str]:
    h = _build_auth_headers(app_key)
    h["Authorization"] = f"Bearer {token}"
    return h

def _request_with_retry(session: requests.Session,
                        method: str,
                        url: str,
                        headers: Dict[str,str],
                        token_box: Optional[_TokenBox] = None,
                        app_key: Optional[str] = None,
                        username: Optional[str] = None,
                        password: Optional[str] = None,
                        **kwargs) -> requests.Response:
    """
    Reintentos para 429/5xx; si 401 y tenemos token_box+credenciales: relogin y reintenta 1 vez.
    """
    kwargs.setdefault("timeout", (10, 30))
    last_exc = None
    relogin_used = False
    for attempt in range(6):
        try:
            r = session.request(method, url, headers=headers, **kwargs)
            if r.status_code == 401 and token_box and app_key:
                if not relogin_used:
                    _debug("401 -> reautenticando token…")
                    # relogin
                    new_token = _ensure_token(session, token_box, app_key, username, password)
                    headers = {**headers, "Authorization": f"Bearer {new_token}"}
                    relogin_used = True
                    continue  # reintenta inmediatamente
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(2**attempt, 30)
                _debug(f"{r.status_code} {method} {url} -> reintento en {wait}s")
                time.sleep(wait); continue
            r.raise_for_status()
            return r
        except requests.exceptions.ReadTimeout as e:
            last_exc = e
            if attempt == 5: break
            wait = min(2**attempt, 30)
            _debug(f"ReadTimeout {method} {url} -> {wait}s"); time.sleep(wait)
        except requests.RequestException as e:
            last_exc = e
            if attempt == 5: break
            wait = min(2**attempt, 30)
            _debug(f"{type(e).__name__} {method} {url} -> {wait}s"); time.sleep(wait)
    if last_exc: raise last_exc
    raise RuntimeError("Sin respuesta estable")

# ======================= Fetchers =======================

def _fetch_all_products_once(session: requests.Session, headers: Dict[str,str],
                             token_box: _TokenBox, app_key: str,
                             username: Optional[str], password: Optional[str]) -> List[Dict[str,Any]]:
    r = _request_with_retry(session,"GET",EP_PRODUCTS,headers=headers,
                            token_box=token_box, app_key=app_key,
                            username=username, password=password)
    data = r.json()
    arr = data if isinstance(data, list) else _items_from_payload(data)
    _debug(f"getallproducts -> {len(arr)} elementos")
    return arr or []

def _post_with_variants(session: requests.Session, url: str, headers: Dict[str,str],
                        ids: List[str], token_box: _TokenBox, app_key: str,
                        username: Optional[str], password: Optional[str]) -> List[Dict[str,Any]]:
    """
    Algunos tenants cambian content-type y la clave del JSON (productsIds/productIds).
    Probamos variantes hasta que funcione. Compatible con refresco 401.
    """
    variants: List[Tuple[str,str]] = [
        ("application/json-patch+json","productsIds"),
        ("application/json","productsIds"),
        ("application/json","productIds"),
        ("application/json-patch+json","productIds"),
    ]
    last_err = None
    for ct,key in variants:
        body = {key: ids}
        try:
            rh = {**headers, "Content-Type": ct}
            r = _request_with_retry(session, "POST", url, headers=rh,
                                    json=body, token_box=token_box, app_key=app_key,
                                    username=username, password=password)
            data = r.json()
            return data if isinstance(data, list) else _items_from_payload(data)
        except Exception as e:
            last_err = e
            _debug(f"{url} falló con CT={ct} key={key}: {type(e).__name__}")
    if last_err: raise last_err
    return []

def _fetch_stockprice_resilient(session: requests.Session, headers: Dict[str,str],
                                product_ids: List[str], token_box: _TokenBox,
                                app_key: str, username: Optional[str], password: Optional[str],
                                batch_size: int = 50):
    prices: Dict[str, Optional[float]] = {}
    stocks: Dict[str, Optional[float]] = {}
    current = batch_size
    i = 0
    while i < len(product_ids):
        chunk = product_ids[i:i+current]
        _debug(f"[stockprice] Lote {i//current+1} ids={len(chunk)} ({i+1}-{i+len(chunk)}/{len(product_ids)})")
        try:
            arr = _post_with_variants(session, EP_STOCKPRICE, headers, chunk,
                                      token_box, app_key, username, password)
            for row in arr:
                pid = str(row.get("productId") or "").strip()
                if not pid: continue
                prices[pid] = row.get("price")
                if "stock" in row: stocks[pid] = row.get("stock")
            i += current
            if current < 100: current = min(current + 10, 100)
        except requests.exceptions.ReadTimeout:
            new_current = max(current // 2, 10)
            _debug(f"[stockprice] ReadTimeout -> {current}->{new_current}")
            current = new_current
        except Exception as e:
            new_current = max(current // 2, 10)
            _debug(f"[stockprice] {type(e).__name__} -> {current}->{new_current}")
            current = new_current
    return prices, stocks

def _fetch_prices(session: requests.Session, headers: Dict[str,str], product_ids: List[str],
                  token_box: _TokenBox, app_key: str, username: Optional[str], password: Optional[str],
                  batch_size: int = 50) -> Dict[str, Optional[float]]:
    prices: Dict[str, Optional[float]] = {}
    for idx,chunk in enumerate(_chunked(product_ids,batch_size),1):
        _debug(f"[price] Lote {idx} ids={len(chunk)}")
        arr = _post_with_variants(session, EP_PRICE, headers, chunk,
                                  token_box, app_key, username, password)
        for row in arr:
            pid = str(row.get("productId") or "").strip()
            if pid: prices[pid] = row.get("price")
    return prices

def _fetch_stocks(session: requests.Session, headers: Dict[str,str], product_ids: List[str],
                  token_box: _TokenBox, app_key: str, username: Optional[str], password: Optional[str],
                  batch_size: int = 50) -> Dict[str, Optional[float]]:
    stocks: Dict[str, Optional[float]] = {}
    for idx,chunk in enumerate(_chunked(product_ids,batch_size),1):
        _debug(f"[stock] Lote {idx} ids={len(chunk)}")
        arr = _post_with_variants(session, EP_STOCK, headers, chunk,
                                  token_box, app_key, username, password)
        for row in arr:
            pid = str(row.get("productId") or "").strip()
            if pid: stocks[pid] = row.get("stock")
    return stocks

# ======================= Transformaciones =======================

def _normalize_image_url(url: Optional[str]) -> Optional[str]:
    if not url: return None
    s = str(url).strip()
    return s or None

def _desc_to_html(raw: Optional[str]) -> str:
    if not raw: return ""
    txt = str(raw).replace("\r\n","\n").replace("\r","\n").strip()
    if "<" in txt and ">" in txt:
        return txt
    parts = [p.strip() for p in re.split(r"\s*[•\-]\s+|\n+", txt) if p.strip()]
    return "<br/>".join(parts) if parts else txt

def _norm_ean(p: Dict[str,Any]) -> str:
    ean = p.get("ean")
    if isinstance(ean,str) and ean.strip(): return ean.strip()
    eans = p.get("eans")
    if isinstance(eans,str) and eans.strip(): return eans.strip()
    if isinstance(eans,list):
        for x in eans:
            if isinstance(x,str) and x.strip(): return x.strip()
    return ""

def _first_in_list_of_objs(lst: Optional[List[Dict[str,Any]]], keys: List[str]) -> Optional[Any]:
    if not isinstance(lst,list) or not lst: return None
    obj = lst[0] or {}
    for k in keys:
        v = obj.get(k)
        if v not in (None, ""):
            return v
    return None

def _peso_parse(val: Any) -> float:
    """Admite '240 g', '0,24', 0.24, etc. Devuelve kg."""
    if val in (None,""): return 0.0
    s = str(val).strip().replace(",", ".").lower()
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)', s)
    if not m:
        try: return float(s)
        except Exception: return 0.0
    num = float(m.group(1)); unit = m.group(2)
    if unit.startswith("g"): num /= 1000.0
    return round(num, 6)

def _load_category_map(path: Optional[str]) -> Dict[str,str]:
    """
    Lee un CSV (2 columnas: origen,destino) permitiendo codificaciones comunes:
    utf-8-sig, utf-8, cp1252, latin-1. Detecta delimitador (coma, punto y coma, tab).
    """
    mapping: Dict[str,str] = {}
    if not path:
        return mapping
    try:
        import os as _os, io as _io, csv as _csv
        if not _os.path.exists(path):
            return mapping
        with open(path, "rb") as fb:
            raw = fb.read()
        txt = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if txt is None:
            return mapping
        sample = txt[:4096]
        try:
            dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            # heurística simple: si hay más ';' que ',' en el sample
            if sample.count(";") > sample.count(","):
                class _Semi(_csv.Dialect):
                    delimiter=";"; quotechar='"'; escapechar=None; doublequote=True; skipinitialspace=False; lineterminator="\n"; quoting=_csv.QUOTE_MINIMAL
                dialect = _Semi
            else:
                dialect = _csv.excel
        rdr = _csv.reader(_io.StringIO(txt), dialect)
        for row in rdr:
            if not row or len(row) < 2:
                continue
            src = (row[0] or "").strip()
            dst = (row[1] or "").strip()
            if not src:
                continue
            # Filtra cabeceras típicas
            low = src.lower()
            if low in ("origen","origen_categoria","source","categoria_origen"):
                continue
            mapping[src] = dst
    except Exception:
        # Silencioso: si algo va mal, devolvemos vacío y seguimos sin mapeo
        return {}
    return mapping

# ======================= API Principal =======================

def get_items(app_key: Optional[str] = None,
              token: Optional[str] = None,
              username: Optional[str] = None,
              password: Optional[str] = None,
              min_stock: int = 1,
              limit: Optional[int] = None,
              vendor_name: Optional[str] = "DMI",
              category_map_csv: Optional[str] = None,
              require_image: bool = True,
              batch_size: int = 50,
              mode: str = "stockprice",  # "stockprice" | "split"
              **kwargs) -> List[Dict[str,Any]]:
    """
    Prioridad credenciales:
      1) token= (si lo pasas)
      2) env DMI_TOKEN
      3) login con username/password (parámetros o env DMI_USERNAME/DMI_PASSWORD)

    App-Key: app_key= o env DMI_APP_KEY (OBLIGATORIO en cualquier caso).
    """
    app_key = (app_key or os.getenv("DMI_APP_KEY") or "").strip()
    if not app_key:
        raise RuntimeError("Falta App-Key (DMI_APP_KEY o parámetro app_key=).")

    # Determinar token inicial
    tok = (token or os.getenv("DMI_TOKEN") or "").strip()
    token_box = _TokenBox(tok)

    # Si no tenemos token, intentamos login (usando params o env)
    session = requests.Session()
    session.trust_env = False  # evita proxies del sistema

    if not token_box.token:
        try:
            _ensure_token(session, token_box, app_key, username, password)
        except Exception as e:
            # Aún sin token; seguimos, porque _request_with_retry puede reloginear en 401 si luego das user/pass
            _debug(f"Token inicial no disponible: {e}")

    # Cabeceras con (o sin) token; si está vacío, el primer 401 disparará login+retry
    headers = _authz_headers(app_key, token_box.token) if token_box.token else _build_auth_headers(app_key)

    # 1) Catálogo base
    raw = _fetch_all_products_once(session, headers, token_box, app_key, username, password)

    uniq: Dict[str,Dict[str,Any]] = {}
    for p in raw:
        pid = str(p.get("productId") or p.get("id") or "").strip()
        if pid and pid not in uniq:
            uniq[pid] = p

    ids = list(uniq.keys())
    lim = int(limit) if (isinstance(limit,(int,str)) and str(limit).isdigit()) else None
    if lim and len(ids) > lim:
        ids = ids[:lim]
    products = [uniq[i] for i in ids]
    _debug(f"Únicos por productId: {len(uniq)}; a exportar: {len(products)}")

    # 2) Precio + Stock
    if mode == "stockprice":
        prices, stocks = _fetch_stockprice_resilient(session, headers, ids, token_box, app_key, username, password, batch_size)
        if not stocks:
            _debug("stockprice no devolvió 'stock' -> completamos con /stock/getstock")
            stocks = _fetch_stocks(session, headers, ids, token_box, app_key, username, password, batch_size)
        if not prices:
            _debug("stockprice no devolvió 'price' -> completamos con /price/getprice")
            prices = _fetch_prices(session, headers, ids, token_box, app_key, username, password, batch_size)
    else:
        prices = _fetch_prices(session, headers, ids, token_box, app_key, username, password, batch_size)
        stocks = _fetch_stocks(session, headers, ids, token_box, app_key, username, password, batch_size)

    # 3) Mapeo de categorías
    # si no viene ruta, usa data/mapeado_dmi.csv si existe
    if not category_map_csv:
        try:
            import os as _os
            _p = _os.path.join(_os.path.dirname(__file__), "data", "mapeado_dmi.csv")
            if _os.path.exists(_p):
                category_map_csv = _p
        except Exception:
            pass
    cat_map = _load_category_map(category_map_csv)

    # 4) Ensamblado
    try: min_stock = int(min_stock or 0)
    except Exception: min_stock = 0

    items: List[Dict[str,Any]] = []
    for p in products:
        pid = str(p.get("productId") or p.get("id") or "").strip()
        price = prices.get(pid)
        stock = stocks.get(pid)
        try: stock_int = int(stock) if stock is not None and str(stock).strip() != "" else 0
        except Exception: stock_int = 0
        try: price_f = float(price) if price is not None and str(price).strip() != "" else 0.0
        except Exception: price_f = 0.0

        if price_f <= 0 or stock_int < min_stock:
            continue

        name = p.get("name") or ""
        ref  = p.get("manufacturerCode") or ""
        ean  = _norm_ean(p)
        img  = _first_in_list_of_objs(p.get("mainImage"), ["thumbnail","smallPhoto","largePhoto"]) or ""
        if require_image and not img:
            continue
        desc = _first_in_list_of_objs(p.get("marketingText"), ["shortDescription","longDescription","shortSummary"]) or ""
        weight_raw = _first_in_list_of_objs(p.get("logistics"), ["weight"])
        weight = _peso_parse(weight_raw)

        subcat = p.get("subCategory") or ""
        category = cat_map.get(subcat, subcat) or None

        item = {
            "name": name,
            "sku": ref or ean or pid,
            "barcode": ean or None,
            "image_url": _normalize_image_url(img),
            "category": category,
            "cost": float(f"{price_f:.2f}"),
            "vendor_code": ref or pid,
            "vendor_name": (vendor_name or "DMI").strip(),
            "vendor_stock": int(stock_int),
            "weight": (weight if (weight and weight > 0) else None),
        }
        html = _desc_to_html(desc)
        if html:
            item["description_ecommerce"] = html
            item["website_description"] = html

        items.append(item)
        if lim and len(items) >= lim:
            break

    return items

def get_items_mapped(**kwargs):
    return get_items(**kwargs)
