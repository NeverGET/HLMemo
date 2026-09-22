import pytest

from hlmemo.core import NORMALIZER_VERSION
from hlmemo.core.normalize import TERM_MAX, extract_terms, identifier_terms, is_identifier, normalize


def test_version_constant():
    assert NORMALIZER_VERSION == 1


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("İstanbul", "istanbul"),
        ("ISTANBUL", "istanbul"),
        ("ıslak", "islak"),
        ("ISLAK", "islak"),
        ("şç", "sc"),
        ("ŞÇĞÜÖ", "scguo"),
        ("Straße", "strasse"),
        ("STRASSE", "strasse"),
        ("Über", "uber"),
        ("Äpfel Öl", "apfel ol"),
        ("ﬁle", "file"),  # NFKC ligature
        ("Ｆｕｌｌ", "full"),  # NFKC full-width
        ("á", "a"),  # precomposed vs combining
        ("á", "a"),
        ("", ""),
    ],
)
def test_normalize_examples(raw, expected):
    assert normalize(raw) == expected


def test_turkish_dotted_dotless_i_collapse():
    # Both Turkish i variants collapse to ASCII i in every case
    assert normalize("İ") == normalize("i") == normalize("I") == normalize("ı") == "i"


@pytest.mark.parametrize(
    "raw",
    ["İstanbul'da ıslak yol", "Straße Über Äpfel", "APP_DB_DSN path/to/file.py E4193", "ﬁle Ｆｕｌｌ á", "ŞÇĞÜÖ ßẞ"],
)
def test_normalize_idempotent(raw):
    once = normalize(raw)
    assert normalize(once) == once


def test_normalize_type_error():
    with pytest.raises(TypeError):
        normalize(None)  # type: ignore[arg-type]


def test_extract_terms_basic_turkish_german():
    assert extract_terms("İstanbul'da ıslak yol.") == ["istanbul", "da", "islak", "yol"]
    assert extract_terms("Die Straße über den Fluss") == ["die", "strasse", "uber", "den", "fluss"]


def test_extract_terms_identifiers_kept_intact():
    terms = extract_terms("Set APP_DB_DSN in path/to/file.py, error E4193 on svc-qx7.")
    assert terms == ["set", "app_db_dsn", "in", "path/to/file.py", "error", "e4193", "on", "svc-qx7"]
    assert identifier_terms(terms) == ["app_db_dsn", "path/to/file.py", "e4193", "svc-qx7"]


def test_extract_terms_drops_short_and_dedupes():
    assert extract_terms("a b cc cc dd a cc") == ["cc", "dd"]


def test_extract_terms_strips_trailing_punct():
    assert extract_terms("done. next- ok/ ") == ["done", "next", "ok"]
    assert extract_terms("... -- //") == []


def test_extract_terms_cap():
    words = " ".join(f"w{i:02d}" for i in range(50))
    terms = extract_terms(words)
    assert len(terms) == TERM_MAX
    assert terms[0] == "w00" and terms[-1] == "w23"


@pytest.mark.parametrize(
    "term, expected",
    [
        ("app_db_dsn", True),
        ("path/to/file.py", True),
        ("e4193", True),
        ("file.py", True),
        ("svc-qx7", True),  # digit
        ("svc-qx", False),  # hyphen alone is not an identifier marker
        ("istanbul", False),
        ("strasse", False),
    ],
)
def test_is_identifier(term, expected):
    assert is_identifier(term) is expected
