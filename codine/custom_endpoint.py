"""Trusted custom-provider routing; never infer free usage from an endpoint URL."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def canonical_endpoint(value):
    if not isinstance(value,str) or not value or len(value)>2048 or any(ord(c)<=32 or ord(c)==127 for c in value) or '\\' in value:
        raise ValueError('Custom endpoint must be a bounded URL without whitespace or controls.')
    try:
        parsed=urlsplit(value)
        host=parsed.hostname
        port=parsed.port
    except ValueError as error:raise ValueError('Invalid custom endpoint URL.') from error
    if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError('Custom endpoint requires a host and no userinfo, query or fragment.')
    if parsed.scheme not in ('http','https'):raise ValueError('Custom endpoints require HTTP(S).')
    host=host.rstrip('.').lower()
    try:
        address=ipaddress.ip_address(host)
        loopback=address.is_loopback
        host=address.compressed
    except ValueError:
        loopback=False
        try:host=host.encode('idna').decode('ascii')
        except UnicodeError as error:raise ValueError('Invalid endpoint hostname.') from error
        if len(host)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
            raise ValueError('Invalid endpoint hostname.')
    if parsed.scheme=='http' and not loopback:
        raise ValueError('Plain HTTP is allowed only for literal loopback IP addresses; remote endpoints require HTTPS.')
    if host=='openrouter.ai' or host.endswith('.openrouter.ai') or host=='integrate.api.nvidia.com':
        raise ValueError('Use the fixed OpenRouter or NVIDIA adapter for this origin; custom cannot bypass its accounting.')
    path=parsed.path.rstrip('/')
    if '%' in path or any(part in ('.','..') for part in path.split('/')) or '//' in path:
        raise ValueError('Custom base paths must be plain normalized URL paths.')
    netloc='['+host+']' if ':' in host else host
    if port is not None and port!=({'http':80,'https':443}[parsed.scheme]):netloc+=':'+str(port)
    return urlunsplit((parsed.scheme,netloc,path,'',''))


def validate_key(key,forbidden=()):
    if not isinstance(key,str) or len(key)>8192 or not key.isascii() or key != key.strip() or any(ord(c)<32 or ord(c)==127 for c in key):
        raise ValueError('Custom credential must be bounded single-line ASCII text without surrounding whitespace.')
    if key and (key.startswith(('sk-or-', 'nvapi-')) or key in forbidden):
        raise ValueError('A known OpenRouter/NVIDIA credential cannot be reused by the custom adapter.')
    return key


def trusted(settings,field,env,default,cli=None):
    layers=[('CLI',{env:cli})] if cli is not None else []
    layers += [('shell',settings.shell),('user .env',settings.user)]
    for source,values in layers:
        if env in values:
            settings.sources[field]=source
            return values[env]
    settings.sources[field]='default'
    return default


def configure(settings,endpoint=None,acknowledged=None):
    endpoint=canonical_endpoint(trusted(settings,'custom_endpoint','CODINE_CUSTOM_ENDPOINT','',endpoint))
    choice=trusted(settings,'custom_unmetered','CODINE_CUSTOM_UNMETERED','false',acknowledged)
    if choice is not True and choice!='true':
        raise ValueError('Custom costs are unknown. Explicitly set --allow-unmetered-custom or trusted CODINE_CUSTOM_UNMETERED=true; no USD cap can be enforced.')
    key=trusted(settings,'api_key','CODINE_CUSTOM_API_KEY','')
    from .credentials import CredentialStore
    store = CredentialStore()
    if settings.sources['api_key'] == 'default':
        key = store.get('custom', endpoint)
        if key: settings.sources['api_key'] = 'user credential store'
    forbidden=tuple(value for layer in (settings.shell,settings.user,settings.legacy,settings.project)
                    for name,value in layer.items() if name in ('OPENROUTER_API_KEY','NVIDIA_API_KEY') and value)
    forbidden += tuple(value for value in (store.get('openrouter'), store.get('nvidia')) if value)
    validate_key(key,forbidden)
    if not key:
        host=urlsplit(endpoint).hostname
        try:local=ipaddress.ip_address(host).is_loopback
        except ValueError:local=False
        if not local:raise ValueError('Remote custom endpoints require a trusted CODINE_CUSTOM_API_KEY.')
        key='codine-local-no-auth'  # Nonsecret SDK placeholder, never a provider key.
        settings.sources['api_key']='nonsecret loopback placeholder'
    return {'custom_endpoint':endpoint,'custom_unmetered':True,'api_key':key,'_custom_forbidden_keys':forbidden}


def validate_session_endpoint(session):
    if session.provider=='custom':
        if canonical_endpoint(session.provider_endpoint)!=session.provider_endpoint:
            raise ValueError('Custom session endpoint must use canonical spelling.')
    elif session.provider_endpoint is not None:
        raise ValueError('Only custom sessions may carry an endpoint binding.')


def match_session(config,session):
    if session.provider=='custom' and (config.provider!='custom' or session.provider_endpoint!=config.custom_endpoint or not config.custom_unmetered):
        raise ValueError('Custom session endpoint differs or unmetered acknowledgement is missing; no request sent.')
