import pytest

from shop.core.price import check, discount


def test_discount():
    assert discount(200) == 10
    assert discount(0) == 0


def test_check():
    assert check(0) == 0
    assert check(3) == 3
    with pytest.raises(ValueError):
        check(-1)
