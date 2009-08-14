# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2009 Async Open Source <http://www.async.com.br>
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
## GNU General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   George Y. Kussumoto     <george@async.com.br>
##
""" NF-e XML document generation
"""

import datetime
import random
from xml.etree.ElementTree import ElementTree, Element, tostring

from stoqdrivers.enum import TaxType

import stoqlib
from stoqlib.domain.interfaces import ICompany, IIndividual
from stoqlib.lib.validators import format_quantity

from utils import get_uf_code_from_state_name


class NFeGenerator(object):

    def __init__(self, sale, conn):
        self._sale = sale
        self.conn = conn
        self.root = Element('NFe', xmlns='http://www.portalfiscal.inf.br/nfe')

    def __str__(self):
        return tostring(self.root)

    def _calculate_dv(self, key):
        # Pg. 72
        assert len(key) == 43

        weights = [2, 3, 4, 5, 6, 7, 8, 9]
        weights_size = len(weights)
        key_characters = list(key)
        key_numbers = [int(k) for k in key_characters]
        key_numbers.reverse()

        dv_sum = 0
        for i, key_number in enumerate(key_numbers):
            # cycle though weights
            i = i % weights_size
            dv_sum += key_number * weights[i]

        dv_mod = dv_sum % 11
        if dv_mod == 0 or dv_mod == 1:
            return '0'
        return str(11 - dv_mod)

    def _get_nfe_number(self):
        #TODO: retrieve the fiscal invoice number
        return 1

    def _get_company(self,  person):
        return ICompany(person, None)

    def _get_cnpj(self, person):
        company = self._get_company(person)
        assert company is not None
        #FIXME: fix get_cnpj_number method
        cnpj = ''.join([c for c in company.cnpj if c in '1234567890'])
        assert len(cnpj) == 14
        return cnpj

    def _get_address_data(self, person):
        """Returns a tuple in the following format:
        (street, streetnumber, district, city, state)
        """
        address = person.get_main_address()
        location = address.city_location
        return (address.street, address.streetnumber, address.district,
                location.city, location.state)

    def _add_identification(self, branch):
        # Pg. 71
        branch_location = branch.person.get_main_address().city_location
        cuf = str(get_uf_code_from_state_name(branch_location.state))

        today = datetime.date.today()
        aamm = today.strftime('%y%m')

        nnf = self._get_nfe_number()
        nfe_idenfitication = NFeIdentification(cuf, branch_location.city,
                                               nnf, today)
        # The nfe-key requires all the "zeros", so we should format the
        # values properly.
        mod = str('%02d' % nfe_idenfitication.get_attr('mod'))
        serie = str('%03d' % nfe_idenfitication.get_attr('serie'))
        cnf = str('%09d' % nfe_idenfitication.get_attr('cNF'))
        nnf_str = '%09d' % nnf
        cnpj = self._get_cnpj(branch)
        # Key format (Pg. 71):
        # cUF + AAMM + CNPJ + mod + serie + nNF + cNF + (cDV)
        key = cuf + aamm + cnpj + mod + serie + nnf_str + cnf
        cdv = self._calculate_dv(key)
        key += cdv

        nfe_idenfitication.set_attr('cDV', cdv)
        self._nfe_identification = nfe_idenfitication

        self._nfe_data = NFeData(key)
        self._nfe_data.element.append(nfe_idenfitication.element)
        self.root.append(self._nfe_data.element)

    def _add_issuer(self, issuer):
        cnpj = self._get_cnpj(issuer)
        person = issuer.person
        name = person.name
        company = self._get_company(issuer)
        state_registry = company.state_registry
        self._nfe_issuer = NFeIssuer(name, cnpj=cnpj,
                                     state_registry=state_registry)
        self._nfe_issuer.set_address(*self._get_address_data(person))
        self._nfe_data.element.append(self._nfe_issuer.element)

    def _add_recipient(self, recipient):
        person = recipient.person
        name = person.name
        individual = IIndividual(person, None)
        if individual is not None:
            cpf = ''.join([c for c in individual.cpf if c in '1234567890'])
            self._nfe_recipient = NFeRecipient(name, cpf=cpf)
        else:
            cnpj = self._get_cnpj(recipient)
            self._nfe_recipient = NFeRecipient(name, cnpj=cnpj)

        self._nfe_recipient.set_address(*self._get_address_data(person))
        self._nfe_data.element.append(self._nfe_recipient.element)

    def _add_sale_items(self, sale_items):
        for item_number, sale_item in enumerate(sale_items):
            sellable = sale_item.sellable
            # item_number should start from 1, not zero.
            item_number += 1
            nfe_item = NFeProduct(item_number)
            # cfop code without dot.
            cfop_code = self._sale.cfop.code.replace('.', '')
            nfe_item.add_product_details(sellable.code,
                                         sellable.get_description(),
                                         cfop_code,
                                         sale_item.quantity,
                                         sale_item.price,
                                         sellable.get_unit_description())

            nfe_item.add_tax_details(sellable.get_tax_constant())
            self._nfe_data.element.append(nfe_item.element)

    def generate(self):
        branch = self._sale.branch
        self._add_identification(branch)
        self._add_issuer(branch)
        self._add_recipient(self._sale.client)
        self._add_sale_items(self._sale.get_items())


class BaseNFeField(object):
    tag = u''
    # key, default value
    attributes = []

    def __init__(self):
        self._element = None
        self._data = dict(self.attributes)

    @property
    def element(self):
        if self._element is not None:
            return self._element

        self._element = Element(self.tag)
        for key, value in self.attributes:
            sub_element = Element(key)
            element_value = self._data[key] or value
            # ignore empty values
            if element_value is None:
                continue
            sub_element.text = str(element_value)
            self._element.append(sub_element)

        return self._element

    def get_attr(self, attr):
        return self._data[attr]

    def set_attr(self, attr, value):
        self._data[attr] = value

    def format_nfe_date(self, nfe_date):
        # Pg. 93 (and others)
        return nfe_date.strftime('%Y-%m-%d')

    def format_value(self, value):
        return format_quantity(value)

    def __str__(self):
        return tostring(self.element)


class NFeData(BaseNFeField):
    """
    - Attributes:

        - versao: Versao do leiaute.

        - Id: Chave de acesso da NF-e precedida do literal 'NFe'.
    """
    tag = 'infNFe'

    def __init__(self, key):
        BaseNFeField.__init__(self)
        self.element.set('versao', u'1.10')

        # Pg. 92
        assert len(key) == 44

        value = u'NFe%s' % key
        self.element.set('Id', value)


class NFeIdentification(BaseNFeField):
    """
    - Attributes:

        - cUF: Código da UF do emitente do Documento Fiscal. Utilizar a Tabela
               do IBGE de código de unidades da federação.

        - cNF: Código numérico que compõe a Chave de Acesso. Número aleatório
               gerado pelo emitente para cada NF-e para evitar acessos
               indevidos da NF-e.

        - natOp: Natureza da operação

        - indPag: 0 - Pagamento a vista (default)
                  1 - Pagamento a prazo
                  2 - outros

        - mod: Utilizar código 55 para identificação de NF-e emitida em
               substituição ao modelo 1 ou 1A.

        - serie: Série do Documento Fiscal, informar 0 (zero) para série
                 única.

        - nNF: Número do documento fiscal.

        - dEmi: Data de emissão do documento fiscal.

        - tpNF: Tipo de documento fiscal.
                0 - entrada
                1 - saída (default)

        - cMunFG: Código do município de ocorrência do fato gerador.

        - tpImp: Formato de impressão do DANFE.
                 1 - Retrato
                 2 - Paisagem (default)

        - tpEmis: Forma de emissão da NF-e
                  1 - Normal (default)
                  2 - Contingência FS
                  3 - Contingência SCAN
                  4 - Contingência DPEC
                  5 - Contingência FS-DA

        - cDV: Dígito verificador da chave de acesso da NF-e.

        - tpAmb: Identificação do ambiente.
                 1 - Produção
                 2 - Homologação

        - finNFe: Finalidade de emissão da NF-e.
                  1 - NF-e normal (default)
                  2 - NF-e complementar
                  3 - NF-e de ajuste

        - procEmi: Identificador do processo de emissão da NF-e.
                   0 - emissãp da NF-e com aplicativo do contribuinte
                       (default)
                   1 - NF-e avulsa pelo fisco
                   2 - NF-e avulsa pelo contribuinte com certificado através
                       do fisco
                   3 - NF-e pelo contribuinte com aplicativo do fisco.

        - verProc: Identificador da versão do processo de emissão (versão do
                   aplicativo emissor de NF-e)
    """
    tag = u'ide'
    attributes = [(u'cUF', ''),
                  (u'cNF', ''),
                  (u'natOp', 'venda'),
                  (u'indPag', '0'),
                  (u'mod', 55),
                  (u'serie', 0),
                  (u'nNF', ''),
                  (u'dEmi', ''),
                  (u'tpNF', '1'),
                  (u'cMunFG', ''),
                  (u'tpImp', '2'),
                  (u'tpEmis', '1'),
                  (u'cDV', ''),
                  #TODO: Change tpAmb=1 in the final version.
                  (u'tpAmb', '2'),
                  (u'finNFe', '1'),
                  (u'procEmi', '0'),
                  (u'verProc', 'stoq-%s' % stoqlib.version)]

    def __init__(self, cUF, city, nnf, emission_date):
        BaseNFeField.__init__(self)

        self.set_attr('cUF', cUF)
        # Pg. 92: Random number of 9-digits
        self.set_attr('cNF', random.randint(100000000, 999999999))
        #TODO: add payment type
        #self.attributes['indPag'] = payment_type
        self.set_attr('nNF', nnf)
        self.set_attr('dEmi', self.format_nfe_date(emission_date))
        #TODO: get city code
        self.set_attr('cMunFG', '1234567')


class NFeAddress(BaseNFeField):
    """
    - Attributes:
        - xLgr: logradouro.
        - nro: número.
        - xBairro: bairro.
        - cMun: código do município.
        - xMun: nome do município.
        - UF: sigla da UF. Informar EX para operações com o exterior.
    """
    attributes = [(u'xLgr', ''),
                  (u'nro', ''),
                  (u'xBairro', ''),
                  (u'cMun', ''),
                  (u'xMun', ''),
                  (u'UF', '')]

    def __init__(self, tag, street, number, district, city, state):
        self.tag = tag
        BaseNFeField.__init__(self)
        self.set_attr('xLgr', street)
        self.set_attr('nro', number)
        self.set_attr('xBairro', district)
        self.set_attr('xMun', city)
        #TODO: add city code
        self.set_attr('cMun', '1234567')
        self.set_attr('UF', state)


class NFeIssuer(BaseNFeField):
    """
    - Attributes:
        - CNPJ: CNPJ do emitente.
        - xNome: Razão social ou nome do emitente
        - IE: inscrição estadual
    """
    tag = u'emit'
    address_tag = u'enderEmit'
    attributes = [(u'CNPJ', None),
                  (u'CPF', None), 
                  (u'xNome', ''),]

    def __init__(self, nome, cpf=None, cnpj=None, state_registry=None):
        BaseNFeField.__init__(self)
        if cnpj is not None:
            self.set_attr('CNPJ', cnpj)
        else:
            self.set_attr('CPF', cpf)

        self.set_attr('xNome', nome)
        self._ie = state_registry

    def set_address(self, street, number, district, city, state):
        address = NFeAddress(
            self.address_tag, street, number, district, city, state)
        self.element.append(address.element)
        # If we set IE in the attributes, the order will not be correct. :(
        ie_element = Element(u'IE')
        ie_element.text = self._ie
        self.element.append(ie_element)


class NFeRecipient(NFeIssuer):
    tag = 'dest'
    address_tag = u'enderDest'
    attributes = NFeIssuer.attributes


class NFeProduct(BaseNFeField):
    """
    - Attributes:
        - nItem: número do item
    """
    tag = u'det'

    def __init__(self, number):
        BaseNFeField.__init__(self)
        self.element.set('nItem', str(number))

    def add_product_details(self, code, description, cfop, quantity, price,
                            unit):
        details = NFeProductDetails(code, description, cfop, quantity, price,
                                    unit)
        self.element.append(details.element)

    def add_tax_details(self, sellable_tax):
        nfe_tax = NFeTax()
        nfe_icms = NFeICMS()
        nfe_pis = NFePIS()
        nfe_cofins = NFeCOFINS()

        tax_type = sellable_tax.tax_type
        if tax_type == TaxType.SUBSTITUTION:
            # Substituição Tributária/ICMS
            pass
        elif tax_type == TaxType.SERVICE:
            # ISS
            pass
        else:
            # Não tributado ou Isento/ICMS
            icms = NFeICMS40(tax_type)
            nfe_icms.element.append(icms.element)
            pis = NFePISOutr()
            nfe_pis.element.append(pis.element)
            cofins = NFeCOFINSOutr()
            nfe_cofins.element.append(cofins.element)

        nfe_tax.element.append(nfe_icms.element)
        nfe_tax.element.append(nfe_pis.element)
        nfe_tax.element.append(nfe_cofins.element)
        self.element.append(nfe_tax.element)



class NFeProductDetails(BaseNFeField):
    """
    - Attributes:
        - cProd: Código do produto ou serviço. Preencher com CFOP caso se
                 trate de itens não relacionados com mercadorias/produtos e
                 que o contribuinte não possua codificação própria.

        - cEAN: GTIN (Global Trade Item Number) do produto, antigo código EAN
                ou código de barras.

        - xProd: Descrição do produto ou serviço.

        - NCM: Código NCM. Preencher de acordo com a tabela de capítulos da
                NCM. EM caso de serviço, não incluir a tag.

        - CFOP: Código fiscal de operações e prestações. Serviço, não incluir
                a tag.

        - uCom: Unidade comercial. Informar a unidade de comercialização do
                produto.

        - qCom: Quantidade comercial. Informar a quantidade de comercialização
                do produto.

        - vUnCom: Valor unitário de comercialização. Informar o valor unitário
                  de comercialização do produto.

        - vProd: Valor total bruto dos produtos ou serviços.

        - cEANTrib: GTIN da unidade tributável, antigo código EAN ou código de
                    barras.

        - uTrib: Unidade tributável.

        - qTrib: Quantidade tributável.

        - vUnTrib: Valor unitário de tributação.
    """
    tag = u'prod'
    attributes = [(u'cProd', ''),
                  (u'cEAN', ''),
                  (u'xProd', ''),
                  (u'CFOP', ''),
                  (u'uCom', u'un'),
                  (u'qCom', ''),
                  (u'vUnCom', u'un'),
                  (u'vProd', ''),
                  (u'cEANTrib', ''),
                  (u'uTrib', u'un'),
                  (u'qTrib', ''),
                  (u'vUnTrib', '')]

    def __init__(self, code, description, cfop, quantity, price, unit):
        BaseNFeField.__init__(self)
        self.set_attr('cProd', code)
        self.set_attr('xProd', description)
        self.set_attr('CFOP', cfop)
        self.set_attr('vUnCom', self.format_value(price))
        self.set_attr('vUnTrib', self.format_value(price))
        self.set_attr('vProd', self.format_value(quantity * price))
        self.set_attr('qCom', self.format_value(quantity))
        self.set_attr('qTrib', self.format_value(quantity))


class NFeTax(BaseNFeField):
    tag = 'imposto'


class NFeICMS(BaseNFeField):
    tag = 'ICMS'


class NFeICMS00(BaseNFeField):
    """Tributada integralmente (CST=00).

    - Attributes:

        - orig: Origem da mercadoria.
                0 – Nacional
                1 – Estrangeira – Importação direta
                2 – Estrangeira – Adquirida no mercado interno

        - CST: Tributação do ICMS - 00 Tributada integralmente.

        - modBC: Modalidade de determinação da BC do ICMS.
                 0 - Margem Valor Agregado (%) (default)
                 1 - Pauta (Valor)
                 2 - Preço Tabelado Máx. (valor)
                 3 - Valor da operação

        - vBC: Valor da BC do ICMS.

        - pICMS: Alíquota do imposto.

        - vICMS: Valor do ICMS
    """
    tag = 'ICMS00'
    attributes = [(u'orig', '0'),
                  (u'CST', '00'),
                  (u'modBC', None),
                  (u'vBC', None),
                  (u'pICMS', None),
                  (u'vICMS', None),]


class NFeICMS10(NFeICMS00):
    """Tributada com cobrança do ICMS por substituição tributária (CST=10).
    - Attributes:

        - modBCST: Modalidade de determinação da BC do ICMS ST.
                   0 - Preço tabelado ou máximo sugerido
                   1 - Lista negativa (valor)
                   2 - Lista positiva (valor)
                   3 - Lista neutra (valor)
                   4 - Margem valor agregado (%)
                   5 - Pauta (valor)

        - pMVAST: Percentual da margem de valor adicionado do ICMS ST.

        - pRedBCST: Percentual da redução de BC do ICMS ST.

        - vBCST: Valor da BC do ICMS ST.

        - pICMSST: Alíquota do imposto do ICMS ST.

        - vICMSST: Valor do ICMS ST.
    """
    tag = 'ICMS10'
    attributes = NFeICMS00.attributes
    attributes.extend([(u'modBCST', ''),
                       (u'pMVAST', ''),
                       (u'pRedBCST', ''),
                       (u'vBCST', ''),
                       (u'pICMSST', ''),
                       (u'vICMSST', '',)])


class NFeICMS20(NFeICMS00):
    """Tributada com redução de base de cálculo (CST=20).

    - Attributes:

        - pRedBC: Percentual de Redução de BC.
    """
    tag = 'ICMS20'
    attributes = NFeICMS00.attributes
    attributes.append(('pRedBC', ''))


class NFeICMS30(NFeICMS10):
    """Isenta ou não tributada e com cobrança do ICMS por substituição
    tributária (CST=30).
    """
    tag = 'ICMS30'
    attributes = NFeICMS00.attributes


class NFeICMS40(BaseNFeField):
    """Isenta (CST=40), Não tributada (CST=41), Suspensão (CST=50).
    """
    tag = 'ICMS40'
    attributes = [('orig', ''), (u'CST', 40)]

    def __init__(self, tax_type):
        BaseNFeField.__init__(self)

        if tax_type == TaxType.EXEMPTION:
            cst = 40
        elif tax_type == TaxType.NONE:
            cst = 41

        self.set_attr('CST', cst)
        self.set_attr('orig', '0')


# Pg. 117
class NFePIS(BaseNFeField):
    tag = u'PIS'


# Pg. 117, 118
class NFePISAliq(BaseNFeField):
    """
    - Attributes:
        - CST: Código de Situação tributária do PIS.
               01 - operação tributável (base de cáculo - valor da operação
               normal (cumulativo/não cumulativo))
               02 - operação tributável (base de cálculo = valor da operação
               (alíquota diferenciada))

        - vBC: Valor da base de cálculo do PIS.

        - pPIS: Alíquota do PIS (em percentual).

        - vPIS: Valor do PIS.
    """
    tag = u'PISAliq'
    attributes = [(u'CST', ''),
                  (u'vBC', '0'),
                  (u'pPIS', '0'),
                  (u'vPIS', '0')]


# Pg. 118
class NFePISOutr(NFePISAliq):
    """
    - Attributes:
        - CST: Código da situação tributária do PIS.
            99 - Operação tributável (tributação monofásica (alíquota zero))
    """
    tag = u'PISOutr'
    attributes = NFePISAliq.attributes

    def __init__(self):
        NFePISAliq.__init__(self)
        self.set_attr('CST', '99')


# Pg. 120, 121
class NFeCOFINS(BaseNFeField):
    tag = u'COFINS'


# Pg. 121
class NFeCOFINSAliq(BaseNFeField):
    """
    - Attributes:
        - CST: Código de situação tributária do COFINS.
               01 - Operação tributável (base de cálculo = valor da operação
               alíquota normal (cumulativo/não cumulativo).

               02 - Operação tributável (base de cálculo = valor da operação
               (alíquota diferenciada)).

        - vBC: Valor da base do cálculo da COFINS.
        - pCOFINS: Alíquota do COFINS (em percentual).
        - vCOFINS: Valor do COFINS.
    """
    tag = u'COFINSAliq'
    attributes = [(u'CST', ''),
                  (u'vBC', '0'),
                  (u'pCOFINS', '0'),
                  (u'vCOFINS', '0')]


# Pg. 121
class NFeCOFINSOutr(NFeCOFINSAliq):
    """
    - Attributes:
        - CST: Código da situação tributária do COFINS.
            99 - Outras operações

    """
    tag = u'COFINSOutr'
    attributes = NFeCOFINSAliq.attributes

    def __init__(self):
        NFeCOFINSAliq.__init__(self)
        self.set_attr('CST', '99')


# Pg. 123
class NFeTotal(BaseNFeField):
    tag = u'total'


# Pg. 123
class NFeICMSTotal(BaseNFeField):
    """
    - Attributes:

        - vBC: Base de Cálculo do ICMS.
        - vICMS: Valor Total do ICMS.
        - vBCST: Base de Cálculo do ICMS ST.
        - vST: Valor Total do ICMS ST.
        - vProd    Valor Total dos produtos e serviços.
        - vFrete: Valor Total do Frete.
        - vSeg: Valor Total do Seguro.
        - vDesc: Valor Total do Desconto.
        - vII Valor Total do II.
        - vIPI: Valor Total do IPI.
        - vPIS: Valor do PIS.
        - vCOFINS Valor do COFINS.
        - vOutro: Outras Despesas acessórias.
        - vNF: Valor Total da NF-e.
    """
    tag = u'ICMSTot'
    attributes = dict(vBC='',
                      vICMS='',
                      vBCST='',
                      vST='',
                      vProd='',
                      vFrete='',
                      vSeg='',
                      vDesc='',
                      vII='',
                      vIPI='',
                      vCOFINS='',
                      vOutro='',
                      vNF='')


# Pg. 124
class NFeTransport(BaseNFeField):
    """
    - Attributes:
        - modFrete: Modalidade do frete.
                    0 - por conta do emitente (padrão)
                    1 - por conta do destinatário
    """
    tag = u'transp'
    attributes = dict(modFrete='0')


# Pg. 124 (optional)
class NFeTransporter(BaseNFeField):
    """
    - Attributes:
        - CNPJ: Informar o CNPJ ou o CPF do transportador.
        - CPF: Informar o CNPJ ou o CPF do transportador.
        - xNome: Razão social ou nome.
        - IE: Inscrição estadual.
        - xEnder: Endereço completo.
        - xMun: Nome do município.
        - UF: Sigla da UF.
    """
    tag = u'transporta'
    attributes = dict(CNPJ='',
                      CPF='',
                      xNome='',
                      IE='',
                      xEnder='',
                      xMun='',
                      UF='',)


# Pg. 127
class NFeSignature(BaseNFeField):
    """Assinatura XML da NF-e segundo o padrão XML Digital Signature.
    """
    tag = u'Signature'
