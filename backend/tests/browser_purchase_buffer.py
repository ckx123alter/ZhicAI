"""Browser regression: cashflow warning, second confirmation, identity check."""
import argparse
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
base = parser.parse_args().url

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1100, 'height': 900}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page, base)
    csrf = page.request.get(base + '/api/auth/me').json()['csrf_token']
    headers = {'X-CSRF-Token': csrf}
    risk = page.request.post(base + '/api/operations/preview',
                             data={'kind': 'risk', 'answers': [3] * 10}, headers=headers)
    assert risk.status == 200, risk.text()
    risk_id = risk.json()['preview_id']
    confirmed = page.request.post(base + f'/api/operations/{risk_id}/confirm',
                                   data={'confirmed': True}, headers=headers)
    assert confirmed.status == 200, confirmed.text()
    assert confirmed.json()['state']['riskLevel'] == 3

    page.goto(base + '/wealth/product/O', wait_until='networkidle')
    expect(page.get_by_role('heading', name='产品详情')).to_be_visible()
    page.get_by_role('button', name='申购', exact=True).click()
    expect(page.get_by_role('heading', name='确认申购')).to_be_visible()
    page.locator('.modal-sheet input').first.fill('10000')

    page.get_by_role('button', name='确认申购', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('预计最低余额将低于')
    assert page.request.get(base + '/api/account-state').json()['balance'] == 12000

    page.get_by_role('button', name='确认申购', exact=True).click()
    expect(page.get_by_role('dialog', name='challenge')).to_be_visible()
    assert page.request.get(base + '/api/account-state').json()['balance'] == 12000
    assert '当前身份无权执行此操作' not in page.locator('body').inner_text()

    page.get_by_role('textbox', name='登录密码').fill('demo-password-123')
    page.get_by_role('button', name='确认验证').click()
    expect(page.locator('.modal-sheet')).not_to_be_visible()
    state = page.request.get(base + '/api/account-state').json()
    assert state['balance'] == 2000, state
    assert any(holding['productId'] == 'O' and holding['principal'] == 10000
               for holding in state['holdings']), state
    assert not errors, errors
    browser.close()
    print('PASS: warning, second confirmation, identity check, single purchase; no false permission error')
