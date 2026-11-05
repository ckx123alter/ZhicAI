"""Investment-limit follow-up with audited context and cashflow navigation."""
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
    def send(message):
        page.locator('.composer input').fill(message)
        page.get_by_role('button',name='发送',exact=True).click()
        expect(page.locator('.composer input')).to_be_enabled()
    send('我想买R2理财，最多能投多少？')
    expect(page.locator('.message.agent').last.locator('.message-action')).to_have_count(2)
    cap=page.request.get(base+'/api/finance/overview').json()['cashflow']['cap']
    send(f'为什么是{cap}？')
    last=page.locator('.message.agent').last
    expect(last).to_contain_text('最低预测余额')
    expect(last).to_contain_text('扣除预留警戒线')
    expect(last.locator('.message-action')).to_have_text(['查看现金流预测'])
    page.reload(wait_until='networkidle')
    page.locator('.message.agent').last.get_by_role('button',name='查看现金流预测',exact=True).click()
    expect(page).to_have_url(base+'/me')
    expect(page.get_by_role('heading',name=re.compile(r'未来 \d+ 天现金流预测'))).to_be_visible()
    assert not errors,errors
    browser.close()
    print('PASS: product question, amount-reason followup, calculation evidence, persisted forecast action and navigation')
