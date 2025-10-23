# -*- coding: utf-8 -*-
from odoo import models

def _smart_split_ecom_path(text):
    """
    Divide solo en separadores de ruta ' / ' o ' > ' cuando NO están dentro de paréntesis.
    Si el texto no tiene esos separadores, se devuelve como un único nodo.
    """
    s = str(text or '')
    parts, buf, depth = [], [], 0
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '(':
            depth += 1
        elif ch == ')' and depth > 0:
            depth -= 1
        # separador ' / ' (con espacios) fuera de paréntesis
        if depth == 0 and i+2 < n and s[i] == ' ' and s[i+1] == '/' and s[i+2] == ' ':
            seg = ''.join(buf).strip()
            if seg: parts.append(seg)
            buf = []; i += 3; continue
        # separador ' > ' fuera de paréntesis
        if depth == 0 and ch == '>' and i > 0 and s[i-1] == ' ' and (i+1 == n or s[i+1] == ' '):
            seg = ''.join(buf).strip()
            if seg: parts.append(seg)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    last = ''.join(buf).strip()
    if last: parts.append(last)
    return parts or [s.strip()]

class VendorCatalogEcomPublicCategoryPatch(models.Model):
    _inherit = "vendor.catalog.config"

    def _vc_get_or_create_public_category(self, path):
        Public = self.env["product.public.category"].sudo()
        parent = None
        for name in _smart_split_ecom_path(path):
            dom = [("name", "=", name), ("parent_id", "=", parent.id if parent else False)]
            if "website_id" in Public._fields and self.website_id:
                dom.append(("website_id", "=", self.website_id.id))
            cat = Public.search(dom, limit=1)
            if not cat:
                vals = {"name": name, "parent_id": parent.id if parent else False}
                if "website_id" in Public._fields and self.website_id:
                    vals["website_id"] = self.website_id.id
                cat = Public.create(vals)
            parent = cat
        return parent

    def _upsert_product(self, item):
        res = super()._upsert_product(item)
        tmpl = res[0] if isinstance(res, (list, tuple)) else res
        if not tmpl:
            return res
        ProductT = self.env["product.template"].sudo()
        if "public_categ_ids" not in ProductT._fields:
            return res

        ecom_val = (item.get("public_category")
                    or item.get("public_categories")
                    or item.get("ecom_category")
                    or item.get("categories_ecommerce")
                    or item.get("category")
                    or (tmpl.categ_id and tmpl.categ_id.display_name))
        if not ecom_val:
            return res

        values = ecom_val if isinstance(ecom_val, (list, tuple)) else [ecom_val]
        ids = []
        for val in values:
            cat = self._vc_get_or_create_public_category(val)
            if cat: ids.append(cat.id)
        if ids:
            tmpl.public_categ_ids = [(6, 0, ids)]
        return res
