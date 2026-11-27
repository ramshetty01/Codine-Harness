"""Search saved sessions by name/model/ID without reading transcript contents."""
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


def clean(value, limit=120):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:limit]


def matching_sessions(rows, query):
    words = query.casefold().split()
    return [row for row in rows if all(word in ' '.join(str(row.get(key, '')) for key in ('title', 'model', 'id')).casefold() for word in words)]


def session_label(row):
    return (f"{clean(row.get('title') or 'Untitled', 50)}  ·  {clean(row.get('updated', ''), 16)}  ·  "
            f"{row.get('messages', 0)} messages  ·  {clean(row.get('model', ''), 48)}  ·  {clean(row['id'], 40)}")


def build_session_picker(rows, current_id=None, query='', input=None, output=None):
    state = {'index': 0, 'matches': matching_sessions(rows, query)}
    if current_id:
        state['index'] = next((i for i, row in enumerate(state['matches']) if row['id'] == current_id), 0)
    search = TextArea(text=query, height=1, prompt='Search › ', multiline=False)
    keys = KeyBindings()

    def refresh(_):
        state['matches'] = matching_sessions(rows, search.text)
        state['index'] = 0
    search.buffer.on_text_changed += refresh

    @keys.add('up')
    @keys.add('c-p')
    def up(event):
        state['index'] = max(0, state['index'] - 1)

    @keys.add('down')
    @keys.add('c-n')
    def down(event):
        state['index'] = min(max(0, len(state['matches']) - 1), state['index'] + 1)

    @keys.add('enter')
    def choose(event):
        if state['matches']:
            event.app.exit(result=state['matches'][state['index']]['id'])

    @keys.add('escape')
    @keys.add('c-c')
    def cancel(event):
        event.app.exit(result=None)

    def content():
        matches, index = state['matches'], state['index']
        if not matches:
            return [('class:muted', 'No matching sessions. Edit your search or Esc to cancel.')]
        start = max(0, index - 7)
        fragments = []
        for i in range(start, min(len(matches), start + 10)):
            marker = '› ' if i == index else '  '
            fragments.append(('class:selected' if i == index else '', marker + session_label(matches[i]) + '\n'))
        fragments.append(('class:muted', f'{index + 1} / {len(matches)} matches'))
        return fragments
    layout = HSplit([
        Window(FormattedTextControl('Sessions · type to filter · ↑/↓ move · Enter resume · Esc cancel'), height=1),
        search, Window(FormattedTextControl(content), height=11),
    ])
    return Application(layout=Layout(layout, focused_element=search), key_bindings=keys,
                       style=Style.from_dict({'selected': 'ansiyellow bold', 'muted': 'ansibrightblack'}),
                       full_screen=False, mouse_support=False, input=input, output=output)


def select_session(rows, current_id=None, query=''):
    return build_session_picker(rows, current_id, query).run()
