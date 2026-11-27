"""Project-relative file operations. These checks are not an OS sandbox."""
import ast
import difflib
import hashlib
import os
from pathlib import Path
import tempfile
from .staging import _read_file

EXCLUDED = {".git", ".codine", ".sessions", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
MAX_FILE_BYTES = 2_000_000


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def preview_change(path, before, after):
    # Preserve diff readability for files without trailing newlines.
    def lines(text):
        return [line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in text.splitlines(keepends=True)]
    return "".join(difflib.unified_diff(lines(before), lines(after), fromfile=f"a/{path}", tofile=f"b/{path}")) or "No changes."


class Workspace:
    def __init__(self, root, approve, emit=print, app_root=None, track_changes=False):
        self.root = Path(root).resolve()
        self.approve = approve
        self.emit = emit
        self.app_root = Path(app_root).resolve() if app_root else None
        self.read_versions = {}
        self.read_ranges = {}
        self.mode = "ask"
        self.track_changes = track_changes

    def resolve(self, name, directory=False):
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("Provide a nonempty project-relative path.")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Paths must stay inside the project.")
        current = self.root
        for part in relative.parts:
            if part in EXCLUDED or (part.startswith(".env") and part != ".env.example") or part.lower().endswith((".pem", ".key", ".p12")) or part in (".ssh", ".aws", ".gnupg"):
                raise ValueError("Credentials and generated/internal files are excluded.")
            current = current / part
            if current.is_symlink():
                raise ValueError("Symbolic links are not allowed.")
        if current.resolve() in getattr(self, 'protected_paths', set()):
            raise ValueError('User command policy is protected from model file tools; edit it outside Codine.')
        if not current.resolve().is_relative_to(self.root):
            raise ValueError("Path escapes the project.")
        if not directory and current.is_dir():
            raise ValueError("Expected a file, not a directory.")
        return current

    def _read(self, path):
        # Reopen every component beneath a directory descriptor: resolve() alone
        # cannot prevent an attacker swapping a symlink after validation.
        parts = path.relative_to(self.root).parts
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            data = _read_file(descriptor, parts, MAX_FILE_BYTES)
        finally:
            os.close(descriptor)
        return data, data.decode("utf-8")

    def list_files(self, path=".", recursive=True, limit=200):
        directory = self.resolve(path, directory=True)
        if not directory.is_dir():
            raise ValueError("Directory does not exist.")
        names = []
        for base, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(base) / d).is_symlink())
            for name in sorted(files):
                relative = (Path(base) / name).relative_to(self.root).as_posix()
                try:
                    self.resolve(relative)
                except ValueError:
                    continue
                names.append(relative)
                if len(names) > limit:
                    return {"files": names[:limit], "truncated": True, "hint": "List a narrower directory."}
            if not recursive:
                break
        return {"files": names, "truncated": False}

    def read_file(self, path, start_line=1, max_lines=200, start_char=0, expected_sha256=None):
        target = self.resolve(path)
        data, content = self._read(target)
        version = fingerprint(data)
        if expected_sha256 is not None and version != expected_sha256:
            raise ValueError("File changed between pages; read again from the beginning.")
        if self.read_versions.get(path) != version:
            self.read_ranges[path] = []
        self.read_versions[path] = version
        lines = content.splitlines(keepends=True)
        if start_line < 1 or max_lines < 1 or max_lines > 2000 or start_char < 0:
            raise ValueError("Invalid page bounds.")
        if start_line > len(lines) + 1:
            raise ValueError(f"File has only {len(lines)} lines.")
        output = []
        budget = 24000
        line_index, column = start_line - 1, start_char
        if line_index < len(lines) and column > len(lines[line_index]):
            raise ValueError("start_char exceeds the selected line length.")
        begin = sum(map(len, lines[:line_index])) + column
        consumed = 0
        while line_index < len(lines) and consumed < max_lines and budget > 0:
            text = lines[line_index][column:]
            piece = text[:budget]
            output.append(f"{line_index + 1}: {piece}")
            budget -= len(piece)
            column += len(piece)
            if column < len(lines[line_index]):
                break
            line_index += 1
            consumed += 1
            column = 0
        self.read_ranges.setdefault(path, []).append((begin, begin + 24000 - budget))
        next_page = {"start_line": line_index + 1, "start_char": column} if line_index < len(lines) else None
        return {"path": path, "total_lines": len(lines), "sha256": fingerprint(data),
                "content": "".join(output), "next_page": next_page}

    def search(self, query, path=".", limit=50):
        if not query:
            raise ValueError("Search query cannot be empty.")
        target = self.resolve(path, directory=True)
        listing = {'files': [path], 'truncated': False} if target.is_file() else self.list_files(path, recursive=True, limit=2000)
        matches = []
        for relative in listing["files"]:
            try:
                _, content = self._read(self.resolve(relative))
            except (ValueError, UnicodeError, OSError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query in line:
                    matches.append({"path": relative, "line": number, "text": line[:1000]})
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": listing["truncated"]}

    def _core(self, target):
        if target in (self.root / 'AGENTS.md', self.root / 'CODINE.md'):
            return True
        if not self.app_root:
            return False
        return (target == self.app_root / "main.py" or target.is_relative_to(self.app_root / "codine")
                or target in [self.app_root / name for name in ("requirements.txt", "model_picker.py", ".gitignore", "RECOVERY.md", "PRD.md")])

    def _confirm(self, target, description, before, after):
        self.emit(preview_change(target.relative_to(self.root).as_posix(), before, after))
        return (self.mode == "auto-edit" and not self._core(target)) or self.approve(description)

    def _atomic_write(self, target, data, original=None, record=True):
        target.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(target.relative_to(self.root).as_posix())
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        fd, temporary = tempfile.mkstemp(prefix=".codine-write-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            # Recheck the target after user approval and before replacement.
            self.resolve(target.relative_to(self.root).as_posix())
            if original is None and target.exists():
                raise ValueError("File appeared after the preview; refusing to overwrite.")
            if original is not None and (target.stat().st_nlink != 1 or target.read_bytes() != original):
                raise ValueError("File changed after the preview. Read it again.")
            if self.track_changes and record:
                from .changes import ChangeStore
                ChangeStore(self.root).prepare(target.relative_to(self.root).as_posix(), original, fingerprint(data))
                self.resolve(target.relative_to(self.root).as_posix())
                if original is None and target.exists():
                    raise ValueError('File appeared while saving checkpoint.')
                if original is not None and (target.stat().st_nlink != 1 or target.read_bytes() != original):
                    raise ValueError('File changed while saving checkpoint.')
            os.replace(temporary, target)
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write_file(self, path, content):
        target = self.resolve(path)
        if target.exists():
            raise ValueError("File already exists. Read it and use edit_file instead.")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES or "\x00" in content:
            raise ValueError("Expected a text file under 2 MB.")
        if not self._confirm(target, f"Create {path}?", "", content):
            return {"status": "declined", "path": path}
        self._atomic_write(target, data)
        self.read_versions[path] = fingerprint(data)
        self.read_ranges[path] = [(0, len(content))]
        return {"status": "created", "path": path}

    def edit_file(self, path, old_text, new_text):
        target = self.resolve(path)
        data, original = self._read(target)
        if target.stat().st_nlink != 1:
            raise ValueError("Hard-linked files cannot be edited.")
        if self.read_versions.get(path) != fingerprint(data):
            raise ValueError("Read this file's current contents before editing; the file is unread or changed.")
        if not old_text or original.count(old_text) != 1:
            raise ValueError("old_text must match exactly one location. Copy literal source text without line-number prefixes; do not double-escape newlines or change punctuation. Alternatively use replace_lines with the SHA256 from read_file.")
        start = original.index(old_text)
        end = start + len(old_text)
        covered = start
        for left, right in sorted(self.read_ranges.get(path, [])):
            if left <= covered:
                covered = max(covered, right)
        if covered < end:
            raise ValueError("Read the complete region being changed before editing.")
        if "\x00" in new_text:
            raise ValueError("Binary content is not supported.")
        updated = original.replace(old_text, new_text, 1)
        if updated == original:
            return {"status": "unchanged", "path": path}
        if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("Updated file exceeds 2 MB.")
        if not self._confirm(target, f"Apply edit to {path}?", original, updated):
            return {"status": "declined", "path": path}
        self._atomic_write(target, updated.encode("utf-8"), data)
        self.read_versions[path] = fingerprint(updated.encode("utf-8"))
        self.read_ranges[path] = [(start, start + len(new_text))]
        return {"status": "updated", "path": path}

    def replace_lines(self, path, start_line, end_line, new_text, expected_sha256):
        """Line-addressed alternative; still requires a fresh, fully read region."""
        target = self.resolve(path)
        data, original = self._read(target)
        version = fingerprint(data)
        if expected_sha256 != version or self.read_versions.get(path) != version:
            raise ValueError('File is unread or changed. Read its current contents and use that SHA256.')
        if target.stat().st_nlink != 1:
            raise ValueError('Hard-linked files cannot be edited.')
        lines = original.splitlines(keepends=True)
        if not 1 <= start_line <= end_line <= len(lines):
            raise ValueError('Provide an existing inclusive line range.')
        start = sum(map(len, lines[:start_line - 1]))
        end = sum(map(len, lines[:end_line]))
        covered = start
        for left, right in sorted(self.read_ranges.get(path, [])):
            if left <= covered:
                covered = max(covered, right)
        if covered < end:
            raise ValueError('Read the complete line range before replacing it.')
        if end_line < len(lines) and new_text and not new_text.endswith('\n'):
            raise ValueError('Replacement must end with a newline when following lines remain.')
        updated = original[:start] + new_text + original[end:]
        encoded = updated.encode('utf-8')
        if '\x00' in new_text or len(encoded) > MAX_FILE_BYTES:
            raise ValueError('Expected text under 2 MB.')
        if updated == original:
            return {'status': 'unchanged', 'path': path}
        if not self._confirm(target, f'Replace lines {start_line}–{end_line} in {path}?', original, updated):
            return {'status': 'declined', 'path': path}
        self._atomic_write(target, encoded, data)
        self.read_versions[path] = fingerprint(encoded)
        self.read_ranges[path] = [(start, start + len(new_text))]
        return {'status': 'updated', 'path': path}

    def show_change(self, ident=None):
        from .changes import ChangeStore
        store = ChangeStore(self.root)
        records = [store.load(ident)] if ident else store.list(1)
        if not records:
            return 'No file-tool checkpoints.'
        record = records[0]
        _, current = self._read(self.resolve(record['path']))
        return preview_change(record['path'], record['before'] or '', current)

    def undo(self, ident=None):
        if self.mode == 'plan':
            raise ValueError('Undo is disabled in planning mode.')
        from .changes import ChangeStore
        store = ChangeStore(self.root)
        records = [store.load(ident)] if ident else store.list(1)
        if not records:
            return {'status': 'unchanged', 'message': 'No file-tool changes to undo.'}
        record = records[0]
        if record.get('undone'):
            raise ValueError('This change was already undone.')
        target = self.resolve(record['path'])
        data, current = self._read(target)
        if target.stat().st_nlink != 1 or fingerprint(data) != record['after_hash']:
            raise ValueError('File changed since this checkpoint. Refusing to overwrite newer work.')
        before = record['before']
        self.emit(preview_change(record['path'], current, before or ''))
        action = 'Remove newly created file' if before is None else 'Restore previous contents of'
        if not self.approve(f"{action} {record['path']}? Shell side effects are not undone."):
            return {'status': 'declined'}
        if before is None:
            self.resolve(record['path'])
            if target.stat().st_nlink != 1 or target.read_bytes() != data:
                raise ValueError('File changed after undo preview.')
            target.unlink()
            descriptor = os.open(target.parent, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        else:
            self._atomic_write(target, before.encode('utf-8'), data, record=False)
        record['undone'] = True
        store.save(record)
        self.read_versions.clear()
        self.read_ranges.clear()
        return {'status': 'undone', 'path': record['path'], 'change': record['id']}

    def check_python(self, path):
        if not path.endswith(".py"):
            raise ValueError("Choose a Python source file.")
        _, content = self._read(self.resolve(path))
        try:
            ast.parse(content, filename=path)
        except SyntaxError as error:
            return {"ok": False, "path": path, "line": error.lineno, "error": error.msg}
        return {"ok": True, "path": path, "check": "syntax only; not executed"}
