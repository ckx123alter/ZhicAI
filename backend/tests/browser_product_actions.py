"""Verify both persisted navigation actions using the isolated demo server."""
import argparse
import re
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
    page.locator('.composer input').fill('我想买R2理财，最多能投多少？')
    page.get_by_role('button',name='发送',exact=True).click()
    actions=page.locator('.message.agent').last.locator('.message-action')
    expect(actions).to_have_text(['查看理财产品','查看现金流预测'])
    page.reload(wait_until='networkidle')
    expect(page.locator('.message.agent').last.locator('.message-action')).to_have_text(['查看理财产品','查看现金流预测'])
    page.get_by_role('button',name='查看理财产品',exact=True).click()
    expect(page).to_have_url(base+'/wealth')
    expect(page.locator('.wealth-summary-bubble')).to_be_visible()
    page.goto(base,wait_until='networkidle')
    page.get_by_role('button',name='查看现金流预测',exact=True).click()
    expect(page).to_have_url(base+'/me')
    expect(page.get_by_role('heading',name=re.compile(r'未来 \d+ 天现金流预测'))).to_be_visible()
    assert not errors,errors
    browser.close()
    print('PASS: two actions persist after reload and independently navigate to products and cashflow')
