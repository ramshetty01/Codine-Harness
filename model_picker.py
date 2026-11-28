"""Fetch free OpenRouter tool-capable models and choose one in the terminal."""

from decimal import Decimal, InvalidOperation
import sys

from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings


def free_tool_models(records):
    """Use advertised prices and capabilities, not just a ':free' suffix."""
    models = {}
    for record in records:
        pricing = record.get("pricing") or {}
        architecture = record.get("architecture") or {}
        parameters = record.get("supported_parameters") or []
        if not record.get("id") or "tools" not in parameters:
            continue
        if "text" not in architecture.get("input_modalities", []):
            continue
        if "text" not in architecture.get("output_modalities", []):
            continue
        if "prompt" not in pricing or "completion" not in pricing:
            continue
        try:
            # Exclude routes with advertised request or reasoning charges too.
            fields = ("prompt", "completion", "request", "internal_reasoning")
            if any(Decimal(str(pricing.get(field, "0"))) != 0 for field in fields):
                continue
        except (InvalidOperation, ValueError, TypeError):
            continue
        models[record["id"]] = record
    return sorted(models.values(), key=lambda item: (item.get("name") or item["id"]).casefold())


def fetch_free_models(client):
    page = client.with_options(timeout=20.0).models.list()
    return free_tool_models([model.model_dump() for model in page.data])


def model_label(model):
    context = model.get('context_length')
    context_label = f'{context:,} ctx' if isinstance(context, int) else 'context unknown'
    pricing = model.get('pricing', {})
    try:
        incoming = Decimal(str(pricing['prompt'])) * 1000000
        outgoing = Decimal(str(pricing['completion'])) * 1000000
        rates = f'${incoming:g} / ${outgoing:g} per 1M in/out'
    except (KeyError, InvalidOperation, ValueError):
        rates = 'pricing unknown'
    label = f"{model['id']} | {rates} | {context_label}"
    return ''.join(char if char.isprintable() else ' ' for char in label)


def build_model_dialog(models, current_model, catalog_notice=''):
    ids = {model["id"] for model in models}
    dialog = radiolist_dialog(
        title="Codine /model",
        text=(
            catalog_notice + ("\n" if catalog_notice else "") +
            "Tool-capable models. Check displayed prices; provider quotas apply.\n"
            "Up/Down: move  Space: select  Tab: buttons  Enter: confirm\n"
            "Esc: cancel. Selection applies to this session only."
        ),
        values=[(model["id"], model_label(model)) for model in models],
        default=current_model if current_model in ids else models[0]["id"],
        ok_text="Use model",
        cancel_text="Cancel",
        style=Style.from_dict({
            "dialog": "noreverse",
            "dialog frame.label": "bold",
            "radio-selected": "ansiyellow bold",
            "button.focused": "reverse",
        }),
    )

    cancel_keys = KeyBindings()

    @cancel_keys.add("escape")
    @cancel_keys.add("c-c")
    def cancel(event):
        event.app.exit(result=None)

    dialog.key_bindings = merge_key_bindings([dialog.key_bindings, cancel_keys])
    return dialog


def select_model(models, current_model, catalog_notice=''):
    if sys.stdin.isatty() and sys.stdout.isatty():
        return build_model_dialog(models, current_model, catalog_notice).run()

    if catalog_notice:
        print(catalog_notice)
    # Numbered selection also works in simple IDE consoles and piped input.
    for number, model in enumerate(models, 1):
        marker = " [current]" if model["id"] == current_model else ""
        print(f"{number}. {model_label(model)}{marker}")
    while True:
        answer = input("Model number (Enter to cancel): ").strip()
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(models):
            return models[int(answer) - 1]["id"]
        print("Choose a number from the list, or press Enter to cancel.")
