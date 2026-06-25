"""Self-contained AWS Cognito SRP (USER_SRP_AUTH) helper.

Pure crypto math only - no network, no boto3. The HTTP InitiateAuth /
RespondToAuthChallenge calls live in api.py. This mirrors the algorithm used by
pycognito/warrant so it can be cross-checked bit-for-bit against pycognito.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import os

# RFC 3526 3072-bit MODP group - the N/g pair AWS Cognito uses for SRP.
_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
_G_HEX = "2"
_INFO_BITS = b"Caldera Derived Key"

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _hash_sha256(buf: bytes) -> str:
    digest = hashlib.sha256(buf).hexdigest()
    return (64 - len(digest)) * "0" + digest


def _hex_hash(hex_string: str) -> str:
    return _hash_sha256(bytearray.fromhex(hex_string))


def _hex_to_long(hex_string: str) -> int:
    return int(hex_string, 16)


def _long_to_hex(long_num: int) -> str:
    return "%x" % long_num


def _get_random(nbytes: int) -> int:
    return _hex_to_long(binascii.hexlify(os.urandom(nbytes)).decode())


def _pad_hex(long_int: int | str) -> str:
    hash_str = long_int if isinstance(long_int, str) else _long_to_hex(long_int)
    if len(hash_str) % 2 == 1:
        hash_str = "0" + hash_str
    elif hash_str[0] in "89ABCDEFabcdef":
        hash_str = "00" + hash_str
    return hash_str


def _compute_hkdf(ikm: bytes, salt: bytes) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    info_bits_update = _INFO_BITS + bytearray(chr(1), "utf-8")
    return hmac.new(prk, info_bits_update, hashlib.sha256).digest()[:16]


def _calculate_u(big_a: int, big_b: int) -> int:
    return _hex_to_long(_hex_hash(_pad_hex(big_a) + _pad_hex(big_b)))


def format_timestamp(now: datetime.datetime) -> str:
    """Locale-independent 'Wed Jun 25 12:00:00 UTC 2026' (day NOT zero-padded)."""
    return "{} {} {} {:02d}:{:02d}:{:02d} UTC {}".format(
        _WEEKDAYS[now.weekday()], _MONTHS[now.month - 1], now.day,
        now.hour, now.minute, now.second, now.year,
    )


class AWSSRP:
    """Minimal Cognito SRP client (no MFA, no device tracking, no client secret)."""

    def __init__(self, username: str, password: str, pool_id: str, client_id: str):
        self.username = username
        self.password = password
        self.pool_id = pool_id
        self.client_id = client_id
        self.big_n = _hex_to_long(_N_HEX)
        self.g = _hex_to_long(_G_HEX)
        self.k = _hex_to_long(_hex_hash("00" + _N_HEX + "0" + _G_HEX))
        self.small_a_value = self._generate_random_small_a()
        self.large_a_value = self._calculate_a()

    def _generate_random_small_a(self) -> int:
        return _get_random(128) % self.big_n

    def _calculate_a(self) -> int:
        big_a = pow(self.g, self.small_a_value, self.big_n)
        if big_a % self.big_n == 0:
            raise ValueError("Safety check for A failed")
        return big_a

    def get_auth_parameters(self) -> dict[str, str]:
        """AuthParameters for the InitiateAuth (USER_SRP_AUTH) call."""
        return {"USERNAME": self.username, "SRP_A": _long_to_hex(self.large_a_value)}

    def get_password_authentication_key(
        self, username: str, password: str, server_b_value: int, salt: str
    ) -> bytes:
        u_value = _calculate_u(self.large_a_value, server_b_value)
        if u_value == 0:
            raise ValueError("U cannot be zero")
        username_password = f"{self.pool_id.split('_')[1]}{username}:{password}"
        username_password_hash = _hash_sha256(username_password.encode("utf-8"))
        x_value = _hex_to_long(_hex_hash(_pad_hex(salt) + username_password_hash))
        g_mod_pow_xn = pow(self.g, x_value, self.big_n)
        int_value2 = server_b_value - self.k * g_mod_pow_xn
        s_value = pow(int_value2, self.small_a_value + u_value * x_value, self.big_n)
        return _compute_hkdf(
            bytearray.fromhex(_pad_hex(s_value)),
            bytearray.fromhex(_pad_hex(_long_to_hex(u_value))),
        )

    def process_challenge(
        self, challenge_parameters: dict[str, str],
        now: datetime.datetime | None = None,
    ) -> dict[str, str]:
        """Build ChallengeResponses for the PASSWORD_VERIFIER challenge."""
        user_id_for_srp = challenge_parameters["USER_ID_FOR_SRP"]
        salt_hex = challenge_parameters["SALT"]
        srp_b_hex = challenge_parameters["SRP_B"]
        secret_block_b64 = challenge_parameters["SECRET_BLOCK"]
        timestamp = format_timestamp(now or datetime.datetime.now(datetime.timezone.utc))
        hkdf = self.get_password_authentication_key(
            user_id_for_srp, self.password, _hex_to_long(srp_b_hex), salt_hex
        )
        secret_block_bytes = base64.standard_b64decode(secret_block_b64)
        msg = (
            bytearray(self.pool_id.split("_")[1], "utf-8")
            + bytearray(user_id_for_srp, "utf-8")
            + bytearray(secret_block_bytes)
            + bytearray(timestamp, "utf-8")
        )
        signature = base64.standard_b64encode(
            hmac.new(hkdf, msg, hashlib.sha256).digest()
        ).decode("utf-8")
        return {
            "TIMESTAMP": timestamp,
            "USERNAME": user_id_for_srp,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block_b64,
            "PASSWORD_CLAIM_SIGNATURE": signature,
        }
