# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    x_vendor_stock_info = fields.Integer(
        string='Stock proveedor (info)',
        help='Stock reportado por el proveedor en la última importación. Solo informativo.',
        readonly=True
    )
