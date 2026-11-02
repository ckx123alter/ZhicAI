"""One-off transfer flow against demo_server and Vite, using an isolated ledger."""
import argparse
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
base=parser.parse_args().url
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1080},reduced_motion='reduce')
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,base)
    page.goto(base,wait_until='networkidle')
    before=page.request.get(base+'/api/account-state').json()
    def send(message):
        page.locator('.composer input').fill(message)
        page.get_by_role('button',name='发送',exact=True).click()
        expect(page.locator('.composer input')).to_be_enabled()
    send('今天给妈妈转钱')
    expect(page.locator('.message.agent').last).to_have_text('这次给妈妈转多少钱？')
    expect(page.get_by_role('button',name='授权定时任务',exact=True)).to_have_count(0)
    send('500元')
    expect(page.locator('.message.agent').last).to_contain_text('请核对本次转账信息')
    page.get_by_role('button',name='确认转账',exact=True).click()
    expect(page.get_by_role('heading',name='确认转账',exact=True)).to_be_visible()
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('妈妈')
    expect(page.locator('.transfer-recipient-summary')).to_contain_text('演示银行')
    expect(page.locator('.modal-sheet input').nth(0)).to_have_value('500')
    expect(page.get_by_text('执行周期',exact=True)).to_have_count(0)
    expect(page.get_by_text('执行日',exact=True)).to_have_count(0)
    after=page.request.get(base+'/api/account-state').json()
    assert before['balance']==after['balance'] and before['tasks']==after['tasks']
    assert not errors,errors
    browser.close()
    print('PASS: immediate question, amount followup, transfer modal prefill, no task/ledger mutation or page error')
