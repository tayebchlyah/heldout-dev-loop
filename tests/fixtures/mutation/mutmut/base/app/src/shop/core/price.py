def discount(total):
    if total > 50:
        return 5
    return 0


def check(qty):
    if qty < 0:
        raise ValueError("bad")
    return qty


def unused(x):
    return x
