"""Check the two new demo steps against an isolated demo server."""
import argparse
from playwright.sync_api import expect, sync_playwright


parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
base=parser.parse_args().url

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':900},reduced_motion='reduce')
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,base)
    page.goto(base,wait_until='networkidle')
    steps=page.locator('.demo-steps button')
    assert steps.count()==7
    assert '01看懂账单每一笔消费' in steps.nth(0).inner_text().replace('\n','')
    assert '02智能转账' in steps.nth(1).inner_text().replace('\n','')
    assert '03警示陌生人转账' in steps.nth(2).inner_text().replace('\n','')
    assert '04授权定时任务' in steps.nth(3).inner_text().replace('\n','')
    before=page.request.get(base+'/api/account-state').json()['balance']
    steps.nth(1).click()
    expect(page.locator('.message.user').last).to_have_text('给妈妈转500元')
    expect(page.locator('.message.agent').last).to_contain_text('请核对本次转账信息，确认后立即执行。')
    expect(page.locator('.message.agent').last.get_by_role('button',name='确认转账')).to_be_visible()
    steps.nth(2).click()
    expect(page.locator('.message.user').last).to_have_text('给0000000000001234转2000元')
    expect(page.locator('.message.agent').last).to_contain_text('请告诉我收款人开户姓名，我会生成一次性转账确认信息供你核对。')
    assert page.request.get(base+'/api/account-state').json()['balance']==before
    assert not errors,errors
    browser.close()
    print('PASS: seven demo steps, smart transfer and unsaved-account warning, no debit')
