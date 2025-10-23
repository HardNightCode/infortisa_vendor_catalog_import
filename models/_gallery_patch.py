# -*- coding: utf-8 -*-
import re, logging
from odoo import models
from .vendor_catalog import _download_first_ok
_logger = logging.getLogger(__name__)

def _to_list_images(v):
    if not v:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if x]
    parts = re.split(r"[|\n;,]+", str(v))
    return [p.strip() for p in parts if p.strip()]

def _derive_numbered_urls(main_url, max_images=8):
    if not main_url:
        return []
    murl = str(main_url).strip()
    m = re.match(r"^(?P<base>.+?)(?P<sep>[-_])?(?P<num>\d{1,2})?\.(?P<ext>jpg|jpeg|png|webp)$", murl, flags=re.I)
    if not m:
        try:
            base, ext = murl.rsplit('.', 1)
        except ValueError:
            return []
        return [f"{base}_{i}.{ext}" for i in range(2, max_images + 1)]
    gd = m.groupdict()
    base, sep, num, ext = gd['base'], gd.get('sep') or '_', gd.get('num'), gd['ext']
    start = int(num) + 1 if num and int(num) >= 1 else 2
    return [f"{base}{sep}{i}.{ext}" for i in range(start, max_images + 1)]

class VendorCatalogConfigGallery(models.Model):
    _inherit = 'vendor.catalog.config'

    def _upsert_product(self, item):
        res = super()._upsert_product(item)

        sku = (item.get('sku') or item.get('default_code') or '').strip()
        barcode = (item.get('barcode') or '').strip()
        tmpl = self._find_template(sku, barcode)
        if not tmpl:
            return res

        Image = self.env['product.image'].sudo()

        # 1) URLs explícitas en el feed
        urls, main = [], (item.get('image_url') or '').strip()
        for k in ('images','extra_images','gallery','image_urls'):
            urls.extend(_to_list_images(item.get(k)))
        for i in range(2, 21):
            for k in (f'image{i}', f'image_{i}', f'img{i}', f'img_{i}',
                      f'imagen{i}', f'imagen_{i}', f'foto{i}', f'foto_{i}', f'image_url_{i}'):
                v = item.get(k)
                if v:
                    urls.append(str(v).strip())

        # 2) Si no hay nada, deducir por patrón numerado desde la principal
        if not urls and main:
            urls = _derive_numbered_urls(main, max_images=8)

        # 3) Limpiar duplicados y quitar la principal
        clean, seen = [], set()
        for u in urls:
            u = (u or '').strip()
            if not u or u == main or u in seen:
                continue
            seen.add(u); clean.append(u)
        if not clean:
            return res

        # 4) Evitar duplicar lo ya creado
        existing_by_url = {im.x_vendor_image_url: im for im in Image.search([
            ('product_tmpl_id','=',tmpl.id),
            ('x_vendor_image','=',True),
        ]) if im.x_vendor_image_url}

        vname = (item.get('vendor_name') or (self.vendor_id and self.vendor_id.display_name) or '').strip() or 'Proveedor'
        seq_base = (max(Image.search([('product_tmpl_id','=',tmpl.id)]).mapped('sequence') or [0]) + 10)
        created = 0

        for idx, url in enumerate(clean, start=1):
            if url in existing_by_url:
                continue
            content = _download_first_ok(url)
            if not content:
                continue
            Image.create({
                'product_tmpl_id': tmpl.id,
                'image_1920': content,
                'name': tmpl.name or sku or barcode,
                'sequence': seq_base + idx,
                'x_vendor_image': True,
                'x_vendor_image_url': url,
                'x_vendor_name': vname,
            })
            created += 1

        if created:
            _logger.info("Vendor gallery: %s/%s -> +%d images (vendor=%s)", sku, barcode, created, vname)
        return res
