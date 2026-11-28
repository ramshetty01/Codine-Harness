"""Deterministic dotenv resolution; values never enter the process environment."""
import io
import os
from pathlib import Path
import stat
from dotenv import dotenv_values

from .paths import APP_ROOT, CONFIG_ROOT, IS_CHECKOUT
USER_ENV = (Path.home() / '.config' / 'codine' / '.env'
            if IS_CHECKOUT and CONFIG_ROOT == APP_ROOT else CONFIG_ROOT / '.env')
LEGACY_ENV = APP_ROOT / '.env' if IS_CHECKOUT and CONFIG_ROOT == APP_ROOT else None
SECRET_WORDS = ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')


def secret_name(name):
    # Token *counts* are public settings, not authentication tokens.
    return name.upper() not in {'CODINE_MAX_TOKENS', 'CODINE_CONTEXT_TOKENS'} and any(word in name.upper() for word in SECRET_WORDS)


def read_env(path):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError('Cannot safely open settings .env file.') from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 65536:
            raise ValueError('Settings .env must be a single-link regular file under 64 KB.')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(65537)
        after = os.fstat(descriptor)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
        if len(raw) > 65536 or identity(before) != identity(after):
            raise ValueError('Settings .env changed while reading or exceeded 64 KB.')
        try:
            # Expansion must not pull arbitrary shell secrets into ordinary settings.
            return {key: value for key, value in dotenv_values(stream=io.StringIO(raw.decode('utf-8')), interpolate=False).items()
                    if value is not None}
        except (UnicodeError, ValueError):
            raise ValueError('Invalid settings .env file.') from None
    finally:
        os.close(descriptor)


class Settings:
    def __init__(self, project, shell):
        project_path = Path(project) / '.env'
        cache = {}
        def read(path):
            path = Path(path).absolute()
            if path not in cache:
                cache[path] = read_env(path)
            return cache[path]
        self.project = read(project_path)
        self.user = read(USER_ENV) if Path(USER_ENV).absolute() != project_path.absolute() else {}
        self.legacy = read(LEGACY_ENV) if LEGACY_ENV is not None and Path(LEGACY_ENV).absolute() != project_path.absolute() else {}
        self.shell = dict(shell)
        self.sources = {}
        self.secret_values = tuple(value for values in (self.legacy, self.user, self.project, self.shell)
                                   for key, value in values.items() if value and secret_name(key))

    def get(self, field, env, default, *, cli=None, project=True):
        layers = [('CLI', {env: cli})] if cli is not None else []
        layers += [('shell', self.shell)]
        if project:
            layers += [('project .env', self.project)]
        layers += [('user .env', self.user), ('legacy checkout .env', self.legacy)]
        for source, values in layers:
            if env in values:
                self.sources[field] = source
                return values[env]
        self.sources[field] = 'default'
        return default

    def budget(self, field, env, default):
        from .budget import money
        trusted = money(self.get(field, env, default, project=False))
        if env in self.project:
            lower = money(self.project[env])
            baseline = self.sources[field]
            if lower < trusted:
                self.sources[field] = 'project .env (tightens ' + baseline + ')'
                return str(lower)
            self.sources[field] = baseline + ' (project cap cannot increase it)'
        return str(trusted)


def describe(config, active_model=None):
    """Allowlisted runtime snapshot; never serialize Config or environment maps."""
    sources = dict(config.setting_sources)
    model = active_model if isinstance(active_model, str) else config.model
    if model != config.model:
        sources['model'] = 'active session override (/model or session switch)'
    active = config.request_limits(model)
    values = {
        'project': str(config.project), 'provider': config.provider, 'model': model,
        'max_tokens': config.max_tokens, 'max_steps': config.max_steps,
        'context_chars': config.context_chars, 'context_tokens': config.context_tokens,
        'model_limits': f'{len(config.model_limits)} overrides; model keys omitted',
        'budget_usd': config.budget_usd, 'request_budget_usd': config.request_budget_usd,
        'show_reasoning': config.show_reasoning,
    }
    if config.provider == 'custom':
        values.update(custom_endpoint=config.custom_endpoint, custom_unmetered=config.custom_unmetered)
    sources['show_reasoning'] = 'runtime display toggle (default off)'
    rows = ['Settings snapshot · restart to reload files/shell; no provider request made.']
    for key, value in values.items():
        rows.append(f'{key}: {value} [{sources.get(key, "programmatic/default")}]')
    rows.append(f'api_key: value hidden [{sources.get("api_key", "programmatic/default")}]')
    rows.append(f'Active local context/output limits: {active[0]}/{active[1]} (configured ceilings, not live model metadata).')
    rows.append('File/shell values are a startup snapshot; active model and reasoning display reflect runtime overrides. Budget values initialize new sessions; existing session/shared ledgers stay authoritative and are not reset or rewritten. /budget shows active ledger limits.')
    return config.redactor().text('\n'.join(rows))
