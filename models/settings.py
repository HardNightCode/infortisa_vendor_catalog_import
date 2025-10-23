# -*- coding: utf-8 -*-
from odoo import api, fields, models
import logging
_logger = logging.getLogger(__name__)

CRON_XMLID = 'vendor_catalog_import.ir_cron_vendor_catalog_all'
PARAM_KEY = 'vendor_catalog.cron_hours'

class VendorCatalogSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Mostrar siempre la frecuencia real del cron
    cron_hours = fields.Integer(
        string='Ejecutar cada (horas)',
        compute='_compute_cron_hours',
        inverse='_inverse_cron_hours',
        compute_sudo=True,
        help='Frecuencia global del cron que ejecuta todas las configuraciones activas.',
    )

    # --- helpers ---
    def _get_cron(self):
        return self.env.ref(CRON_XMLID, raise_if_not_found=False)

    def _read_cron_hours(self):
        """Lee SIEMPRE del cron; si no existe, cae al parámetro y luego a 6."""
        cron = self._get_cron()
        if cron:
            n = int(cron.interval_number or 0)
            if cron.interval_type == 'hours' and n > 0:
                return n
            if cron.interval_type == 'minutes' and n > 0:
                return max(1, n // 60) or 1
            if cron.interval_type == 'days' and n > 0:
                return max(1, n * 24)
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(PARAM_KEY, '6'))
        except Exception:
            return 6

    # --- defaults / compute / inverse ---
    @api.model
    def default_get(self, fields_list):
        """Evita que aparezca 0 al abrir la vista."""
        res = super().default_get(fields_list)
        if 'cron_hours' in fields_list:
            res['cron_hours'] = self._read_cron_hours()
        return res

    @api.depends()
    def _compute_cron_hours(self):
        hours = self._read_cron_hours()
        for rec in self:
            rec.cron_hours = hours

    def _inverse_cron_hours(self):
        """Guarda tanto en el cron como en ir.config_parameter."""
        hours = max(1, int(self.cron_hours or 6))
        cron = self._get_cron()
        if cron:
            cron.sudo().write({
                'active': True,
                'interval_number': hours,
                'interval_type': 'hours',
            })
        # ¡Esto es lo que faltaba!
        self.env['ir.config_parameter'].sudo().set_param(PARAM_KEY, str(hours))
        _logger.info("Sincronizado cron y parámetro %s -> %s horas", PARAM_KEY, hours)
