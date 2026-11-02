"""Log in through the isolated browser-test server without touching user accounts."""


def login(page, base):
    response = page.request.post(base+'/api/auth/login',
                                 data={'username':'demo-user','password':'demo-password-123'})
    assert response.status == 200, response.text()
