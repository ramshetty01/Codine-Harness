from .calculation import normalize as clean


def build_message(value):
    return clean(value)


def shadowed(normalize):
    return normalize('local binding, not necessarily calculation.normalize')
