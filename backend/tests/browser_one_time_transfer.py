"""Exercise the one-time bank account flow against an isolated demo ledger."""
import argparse
from playwright.sync_api import expect, sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
base=parser.parse_args().url

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':390,'height':844},reduced_motion='reduce')
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,base)
    page.goto(base,wait_until='networkidle')
    before_payees=page.request.get(base+'/api/payees').json()
    before_balance=page.request.get(base+'/api/account-state').json()['balance']
    page.get_by_role('tab',name='转账',exact=True).click()
    page.get_by_role('button',name='银行账号转账',exact=True).click()
    expect(page.get_by_role('heading',name='银行账号转账',exact=True)).to_be_visible()
    page.locator('input[name="bank_holder_name"]').fill('一次性收款人')
    page.locator('input[name="bank_bank_name"]').fill('演示银行')
    page.locator('input[name="bank_account_number"]').fill('0000000000003698')
    page.locator('input[name="bank_amount"]').fill('1.00')
    page.get_by_role('button',name='确认',exact=True).click()
    expect(page.get_by_role('heading',name='确认转账',exact=True)).to_be_visible()
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('收款账号 0000000000003698')
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('一次性收款人')
    assert page.request.get(base+'/api/payees').json()==before_payees
    page.get_by_role('button',name='确认',exact=True).click()
    expect(page.get_by_role('alert')).to_have_text('该账号不是常用收款人，请警慎核对信息。')
    assert page.request.get(base+'/api/account-state').json()['balance']==before_balance
    page.get_by_role('button',name='确认',exact=True).click()
    expect(page.get_by_role('heading',name='确认转账',exact=True)).not_to_be_visible()
    assert page.request.get(base+'/api/payees').json()==before_payees
    assert page.request.get(base+'/api/account-state').json()['balance']==round(before_balance-1,2)
    records=page.request.get(base+'/api/transfers').json()
    assert records[0]['payee_id'] is None and records[0]['account_masked']=='**** 3698'
    expect(page.locator('.recent-payee-row').filter(has_text='一次性收款人').locator('.payee-row-copy small').first).to_have_text('非常用联系人')
    page.goto(base,wait_until='networkidle')
    page.locator('.composer input').fill('向李小萌账号0000000000001004转账200元')
    page.get_by_role('button',name='发送').click()
    expect(page.locator('.message.agent').last).to_contain_text('请核对本次转账信息，确认后立即执行。')
    page.locator('.message.agent').last.get_by_role('button',name='确认转账').click()
    expect(page.get_by_role('heading',name='确认转账',exact=True)).to_be_visible()
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('收款人姓名 李小萌')
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('收款账号 0000000000001004')
    assert page.request.get(base+'/api/account-state').json()['balance']==round(before_balance-1,2)
    page.get_by_role('button',name='确认',exact=True).click()
    expect(page.get_by_role('alert')).to_have_text('该账号不是常用收款人，请警慎核对信息。')
    assert page.request.get(base+'/api/account-state').json()['balance']==round(before_balance-1,2)
    assert not errors,errors
    browser.close()
    print('PASS: bank form and chat confirmation, full account review, stranger warning before debit')
