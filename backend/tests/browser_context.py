"""Cross-topic draft recovery and separate browser conversations on demo_server."""
import argparse
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
base=parser.parse_args().url
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    a=browser.new_context(reduced_motion='reduce')
    page=a.new_page()
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    from backend.tests.browser_auth import login
    login(page,base)
    page.goto(base,wait_until='networkidle')
    cid=page.evaluate("localStorage.getItem('zhicai-conversation-id')")
    headers={'X-Conversation-Id':cid}
    before=page.request.get(base+'/api/account-state').json()
    def send(message):
        page.locator('.composer input').fill(message)
        page.get_by_role('button',name='发送',exact=True).click()
        expect(page.locator('.composer input')).to_be_enabled()
    send('每月5号给测试房东交房租')
    expect(page.locator('.message.agent').last).to_contain_text('每次金额')
    send('先看看R2理财')
    send('回到刚才的房租，每次3000元')
    expect(page.locator('.message.agent').last).to_contain_text('请核对定时任务信息')
    page.reload(wait_until='networkidle')
    assert page.evaluate("localStorage.getItem('zhicai-conversation-id')")==cid
    page.get_by_role('button',name='授权定时任务',exact=True).click()
    expect(page.locator('.task-sheet input').nth(0)).to_have_value('3000')
    expect(page.locator('.task-sheet .recipient-choice')).to_contain_text('测试房东')
    expect(page.locator('.task-sheet select').nth(3)).to_have_value('5')
    after=page.request.get(base+'/api/account-state').json()
    assert before['balance']==after['balance'] and before['tasks']==after['tasks']
    b=browser.new_context(reduced_motion='reduce')
    other=b.new_page()
    other.goto(base,wait_until='networkidle')
    expect(other.get_by_text('告诉知财，你想做什么？')).to_be_visible()
    other_cid=other.evaluate("localStorage.getItem('zhicai-conversation-id')")
    assert other_cid!=cid
    assert other.request.get(base+'/api/conversation/messages',headers={'X-Conversation-Id':other_cid}).json()==[]
    assert len(page.request.get(base+'/api/conversation/messages',headers=headers).json())==6
    assert not errors,errors
    browser.close()
    print('PASS: interrupted task resumes, prefill survives reload, second browser conversation is isolated, no account writes')
