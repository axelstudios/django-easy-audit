import base64
import json
import re
import zlib
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.signals import request_started
from django.http.cookie import parse_cookie
from django.utils import timezone
from django.utils.module_loading import import_string

# try and get the user from the request; commented for now, may have a bug in this flow.
# from easyaudit.middleware.easyaudit import get_current_user
from easyaudit.settings import REMOTE_ADDR_HEADER, UNREGISTERED_URLS, REGISTERED_URLS, WATCH_REQUEST_EVENTS, \
    LOGGING_BACKEND

audit_logger = import_string(LOGGING_BACKEND)()


def should_log_url(url):
    # check if current url is blacklisted
    for unregistered_url in UNREGISTERED_URLS:
        pattern = re.compile(unregistered_url)
        if pattern.match(url):
            return False

    # only audit URLs listed in REGISTERED_URLS (if it's set)
    if len(REGISTERED_URLS) > 0:
        for registered_url in REGISTERED_URLS:
            pattern = re.compile(registered_url)
            if pattern.match(url):
                return True
        return False

    # all good    
    return True


def request_started_handler(sender, **kwargs):
    environ = kwargs.get("environ")
    scope = kwargs.get("scope")
    if environ:
        path = environ["PATH_INFO"]
        cookie_string = environ.get('HTTP_COOKIE')
        remote_ip = environ.get(REMOTE_ADDR_HEADER, None)
        method = environ['REQUEST_METHOD']
        query_string = environ["QUERY_STRING"]

    else:
        method = scope.get('method')
        path = scope.get("path")
        headers = dict(scope.get('headers'))
        cookie_string = headers.get(b'cookie')
        if isinstance(cookie_string, bytes):
            cookie_string = cookie_string.decode("utf-8")
        remote_ip = list(scope.get('client', ('0.0.0.0', 0)))[0]
        query_string = scope.get("query_string")

    if not should_log_url(path):
        return

    # try and get the user from the request; commented for now, may have a bug in this flow.
    # user = get_current_user()
    user = None
    # get the user from cookies
    if not user and cookie_string:
        cookie = parse_cookie(cookie_string)
        session_cookie_name = settings.SESSION_COOKIE_NAME
        if session_cookie_name in cookie:
            session_id = cookie[session_cookie_name]

            if settings.SESSION_ENGINE == 'django.contrib.sessions.backends.db':
                try:
                    session = Session.objects.get(session_key=session_id)
                except Session.DoesNotExist:
                    session = None

                if session:
                    user_id = session.get_decoded().get('_auth_user_id')
                    try:
                        user = get_user_model().objects.get(id=user_id)
                    except:
                        user = None

            elif settings.SESSION_ENGINE == 'django.contrib.sessions.backends.signed_cookies':
                try:
                    payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(session_id)))
                    user_id = int(payload['_auth_user_id'])
                    hash = payload['_auth_user_hash']
                    # Confirm hash to verify user
                    user = get_user_model().objects.get(id=user_id)
                    stored_hash = user.get_session_auth_hash()
                    if hash != stored_hash:
                        user = None
                except:
                    session = None
                    user = None


    # may want to wrap this in an atomic transaction later
    request_event = audit_logger.request({
        'url': path,
        'method': method,
        'query_string': query_string,
        'user_id': getattr(user, 'id', None),
        'remote_ip': remote_ip,
        'datetime': timezone.now()
    })


if WATCH_REQUEST_EVENTS:
    request_started.connect(request_started_handler, dispatch_uid='easy_audit_signals_request_started')
