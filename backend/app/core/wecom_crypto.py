import base64
import hashlib
import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCryptoError(Exception):
    pass


def _sha1_hex(items: list[str]) -> str:
    raw = "".join(sorted(items)).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise WeComCryptoError("PKCS7: empty data")
    pad = data[-1]
    if pad < 1 or pad > 32:
        raise WeComCryptoError("PKCS7: invalid padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise WeComCryptoError("PKCS7: invalid padding bytes")
    return data[:-pad]


def _pkcs7_pad(data: bytes) -> bytes:
    block_size = 32
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def _aes_key_from_encoding_aes_key(encoding_aes_key: str) -> bytes:
    try:
        return base64.b64decode(encoding_aes_key + "=", validate=False)
    except Exception as e:
        raise WeComCryptoError(f"EncodingAESKey decode failed: {e}") from e


@dataclass(frozen=True)
class WeComCrypto:
    token: str
    encoding_aes_key: str
    corp_id: str

    def signature(self, *, timestamp: str, nonce: str, encrypt: str) -> str:
        return _sha1_hex([self.token, timestamp, nonce, encrypt])

    def verify_signature(self, *, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> None:
        expected = self.signature(timestamp=timestamp, nonce=nonce, encrypt=encrypt)
        if expected != msg_signature:
            raise WeComCryptoError("invalid msg_signature")

    def decrypt(self, *, encrypt: str) -> str:
        aes_key = _aes_key_from_encoding_aes_key(self.encoding_aes_key)
        if len(aes_key) != 32:
            raise WeComCryptoError("invalid AES key length (expected 32 bytes)")
        iv = aes_key[:16]

        try:
            cipher_text = base64.b64decode(encrypt)
        except Exception as e:
            raise WeComCryptoError(f"base64 decrypt input failed: {e}") from e

        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plain_padded = decryptor.update(cipher_text) + decryptor.finalize()
        plain = _pkcs7_unpad(plain_padded)

        if len(plain) < 20:
            raise WeComCryptoError("decrypted payload too short")

        msg_len = struct.unpack("!I", plain[16:20])[0]
        msg_start = 20
        msg_end = msg_start + msg_len
        if msg_end > len(plain):
            raise WeComCryptoError("invalid message length in payload")

        msg = plain[msg_start:msg_end]
        corp_id = plain[msg_end:].decode("utf-8", errors="replace")
        if self.corp_id and corp_id != self.corp_id:
            raise WeComCryptoError("corp_id mismatch")

        return msg.decode("utf-8", errors="replace")

    def encrypt(self, *, plaintext: str) -> str:
        aes_key = _aes_key_from_encoding_aes_key(self.encoding_aes_key)
        if len(aes_key) != 32:
            raise WeComCryptoError("invalid AES key length (expected 32 bytes)")
        iv = aes_key[:16]

        msg = plaintext.encode("utf-8")
        corp_id = (self.corp_id or "").encode("utf-8")

        raw = os.urandom(16) + struct.pack("!I", len(msg)) + msg + corp_id
        raw_padded = _pkcs7_pad(raw)

        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        cipher_text = encryptor.update(raw_padded) + encryptor.finalize()
        return base64.b64encode(cipher_text).decode("utf-8")


def extract_encrypt_from_xml(xml_text: str) -> str:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        raise WeComCryptoError(f"invalid xml: {e}") from e

    encrypt_node = root.find("Encrypt")
    if encrypt_node is None or not encrypt_node.text:
        raise WeComCryptoError("missing Encrypt node")
    return encrypt_node.text.strip()


def build_encrypted_reply_xml(*, crypto: WeComCrypto, plaintext: str, timestamp: str, nonce: str) -> str:
    encrypt = crypto.encrypt(plaintext=plaintext)
    msg_signature = crypto.signature(timestamp=timestamp, nonce=nonce, encrypt=encrypt)

    return (
        "<xml>" 
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{msg_signature}]]></MsgSignature>"
        f"<TimeStamp>{timestamp}</TimeStamp>"
        f"<Nonce><![CDATA[{nonce}]]></Nonce>"
        "</xml>"
    )

