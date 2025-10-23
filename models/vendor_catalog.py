# -*- coding: utf-8 -*-
"""
(…contenido original…)
"""
import base64, csv, io, json, importlib
import logging
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from odoo import api, fields, models, _
_t = _  # alias seguro de traducción
from odoo.exceptions import UserError
try:
    import requests
except Exception:
    requests = None

# === util (idéntico a tu fichero original) ===
_logger = logging.getLogger(__name__)

def _looks_like_image(b: bytes) -> bool:
    if not b or len(b) < 12:
        return False
    # JPEG, PNG, WEBP (RIFF/WEBP)
    sigs = (b"\xff\xd8", b"\x89PNG\r\n\x1a\n", b"RIFF")
    return any(b.startswith(s) for s in sigs)

def _robust_float(x):
    if x is None: return None
    try: return float(x)
    except Exception: return 0.0

def _import_callable(module_path: str, func_name: str):
    last_ex = None
    cands = [module_path] if module_path else []
    if module_path and not module_path.startswith("odoo.addons."):
        cands.append(f"odoo.addons.{module_path}")
    for mod_name in cands:
        try:
            mod = importlib.import_module(mod_name)
            return getattr(mod, func_name)
        except Exception as ex:
            last_ex = ex
    raise UserError(_t("No se pudo importar %(m)s.%(f)s: %(e)s", m=module_path, f=func_name, e=last_ex or "unknown"))

def _iter_getpicture_candidates(url: str):
    if not url: return
    yield url
    try:
        parts = urlsplit(url)
        qs = parse_qs(parts.query, keep_blank_values=True)
        keys_lower = {k.lower(): k for k in qs.keys()}
        is_get = "getpicture" in (parts.path or "").lower()
        if not is_get: return
        def build(new_qs):
            return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, urlencode(new_qs, doseq=True), parts.fragment))
        q1 = dict(qs); q1[keys_lower.get("action","action")] = ["large"];  yield build(q1)
        q2 = dict(qs); q2[keys_lower.get("action","action")] = ["normal"]; yield build(q2)
        q3 = {k:v for k,v in qs.items() if k.lower()!="action"};           yield build(q3)
    except Exception:
        return

def _download_first_ok(url: str):
    if not (requests and url): return None
    for c in _iter_getpicture_candidates(url):
        try:
            r = requests.get(c, timeout=30)
            if r.ok and r.content: return r.content
        except Exception:
            continue
    return None

# =================== MODELOS ===================
class VendorCatalogConfig(models.Model):
    _name = "vendor.catalog.config"
    _description = "Configuración de importación de catálogo de proveedor"
    _rec_name = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    vendor_id = fields.Many2one("res.partner", string="Proveedor", domain=[("supplier_rank", ">", 0)])
    map_by = fields.Selection([
        ("supplierinfo","Código proveedor (recomendado)"),
        ("default_code","Referencia interna (SKU)"),
        ("barcode","Código de barras"),
    ], default="supplierinfo", required=True)

    feed_format = fields.Selection([("json","JSON"),("csv","CSV"),("python","Script Python")], default="python", required=True)
    feed_url = fields.Char("URL del feed/endpoint")
    auth_token = fields.Char("Token/Bearer")

    python_module = fields.Char("Módulo Python", default="vendor_catalog_import.myscript.catalog")
    python_callable = fields.Char("Función", default="get_items")
    python_kwargs = fields.Text("Parámetros (JSON)", help='Ej.: {"min_stock": 1}')

    default_categ_id = fields.Many2one("product.category", string="Categoría por defecto")
    publish_on_website = fields.Boolean("Publicar en tienda", default=True)
    website_id = fields.Many2one("website", string="Website")
    batch_commit = fields.Integer("Tamaño de lote (commit)", default=100)
    last_run = fields.Datetime(readonly=True)
    last_result = fields.Text(readonly=True)

    # --- helpers UI/shell ---
    def _parse_kwargs_if_any(self):
        """Devuelve dict a partir de python_kwargs con tolerancia."""
        import json, ast
        val = self.python_kwargs
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        if not isinstance(val, str):
            try:
                return dict(val) or {}
            except Exception:
                return {}
        txt = val.strip()
        if not txt:
            return {}
        try:
            return json.loads(txt)
        except Exception:
            pass
        try:
            return ast.literal_eval(txt)
        except Exception:
            return {}
    def action_run_import(self):
        self = self.with_context(replace_gallery=True)
        from odoo.exceptions import UserError
    
        self.ensure_one()
    
        # Validaciones mínimas
        if self.feed_format in ("json", "csv") and not self.feed_url:
            raise UserError(_("Debes indicar la URL del feed."))
        if self.feed_format == "python" and not (self.python_module and self.python_callable):
            raise UserError(_("Indica módulo y función Python."))
    
        created = updated = skipped = 0
        details = []
    
        items = self._fetch_items()
        for i, raw in enumerate(items, start=1):
            try:
                res = self._upsert_product(raw)
                was_created = bool(res[1]) if isinstance(res, (list, tuple)) and len(res) > 1 else False
                if was_created:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                skipped += 1
                code = (raw.get("sku") or raw.get("default_code") or raw.get("barcode") or "N/A") if isinstance(raw, dict) else "N/A"
                details.append(f"ERROR {code}: {e}")
            if getattr(self, "batch_commit", 0) and (i % self.batch_commit == 0):
                self.env.cr.commit()
    
        total = len(items)
        now = fields.Datetime.now()
        msg_line = f"Total: {total} | Creados: {created} | Actualizados: {updated} | Fallidos: {skipped}"
        result_text = msg_line if not details else "\n".join([msg_line] + details[:200])
    
        self.sudo().write({
            "last_run": now,
            "last_result": result_text,
        })
    
        # Registrar log si el modelo existe
        try:
            self.env["vendor.catalog.log"].create({
                "config_id": self.id,
                "run_date": now,
                "total": total,
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "notes": msg_line,
            })
        except Exception:
            pass
    
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Catálogo importado"),
                "message": msg_line + f"\n{_('Última ejecución')}: {now}",
                "type": "success" if skipped == 0 else "warning",
                "sticky": False,
            },
        }




    def _fetch_items(self):

        """Carga módulo/función, parsea kwargs de JSON y (si from_shell/ignore_limit) ignora 'limit'."""

        import importlib, json

        # Nombres de campos (según tu modelo)

        module_name = getattr(self, 'python_module', None) or getattr(self, 'py_module', None)

        func_name   = getattr(self, 'python_callable', None) or getattr(self, 'py_callable', None)

        raw_kwargs  = getattr(self, 'python_kwargs', None) or getattr(self, 'params_json', None) or "{}"

    

        # 1) Parámetros JSON

        try:

            kwargs = raw_kwargs if isinstance(raw_kwargs, dict) else json.loads(raw_kwargs or "{}")

            # -- IGNORAR 'limit' cuando ejecutamos desde shell/cron --

            ctx = self.env.context or {}

            if ctx.get('ignore_limit') or ctx.get('from_shell'):

                if isinstance(kwargs, dict):

                    kwargs.pop('limit', None)

        except Exception as e:

            from odoo.exceptions import UserError

            from odoo.tools.translate import _

            raise UserError(_("Parámetros JSON inválidos: %s") % e)

    

        # 2) Importar módulo/función

        if not module_name or not func_name:

            from odoo.exceptions import UserError

            from odoo.tools.translate import _

            raise UserError(_("Faltan módulo o función Python en la configuración."))

    

        mod = importlib.import_module(module_name)

        func = getattr(mod, func_name, None)

        if not callable(func):

            from odoo.exceptions import UserError

            from odoo.tools.translate import _

            raise UserError(_("La función %s no existe en %s") % (func_name, module_name))

    

        # 3) Ejecutar y normalizar

        res = func(**kwargs) or []

        if not isinstance(res, list):

            res = list(res)

        return res
    def _find_template(self, sku, barcode):
        Product = self.env["product.template"].sudo()
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        tmpl = None
        # 1) supplierinfo del partner actual
        if self.map_by == "supplierinfo" and sku:
            if self.vendor_id:
                si = SupplierInfo.search([("partner_id","=",self.vendor_id.id),("product_code","=",sku)], limit=1)
                if si: tmpl = si.product_tmpl_id
            # 2) si cambiaste de partner: intenta por cualquier supplierinfo con ese code
            if not tmpl:
                si_any = SupplierInfo.search([("product_code","=",sku)], limit=1)
                if si_any: tmpl = si_any.product_tmpl_id
        # 3) fallback por default_code / barcode
        if not tmpl and sku:
            tmpl = Product.search([("default_code","=",sku)], limit=1)
        if not tmpl and barcode:
            tmpl = Product.search([("barcode","=",barcode)], limit=1)
        return tmpl

    def _upsert_product(self, item):
        ProductT = self.env["product.template"].sudo()
        SupplierInfo = self.env["product.supplierinfo"].sudo()

        name = item.get("name") or item.get("title")
        sku = (item.get("sku") or item.get("default_code") or "").strip()
        barcode = (item.get("barcode") or "").strip()

        list_price = _robust_float(item.get("list_price"))
        cost = _robust_float(item.get("cost"))
        if cost is None:
            cost = _robust_float(item.get("price"))

        image_url = item.get("image_url")
        categ_name = item.get("category")

        if not name:
            raise UserError(_t("Falta 'name' en el item"))
        if not (sku or barcode):
            raise UserError(_t("Falta SKU/default_code o barcode en el item"))

        tmpl = self._find_template(sku, barcode)

        was_created = False
        vals = {"name": name, "sale_ok": True, "purchase_ok": True}
        desc_html = item.get("description_ecommerce")
        if desc_html and "description_ecommerce" in ProductT._fields:
            vals["description_ecommerce"] = desc_html
        if cost is not None:
            vals["standard_price"] = cost

        # === PESO: prioridad weight_0, si no existe cae a weight ===
        try:
            _w = float(str(item.get('weight') or 0).replace(',', '.'))
        except Exception:
            _w = 0.0
        if _w and _w > 0:
            if 'weight_0' in ProductT._fields:
                vals['weight_0'] = _w
            elif 'weight' in ProductT._fields:
                vals['weight'] = _w

        # Categoría
        if categ_name:
            cat = self.env["product.category"].sudo().search([("name","=",categ_name)], limit=1)
            if not cat and self.default_categ_id: cat = self.default_categ_id
            elif not cat: cat = self.env["product.category"].sudo().create({"name":categ_name})
            vals["categ_id"] = cat.id
        elif self.default_categ_id:
            vals["categ_id"] = self.default_categ_id.id

        if sku:
            vals["default_code"] = sku

        barcode_to_set = None
        if barcode:
            existing = ProductT.search([("barcode","=",barcode)], limit=1)
            if not existing or (tmpl and existing.id == tmpl.id):
                barcode_to_set = barcode
        if barcode_to_set:
            vals["barcode"] = barcode_to_set

        # Descripción extendida + x_vendor_* (tal cual tenías)
        desc_html = (item.get('description_ecommerce') or item.get('website_description') or
                     item.get('description') or item.get('description_sale'))
        if desc_html and 'description_ecommerce' in ProductT._fields:
            vals['description_ecommerce'] = desc_html
        if 'x_vendor_stock' in ProductT._fields:
            try: vstock = int(item.get('vendor_stock') or item.get('stock') or item.get('stock_total') or 0)
            except Exception: vstock = 0
            vals['x_vendor_stock'] = max(vstock, 0)
        if 'x_vendor_name' in ProductT._fields:
            vname = (item.get('vendor_name') or (self.vendor_id and self.vendor_id.display_name) or '').strip()
            vals['x_vendor_name'] = vname or False

        if tmpl:
            tmpl.write(vals)
        else:
            tmpl = ProductT.create(vals); was_created = True

        # --- Mantener un ÚNICO proveedor (el de esta configuración) por SKU ---
        if self.vendor_id and sku:
            # 1) línea del partner "nuevo"
            si_new = SupplierInfo.search([
                ('partner_id', '=', self.vendor_id.id),
                ('product_tmpl_id', '=', tmpl.id),
            ], limit=1)

            # 2) Otras líneas con el mismo SKU (product_code) y distinto partner
            si_others = SupplierInfo.search([
                ('product_tmpl_id', '=', tmpl.id),
                ('product_code', '=', sku),
                ('partner_id', '!=', self.vendor_id.id),
            ])

            if not si_new and si_others:
                # Reaprovecha la primera como "nueva"
                si_new = si_others[0]
                si_new.write({'partner_id': self.vendor_id.id})
                # Elimina duplicadas (mismo SKU)
                (si_others - si_new).unlink()
            else:
                # Si ya existía la del nuevo, borra duplicadas del mismo SKU
                if si_others:
                    si_others.unlink()

            # 3) Actualiza/crea la línea "buena"
            si_vals = {
                'partner_id': self.vendor_id.id,
                'product_tmpl_id': tmpl.id,
                'product_code': sku,
                'x_vendor_stock_info': int(vals.get('x_vendor_stock') or 0),
            }
            if si_new:
                si_new.write(si_vals)
            else:
                SupplierInfo.create(si_vals)

        if image_url and requests is not None:
            content = _download_first_ok(image_url)
            if content and _looks_like_image(content):
                try:
                    tmpl.image_1920 = base64.b64encode(content)
                except Exception as e:
                    _logger.info("Imagen principal omitida %s: %s", sku or barcode or "N/A", e)
            else:
                _logger.info("Omitida imagen no válida %s", image_url)

        if self.publish_on_website:
            if "website_published" in ProductT._fields: tmpl.website_published = True
            if self.website_id:
                if "website_ids" in ProductT._fields: tmpl.website_ids = [(4, self.website_id.id)]
                elif "website_id" in ProductT._fields: tmpl.website_id = self.website_id.id
        # --- Galería de imágenes (no bloqueante) ---
        urls = item.get("image_urls") or item.get("gallery") or item.get("images") or []
        if urls:
            try:
                replace = bool(self.env.context.get("replace_gallery"))
                tmpl.vc_apply_gallery_urls(urls, replace=replace)
            except Exception as _e:  # no cuenta como fallo del producto
                _logger = globals().get("_logger")
                if _logger:
                    _logger.info("Galería saltada %s: %s", item.get("sku") or item.get("default_code") or "N/A", _e)


        return tmpl, was_created

class VendorCatalogLog(models.Model):
    _name = "vendor.catalog.log"
    _description = "Logs de importación de catálogo"
    _order = "run_date desc, id desc"

    config_id = fields.Many2one("vendor.catalog.config", required=True, ondelete="cascade")
    run_date = fields.Datetime(default=lambda self: fields.Datetime.now())
    total = fields.Integer()
    created = fields.Integer()
    updated = fields.Integer()
    skipped = fields.Integer()
    no_extra_images = fields.Integer(string='Sin imagen extra', default=0)
    notes = fields.Text()
