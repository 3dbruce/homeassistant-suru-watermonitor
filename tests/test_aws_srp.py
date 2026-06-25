"""Unit tests for the self-contained Cognito SRP helper (no Home Assistant)."""
import datetime
import pathlib
import sys

import pytest

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[1]
        / "custom_components" / "suru_water_monitor"),
)

import aws_srp  # noqa: E402

POOL = "eu-central-1_8hkX7Bvjh"
CLIENT = "1p3lj6d18fehl88ugahglf6kc8"
CHALLENGE = {
    "USER_ID_FOR_SRP": "uid-1",
    "SALT": "a1b2c3d4",
    "SRP_B": "ff" * 64,
    "SECRET_BLOCK": "c2VjcmV0",  # base64("secret")
}


def test_format_timestamp_is_locale_independent_and_unpadded_day():
    assert (
        aws_srp.format_timestamp(datetime.datetime(2025, 1, 5, 3, 4, 9))
        == "Sun Jan 5 03:04:09 UTC 2025"
    )
    assert (
        aws_srp.format_timestamp(datetime.datetime(2026, 6, 25, 12, 0, 1))
        == "Thu Jun 25 12:00:01 UTC 2026"
    )


def test_auth_parameters():
    srp = aws_srp.AWSSRP("u@example.com", "pw", POOL, CLIENT)
    params = srp.get_auth_parameters()
    assert params["USERNAME"] == "u@example.com"
    assert int(params["SRP_A"], 16) > 0
    assert len(params["SRP_A"]) > 100


def test_process_challenge_shape_and_determinism():
    srp = _fixed_srp("Secret1!")
    now = datetime.datetime(2026, 6, 25, 12, 0, 1)
    res = srp.process_challenge(CHALLENGE, now=now)

    assert set(res) == {
        "TIMESTAMP",
        "USERNAME",
        "PASSWORD_CLAIM_SECRET_BLOCK",
        "PASSWORD_CLAIM_SIGNATURE",
    }
    assert res["USERNAME"] == "uid-1"
    assert res["PASSWORD_CLAIM_SECRET_BLOCK"] == "c2VjcmV0"
    assert res["TIMESTAMP"] == "Thu Jun 25 12:00:01 UTC 2026"
    # deterministic for fixed inputs
    assert srp.process_challenge(CHALLENGE, now=now) == res


def test_signature_depends_on_password():
    now = datetime.datetime(2026, 6, 25, 12, 0, 1)
    sig_a = _fixed_srp("Secret1!").process_challenge(CHALLENGE, now=now)
    sig_b = _fixed_srp("Different1!").process_challenge(CHALLENGE, now=now)
    assert sig_a["PASSWORD_CLAIM_SIGNATURE"] != sig_b["PASSWORD_CLAIM_SIGNATURE"]


def test_matches_pycognito_reference():
    """The authoritative check: identical key to the proven pycognito impl."""
    ref_mod = pytest.importorskip("pycognito.aws_srp")
    mine = aws_srp.AWSSRP("u@example.com", "Secret1!", POOL, CLIENT)
    # Pass a dummy client so pycognito does not create a boto3 client (which
    # would touch a socket - blocked by pytest-socket in the HA test env).
    ref = ref_mod.AWSSRP(
        "u@example.com", "Secret1!", POOL, CLIENT, client=object()
    )
    ref.small_a_value = mine.small_a_value
    ref.large_a_value = mine.large_a_value

    server_b = int(CHALLENGE["SRP_B"], 16)
    assert mine.get_password_authentication_key(
        "uid-1", "Secret1!", server_b, CHALLENGE["SALT"]
    ) == ref.get_password_authentication_key(
        "uid-1", "Secret1!", server_b, CHALLENGE["SALT"]
    )


def _fixed_srp(password: str) -> "aws_srp.AWSSRP":
    """AWSSRP with a frozen small_a so results are deterministic."""
    srp = aws_srp.AWSSRP("u@example.com", password, POOL, CLIENT)
    srp.small_a_value = 0x12345
    srp.large_a_value = pow(srp.g, srp.small_a_value, srp.big_n)
    return srp
