# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductImageVendor(models.Model):
    _inherit = 'product.image'
    x_vendor_image = fields.Boolean(string='Imagen de proveedor', default=False)
    x_vendor_image_url = fields.Char(string='URL origen')
    x_vendor_name = fields.Char(string='Proveedor imagen')
