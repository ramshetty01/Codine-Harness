"""User-owned command decisions; project text cannot grant execution rights."""
from dataclasses import dataclass
import json
import fnmatch
from functools import lru_cache
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata


@dataclass(frozen=True)
class Rule:
    id: str
    action: str
    executor: str
    project: str
    cwd: str
    match: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class FileRule:
    id: str
    action: str
    operation: str
    project: str
    match: str
    path: str


def file_glob_matches(path, pattern):
    """Segment-aware globs: ** spans directories, * and ? stay in one segment."""
    path, pattern = unicodedata.normalize('NFC', path), unicodedata.normalize('NFC', pattern)
    names, parts = PurePosixPath(path).parts, PurePosixPath(pattern).parts
    @lru_cache(None)
    def match(i, j):
        if i == len(parts):
            return j == len(names)
        if parts[i] == '**':
            return match(i + 1, j) or (j < len(names) and match(i, j + 1))
        return j < len(names) and fnmatch.fnmatchcase(names[j], parts[i]) and match(i + 1, j + 1)
    return match(0, 0)


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    rules: tuple[str, ...] = ()


def default_policy_path():
    # Deliberately do not use paths.CONFIG_ROOT: editable checkouts use APP_ROOT
    # for legacy model config, which must never implicitly grant command rights.
    root = (Path(os.environ['CODINE_HOME']).expanduser() if os.getenv('CODINE_HOME') else
            Path(os.getenv('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'codine')
    return root/'command-policy.json'


@dataclass(frozen=True)
class CommandPolicy:
    source: Path
    rules: tuple[Rule, ...] = ()
    file_rules: tuple[FileRule, ...] = ()

    @classmethod
    def load(cls, path=None):
        source = Path(path).expanduser().absolute() if path is not None else default_policy_path().absolute()
        fd = None
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 65536):
                raise ValueError('Policy must be a user-owned, non-shared regular file under 64 KB.')
            data = os.read(fd, 65537)
            if len(data) > 65536:
                raise ValueError('Policy exceeds 64 KB.')
            raw = json.loads(data)
        except FileNotFoundError:
            if path is None:
                return cls(source)
            raise ValueError('Explicit command policy file is missing.') from None
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('Cannot safely load command policy: ' + str(error)) from error
        finally:
            if fd is not None:
                os.close(fd)
        if (not isinstance(raw, dict) or not {'version', 'rules'} <= set(raw) or
                set(raw) - {'version', 'rules', 'file_rules'} or
                type(raw['version']) is not int or raw['version'] != 1 or
                not isinstance(raw['rules'], list) or not isinstance(raw.get('file_rules', []), list) or
                len(raw['rules']) + len(raw.get('file_rules', [])) > 128):
            raise ValueError('Policy requires version 1 and at most 128 rules.')
        rules=[]; ids=set()
        for item in raw['rules']:
            fields={'id','action','executor','project','cwd','match','argv'}
            if not isinstance(item,dict) or set(item) != fields:
                raise ValueError('Every policy rule must declare id/action/executor/project/cwd/match/argv.')
            if any(not isinstance(item[field],str) or not item[field] or '\0' in item[field]
                   for field in fields-{'argv'}):
                raise ValueError('Rule fields must be nonempty strings.')
            if (item['id'] in ids or item['action'] not in {'allow','ask','deny'} or
                    item['executor'] not in {'host','docker'} or item['match'] not in {'exact','prefix'}):
                raise ValueError('Invalid or duplicate policy rule.')
            cwd=PurePosixPath(item['cwd'])
            if cwd.is_absolute() or str(cwd)!=item['cwd'] or '..' in cwd.parts or '\\' in item['cwd']:
                raise ValueError('Rule cwd must be a normalized relative directory.')
            project=Path(item['project'])
            if not project.is_absolute() or str(project.resolve()) != item['project']:
                raise ValueError('Rule project must be an exact canonical absolute path.')
            argv=item['argv']
            if (not isinstance(argv,list) or not 1 <= len(argv) <= 100 or
                    any(not isinstance(arg,str) or '\0' in arg for arg in argv) or not argv[0] or
                    sum(len(arg.encode()) for arg in argv)>32768):
                raise ValueError('Rule argv must be a bounded argument array.')
            ids.add(item['id']); rules.append(Rule(**{**item,'argv':tuple(argv)}))
        file_rules=[]
        for item in raw.get('file_rules', []):
            fields={'id','action','operation','project','match','path'}
            if not isinstance(item,dict) or set(item) != fields:
                raise ValueError('File rules require id/action/operation/project/match/path.')
            if any(not isinstance(value,str) or not value or any(ord(char)<32 or ord(char)==127 for char in value)
                   for value in item.values()):
                raise ValueError('File rule fields must be nonempty strings without controls.')
            if (item['id'] in ids or item['action'] not in {'allow','ask','deny'} or
                    item['operation'] not in {'read','write'} or item['match'] not in {'exact','glob'}):
                raise ValueError('Invalid or duplicate file rule.')
            project=Path(item['project'])
            if not project.is_absolute() or str(project.resolve()) != item['project']:
                raise ValueError('File rule project must be an exact canonical absolute path.')
            path=PurePosixPath(item['path'])
            if (path.is_absolute() or str(path)!=item['path'] or '..' in path.parts or
                    '\\' in item['path'] or len(item['path'].encode())>240):
                raise ValueError('File rule path must be normalized, relative and at most 240 bytes.')
            ids.add(item['id']); file_rules.append(FileRule(**item))
        return cls(source,tuple(rules),tuple(file_rules))

    def decide(self, *, executor, project, cwd, argv, interactive):
        project=str(Path(project).resolve())
        argv=tuple(argv)
        matched=[rule for rule in self.rules if rule.executor==executor and rule.project==project
                 and rule.cwd==cwd and (argv==rule.argv if rule.match=='exact' else argv[:len(rule.argv)]==rule.argv)]
        for action in ('deny','ask','allow'):
            selected=tuple(rule.id for rule in matched if rule.action==action)
            if selected:
                if action=='allow' and not interactive:
                    return Decision('ask','Allow rules cannot bypass noninteractive approval denial.',selected)
                return Decision(action,action.upper()+' by '+', '.join(selected)+' (executor/project/cwd/argv matched).',selected)
        return Decision('ask','No rule matched exact executor/project/cwd and argv; default ASK.')

    def decide_file(self, *, operation, project, path, interactive):
        if operation not in ('read', 'write'):
            raise ValueError('Unknown file policy operation.')
        project=str(Path(project).resolve())
        matched=[rule for rule in self.file_rules if rule.operation==operation and rule.project==project
                 and (unicodedata.normalize('NFC',rule.path)==unicodedata.normalize('NFC',path)
                      if rule.match=='exact' else file_glob_matches(path,rule.path))]
        for action in ('deny','ask','allow'):
            selected=tuple(rule.id for rule in matched if rule.action==action)
            if selected:
                if action=='allow' and operation=='write' and not interactive:
                    return Decision('ask','Write allow cannot bypass noninteractive approval denial.',selected)
                return Decision(action,action.upper()+' '+operation+' '+path+' by '+', '.join(selected)+'.',selected)
        default='allow' if operation=='read' else 'ask'
        return Decision(default,'No file rule matched; default '+default.upper()+' for '+operation+'.')

    def describe(self):
        rows=[f'Command policy: {self.source}', 'Precedence: deny > ask > allow; unmatched commands ask. Loaded once per process.']
        rows.extend(f'{rule.id}: {rule.action} {rule.executor} {rule.project} cwd={rule.cwd} {rule.match} argv={json.dumps(rule.argv)}' for rule in self.rules)
        rows.append('Files: unmatched reads allowed; writes keep existing approval mode. Built-in exclusions and core approval always apply.')
        rows.extend(f'{rule.id}: {rule.action} {rule.operation} {rule.project} {rule.match} path={rule.path}' for rule in self.file_rules)
        return '\n'.join(rows)
