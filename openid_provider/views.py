# -*- coding: utf-8 -*-
# some code from http://www.djangosnippets.org/snippets/310/ by simon
# and from examples/djopenid from python-openid-2.2.4
import urllib.parse
import logging

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import render
from django.utils.translation import ugettext as _

from django.utils.encoding import smart_str
try:
    from django.views.decorators.csrf import csrf_exempt
except ImportError:
    from django.contrib.csrf.middleware import csrf_exempt

from django.contrib.auth import REDIRECT_FIELD_NAME

from openid.association import default_negotiator, encrypted_negotiator
from openid.consumer.discover import OPENID_IDP_2_0_TYPE, OPENID_2_0_TYPE
from openid.extensions import sreg, ax
from openid.fetchers import HTTPFetchingError
from openid.server.server import Server, BROWSER_REQUEST_MODES
from openid.server.trustroot import verifyReturnTo
from openid.yadis.discover import DiscoveryFailure
from openid.yadis.constants import YADIS_CONTENT_TYPE

from openid_provider import conf
from openid_provider.utils import add_sreg_data, add_ax_data, get_store

logger = logging.getLogger(__name__)

@csrf_exempt
def openid_server(request):
    """
    This view is the actual OpenID server - running at the URL pointed to by
    the <link rel="openid.server"> tag.
    """
    logger.debug('server request %s: %s',
                 request.method, request.POST or request.GET)
    server = Server(get_store(request),
        op_endpoint=request.build_absolute_uri(reverse('openid-provider-root')))

    if not request.is_secure():
        # if request is not secure allow only encrypted association sessions
        server.negotiator = encrypted_negotiator

    # Clear AuthorizationInfo session var, if it is set
    if request.session.get('AuthorizationInfo', None):
        del request.session['AuthorizationInfo']

    querydict = dict(request.POST.items())
    orequest = server.decodeRequest(querydict)
    if not orequest:
        orequest = request.session.get('OPENID_REQUEST', None)
        if orequest:
            # remove session stored data:
            del request.session['OPENID_REQUEST']
        else:
            # not request, render info page:
            data = {
                'host': request.build_absolute_uri('/'),
                'xrds_location': request.build_absolute_uri(
                    reverse('openid-provider-xrds')),
            }
            # Return empty string
            return HttpResponse("", content_type="text/plain")

    if orequest.mode in BROWSER_REQUEST_MODES:
        if not request.user.is_authenticated():
            logger.debug('no local authentication, sending landing page')
            return landing_page(request, orequest)

        openid = openid_is_authorized(request, orequest.identity,
                                      orequest.trust_root)

        if openid is not None:
            id_url = request.build_absolute_uri(
                reverse('openid-provider-identity', args=[openid.openid]))
            oresponse = orequest.answer(True, identity=id_url)
            logger.debug('orequest.answer(True, identity="%s")', id_url)
        elif orequest.immediate:
            logger.debug('checkid_immediate mode not supported')
            raise Exception('checkid_immediate mode not supported')
        else:
            request.session['OPENID_REQUEST'] = orequest
            logger.debug('redirecting to decide page')
            return HttpResponseRedirect(reverse('openid-provider-decide'))
    else:
        oresponse = server.handleRequest(orequest)
    if request.user.is_authenticated():
        add_sreg_data(request, orequest, oresponse)
        if conf.AX_EXTENSION:
            add_ax_data(request, orequest, oresponse)
    # Convert a webresponse from the OpenID library in to a Django HttpResponse
    webresponse = server.encodeResponse(oresponse)
    if webresponse.code == 200 and orequest.mode in BROWSER_REQUEST_MODES:
        response = render(request, 'openid_provider/response.html', {
            'body': webresponse.body,
        })
        logger.debug('rendering browser response')
    else:
        response = HttpResponse(webresponse.body)
        response.status_code = webresponse.code
        for key, value in webresponse.headers.items():
            response[key] = value
        logger.debug('rendering raw response')
    return response

def openid_xrds(request, identity=False, id=None):
    if identity:
        types = [OPENID_2_0_TYPE]
    else:
        types = [OPENID_IDP_2_0_TYPE, sreg.ns_uri]
        if conf.AX_EXTENSION:
            types.append(ax.AXMessage.ns_uri)
    endpoints = [request.build_absolute_uri(reverse('openid-provider-root'))]
    return render(request, 'openid_provider/xrds.xml', {
        'host': request.build_absolute_uri('/'),
        'types': types,
        'endpoints': endpoints,
    }, content_type=YADIS_CONTENT_TYPE)

def openid_decide(request):
    """
    The page that asks the user if they really want to sign in to the site, and
    lets them add the consumer to their trusted whitelist.
    # If user is logged in, ask if they want to trust this trust_root
    # If they are NOT logged in, show the landing page
    """
    orequest = request.session.get('OPENID_REQUEST')

    if not request.user.is_authenticated():
        return landing_page(request, orequest)

    openid = openid_get_identity(request, orequest.identity) if orequest else None
    if openid is None:
        return error_page(request,
            "A website tried to authenticate you using url %s, "
            "but this url is not associated with your account." %
            (orequest.identity if orequest else '<none>'))

    # We unconditionally allow access without prompting the user
    openid.trustedroot_set.create(trust_root=orequest.trust_root)
    return HttpResponseRedirect(reverse('openid-provider-root'))


def error_page(request, msg):
    return render(request, 'openid_provider/error.html', {
        'title': _('Error'),
        'msg': msg,
    })

class SafeQueryDict(QueryDict):
    """
    A custom QueryDict class that implements a urlencode method
    knowing how to excempt some characters as safe.

    Backported from Django 1.3
    """
    def urlencode(self, safe=None):
        output = []
        if safe:
            encode = lambda k, v: '%s=%s' % ((urllib.parse.quote(k, safe), urllib.parse.quote(v, safe)))
        else:
            encode = lambda k, v: urllib.parse.urlencode({k: v})
        for k, list_ in self.lists():
            k = smart_str(k, self.encoding)
            output.extend([encode(k, smart_str(v, self.encoding))
                           for v in list_])
        return '&'.join(output)

def landing_page(request, orequest, login_url=None,
                 redirect_field_name=REDIRECT_FIELD_NAME):
    """
    The page shown when the user attempts to sign in somewhere using OpenID
    but is not authenticated with the site. For idproxy.net, a message telling
    them to log in manually is displayed.
    """
    request.session['OPENID_REQUEST'] = orequest
    if not login_url:
        login_url = settings.LOGIN_URL
    path = request.get_full_path()
    login_url_parts = list(urllib.parse.urlparse(login_url))
    if redirect_field_name:
        querystring = SafeQueryDict(login_url_parts[4], mutable=True)
        querystring[redirect_field_name] = path
        login_url_parts[4] = querystring.urlencode(safe='/')
    return HttpResponseRedirect(urllib.parse.urlunparse(login_url_parts))

def openid_is_authorized(request, identity_url, trust_root):
    """
    Check that they own the given identity URL, and that the trust_root is
    in their whitelist of trusted sites.
    """
    if not request.user.is_authenticated():
        return None

    openid = openid_get_identity(request, identity_url)
    if openid is None:
        return None

    if openid.trustedroot_set.filter(trust_root=trust_root).count() < 1:
        return None

    return openid

def openid_get_identity(request, identity_url):
    """
    Select openid based on claim (identity_url).
    If none was claimed identity_url will be 'http://specs.openid.net/auth/2.0/identifier_select'
    - in that case return default one
    - if user has no default one, return any
    - in other case return None!
    """
    for openid in request.user.openid_set.iterator():
        if identity_url == request.build_absolute_uri(
                reverse('openid-provider-identity', args=[openid.openid])):
            return openid
    if identity_url == 'http://specs.openid.net/auth/2.0/identifier_select':
        # no claim was made, choose user default openid:
        openids = request.user.openid_set.filter(default=True)
        if openids.count() == 1:
            return openids[0]
        if request.user.openid_set.count() > 0:
            return request.user.openid_set.all()[0]
    return None
