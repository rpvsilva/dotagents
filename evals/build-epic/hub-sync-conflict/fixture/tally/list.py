def run(items):
    return [("[x] " if i["done"] else "[ ] ") + i["title"] for i in items]
