# -*- coding: utf-8 -*-
{
    "name": "Vendor Catalog Import",
    "summary": "Importa catálogo de un distribuidor y publica en eCommerce",
    "version": "18.0.1.1.0",
    "author": "Tu Empresa",
    "website": "",
    "category": "Productivity",
    "license": "LGPL-3",
    "depends": ["product", "purchase", "website_sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/vendor_catalog_views.xml",
    
        "views/website_vendor_stock.xml",
        "views/product_supplierinfo_vendor_stock.xml",


        "views/website_hide_full_desc.xml",
        "views/product_list_vendor_stock.xml",
],
    "installable": True,
    "application": False,
}
