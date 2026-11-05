"""Detailed paginated bill insights using an isolated test server and ledger."""
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
    page.locator('.demo-steps button').nth(0).click()
    expect(page.get_by_role('button',name='查看账单分析',exact=True)).to_be_visible()
    page.reload(wait_until='networkidle')
    page.get_by_role('button',name='查看账单分析',exact=True).click()
    bubble=page.locator('.bill-summary-bubble')
    expect(bubble).to_contain_text('占这月支出')
    expect(bubble).to_contain_text('平均每笔')
    bubble.get_by_role('button',name='下一段',exact=True).click()
    expect(bubble).to_contain_text('高频商户')
    expect(bubble).to_contain_text('最大单笔')
    bubble.get_by_role('button',name='下一段',exact=True).click()
    expect(bubble).to_contain_text('下月预算')
    bubble.get_by_role('button',name='下一段',exact=True).click()
    expect(bubble.get_by_role('button',name='查看消费明细',exact=True)).to_be_visible()
    bubble.get_by_role('button',name='查看消费明细',exact=True).click()
    expect(page).to_have_url(base+'/transactions')
    expect(page.get_by_role('heading',name='消费明细',exact=True)).to_be_visible()
    assert not errors,errors
    browser.close()
    print('PASS: persisted bill insights, structure/frequency/change pages, final detail navigation, no page errors')
