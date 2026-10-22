import os
from pkg.helper import compute

class Greeter:
    """Greets people."""

    def greet(self, name):
        """Return a greeting."""
        msg = compute(name)
        return self.format(msg)

    def format(self, msg):
        return msg.upper()


def main():
    g = Greeter()
    print(g.greet("world"))
