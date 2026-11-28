"""Bounded static Python navigation and explicitly non-semantic text fallback."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from .repository_search import discover, search

MAX_FILES = 200
MAX_FILE_BYTES = 256000
MAX_TOTAL_BYTES = 2_000_000
MAX_NODES = 40000
PARSE_SECONDS = 2.0

# This trusted script receives source as JSON data. It never imports that source.
AST_WORKER = r'''
import ast, hashlib, json, sys, warnings
warnings.simplefilter('ignore')
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
except (ImportError, OSError, ValueError):
    pass
payload = json.load(sys.stdin)
action, query, limit = payload['action'], payload['symbol'], payload['limit']
leaf = query.rsplit('.', 1)[-1]
results, diagnostics, reasons = [], [], []
parsed = 0

def add(row):
    if len(results) < limit:
        results.append(row)
    elif 'result_limit' not in reasons:
        reasons.append('result_limit')

def expression(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return '.'.join([node.id] + list(reversed(names)))[:1024]
    return None

for item in payload['files']:
    path, content, sha = item['path'], item['content'], item['sha256']
    module = path.rsplit('.', 1)[0].replace('/', '.')
    if module.endswith('.__init__'): module = module[:-9]
    lines = content.splitlines()
    try:
        tree = ast.parse(content, filename=path, mode='exec')
        count = 0
        for node in ast.walk(tree):
            count += 1
            if count > payload['max_nodes']:
                raise OverflowError('AST node allowance exceeded')
    except (SyntaxError, RecursionError, MemoryError, OverflowError, ValueError) as error:
        kind = 'syntax_error' if isinstance(error, SyntaxError) else 'parser_limit'
        if len(diagnostics) < limit:
            diagnostics.append({'path': path, 'sha256': sha, 'kind': kind,
                'line': getattr(error, 'lineno', None), 'column': getattr(error, 'offset', None),
                'message': str(getattr(error, 'msg', error))[:240]})
        elif 'diagnostic_limit' not in reasons: reasons.append('diagnostic_limit')
        if kind == 'parser_limit' and kind not in reasons: reasons.append(kind)
        if kind == 'syntax_error' and action != 'diagnostics' and 'syntax_errors' not in reasons: reasons.append('syntax_errors')
        continue
    parsed += 1
    if action == 'diagnostics': continue
    stack = [(tree, [])]
    class_scopes = set()
    while stack:
        node, scope = stack.pop()
        name, kind, reference, children_scope = None, None, None, scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            kind = 'class' if isinstance(node, ast.ClassDef) else 'async_function' if isinstance(node, ast.AsyncFunctionDef) else 'function'
            if kind != 'class' and tuple(scope) in class_scopes:
                kind = 'async_method' if kind == 'async_function' else 'method'
            children_scope = scope + [name]
            if isinstance(node, ast.ClassDef): class_scopes.add(tuple(children_scope))
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store): name, kind = node.id, 'binding'
            elif isinstance(node.ctx, ast.Load): reference = node.id
        elif isinstance(node, ast.arg): name, kind = node.arg, 'parameter'
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load): reference = node.attr
        elif isinstance(node, ast.alias):
            name, kind = node.asname or node.name.split('.')[0], 'import_binding'
            reference = node.name.rsplit('.', 1)[-1]
        selected = False
        qualified = '.'.join([module] + scope + ([name] if name else []))
        if action == 'definitions' and name:
            selected = not query or query == name or qualified == query or qualified.endswith('.' + query)
        elif action == 'references' and reference == leaf:
            selected = True
        if selected:
            line = getattr(node, 'lineno', 1)
            offset = getattr(node, 'col_offset', 0)
            if isinstance(node, ast.Attribute):
                line = getattr(node, 'end_lineno', line)
                offset = max(0, getattr(node, 'end_col_offset', offset) - len(node.attr.encode('utf-8')))
            row = lines[line - 1] if 0 < line <= len(lines) else ''
            column = len(row.encode('utf-8')[:offset].decode('utf-8', errors='ignore')) + 1
            definition = action == 'definitions'
            add({'path': path, 'sha256': sha, 'name': (name if definition else reference)[:256],
                 'qualified_name': qualified[:1024] if definition else None,
                 'scope': '.'.join([module] + scope)[:1024],
                 'kind': kind if definition else 'attribute_reference' if isinstance(node, ast.Attribute) else 'import_reference' if isinstance(node, ast.alias) else 'name_reference',
                 'expression': expression(node) if isinstance(node, ast.Attribute) else node.name if isinstance(node, ast.alias) and not definition else reference,
                 'line': line, 'column': column, 'end_line': getattr(node, 'end_lineno', line),
                 'label_truncated': len(name or reference or '') > 256 or len(qualified) > 1024})
        # Iterative traversal avoids a recursive visitor stack overflow. Scope is
        # structural containment, not Python name binding or type resolution.
        stack.extend((child, children_scope) for child in reversed(list(ast.iter_child_nodes(node))))
encoded = json.dumps({'matches': results, 'diagnostics': diagnostics, 'parsed_files': parsed,
                      'reasons': reasons}, ensure_ascii=True)
if len(encoded) > 900000:
    encoded = json.dumps({'matches': [], 'diagnostics': [], 'parsed_files': parsed, 'reasons': ['output_limit']})
print(encoded)
'''


def navigate(workspace, action='definitions', symbol='', path='.', glob='*',
             language='python', limit=100, respect_ignore=True):
    if action not in ('definitions', 'references', 'diagnostics'):
        raise ValueError('Choose definitions, references or diagnostics.')
    if language not in ('python', 'text'):
        raise ValueError('Supported modes: python AST or explicit text fallback; no other language server is configured.')
    if not isinstance(symbol, str) or len(symbol) > 256 or '\0' in symbol:
        raise ValueError('Symbol must contain at most 256 characters without NUL.')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Navigation limit must be between 1 and 200.')
    if language == 'text':
        if action == 'diagnostics': raise ValueError('Text fallback cannot produce language diagnostics.')
        if not symbol: raise ValueError('Text fallback requires a nonempty literal query.')
        result = search(workspace, symbol, path, limit, glob, False, True, respect_ignore)
        result.update(language='text', analysis='literal_text_fallback', action=action,
                      limitations='Text matches include comments/strings and are not definitions or resolved references. No diagnostics or edit authorization.')
        return result
    if symbol and any(not part.isidentifier() for part in symbol.split('.')):
        raise ValueError('Python symbol must be an identifier or dotted identifier, not a regex.')
    if action == 'references' and not symbol:
        raise ValueError('Reference candidates require a symbol name.')
    listing = discover(workspace, path, True, glob, respect_ignore)
    files, total, unsupported, skipped = [], 0, 0, 0
    reasons = [listing['reason']] if listing['truncated'] else []
    diagnostics = []
    for name in listing['files']:
        if Path(name).suffix not in ('.py', '.pyi'):
            unsupported += 1
            continue
        if len(files) >= MAX_FILES:
            reasons.append('file_limit'); break
        try:
            data, content = workspace._read(workspace.resolve(name))
        except (ValueError, UnicodeError, OSError):
            skipped += 1
            continue
        if len(data) > MAX_FILE_BYTES:
            reasons.append('source_size_limit')
            if len(diagnostics) < limit:
                diagnostics.append({'path': name, 'kind': 'source_size_limit',
                                    'message': 'Source exceeds the 256 KB parser allowance; use bounded read/search.'})
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            reasons.append('byte_limit'); break
        total += len(data)
        files.append({'path': name, 'content': content, 'sha256': hashlib.sha256(data).hexdigest()})
    if skipped: reasons.append('unreadable_files')
    result = {'matches': [], 'diagnostics': [], 'parsed_files': 0, 'reasons': []}
    if files:
        payload = {'action': action, 'symbol': symbol, 'limit': limit, 'max_nodes': MAX_NODES, 'files': files}
        try:
            with tempfile.TemporaryDirectory(prefix='codine-symbols-') as directory:
                process = subprocess.run([sys.executable, '-I', '-W', 'ignore', '-c', AST_WORKER],
                    input=json.dumps(payload), capture_output=True, text=True, cwd=directory,
                    env={key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}, timeout=PARSE_SECONDS)
            if process.returncode != 0 or len(process.stdout) > 1_000_000:
                reasons.append('parser_failed')
            else:
                result = json.loads(process.stdout)
        except subprocess.TimeoutExpired:
            reasons.append('parser_timeout')
        except (ValueError, OSError):
            reasons.append('parser_failed')
    reasons.extend(result.pop('reasons'))
    diagnostics.extend(result['diagnostics'])
    if len(diagnostics) > limit: reasons.append('diagnostic_limit')
    result.update(language='python', analysis='static_ast', action=action, requested_symbol=symbol,
        diagnostics=diagnostics[:limit], truncated=bool(reasons), reasons=sorted(set(reasons)),
        scanned_files=len(files), scanned_bytes=total, unsupported_files=unsupported, skipped_files=skipped,
        column_unit='one-based Unicode characters; definition locations start at their statement',
        limitations='Python syntax only. References match the final name and are lexical candidates, not resolved targets; aliases/dynamic dispatch may be missed. Scope is structural containment. Diagnostics are parse errors, not lint/type checks. Snapshots do not grant edit permission.')
    if reasons: result['hint'] = 'Incomplete coverage: narrow path/glob or use read_file/search. No complete-reference claim.'
    if unsupported: result['fallback_hint'] = 'Non-Python files were not parsed; request language="text" with a symbol for literal search.'
    return result


def register_symbol_tool(registry, workspace):
    from .registry import ToolSpec
    registry.register(ToolSpec('navigate_symbols',
        'Read-only bounded static navigation. Python definitions, lexical reference candidates or syntax diagnostics; no imports/execution or type resolution. Explicit language=text is literal-search fallback for other languages, not semantic navigation. Honors ignore/filesystem policy. Use read_file before editing.',
        {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['definitions', 'references', 'diagnostics']},
            'symbol': {'type': 'string'}, 'path': {'type': 'string'}, 'glob': {'type': 'string'},
            'language': {'type': 'string', 'enum': ['python', 'text']},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
            'respect_ignore': {'type': 'boolean'}}, 'additionalProperties': False},
        lambda **args: navigate(workspace, **args)))
