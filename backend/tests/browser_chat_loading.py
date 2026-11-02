"""Pending-chat UI regression. All chat/history writes are intercepted locally.

Run against the local Vite service; no real LLM request or account operation.
"""
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:5180'
QUESTION = '帮我分析上个月的账单'


def isolated_page(browser):
    page = browser.new_page()
    pending, history, writes, errors = [], [], [], []

    def bootstrap(route):
        response = route.fetch()
        data = response.json()
        data['messages'] = []
        route.fulfill(response=response, json=data)

    def messages(route):
        if route.request.method != 'GET':
            writes.append(route.request.method)
            route.abort()
        else:
            route.fulfill(json=history)

    page.route('**/api/bootstrap', bootstrap)
    page.route('**/api/conversation/messages', messages)
    page.route('**/api/chat', lambda route: pending.append(route))
    page.on('pageerror', lambda error: errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,BASE)
    page.goto(BASE, wait_until='networkidle')
    expect(page.get_by_text('告诉知财，你想做什么？')).to_be_visible()
    return page, pending, history, writes, errors


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    page, pending, history, writes, errors = isolated_page(browser)
    page.locator('.demo-steps button').nth(0).click()
    expect(page.locator('.message.user')).to_have_text(QUESTION)
    expect(page.locator('.chat-thinking')).to_be_visible()
    assert len(pending) == 1
    expect(page.get_by_text('告诉知财，你想做什么？')).not_to_be_visible()
    # The request has not returned: the question and loading state must already exist.
    pending.pop().fulfill(status=503, json={'detail': '意图解析阶段失败，请重试'})
    expect(page.locator('.demo-steps button').nth(0)).to_be_enabled()
    expect(page.locator('.message.user')).to_have_text(QUESTION)
    expect(page.locator('.message.agent')).to_have_text('抱歉，知财暂不支持这类请求。')
    expect(page.get_by_text('意图解析阶段失败，请重试')).not_to_be_visible()
    expect(page.locator('.chat-thinking')).not_to_be_visible()
    assert not writes and not errors, (writes, errors)
    page.close()

    page, pending, history, writes, errors = isolated_page(browser)
    page.locator('.composer input').fill('请分析上个月的消费')
    page.locator('.composer button').click()
    expect(page.locator('.message.user')).to_have_text('请分析上个月的消费')
    expect(page.locator('.chat-thinking')).to_be_visible()
    history.extend([
        {'id': 'saved-user', 'role': 'user', 'text': '请分析上个月的消费'},
        {'id': 'saved-agent', 'role': 'agent', 'text': '模拟账单回复'},
    ])
    pending.pop().fulfill(json={'id': 'saved-agent', 'text': '模拟账单回复',
                               'response_mode': 'direct', 'actions': [], 'evidence': {}})
    expect(page.locator('.message.user')).to_have_count(1)
    expect(page.locator('.message.agent')).to_have_text('模拟账单回复')
    expect(page.locator('.chat-thinking')).not_to_be_visible()
    page.locator('.composer input').fill('继续分析周期扣款')
    page.locator('.composer button').click()
    expect(page.locator('.message.user')).to_have_count(2)
    expect(page.locator('.message.user').last).to_have_text('继续分析周期扣款')
    expect(page.locator('.chat-thinking')).to_be_visible()
    pending.pop().fulfill(status=503, json={'detail': '模拟服务失败'})
    expect(page.locator('.composer input')).to_be_enabled()
    expect(page.locator('.message.agent').last).to_have_text('抱歉，知财暂不支持这类请求。')
    assert not writes and not errors, (writes, errors)
    page.close()

    page, pending, history, writes, errors = isolated_page(browser)
    page.get_by_role('tab', name='我的', exact=True).click()
    page.get_by_role('button', name='打开知财聊天', exact=True).click()
    page.locator('.chat-sheet input').fill('查询可用余额')
    page.locator('.chat-sheet .composer button').click()
    expect(page.locator('.chat-sheet .message.user')).to_have_text('查询可用余额')
    expect(page.locator('.chat-sheet .chat-thinking')).to_be_visible()
    pending.pop().fulfill(status=503, json={'detail': '模拟服务失败'})
    expect(page.locator('.chat-sheet input')).to_be_enabled()
    expect(page.locator('.chat-sheet .message.agent')).to_have_text('抱歉，知财暂不支持这类请求。')
    assert not writes and not errors, (writes, errors)
    browser.close()

print('PASS: first-step/manual/popup questions visible before response, loading visible, failure recovers, success deduplicates; no real model or chat writes')
