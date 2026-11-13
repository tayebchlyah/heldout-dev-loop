def discount(total):
    if total > 100:
        return 10
    return 0


def check(qty):
    if qty < 0:
        raise ValueError("negative quantity")
    return qty


def unused(x):
    return x * 2
