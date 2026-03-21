from django.shortcuts import redirect
from django.conf import settings

EXEMPT_URLS = [
    settings.LOGIN_URL,
    '/accounts/login/',
    '/accounts/logout/',
    '/accounts/password_reset/',
    '/accounts/password_reset/done/',
    '/accounts/reset/',
    '/webauthn/auth/begin/',
    '/webauthn/auth/complete/',
]


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path_info
            exempt = any(path.startswith(url) for url in EXEMPT_URLS)
            if not exempt:
                return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return self.get_response(request)
