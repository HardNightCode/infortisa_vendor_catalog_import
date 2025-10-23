# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class VendorCatalogLogNotesNX(models.Model):
    _inherit = 'vendor.catalog.log'

    @api.depends('total','created','updated','skipped','no_extra_images')
    def _compute_notes(self):
        for rec in self:
            base = _("Total: %(t)s | Creados: %(c)s | Actualizados: %(u)s | Fallidos: %(f)s") % {
                't': rec.total, 'c': rec.created, 'u': rec.updated, 'f': rec.skipped
            }
            if getattr(rec, 'no_extra_images', 0):
                base += _(" | Sin imagen extra: %(x)s") % {'x': rec.no_extra_images}
            rec.notes = base
