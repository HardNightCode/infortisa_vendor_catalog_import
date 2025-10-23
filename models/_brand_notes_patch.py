# -*- coding: utf-8 -*-
from odoo import models
import re

def _as_html(text):
    if not text:
        return False
    t = str(text).replace("\r\n","\n").replace("\r","\n").strip()
    # si ya es HTML, respétalo
    if "<" in t and ">" in t:
        return t
    # viñetas por línea
    lines = t.split("\n")
    bullet = re.compile(r"^\s*[\-\*•·–—]\s+\S")
    if sum(1 for ln in lines if bullet.match(ln)) >= 2:
        items = [re.sub(r"^\s*[\-\*•·–—]\s+","",ln).strip() for ln in lines if bullet.match(ln)]
        return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
    # viñetas “aplastadas” con ‘ - ’
    t_norm = re.sub(r"([.;:])\s*-\s+", r"\1 - ", t)
    if t_norm.count(" - ") >= 2:
        parts = [p.strip(" -") for p in re.split(r"\s+-\s+", t_norm) if p.strip(" -")]
        if len(parts) >= 2:
            return "<ul>" + "".join(f"<li>{p}</li>" for p in parts) + "</ul>"
    # párrafos
    paras = [p.strip() for p in t.split("\n") if p.strip()]
    return "<p>" + "</p><p>".join(paras) + "</p>" if paras else False

class VendorCatalogConfigBrandNotes(models.Model):
    _inherit = 'vendor.catalog.config'

    def _upsert_product(self, item):
        res = super()._upsert_product(item)

        # localizar plantilla
        sku = (item.get('sku') or item.get('default_code') or '').strip()
        barcode = (item.get('barcode') or '').strip()
        tmpl = self._find_template(sku, barcode)
        if not tmpl:
            return res

        PT = self.env['product.template']
        PP = self.env['product.product']
        fpt = PT._fields
        fpp = PP._fields

        vals_t, vals_p = {}, {}

        # ---- ÚNICO campo permitido: website_description (HTML) ----
        web_html = item.get('website_description')
        if not web_html:
            # por si el script mandó otras descripciones: las convertimos y usamos como fallback
            web_html = _as_html(item.get('description') or item.get('description_sale'))
        if 'website_description' in fpt:
            vals_t['website_description'] = web_html or False
        if 'website_description' in fpp:
            vals_p['website_description'] = web_html or False

        # ---- BORRAR TODAS las demás descripciones en plantilla y variantes ----
        for field in ('description', 'description_sale', 'website_short_description'):
            if field in fpt:
                vals_t[field] = False
            if field in fpp:
                vals_p[field] = False

        # Marca (opcional, no es descripción)
        brand_name = item.get('brand') or item.get('product_brand') or item.get('manufacturer')
        if brand_name:
            brand_field = 'product_brand_id' if 'product_brand_id' in fpt else ('brand_id' if 'brand_id' in fpt else None)
            if brand_field:
                brand_model = 'product.brand' if 'product.brand' in self.env else ('brand.brand' if 'brand.brand' in self.env else None)
                if brand_model:
                    Brand = self.env[brand_model].sudo()
                    brand = Brand.search([('name','=',brand_name)], limit=1)
                    if not brand:
                        brand = Brand.create({'name': brand_name})
                    vals_t[brand_field] = brand.id

        if vals_t:
            tmpl.sudo().write(vals_t)
        if vals_p and tmpl.product_variant_ids:
            tmpl.product_variant_ids.sudo().write(vals_p)

        return res
