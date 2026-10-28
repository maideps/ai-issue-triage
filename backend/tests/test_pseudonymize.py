from issue_triage.domain.pseudonymize import GHOST, Pseudonymizer


def test_same_login_same_pseudonym_case_insensitive() -> None:
    p = Pseudonymizer("salt")

    assert p.user("Alice") == p.user("alice")
    assert p.user("alice").startswith("user-")
    assert p.user("alice") != p.user("bob")


def test_salt_changes_the_pseudonym() -> None:
    assert Pseudonymizer("one").user("alice") != Pseudonymizer("two").user("alice")


def test_missing_login_becomes_ghost() -> None:
    assert Pseudonymizer("salt").user(None) == GHOST
    assert Pseudonymizer("salt").user("") == GHOST


def test_mentions_are_replaced_outside_code() -> None:
    p = Pseudonymizer("salt")
    text = "cc @bob and @Alice-2\n```\n@dataclass\n```\nsee `@pytest.fixture`"

    result = p.text(text)

    assert "@bob" not in result and "@Alice-2" not in result
    assert f"@{p.user('bob')}" in result
    assert "@dataclass" in result and "`@pytest.fixture`" in result


def test_emails_urls_and_version_pins_are_untouched() -> None:
    text = "mail alice@example.com, https://github.com/@bob, pkg@1.2.3"

    assert Pseudonymizer("salt").text(text) == text
