"""Shared plain helper functions (not fixtures) for the backend test suite.

These are ordinary callables that take a ``client``/``monkeypatch`` argument;
import them directly in a test module via ``from backend.tests.helpers import ...``.
"""
from backend.app.agent import chat as agent


def operation(client, command, ack=False, password='test-password-123'):
    """Preview an operation and confirm it, completing the re-auth challenge when needed."""
    p = client.post('/api/operations/preview', json=command)
    assert p.status_code == 200, p.text
    body = p.json()
    confirm_body = {'confirmed': True, 'buffer_acknowledged': ack}
    if body.get('requires_challenge'):
        ch = client.post('/api/operations/' + body['preview_id'] + '/challenge',
                         json={'password': password})
        assert ch.status_code == 200, ch.text
        confirm_body['challenge_token'] = ch.json()['challenge_token']
    return client.post('/api/operations/' + body['preview_id'] + '/confirm', json=confirm_body)


def add(client, name='阿姨', number='0000000000002789'):
    response = client.post('/api/payees', json={'nickname': name, 'holder_name': '陈某（演示）',
                                                'bank_name': '演示银行', 'account_number': number})
    assert response.status_code == 200, response.text
    return response.json()


def new_session(client, adopt=False):
    response = client.post('/api/conversations', json={'adopt_legacy': adopt})
    assert response.status_code == 200, response.text
    return {'X-Conversation-Id': response.json()['conversation_id']}


def topics(client, headers=None):
    return client.get('/api/conversation/context', headers=headers).json()['summary'].get('topics', [])


def sequence(monkeypatch, answers):
    """Replace the intent model with a scripted list of answers; returns the call log."""
    calls = []

    class Model:
        def invoke(self, messages):
            answer = answers[len(calls)]
            calls.append(messages)
            return answer(messages) if callable(answer) else answer

    monkeypatch.setattr(agent, 'intent_client', lambda: Model())
    return calls
