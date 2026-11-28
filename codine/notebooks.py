"""Bounded nbformat-4 cell inspection/editing; never starts a notebook kernel."""
import json
from .workspace import fingerprint, MAX_FILE_BYTES
from .registry import ToolSpec


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate notebook JSON key.')
        result[key] = value
    return result


def source(cell):
    value = cell.get('source', '')
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        value = ''.join(value)
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('Invalid notebook cell source.')
    return value


class Notebooks:
    def __init__(self, workspace):
        self.workspace = workspace
        self.coverage = {}

    def load(self, path):
        target = self.workspace.resolve(path)
        if target.suffix.lower() != '.ipynb':
            raise ValueError('Notebook tools require an .ipynb file.')
        raw, text = self.workspace._read(target)
        try:
            document = json.loads(text, object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number.')))
        except (RecursionError, json.JSONDecodeError) as error:
            raise ValueError('Invalid or excessively nested notebook JSON.') from error
        if (not isinstance(document, dict) or type(document.get('nbformat')) is not int or
                document['nbformat'] != 4 or not isinstance(document.get('cells'), list) or
                len(document['cells']) > 1000 or not isinstance(document.get('metadata', {}), dict)):
            raise ValueError('Expected nbformat 4 with at most 1000 cells.')
        for cell in document['cells']:
            if (not isinstance(cell, dict) or cell.get('cell_type') not in ('code', 'markdown', 'raw') or
                    not isinstance(cell.get('metadata', {}), dict)):
                raise ValueError('Invalid notebook cell.')
            source(cell)
        normalized = target.relative_to(self.workspace.root).as_posix()
        return target, normalized, raw, text, document

    def key(self, path, index, version):
        return (getattr(self.workspace, 'change_session_id', None), path, index, version)

    def read(self, path, cell=0, start_char=0, max_chars=12000, expected_sha256=None):
        if type(cell) is not int or cell < 0 or type(start_char) is not int or start_char < 0 or type(max_chars) is not int or not 1 <= max_chars <= 24000:
            raise ValueError('Invalid notebook page bounds.')
        _, normalized, raw, _, document = self.load(path)
        version = fingerprint(raw)
        if expected_sha256 is not None and expected_sha256 != version:
            raise ValueError('Notebook changed between pages; read it again.')
        cells = document['cells']
        if not cells and cell == 0 and start_char == 0:
            return {'path': normalized, 'sha256': version, 'total_cells': 0, 'cell': None}
        if cell >= len(cells):
            raise ValueError('Cell index is outside this notebook (indices start at zero).')
        selected = cells[cell]
        text = source(selected)
        if start_char > len(text):
            raise ValueError('Character offset exceeds this cell source.')
        end = min(len(text), start_char + max_chars)
        key = self.key(normalized, cell, version)
        if key not in self.coverage and len(self.coverage) >= 64:
            self.coverage.pop(next(iter(self.coverage)))
        ranges = self.coverage.setdefault(key, [])
        # Merge overlapping/adjacent pages to keep repeat reads bounded.
        merged = []
        for left, right in sorted(ranges + [(start_char, end)]):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        self.coverage[key] = merged[:256]
        outputs = selected.get('outputs', [])
        return {'path': normalized, 'sha256': version, 'total_cells': len(cells),
                'cell': cell, 'cell_type': selected['cell_type'], 'source': text[start_char:end],
                'source_characters': len(text), 'start_char': start_char,
                'outputs': 'Stored rich outputs are not rendered or executed.',
                'output_count': len(outputs) if isinstance(outputs, list) else None,
                'next_page': {'cell': cell, 'start_char': end} if end < len(text) else
                             ({'cell': cell + 1, 'start_char': 0} if cell + 1 < len(cells) else None)}

    def edit(self, path, cell, new_source, expected_sha256):
        if self.workspace.mode == 'plan':
            raise ValueError('Notebook edits are disabled in planning mode.')
        if type(cell) is not int or cell < 0 or not isinstance(new_source, str) or '\0' in new_source or len(new_source) > 64000:
            raise ValueError('Expected a cell index and text source up to 64,000 characters.')
        target, normalized, raw, original, document = self.load(path)
        version = fingerprint(raw)
        if version != expected_sha256 or cell >= len(document['cells']):
            raise ValueError('Notebook or cell changed; read again before editing.')
        selected = document['cells'][cell]
        old = source(selected)
        ranges = self.coverage.get(self.key(normalized, cell, version), [])
        if not ranges or ranges[0][0] != 0 or ranges[0][1] < len(old):
            raise ValueError('Read the entire current cell source with read_notebook before editing.')
        self.workspace.resolve(normalized, operation='write')
        if old == new_source:
            return {'status': 'unchanged', 'path': normalized, 'cell': cell}
        selected['source'] = new_source.splitlines(keepends=True) if isinstance(selected.get('source'), list) else new_source
        if selected['cell_type'] == 'code':
            selected['outputs'] = []
            selected['execution_count'] = None
        updated = json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + '\n'
        if len(updated.encode()) > MAX_FILE_BYTES:
            raise ValueError('Updated notebook exceeds 2 MB.')
        if not self.workspace._confirm(target, f'Edit notebook {normalized} cell {cell}? Code outputs/count are cleared; JSON formatting may change.', original, updated):
            return {'status': 'declined', 'path': normalized, 'cell': cell}
        self.workspace._atomic_write(target, updated.encode(), raw)
        self.coverage.clear()
        return {'status': 'updated', 'path': normalized, 'cell': cell,
                'sha256': fingerprint(updated.encode()), 'executed': False}


def register_notebook_tools(registry, workspace):
    notebooks = Notebooks(workspace)
    string = {'type': 'string'}
    index = {'type': 'integer', 'minimum': 0, 'maximum': 999}
    for name, description, handler, properties, required, effect in (
        ('read_notebook', 'Read one nbformat-4 notebook cell source page. Zero-based cells. Follow next_page with expected_sha256; rich outputs are never rendered/executed. Complete cell read required before edit_notebook_cell.', notebooks.read,
         {'path': string, 'cell': index, 'start_char': {'type': 'integer', 'minimum': 0}, 'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 24000}, 'expected_sha256': string}, ['path'], 'read'),
        ('edit_notebook_cell', 'Replace a completely read current notebook cell source after diff/approval. Preserves unrelated cells/metadata; clears edited code-cell outputs/count. Never executes. JSON formatting may change. Changes use normal checkpoints.', notebooks.edit,
         {'path': string, 'cell': index, 'new_source': string, 'expected_sha256': string}, ['path', 'cell', 'new_source', 'expected_sha256'], 'write'),
    ):
        registry.register(ToolSpec(name, description, {'type': 'object', 'properties': properties,
            'required': required, 'additionalProperties': False}, handler, effect))
