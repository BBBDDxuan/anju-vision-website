"""Bearer access for the opt-in phone listener; loopback keeps the local UI usable."""
import hmac
import ipaddress

from .errors import AppError


def authorize_lan(request, token):
    if not token:
        return
    # Only the transport peer is trusted. The phone launcher disables Uvicorn's
    # proxy-header processing; X-Forwarded-For cannot turn a phone into localhost.
    peer = request.client.host if request.client else ''
    try:
        if ipaddress.ip_address(peer).is_loopback:
            return
    except ValueError:
        pass
    scheme, _, supplied = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not hmac.compare_digest(supplied.encode('utf8'), token.encode('ascii')):
        raise AppError('PHONE_AUTH_REQUIRED', '请在 App 录像服务设置中填写本机生成的访问令牌。', 401)
