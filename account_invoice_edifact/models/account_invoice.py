# -*- coding: utf-8 -*-
# Copyright 2019 PlanetaTIC - Marc Poch <mpoch@planetatic.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
from odoo import models, api, _
from odoo.exceptions import UserError


class AccountInvoice(models.Model):
    _name = 'account.invoice'
    _inherit = ['account.invoice', 'base.edifact']

    def edifact_make_header(self):
        edifact_header = self.edifact_invoice_init()  # INVOIC
        edifact_header += self.edifact_invoice_name(
            self.number, self.type)  # INV
        edifact_header += self.edifact_invoice_date(self.date_invoice)  # DTM
        sale_order_partner = self.partner_id
        picking = False
        if self.picking_ids:
            # FIXME: Què passa quan hi ha més d'un albarà?
            picking = self.picking_ids[0]
            if not picking.partner_id.edifact_code:
                raise UserError(_('Partner %s has no Edifact Code') %
                                picking.partner_id.name)
            edifact_header += self.edifact_invoice_references(
                'picking', picking.name, picking.date_done)
            if picking.sale_id:
                sale = picking.sale_id
                if not sale.partner_id.edifact_code:
                    raise UserError(_('Partner %s has no Edifact Code') %
                                    picking.partner_id.name)
                sale_order_partner = sale.partner_id
                edifact_header += self.edifact_invoice_references(
                    'order', sale.name, sale.date_order)

        edifact_header += self.edifact_invoice_buyer(
            sale_order_partner)  # NADBY
        edifact_header += self.edifact_invoice_receiver(
            sale_order_partner)  # NADIV
        edifact_header += self.edifact_invoice_legal_buyer(
            self.partner_id)  # NADBCO
        company_registry = self.company_id.company_registry
        edifact_header += self.edifact_invoice_supplier(
            self.company_id.partner_id, company_registry)  # NADSU
        edifact_header += self.edifact_invoice_legal_supplier(
            self.company_id.partner_id, company_registry)  # NADSCO
        if picking:
            edifact_header += self.edifact_invoice_goods_receiver(
                picking.partner_id)  # NADDP
        edifact_header += self.edifact_invoice_payer(self.partner_id)  # NADPR
        edifact_header += self.edifact_invoice_currency(
            self.currency_id)  # CUX

        return edifact_header

    def _get_edifact_tax_code(self, tax_description, tax_percent):
        tax_code = 'VAT'
        if tax_description.startswith('P_REQ')\
                or tax_description.startswith('S_REQ'):
            tax_code = 'RE'
        elif tax_description.startswith('P_RAC')\
                or tax_description.startswith('S_RAC'):
            tax_code = 'RET'
        elif tax_percent == 0.0:
            tax_code = 'EXT'
        return tax_code

    def edifact_make_body(self):
        edifact_lines = []
        index = 0
        for inv_line in self.invoice_line_ids:
            if not inv_line.product_id.barcode:
                raise UserError(
                    _('Product %s does not have barcode assigned.'))
            without_discount = inv_line.price_unit * inv_line.quantity
            with_discount = without_discount - (
                without_discount * inv_line.discount / 100.0)

            edifact_line = self.edifact_invoice_line_init(inv_line, index)
            edifact_line += self.edifact_invoice_line_description(inv_line)
            edifact_line += self.edifact_invoice_line_quantity(inv_line)
            edifact_line += self.edifact_invoice_line_price_unit(inv_line)
            for tax in inv_line.invoice_line_tax_ids:
                tax_code = self._get_edifact_tax_code(
                    tax.description, tax.amount)
                tax_amount = with_discount + with_discount * tax.amount / 100.0
                edifact_line += self.edifact_invoice_line_taxes(
                    tax_code, tax.amount, tax_amount)
            edifact_line += self.edifact_invoice_line_total(inv_line)
            edifact_lines.append(edifact_line)
            index += 1

        edifact_body = ''.join(edifact_lines)

        return edifact_body

    def _compute_edifact_invoice_result(self):
        res = {
            'untaxed_amount': self.amount_untaxed,
            'total_amount': self.amount_total,
            'total_tax_amount': self.amount_tax,
            'without_discounts': 0.0,
            'with_discounts': 0.0,
            'discounts_amount': 0.0,
        }
        taxes = {}
        for inv_line in self.invoice_line_ids:
            without_discount = inv_line.price_unit * inv_line.quantity
            with_discount = without_discount - (
                without_discount * inv_line.discount / 100.0)
            res['without_discounts'] += without_discount
            res['with_discounts'] += with_discount
            res['discounts_amount'] +=\
                without_discount * inv_line.discount / 100.0

            for tax in inv_line.invoice_line_tax_ids:
                tax_code = self._get_edifact_tax_code(
                    tax.description, tax.amount)
                tax_amount = with_discount + with_discount * tax.amount / 100.0
                if tax_code not in taxes:
                    taxes[tax_code] = {}
                if tax.amount not in taxes[tax_code]:
                    taxes[tax_code][tax.amount] = {
                        'tax_amount': 0.0,
                        'untaxed_amount': 0.0,
                    }
                taxes[tax_code][tax.amount]['tax_amount'] +=\
                    tax_amount
                taxes[tax_code][tax.amount]['untaxed_amount'] +=\
                    with_discount
        res['taxes'] = taxes
        return res

    def edifact_make_result(self):
        # Compute invoice total:
        inv_res = self._compute_edifact_invoice_result()

        edifact_result = self.edifact_invoice_detail_lines()
        edifact_result += self.edifact_invoice_amount_total(
            inv_res['untaxed_amount'], inv_res['total_amount'],
            inv_res['total_tax_amount'], inv_res['without_discounts'],
            inv_res['with_discounts'], inv_res['discounts_amount'])
        for tax_code, percent_dict in inv_res['taxes'].items():
            for tax_percent, amount_dict in percent_dict.items():
                tax_amount = amount_dict['tax_amount']
                untaxed_amount = amount_dict['untaxed_amount']
                edifact_result += self.edifact_invoice_result_taxes(
                    tax_code, tax_percent, tax_amount, untaxed_amount)

        return edifact_result

    def generate_edifact_invoice(self):

        edifact_content = self.edifact_make_header()
        edifact_content += self.edifact_make_body()
        edifact_content += self.edifact_make_result()

        return edifact_content

    def get_edifact_invoice_name(self):
        return '%s.pla' % self.number.replace('/', '_')

    @api.multi
    def invoice_export_edifact(self):
        self.ensure_one()
        assert self.type in ('out_invoice', 'out_refund')
        assert self.state in ('open', 'paid')

        edifact_content = self.generate_edifact_invoice()
        filename = self.get_edifact_invoice_name()

        ctx = {}
        attach = self.env['ir.attachment'].with_context(ctx).create({
            'name': filename,
            'res_id': self.id,
            'res_model': str(self._name),
            'datas': base64.b64encode(edifact_content.encode()),
            'datas_fname': filename,
            'type': 'binary',
            })
        action = self.env['ir.actions.act_window'].for_xml_id(
            'base', 'action_attachment')
        action.update({
            'res_id': attach.id,
            'views': False,
            'view_mode': 'form,tree'
            })
        return action
