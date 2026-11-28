"""Reviewed local extension packages. No downloads, installers, imports or trust grants."""
import base64
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from .extension_api import API_VERSION
from .extension_manifest import canonical, parse_manifest, read_bytes, relative_path
from .input_settings import directory, private_read
from .rename_noreplace import rename_noreplace
from .staging import _validate_path, StagingLimits

INSTALL_ROOT = '.codine-extensions'
MAX_BYTES = 1024 * 1024
NAME = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){0,3}\Z')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate package field.')
            result[key] = value
        return result
    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (RecursionError, UnicodeError) as error:
        raise ValueError('Package metadata must be bounded UTF-8 JSON.') from error
    fields = {'schema_version', 'name', 'version', 'extension', 'python', 'api', 'dependencies'}
    if not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Expected package schema_version 1 and documented fields only.')
    if not isinstance(data['name'], str) or not NAME.fullmatch(data['name']):
        raise ValueError('Package name must be a simple lowercase identifier, at most 48 characters.')
    if not isinstance(data['version'], str) or len(data['version']) > 32 or not VERSION.fullmatch(data['version']):
        raise ValueError('Package version requires 1–4 dotted numeric components.')
    _validate_path(data['extension'], StagingLimits())
    if data['extension'] == 'package.json': raise ValueError('Extension manifest must be separate from package.json.')
    if type(data['api']) is not int: raise ValueError('Package API version must be an integer.')
    python = data['python']
    if not isinstance(python, dict) or set(python) != {'min', 'max_exclusive'}:
        raise ValueError('Python compatibility requires min and max_exclusive.')
    for value in python.values():
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}', value):
            raise ValueError('Python versions require major.minor.')
    if tuple(map(int, python['min'].split('.'))) >= tuple(map(int, python['max_exclusive'].split('.'))):
        raise ValueError('Python compatibility interval must be nonempty.')
    deps = data['dependencies']
    if not isinstance(deps, dict) or len(deps) > 32:
        raise ValueError('At most 32 exact distribution dependencies are supported.')
    normalized = set()
    for name, value in deps.items():
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name) or
                not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+!-]{0,79}', value)):
            raise ValueError('Dependency names and exact versions must be bounded simple strings.')
        key = re.sub(r'[-_.]+', '-', name).lower()
        if key in normalized: raise ValueError('Duplicate normalized dependency name.')
        normalized.add(key)
    return data


def compatibility(package, version_lookup=importlib.metadata.version, python_version=None):
    current = tuple(python_version or sys.version_info[:2])
    low = tuple(map(int, package['python']['min'].split('.')))
    high = tuple(map(int, package['python']['max_exclusive'].split('.')))
    errors = []
    if package['api'] != API_VERSION: errors.append('Unsupported extension API version.')
    if not low <= current < high: errors.append('Current Python is outside the declared interval.')
    for name, expected in package['dependencies'].items():
        try: actual = version_lookup(name)
        except importlib.metadata.PackageNotFoundError: actual = None
        if actual != expected: errors.append(f'Dependency {name} requires exactly {expected}; installed: {actual or "missing"}.')
    return {'compatible': not errors, 'errors': errors, 'python': '.'.join(map(str, current)), 'api': API_VERSION}


def owned(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Package directories must be user-owned and not shared-writable.')


class Packages:
    def __init__(self, workspace, manager=None, version_lookup=importlib.metadata.version):
        self.workspace, self.manager, self.version_lookup = workspace, manager, version_lookup
        self.state = workspace.root / '.codine' / 'packages'
        workspace.protected_roots.add(self.state.resolve())
        self.reviews = {}

    def snapshot(self, source):
        source = relative_path(source)
        self.workspace.resolve(source, directory=True)
        rows, total = {}, 0
        def read(relative, limit):
            nonlocal total
            _validate_path(relative, StagingLimits())
            path = f'{source}/{relative}'
            target = self.workspace.resolve(path)
            self.workspace._authorize_read(target)
            raw, identity = read_bytes(self.workspace.root, path, min(limit, MAX_BYTES-total))
            try: raw.decode('utf-8')
            except UnicodeError as error: raise ValueError('Initial package format supports UTF-8 source/text files only.') from error
            total += len(raw)
            if len(Path(relative).parts) > 8:
                raise ValueError('Package file depth exceeds eight components.')
            rows[relative] = {'sha256': sha(raw), 'identity': identity, 'content': base64.b64encode(raw).decode()}
            return raw
        package = parse(read('package.json', 16384))
        manifest = parse_manifest(read(package['extension'], 65536))
        if manifest['name'] != package['name']: raise ValueError('Package and extension names must match.')
        for path in manifest['files']:
            relative = (Path(package['extension']).parent / path).as_posix()
            if relative in rows: raise ValueError('Package metadata cannot also be declared extension source.')
            read(relative, MAX_BYTES)
        directories = {str(parent) for path in rows for parent in Path(path).parents if str(parent) != '.'}
        if len(directories) > 64: raise ValueError('Package has too many directories.')
        root = self.workspace.root.stat()
        snapshot = {'source': source, 'project': str(self.workspace.root), 'project_identity': [root.st_dev, root.st_ino],
                    'package': package, 'files': rows, 'compatibility': compatibility(package, self.version_lookup)}
        snapshot['digest'] = sha(canonical(snapshot))
        return snapshot

    def review(self, source):
        snapshot = self.snapshot(source)
        if len(self.reviews) >= 16: self.reviews.pop(next(iter(self.reviews)))
        self.reviews[snapshot['digest']] = copy.deepcopy(snapshot)
        return self.summary(snapshot)

    @staticmethod
    def summary(snapshot):
        return {'digest': snapshot['digest'], 'source': snapshot['source'], 'package': snapshot['package'],
                'compatibility': snapshot['compatibility'], 'destination': INSTALL_ROOT+'/'+snapshot['package']['name'],
                'files': {name: row['sha256'] for name,row in snapshot['files'].items()}, 'trust_granted': False}

    @contextmanager
    def locked(self):
        owned(self.workspace.root.stat())
        with directory(self.workspace.root / '.codine', create=True):
            with directory(self.state, create=True) as fd:
                lock = os.open('lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600, dir_fd=fd)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                        raise ValueError('Unsafe package state lock.')
                    try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError: raise ValueError('Package store is busy; retry later.') from None
                    yield fd
                finally: os.close(lock)

    def receipts(self, fd):
        try: data=json.loads(private_read(fd,'receipts.json',MAX_BYTES))
        except FileNotFoundError: return {}
        except (RecursionError,UnicodeError) as error:raise ValueError('Invalid package receipt encoding or nesting.') from error
        if not isinstance(data,dict) or type(data.get('version')) is not int or data.get('version') != 1 or not isinstance(data.get('packages'),dict):
            raise ValueError('Invalid package receipts; inspect state manually.')
        rows = data['packages']
        if len(rows)>64: raise ValueError('Too many package receipts.')
        for name,row in rows.items():
            if (not isinstance(name,str) or not NAME.fullmatch(name) or not isinstance(row,dict) or
                    row.get('destination') != INSTALL_ROOT+'/'+name or
                    row.get('status') not in ('pending_install','installed','pending_remove','removed','removal_needs_review') or
                    not isinstance(row.get('files'),dict) or not 1<=len(row['files'])<=34 or
                    not isinstance(row.get('file_identities'),dict) or set(row['file_identities'])!=set(row['files'])):
                raise ValueError('Invalid package receipt fields.')
            for path,value in row['files'].items():
                _validate_path(path,StagingLimits())
                if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Invalid receipt hash.')
                identity=row['file_identities'][path]
                if not isinstance(identity,list) or len(identity)!=2 or any(type(v)is not int or v<0 for v in identity):
                    raise ValueError('Invalid receipt file identity.')
            identity=row.get('identity')
            if not isinstance(identity,list) or len(identity)!=2 or any(type(v)is not int or v<0 for v in identity):
                raise ValueError('Invalid receipt directory identity.')
            for key,prefix in (('staging','staging-'),('backup','removed-')):
                if key in row and (not isinstance(row[key],str) or not re.fullmatch(prefix+'[0-9a-f]{32}',row[key])):
                    raise ValueError('Invalid package recovery location.')
        return rows

    def save(self, fd, rows):
        raw=canonical({'version':1,'packages':rows})
        if len(raw)>MAX_BYTES or len(rows)>64:raise ValueError('Package receipt limit reached.')
        name='receipt-'+uuid.uuid4().hex
        handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(handle,'wb',closefd=False) as stream:
                stream.write(raw);stream.flush();os.fsync(handle)
            os.replace(name,'receipts.json',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            os.close(handle)
            try:os.unlink(name,dir_fd=fd)
            except FileNotFoundError:pass

    def list(self):
        with self.locked() as fd:
            return [{'name': name, **row} for name,row in self.receipts(fd).items()]

    def _allowed(self):
        if self.workspace.mode=='plan':raise ValueError('Package installation/removal is disabled in plan mode.')

    def _not_loaded(self,name,destination):
        if self.manager:
            for row in self.manager.list():
                if row['name']==name or Path(row['manifest_path']).is_relative_to(destination):
                    raise ValueError('Unload this extension before package installation/removal.')

    def _write_policy(self, destination, files):
        self.workspace.resolve(INSTALL_ROOT,directory=True,operation='write')
        self.workspace.resolve(destination,directory=True,operation='write')
        for parent in sorted({str(parent) for name in files for parent in Path(name).parents if str(parent)!='.'}):
            self.workspace.resolve(destination+'/'+parent,directory=True,operation='write')
        for name in files:
            self.workspace.resolve(destination+'/'+name,operation='write')

    def install(self, digest, approve, emit):
        self._allowed()
        snapshot=self.reviews.get(digest)
        if snapshot is None:raise ValueError('Review the local package in this process before installing its digest.')
        if not snapshot['compatibility']['compatible']:raise ValueError('Package compatibility check failed.')
        name=snapshot['package']['name'];destination=INSTALL_ROOT+'/'+name
        self._not_loaded(name,destination)
        self._write_policy(destination,snapshot['files'])
        with self.locked() as state:
            rows=self.receipts(state)
            if name in rows:raise ValueError('A package receipt already exists for this name; inspect it before reuse.')
            if (self.workspace.root/destination).exists() or (self.workspace.root/destination).is_symlink():
                raise ValueError('Package destination already exists.')
            emit(json.dumps(self.summary(snapshot),indent=2))
            for file,row in snapshot['files'].items():
                emit('--- /dev/null\n+++ b/'+destination+'/'+file+'\n'+''.join('+'+line+'\n' for line in base64.b64decode(row['content']).decode().splitlines()))
            if not approve('Install exactly this local package snapshot? No code runs and no extension trust is granted.'):
                return {'status':'declined'}
            if self.snapshot(snapshot['source'])['digest']!=digest:raise ValueError('Package source or compatibility changed after review; review again.')
            self._not_loaded(name,destination);self._write_policy(destination,snapshot['files'])
            # Parent is user-owned; never follow a link into another installation location.
            with directory(self.workspace.root/INSTALL_ROOT,create=True) as parent:
                try:os.stat(name,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:pass
                else:raise ValueError('Package destination changed during approval.')
                staging='staging-'+uuid.uuid4().hex
                os.mkdir(staging,0o700,dir_fd=state)
                stage_path=self.state/staging
                identities={}
                for relative,row in snapshot['files'].items():
                    with directory(stage_path/Path(relative).parent,create=True) as folder:
                        handle=os.open(Path(relative).name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=folder)
                        with os.fdopen(handle,'wb') as stream:
                            stream.write(base64.b64decode(row['content']));stream.flush();os.fsync(stream.fileno())
                            file_info=os.fstat(stream.fileno());identities[relative]=[file_info.st_dev,file_info.st_ino]
                info=stage_path.stat()
                receipt={'status':'pending_install','version':snapshot['package']['version'],'digest':digest,
                    'destination':destination,'manifest_path':destination+'/'+snapshot['package']['extension'],
                    'identity':[info.st_dev,info.st_ino], 'file_identities':identities, 'files':{key:value['sha256'] for key,value in snapshot['files'].items()},
                    'staging':staging}
                rows[name]=receipt;self.save(state,rows)
                self._contents('.codine/packages/'+staging,receipt)
                rename_noreplace(staging,name,src_dir_fd=state,dst_dir_fd=parent);os.fsync(parent)
                try:self._contents(destination,receipt)
                except (OSError,ValueError):
                    # Publication is not an atomic content compare-and-swap.
                    # Preserve the pending receipt and all bytes if a concurrent
                    # writer changed the package during the no-replace move.
                    raise ValueError('Published package changed during installation; bytes retained at destination with pending receipt. Inspect package state.') from None
                receipt['status']='installed';receipt.pop('staging');self.save(state,rows)
        return {'status':'installed','destination':destination,'manifest_path':receipt['manifest_path'],'trust_granted':False}

    def _contents(self, destination, receipt):
        root=self.workspace.root/destination
        with directory(root) as fd:
            info=os.fstat(fd)
            if [info.st_dev,info.st_ino]!=receipt['identity']:raise ValueError('Installed directory identity changed.')
            found={};count=0
            def walk(folder,prefix):
                nonlocal count
                with os.scandir(folder) as entries:
                    for entry in entries:
                        count+=1
                        if count>128:raise ValueError('Installed package contains unexpected entries.')
                        relative=(Path(prefix)/entry.name).as_posix()
                        metadata=entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(metadata.st_mode):
                            owned(metadata)
                            if not any(name.startswith(relative+'/') for name in receipt['files']):
                                raise ValueError('Installed package contains untracked directories.')
                            child=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=folder)
                            try:walk(child,relative)
                            finally:os.close(child)
                        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1:
                            if relative not in receipt['files']:raise ValueError('Installed package contains untracked files.')
                            raw,identity=read_bytes(root,relative,MAX_BYTES)
                            if identity != receipt['file_identities'][relative]:raise ValueError('Installed file identity changed.')
                            found[relative]=sha(raw)
                        else:raise ValueError('Installed package contains unsafe entries.')
            walk(fd,'')
            if found!=receipt['files']:raise ValueError('Installed files changed; removal refused.')

    def remove(self,name,approve,emit):
        self._allowed()
        if not NAME.fullmatch(name):raise ValueError('Invalid package name.')
        with self.locked() as state:
            rows=self.receipts(state);receipt=rows.get(name)
            if not receipt or receipt['status']!='installed':raise ValueError('No clean installed receipt; inspect package state.')
            destination=receipt['destination'];self._not_loaded(name,destination)
            self._write_policy(destination,receipt['files']);self._contents(destination,receipt)
            emit(json.dumps({'remove':destination,'files':receipt['files'],'recovery':'Protected package backup retained; no permanent deletion.'},indent=2))
            if not approve('Remove this unchanged, unloaded package from discovery and keep a recovery backup?'):
                return {'status':'declined'}
            self._not_loaded(name,destination);self._write_policy(destination,receipt['files']);self._contents(destination,receipt)
            backup='removed-'+uuid.uuid4().hex
            with directory(self.workspace.root/INSTALL_ROOT) as parent:
                receipt['status']='pending_remove';receipt['backup']=backup;self.save(state,rows)
                rename_noreplace(name,backup,src_dir_fd=parent,dst_dir_fd=state);os.fsync(parent);os.fsync(state)
                try:self._contents('.codine/packages/'+backup,receipt)
                except (OSError,ValueError):
                    # Keep all bytes for recovery if a concurrent edit raced the move.
                    receipt['status']='removal_needs_review';self.save(state,rows)
                    raise ValueError('Package changed during removal; bytes retained in protected backup. Inspect receipts.') from None
                receipt['status']='removed';self.save(state,rows)
        return {'status':'removed','backup':'.codine/packages/'+backup,'trust_revoked':False}


def command(argument,agent,workspace,ui):
    if getattr(agent,'_in_turn',False):raise ValueError('Package commands require the idle prompt.')
    packages=getattr(agent,'extension_packages',None)
    if packages is None:
        packages=agent.extension_packages=Packages(workspace,getattr(agent,'extension_manager',None))
    action,_,value=argument.strip().partition(' ');value=value.strip()
    if action in ('','list') and not value:result=packages.list()
    elif action=='review' and value:result=packages.review(value)
    elif action=='install' and value:result=packages.install(value,ui.approve,ui.write)
    elif action=='remove' and value:result=packages.remove(value,ui.approve,ui.write)
    else:raise ValueError('Usage: /packages list | review SOURCE_DIRECTORY | install REVIEW_DIGEST | remove NAME')
    ui.write(json.dumps(result,ensure_ascii=False,indent=2))
    return result
