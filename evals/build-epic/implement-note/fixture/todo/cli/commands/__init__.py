"""One module per command; each has register(parser) and run(args, ctx).

Every module in this package is a command, named after the module with a
trailing `_cmd` stripped. Adding a command is adding a file.
"""
import importlib
import pkgutil


def discover(package: str, path) -> dict:
    commands = {}
    for info in pkgutil.iter_modules(path):
        module = importlib.import_module(f"{package}.{info.name}")
        for attr in ("register", "run"):
            if not callable(getattr(module, attr, None)):
                raise ImportError(f"command module {module.__name__} has no {attr}()")
        commands[info.name.removesuffix("_cmd")] = module
    return dict(sorted(commands.items()))


COMMANDS = discover(__name__, __path__)
