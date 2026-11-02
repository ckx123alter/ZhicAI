"""Run against demo_server plus Vite on 15180. No live account/model is touched."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:15180')
base=parser.parse_args().url

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1080})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,base)
    page.goto(base,wait_until='networkidle')
    expect(page.get_by_text('告诉知财，你想做什么？')).to_be_visible()
    print('Rendered buttons:',page.get_by_role('button').all_text_contents()[:12])
    page.locator('.demo-steps button').nth(0).click()
    page.get_by_role('button',name='查看账单分析',exact=True).click()
    expect(page.get_by_role('heading',name='账单分析',exact=True)).to_be_visible()
    expect(page.locator('.bill-summary-bubble')).to_contain_text('占这月支出')
    page.locator('.demo-steps button').nth(3).click()
    expect(page.locator('.message.agent').last).to_contain_text('请核对定时任务信息，授权后开始执行。')
    page.get_by_role('button',name='授权定时任务',exact=True).last.click()
    expect(page.get_by_role('heading',name='授权定时任务')).to_be_visible()
    expect(page.locator('.task-sheet select').nth(0)).to_have_value('fixed_expense')
    expect(page.locator('.task-sheet select').nth(2)).to_have_value('monthly')
    expect(page.locator('.task-sheet select').nth(3)).to_have_value('5')
    expect(page.locator('.task-sheet input').nth(0)).to_have_value('3000')
    expect(page.locator('.task-sheet .recipient-choice')).to_contain_text('房东')
    assert not page.request.get(base+'/api/account-state').json()['tasks']
    page.locator('.task-sheet .t-checkbox').click()
    page.get_by_role('button',name='确认授权',exact=True).click()
    expect(page.get_by_role('dialog',name='challenge')).to_be_visible()
    page.get_by_role('textbox',name='登录密码').fill('demo-password-123')
    page.get_by_role('button',name='确认验证').click()
    expect(page.locator('.task-card')).to_contain_text('租金')
    page.get_by_role('button',name='查看安盈现金管理持仓详情',exact=True).click()
    page.get_by_role('button',name='追加购买',exact=True).click()
    page.get_by_role('button',name='确认申购',exact=True).click()
    expect(page.locator('.modal-sheet')).not_to_be_visible()
    state=page.request.get(base+'/api/account-state').json()
    assert state['balance']==11000,state
    page.reload(wait_until='networkidle')
    expect(page.get_by_role('heading',name='持仓详情',exact=True)).to_be_visible()
    page.get_by_role('button',name='赎回',exact=True).click()
    page.get_by_role('button',name='确认赎回',exact=True).click()
    expect(page.locator('.modal-sheet')).not_to_be_visible()
    state=page.request.get(base+'/api/account-state').json()
    assert state['balance']==20014.55,state
    assert not any(h['productId']=='D' for h in state['holdings'])
    page.locator('.demo-steps button').nth(6).click()
    expect(page.locator('.message.agent').last).to_contain_text('年化')
    output=Path('.test-runs/browser-result.png')
    output.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(output),full_page=True)
    assert not errors,errors
    browser.close()
    print('PASS: chat, persisted bubble, authorized task, server purchase, reload, principal/profit redemption, performance; no page errors')
