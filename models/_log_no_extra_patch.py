# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class VendorCatalogLogNoExtra(models.Model):
    _inherit = 'vendor.catalog.log'

    # Nuevo campo en el log
    no_extra_images = fields.Integer(string='Sin imagen extra', default=0)

    def _recalc_notes(self):
        for rec in self:
            base = _("Total: %s | Creados: %s | Actualizados: %s | Fallidos: %s") % (
                rec.total or 0, rec.created or 0, rec.updated or 0, rec.skipped or 0
            )
            # Mostrar SIEMPRE el contador de 'Sin imagen extra'
            base += _(" | Sin imagen extra: %s") % (rec.no_extra_images or 0)
            rec.notes = base

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._recalc_notes()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {'total','created','updated','skipped','no_extra_images'}.intersection(vals.keys()):
            self._recalc_notes()
        return res
