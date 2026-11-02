"""Explicit fictional saved recipients for isolated tests only; never used by the app."""
from backend.app.services.payees import PayeeInput,save_payee

NAMES=('妈妈','房东','房东乙','房东甲','旧房东','旧收款方','测试','测试房东','测试收款人','虚构收款人','虚构收款方')


def seed_test_payees(c,names=NAMES):
    for index,name in enumerate(names,1):
        save_payee(c,PayeeInput(nickname=name,holder_name=name+'（演示）',bank_name='演示银行',account_number='000000000000'+str(1000+index)))
