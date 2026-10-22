import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from config import get_aes_key


def generate_key() -> str:
    """Generates a random 32-byte key encoded in Base64 for .env AES_KEY."""
    random_bytes = os.urandom(32)
    return base64.b64encode(random_bytes).decode("utf-8")


def encrypt_token(plain_text: str) -> tuple[bytes, bytes, bytes]:
    """
    Encrypts plaintext using AES-256-GCM.
    Returns: (ciphertext, nonce, tag) matching the database schema.
    """
    key = get_aes_key()
    aesgcm = AESGCM(key)
    # 12-byte nonce recommended for AES-GCM
    nonce = os.urandom(12)
    pt_bytes = plain_text.encode("utf-8")

    # AESGCM.encrypt returns ciphertext + 16-byte tag
    ct_with_tag = aesgcm.encrypt(nonce, pt_bytes, None)
    ciphertext = ct_with_tag[:-16]
    tag = ct_with_tag[-16:]

    return ciphertext, nonce, tag


def decrypt_token(ciphertext: bytes, nonce: bytes, tag: bytes) -> str:
    """
    Decrypts AES-256-GCM ciphertext using nonce and tag.
    Returns decrypted plaintext string.
    """
    key = get_aes_key()
    aesgcm = AESGCM(key)
    ct_with_tag = ciphertext + tag
    decrypted_bytes = aesgcm.decrypt(nonce, ct_with_tag, None)
    return decrypted_bytes.decode("utf-8")
