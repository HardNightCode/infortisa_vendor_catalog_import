# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_vendor_stock = fields.Integer(
        string='Stock distribuidor',
        default=0,
        help='Stock informado por el proveedor (solo informativo, no inventario real).'
    )
    x_vendor_name = fields.Char(
        string='Proveedor (catálogo)',
        help='Nombre del proveedor que aporta el stock/precio.'
    )
