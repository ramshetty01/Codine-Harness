"""Explicit linked-worktree mutations with no checkout, hook or lazy fetch."""
import os
from pathlib import Path
import re
from .git_awareness import git_read, GitError


def command(root, arguments, limit=16384):
    result = git_read(root, arguments, limit=limit, timeout=10)
    if result['code'] != 0 or result['truncated']:
        raise GitError('Guarded Git worktree operation failed; no diagnostics or automatic cleanup replayed.')
    return result['data']


def lineage(root):
    top = command(root, ['rev-parse','--show-toplevel']).decode().strip()
    if Path(top).resolve() != root:
        raise GitError('Linked allocation requires the repository root as the project.')
    oid = command(root, ['rev-parse','--verify','HEAD^{commit}'],256).decode().strip()
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',oid): raise GitError('Repository needs a valid local base commit.')
    common = command(root, ['rev-parse','--path-format=absolute','--git-common-dir']).decode().strip()
    return {'base_revision':oid,'common_dir':str(Path(common).resolve())}


def attached(root, destination):
    raw = command(root, ['worktree','list','--porcelain','-z'],65536)
    for block in raw.split(b'\0\0'):
        row={}
        for field in block.split(b'\0'):
            key, _, value = field.partition(b' ')
            if key in (b'worktree',b'HEAD'): row[key.decode()] = value.decode('utf-8')
        if row.get('worktree') == str(destination): return row
    return None


def attach(root, destination, source):
    # Pin the supplied immutable object ID. Never infer/fetch a branch or checkout.
    if lineage(root)['common_dir'] != source['common_dir']: raise GitError('Parent Git lineage changed.')
    if destination.exists(): raise GitError('Git allocation destination already exists.')
    command(root,['worktree','add','--detach','--no-checkout','--lock','--reason','Codine isolated editing',str(destination),source['base_revision']])
    row=attached(root,destination)
    if not row or row.get('HEAD') != source['base_revision']: raise GitError('Linked worktree identity could not be verified.')


def detach(root, destination, source):
    if lineage(root)['common_dir'] != source['common_dir']: raise GitError('Parent Git lineage changed.')
    row=attached(root,destination)
    if row is None:
        if destination.exists(): raise GitError('Directory is not registered as the expected worktree; refusing removal.')
        return
    if destination.exists():
        common=command(destination,['rev-parse','--path-format=absolute','--git-common-dir']).decode().strip()
        if str(Path(common).resolve()) != source['common_dir']:
            raise GitError('Child Git linkage changed; refusing cleanup.')
    # Unlock only this exact registered destination; no pruning, reset or branch deletion.
    result=git_read(root,['worktree','unlock',str(destination)],limit=1024,timeout=10)
    # Already-unlocked is harmless; remove must still succeed and postverify.
    command(root,['worktree','remove','--force',str(destination)])
    if attached(root,destination) is not None or destination.exists(): raise GitError('Worktree cleanup remains incomplete.')
