# -*- coding: utf-8 -*-
from odoo import models, fields
from datetime import timedelta
import re as _re

class VendorCatalogStatsPatch(models.Model):
    _inherit = 'vendor.catalog.config'

    # Contador persistido para mostrar en la vista
    last_no_extra_images = fields.Integer(readonly=True)

    def action_run_import(self):
        """
        - Ejecuta el importador con contador 'no_extra_images' en contexto.
        - Reclasifica las imágenes no descifrables y 'Galería saltada' como 'Sin imagen extra'.
        - Recalcula Creados/Actualizados comparando snapshot previo/posterior de claves (según map_by).
        - Reescribe la PRIMERA línea: Total | Creados | Actualizados | Fallidos | Sin imagen extra.
        """
        # Mapa de 'map_by' -> campo en product.template
        field_map = {
            'sku': 'default_code',
            'vendor_code': 'default_code',
            'default_code': 'default_code',
            'barcode': 'barcode',
        }

        def _key_of(item: dict, map_by: str):
            if map_by == 'barcode':
                return (item.get('barcode')
                        or item.get('sku')
                        or item.get('vendor_code')
                        or item.get('default_code'))
            # por defecto priorizamos sku/vendor_code/default_code/barcode
            return (item.get('sku')
                    or item.get('vendor_code')
                    or item.get('default_code')
                    or item.get('barcode'))

        for cfg in self:
            # --- 1) Snapshot previo de claves del feed ---
            try:
                items = cfg._fetch_items() or []
            except Exception:
                items = []

            map_by = (cfg.map_by or 'sku').strip().lower()
            pt_field = field_map.get(map_by, 'default_code')

            keys = []
            for it in items:
                k = _key_of(it, map_by)
                if k and k not in keys:
                    keys.append(k)

            PT = cfg.env['product.template']
            pre_existing = set()
            if keys:
                pre_existing = set(PT.search([(pt_field, 'in', keys)]).mapped(pt_field))

            # margen temporal defensivo
            start = fields.Datetime.now() - timedelta(minutes=2)

            # --- 2) Ejecutar import con contador de galería ---
            stats = {'no_extra_images': 0}
            res = super(VendorCatalogStatsPatch, cfg.with_context(vc_stats=stats)).action_run_import()

            # --- 3) Parsear resultado base del super ---
            base_text = (cfg.last_result or "").rstrip("\n")
            lines = base_text.splitlines()
            if not lines:
                continue

            first = lines[0]
            tail = lines[1:]

            # Soft errors (imagen no descifrable)
            pat_soft = _re.compile(r"No se pudo descifrar este archivo como un archivo de imagen\.", _re.I)
            soft = sum(1 for ln in tail if "ERROR " in ln and pat_soft.search(ln))

            # Casos de "Galería saltada ..."
            pat_skip = _re.compile(r"Galería\s+saltada\s+[A-Z0-9]+", _re.I)
            skipped = sum(1 for ln in tail if pat_skip.search(ln))

            # Extraer totales de la primera línea
            m = _re.search(
                r"Total:\s*(\d+)\s*\|\s*Creados:\s*(\d+)\s*\|\s*Actualizados:\s*(\d+)\s*\|\s*Fallidos:\s*(\d+)",
                first
            )

            if not m:
                # Si no casa el patrón, al menos añadimos "Sin imagen extra"
                no_extra = int(stats.get('no_extra_images', 0)) + soft + skipped
                lines[0] = f"{first} | Sin imagen extra: {no_extra}"
                cfg.sudo().write({
                    'last_no_extra_images': no_extra,

                    'last_result': "\n".join(lines),
                })
                return res

            total        = int(m.group(1))
            failed_base  = int(m.group(4))

            # Fallidos reales (excluimos soft) y éxitos
            failed_real = max(0, failed_base - soft)
            success     = max(0, total - failed_real)

            # --- 4) Snapshot posterior para saber creados/actualizados ---
            post_existing = set()
            if keys:
                post_existing = set(PT.search([(pt_field, 'in', keys)]).mapped(pt_field))

            # Creados: claves del feed que no existían antes y existen ahora
            created_real = 0
            if keys:
                created_real = sum(1 for k in keys if (k not in pre_existing) and (k in post_existing))
                if created_real > success:
                    created_real = success

            updated_real = max(0, success - created_real)

            # --- 5) Sin imagen extra ---
            no_extra = int(stats.get('no_extra_images', 0)) + soft + skipped

            # --- 6) Reescribir PRIMERA línea y guardar ---
            lines[0] = (
                f"Total: {total} | Creados: {created_real} | Actualizados: {updated_real} "
                f"| Fallidos: {failed_real} | Sin imagen extra: {no_extra}"
            )
            cfg.sudo().write({
                'last_no_extra_images': no_extra,

                'last_result': "\n".join(lines),
            })
        return res