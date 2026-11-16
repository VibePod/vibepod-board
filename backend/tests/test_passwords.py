import re

import pytest

from vibepod_board import passwords


def test_a_hash_verifies_only_its_own_password() -> None:
    stored = passwords.hash_password("correct horse battery")

    assert stored.startswith("scrypt$16384$8$1$")
    assert "correct horse battery" not in stored
    assert passwords.verify_password("correct horse battery", stored)
    assert not passwords.verify_password("correct horse batterx", stored)


def test_the_same_password_hashes_differently_each_time() -> None:
    assert passwords.hash_password("same password") != passwords.hash_password("same password")


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain",
        "md5$1$2$3$4$5",
        "scrypt$16384$8$1$not base64$AAAA",
        "scrypt$x$8$1$AAAA$AAAA",
    ],
)
def test_a_stored_value_this_module_did_not_write_matches_nothing(stored: str) -> None:
    assert not passwords.verify_password("anything", stored)


def test_temporary_passwords_are_random_and_long_enough() -> None:
    first = passwords.generate_temporary_password()
    second = passwords.generate_temporary_password()

    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", first)
    assert first != second
    assert len(first) >= passwords.MIN_LENGTH


def test_the_dummy_hash_is_a_real_hash_of_a_password_nobody_knows() -> None:
    assert passwords.DUMMY_HASH.startswith("scrypt$")
    assert not passwords.verify_password("", passwords.DUMMY_HASH)
