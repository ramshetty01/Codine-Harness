"""Render a fictional UI transcript without credentials, sessions, or model calls."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codine.config import Redactor
from codine.terminal import Terminal

ui = Terminal(Redactor(), interactive=False)
ui.color = sys.stdout.isatty() and 'NO_COLOR' not in os.environ and os.getenv('TERM') != 'dumb'
ui.welcome('~/projects/orbit', 'preview-only', 'provider / coding-model')
ui.line('\n› Fix the invoice rounding bug.\n', 'prompt')
ui.emit('tool_request', {'function': {'arguments': json.dumps({'path': 'shop/invoice.py'})}})
ui.emit('tool', 'read_file')
ui.emit('result', json.dumps({'path': 'shop/invoice.py', 'content': 'def total(items):\n    return sum(price * qty for price, qty in items)'}))
ui.emit('text', 'The total needs rounding after all line items are added.\n')
ui.emit('end_stream', '')
ui.write('--- a/shop/invoice.py\n+++ b/shop/invoice.py\n@@ -2 +2 @@\n-    return sum(price * qty for price, qty in items)\n+    return round(sum(price * qty for price, qty in items), 2)')
ui.panel('Review required', ['Edit shop/invoice.py?', 'yes = approve    anything else = decline'], 'accent')
ui.line('\n  Preview only · no files changed or API calls made', 'muted')
