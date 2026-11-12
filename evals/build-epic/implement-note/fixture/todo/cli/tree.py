"""Order shown items as a tree for `todo list`."""


def tree(shown) -> list:
    """(item, depth) pairs: each child right after its parent, in the shown order.

    A root is a shown item whose parent is unset or not shown. Every shown item appears once;
    items a parent cycle leaves unreached come last, at depth 0.
    """
    ids = {i.id for i in shown}
    children: dict = {}
    for item in shown:
        if item.parent in ids:
            children.setdefault(item.parent, []).append(item)
    out: list = []
    seen: set = set()

    def walk(item, depth) -> None:
        stack = [(item, depth)]
        while stack:
            current, level = stack.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            out.append((current, level))
            stack.extend((c, level + 1) for c in reversed(children.get(current.id, ())))

    for item in shown:
        if item.parent not in ids:
            walk(item, 0)
    out.extend((i, 0) for i in shown if i.id not in seen)
    return out
