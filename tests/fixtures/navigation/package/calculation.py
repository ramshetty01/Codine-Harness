"""Fixture for finding a shared calculation without executing project imports."""
def normalize(value):
    return value.strip().lower()


class Formatter:
    def normalize(self, value):
        return value.strip()

    async def render(self, value):
        return self.normalize(value)
