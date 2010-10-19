# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2010 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   George Y. Kussumoto                <george@async.com.br>
##
##
""" Loan wizard"""

from decimal import Decimal
import datetime

from kiwi.datatypes import ValidationError, currency
from kiwi.python import Settable
from kiwi.ui.objectlist import Column, SearchColumn

from stoqlib.database.orm import ORMObjectQueryExecuter
from stoqlib.database.runtime import (get_current_branch, get_current_user,
                                      new_transaction, finish_transaction)
from stoqlib.domain.interfaces import IStorable, ISalesPerson, ITransporter
from stoqlib.domain.person import ClientView, PersonAdaptToUser, Person
from stoqlib.domain.loan import Loan, LoanItem
from stoqlib.domain.payment.group import PaymentGroup
from stoqlib.domain.sale import Sale
from stoqlib.domain.views import LoanView
from stoqlib.lib.message import info
from stoqlib.lib.translation import stoqlib_gettext
from stoqlib.lib.parameters import sysparam
from stoqlib.lib.validators import format_quantity, get_formatted_cost
from stoqlib.gui.base.dialogs import run_dialog
from stoqlib.gui.base.search import StoqlibSearchSlaveDelegate
from stoqlib.gui.base.wizards import (WizardEditorStep, BaseWizard,
                                      BaseWizardStep)
from stoqlib.gui.editors.noteeditor import NoteEditor
from stoqlib.gui.editors.personeditor import ClientEditor
from stoqlib.gui.editors.loaneditor import LoanItemEditor
from stoqlib.gui.wizards.personwizard import run_person_role_dialog
from stoqlib.gui.wizards.salequotewizard import SaleQuoteItemStep

_ = stoqlib_gettext


#
# Wizard Steps
#


class StartNewLoanStep(WizardEditorStep):
    gladefile = 'SalesPersonStep'
    model_type = Loan
    proxy_widgets = ('client', 'salesperson', 'expire_date')
    cfop_widgets = ('cfop',)

    def _setup_widgets(self):
        # Hide total and subtotal
        self.table1.hide()
        self.hbox4.hide()
        # Hide invoice number details
        self.invoice_number_label.hide()
        self.invoice_number.hide()
        # Responsible combo
        self.salesperson_lbl.set_text(_(u'Responsible:'))
        self.salesperson.set_property('model-attribute', 'responsible')
        users = PersonAdaptToUser.selectBy(is_active=True, connection=self.conn)
        items = [(u.person.name, u) for u in users]
        self.salesperson.prefill(items)
        self.salesperson.set_sensitive(False)
        # Clients combo
        clients = ClientView.get_active_clients(self.conn)
        max_results = sysparam(self.conn).MAX_SEARCH_RESULTS
        clients = clients[:max_results]
        items = [(c.name, c.client) for c in clients]
        self.client.prefill(sorted(items))
        self.client.set_property('mandatory', True)
        # expire date combo
        self.expire_date.set_property('mandatory', True)
        # CFOP combo
        self.cfop_lbl.hide()
        self.cfop.hide()
        self.create_cfop.hide()

        # Transporter Combo
        self.transporter_lbl.hide()
        self.transporter.hide()
        self.create_transporter.hide()

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()

    def next_step(self):
        return LoanItemStep(self.wizard, self, self.conn, self.model)

    def has_previous_step(self):
        return False

    def setup_proxies(self):
        self._setup_widgets()
        self.proxy = self.add_proxy(self.model,
                                    StartNewLoanStep.proxy_widgets)

    #
    #   Callbacks
    #

    def on_create_client__clicked(self, button):
        trans = new_transaction()
        client = run_person_role_dialog(ClientEditor, self, trans, None)
        if not finish_transaction(trans, client):
            return
        if len(self.client) == 0:
            self._fill_clients_combo()
        else:
            self.client.append_item(client.person.name, client)
        self.client.select(client)

    def on_expire_date__validate(self, widget, value):
        if value < datetime.date.today():
            msg = _(u"The expire date must be set to today or a future date.")
            return ValidationError(msg)

    def on_notes_button__clicked(self, *args):
        run_dialog(NoteEditor, self.wizard, self.conn, self.model, 'notes',
                   title=_("Additional Information"))


class LoanItemStep(SaleQuoteItemStep):
    """ Wizard step for loan items selection """
    model_type = Loan
    item_table = LoanItem

    def post_init(self):
        SaleQuoteItemStep.post_init(self)
        self.slave.set_editor(LoanItemEditor)

    def _has_stock(self, sellable, quantity):
        storable = IStorable(sellable.product, None)
        if storable is not None:
            balance = storable.get_full_balance(self.model.branch)
        else:
            balance = Decimal(0)
        return balance >= quantity

    def on_quantity__validate(self, widget, value):
        if value <= 0:
            return ValidationError(_(u'Quantity should be positive.'))

        sellable = self.proxy.model.sellable
        if not self._has_stock(sellable, value):
            return ValidationError(
                _(u'The quantity is greater than the quantity in stock.'))


class LoanSelectionStep(BaseWizardStep):
    gladefile = 'HolderTemplate'

    def __init__(self, wizard, conn):
        BaseWizardStep.__init__(self, conn, wizard)
        self.setup_slaves()

    def _create_filters(self):
        self.search.set_text_field_columns(['client_name'])

    def _get_columns(self):
        return [SearchColumn('id', title=_(u'Number'), sorted=True,
                             data_type=str, width=80),
                SearchColumn('responsible_name', title=_(u'Responsible'),
                             data_type=str, expand=True),
                SearchColumn('client_name', title=_(u'Name'),
                             data_type=str, expand=True),
                SearchColumn('open_date', title=_(u'Opened'),
                             data_type=datetime.date),
                SearchColumn('expire_date', title=_(u'Expire'),
                             data_type=datetime.date),
                SearchColumn('loaned', title=_(u'Loaned'),
                             data_type=Decimal),
        ]

    def _refresh_next(self, value=None):
        has_selected = self.search.results.get_selected() is not None
        self.wizard.refresh_next(has_selected)

    def get_extra_query(self, states):
        return LoanView.q.status == Loan.STATUS_OPEN

    def setup_slaves(self):
        self.search = StoqlibSearchSlaveDelegate(self._get_columns(),
                                        restore_name=self.__class__.__name__)
        self.search.enable_advanced_search()
        self.attach_slave('place_holder', self.search)
        self.executer = ORMObjectQueryExecuter()
        self.search.set_query_executer(self.executer)
        self.executer.set_table(LoanView)
        self.executer.add_query_callback(self.get_extra_query)
        self._create_filters()
        self.search.results.connect('selection-changed',
                                    self._on_results_selection_changed)
        self.search.focus_search_entry()

    #
    # WizardStep
    #

    def has_previous_step(self):
        return False

    def post_init(self):
        self.register_validate_function(self._refresh_next)
        self.force_validation()

    def next_step(self):
        loan = Loan.get(self.search.results.get_selected().id,
                        connection=self.conn)
        self.wizard.model = loan
        return LoanItemSelectionStep(self.wizard, self, self.conn, loan)

    #
    # Callbacks
    #

    def _on_results_selection_changed(self, widget, selection):
        self._refresh_next()


class LoanItemSelectionStep(BaseWizardStep):
    gladefile = 'LoanItemSelectionStep'

    def __init__(self, wizard, previous, conn, loan):
        self.loan = loan
        BaseWizardStep.__init__(self, conn, wizard, previous)
        self._original_items = {}
        self._setup_widgets()

    def _setup_widgets(self):
        self.loan_items.set_columns(self.get_columns())
        self.loan_items.add_list(self.get_saved_items())
        self.edit_button.set_sensitive(False)

    def _validate_step(self, value):
        self.wizard.refresh_next(value)

    def _edit_item(self, item):
        retval = run_dialog(LoanItemEditor, self, self.conn, item,
                            expanded_edition=True)
        if retval:
            self.loan_items.update(item)
            self._validate_step(True)

    def _create_sale(self, sale_items):
        user = get_current_user(self.conn)
        sale = Sale(connection=self.conn,
                    branch=self.loan.branch,
                    client=self.loan.client,
                    salesperson=ISalesPerson(user.person),
                    cfop=sysparam(self.conn).DEFAULT_SALES_CFOP,
                    group=PaymentGroup(connection=self.conn),
                    coupon_id=None)
        for item, quantity in sale_items:
            sale.add_sellable(item.sellable, price=item.price,
                               quantity=quantity)
        sale.order()
        return sale

    def get_saved_items(self):
        for item in self.loan.get_items():
            self._original_items[item.id] = Settable(item_id=item.id,
                                 sale_qty=item.sale_quantity or Decimal(0),
                                 return_qty=item.return_quantity or Decimal(0))
            yield item

    def get_columns(self):
        return [
            Column('id', title=_('# '), width=60, data_type=str,
                   sorted=True),
            Column('sellable.code', title=_('Code'), width=70, data_type=str),
            Column('sellable.description', title=_('Description'),
                   data_type=str, expand=True, searchable=True),
            Column('quantity', title=_('Loaned'), data_type=Decimal,
                   format_func=format_quantity),
            Column('sale_quantity', title=_('Sold'), data_type=Decimal,
                   format_func=format_quantity),
            Column('return_quantity', title=_('Returned'), data_type=Decimal,
                   format_func=format_quantity),
            Column('price', title=_('Price'), data_type=currency,
                   format_func=get_formatted_cost),]

    #
    # WizardStep
    #

    def post_init(self):
        self.register_validate_function(self._validate_step)
        self.force_validation()
        self._validate_step(False)
        self.wizard.enable_finish()

    def has_previous_step(self):
        return True

    def has_next_step(self):
        return True

    def next_step(self):
        has_returned = False
        sale_items = []
        for final in self.loan_items:
            initial = self._original_items[final.id]
            sale_quantity = final.sale_quantity - initial.sale_qty
            if sale_quantity > 0:
                sale_items.append((final, sale_quantity))
                # we have to return the product, so it will be available when
                # the user confirm the created sale.
                final.return_product(sale_quantity)

            return_quantity = final.return_quantity - initial.return_qty
            if return_quantity > 0:
                final.return_product(return_quantity)
                if not has_returned:
                    has_returned = True

        msg = ''
        if sale_items:
            self._create_sale(sale_items)
            msg = _(u'A sale was created from loan items. You can confirm '
                     'the sale in the Till application.')
        if has_returned:
            msg += _(u'\nSome products have returned to stock. You can '
                    'check the stock of the items in the Stock application.')
        if sale_items or has_returned:
            info(_(u'Close loan details...'), msg)
            self.wizard.finish()

    #
    # Kiwi Callbacks
    #

    def on_loan_items__selection_changed(self, widget, item):
        self.edit_button.set_sensitive(bool(item))

    def on_loan_items__row_activated(self, widget, item):
        self._edit_item(item)

    def on_edit_button__clicked(self, widget):
        item = self.loan_items.get_selected()
        self._edit_item(item)


#
# Main wizard
#


class NewLoanWizard(BaseWizard):
    size = (775, 400)

    def __init__(self, conn, model=None):
        title = self._get_title(model)
        model = model or self._create_model(conn)
        if model.status != Loan.STATUS_OPEN:
            raise ValueError('Invalid loan status. It should '
                             'be STATUS_OPEN')

        first_step = StartNewLoanStep(conn, self, model)
        BaseWizard.__init__(self, conn, first_step, model, title=title,
                            edit_mode=False)

    def _get_title(self, model=None):
        if not model:
            return _('New Loan Wizard')

    def _create_model(self, conn):
        return Loan(responsible=get_current_user(conn),
                    branch=get_current_branch(conn),
                    connection=conn)

    #
    # WizardStep hooks
    #

    def finish(self):
        branch = self.model.branch
        for item in self.model.get_items():
            item.do_loan(branch)
        self.retval = self.model
        self.close()


class CloseLoanWizard(BaseWizard):
    size = (775, 400)
    title = _(u'Close Loan Wizard')

    def __init__(self, conn):
        first_step = LoanSelectionStep(self, conn)
        BaseWizard.__init__(self, conn, first_step, model=None,
                            title=self.title, edit_mode=False)

    #
    # WizardStep hooks
    #

    def finish(self):
        if self.model.can_close():
            self.model.close()
        self.retval = self.model
        self.close()
