#!/usr/bin/env python3
"""Standalone ECMA-376 Agile Encryption for Microsoft Excel (.xlsx) files.

Encrypts .xlsx files with a password so Microsoft Excel prompts for password on launch.
Uses OpenSSL CLI or Python cryptography library for AES-256-CBC encryption.
Requires 'olefile' (which is already installed on the BeagleBone).
"""

import base64
from datetime import datetime
import functools
from hashlib import sha512
import hmac
import io
import os
from pathlib import Path
import secrets
from struct import pack, unpack
import subprocess
import sys

try:
    import olefile
except ImportError:
    olefile = None


def _random_buffer(sz):
    return secrets.token_bytes(sz)


def _resize_buffer(buf, n, c=b"\x00"):
    if len(buf) >= n:
        return buf[:n]
    return buf + c * (n - len(buf))


def _normalize_key(key, n):
    return _resize_buffer(key, n, b"\x36")


def _round_up(num, step):
    remainder = num % step
    return num if remainder == 0 else (num + step - remainder)


def _encrypt_aes_cbc(data, key, iv):
    """Encrypt using AES-CBC with OpenSSL or cryptography."""
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        aes = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = aes.encryptor()
        return encryptor.update(data) + encryptor.finalize()
    except Exception:
        pass

    # Fallback to OpenSSL CLI
    cmd = ["openssl", "enc", f"-aes-{len(key)*8}-cbc", "-K", key.hex(), "-iv", iv.hex(), "-nopad"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(input=data)
    if proc.returncode != 0:
        raise RuntimeError(f"OpenSSL encrypt failed: {err.decode()}")
    return out


def _encrypt_aes_cbc_padded(data, key, iv, block_size=16):
    buf = data
    if len(buf) % block_size:
        buf = _resize_buffer(buf, _round_up(len(buf), block_size))
    return _encrypt_aes_cbc(buf, key, iv)


# Block keys for Agile encryption (MS-OFFCRYPTO spec)
BLK_VERIFIER_HASH_INPUT = bytearray([0xFE, 0xA7, 0xD2, 0x76, 0x3B, 0x4B, 0x9E, 0x79])
BLK_ENCRYPTED_VERIFIER_HASH = bytearray([0xD7, 0xAA, 0x0F, 0x6D, 0x30, 0x61, 0x34, 0x4E])
BLK_ENCRYPTED_KEY_VALUE = bytearray([0x14, 0x6E, 0x0B, 0xE7, 0xAB, 0xAC, 0xD0, 0xD6])
BLK_INTEGRITY_1 = bytearray([0x5F, 0xB2, 0xAD, 0x01, 0x0C, 0xB9, 0xE1, 0xF6])
BLK_INTEGRITY_2 = bytearray([0xA0, 0x67, 0x7F, 0x02, 0xB2, 0x2C, 0x84, 0x33])


class CipherParams:
    def __init__(self):
        self.cipherName = "AES"
        self.hashName = "SHA512"
        self.keyBits = 256
        self.blockSize = 16
        self.saltSize = 16
        self.saltValue = None


class AgileEncryptionInfo:
    def __init__(self):
        self.spinCount = 100000
        self.keyData = CipherParams()
        self.encryptedKey = CipherParams()
        self.encryptedVerifierHashInput = None
        self.encryptedVerifierHashValue = None
        self.encryptedKeyValue = None
        self.encryptedHmacKey = None
        self.encryptedHmacValue = None


def _derive_iterated_hash(password, salt, spin_count=100000):
    password_utf16 = password.encode("utf-16le")
    h = sha512(salt + password_utf16).digest()
    for i in range(spin_count):
        h = sha512(pack("<I", i) + h).digest()
    return h


def _derive_key(iterated_hash, block_key, key_bits=256):
    h = sha512(iterated_hash + block_key).digest()
    return h[:key_bits // 8]


def generate_encryption_info(password, spin_count=100000):
    info = AgileEncryptionInfo()
    info.spinCount = spin_count
    info.encryptedKey.saltValue = _random_buffer(info.encryptedKey.saltSize)

    iterated_hash = _derive_iterated_hash(password, info.encryptedKey.saltValue, spin_count)

    key1 = _derive_key(iterated_hash, BLK_VERIFIER_HASH_INPUT, info.encryptedKey.keyBits)
    key2 = _derive_key(iterated_hash, BLK_ENCRYPTED_VERIFIER_HASH, info.encryptedKey.keyBits)
    key3 = _derive_key(iterated_hash, BLK_ENCRYPTED_KEY_VALUE, info.encryptedKey.keyBits)

    verifier_hash_input = _random_buffer(info.encryptedKey.saltSize)
    verifier_hash_input = _resize_buffer(verifier_hash_input, _round_up(len(verifier_hash_input), info.encryptedKey.blockSize))

    info.encryptedVerifierHashInput = _encrypt_aes_cbc(verifier_hash_input, key1, info.encryptedKey.saltValue)

    verifier_hash_value = sha512(verifier_hash_input).digest()
    verifier_hash_value = _resize_buffer(verifier_hash_value, _round_up(len(verifier_hash_value), info.encryptedKey.blockSize))

    info.encryptedVerifierHashValue = _encrypt_aes_cbc(verifier_hash_value, key2, info.encryptedKey.saltValue)

    secret_key = _random_buffer(info.encryptedKey.keyBits // 8)
    info.encryptedKeyValue = _encrypt_aes_cbc(secret_key, key3, info.encryptedKey.saltValue)

    info.keyData.saltValue = _random_buffer(info.keyData.saltSize)
    return info, secret_key


def encrypt_payload(plain_bytes, params: CipherParams, secret_key, salt_value):
    total_size = len(plain_bytes)
    obuf = io.BytesIO()
    obuf.write(pack("<Q", total_size))

    SEGMENT = 4096
    for i in range(0, total_size, SEGMENT):
        chunk = plain_bytes[i:i+SEGMENT]
        iv = _normalize_key(sha512(salt_value + pack("<I", i // SEGMENT)).digest(), params.saltSize)
        enc = _encrypt_aes_cbc_padded(chunk, secret_key, iv, params.blockSize)
        obuf.write(enc)
    return obuf.getvalue()


def build_encryption_info_xml(info: AgileEncryptionInfo, encrypted_data, secret_key):
    # Integrity parameters
    hmac_key = _random_buffer(info.encryptedKey.keyBits // 8)
    iv_hmac = _normalize_key(sha512(info.keyData.saltValue + BLK_INTEGRITY_1).digest(), info.keyData.saltSize)
    encrypted_hmac_key = _encrypt_aes_cbc_padded(hmac_key, secret_key, iv_hmac, info.keyData.blockSize)

    calculated_hmac = hmac.new(hmac_key, encrypted_data, sha512).digest()
    iv_val = _normalize_key(sha512(info.keyData.saltValue + BLK_INTEGRITY_2).digest(), info.keyData.saltSize)
    encrypted_hmac_value = _encrypt_aes_cbc_padded(calculated_hmac, secret_key, iv_val, info.keyData.blockSize)

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<encryption xmlns="http://schemas.microsoft.com/office/2006/encryption" '
        'xmlns:p="http://schemas.microsoft.com/office/2006/keyEncryptor/password" '
        'xmlns:c="http://schemas.microsoft.com/office/2006/keyEncryptor/certificate">'
        f'<keyData saltSize="{info.keyData.saltSize}" blockSize="{info.keyData.blockSize}" '
        f'keyBits="{info.keyData.keyBits}" hashSize="64" cipherAlgorithm="AES" '
        f'cipherChaining="ChainingModeCBC" hashAlgorithm="SHA512" '
        f'saltValue="{base64.b64encode(info.keyData.saltValue).decode()}"/>'
        f'<dataIntegrity encryptedHmacKey="{base64.b64encode(encrypted_hmac_key).decode()}" '
        f'encryptedHmacValue="{base64.b64encode(encrypted_hmac_value).decode()}"/>'
        '<keyEncryptors><keyEncryptor uri="http://schemas.microsoft.com/office/2006/keyEncryptor/password">'
        f'<p:encryptedKey spinCount="{info.spinCount}" saltSize="{info.encryptedKey.saltSize}" '
        f'blockSize="{info.encryptedKey.blockSize}" keyBits="{info.encryptedKey.keyBits}" '
        'hashSize="64" cipherAlgorithm="AES" cipherChaining="ChainingModeCBC" hashAlgorithm="SHA512" '
        f'saltValue="{base64.b64encode(info.encryptedKey.saltValue).decode()}" '
        f'encryptedVerifierHashInput="{base64.b64encode(info.encryptedVerifierHashInput).decode()}" '
        f'encryptedVerifierHashValue="{base64.b64encode(info.encryptedVerifierHashValue).decode()}" '
        f'encryptedKeyValue="{base64.b64encode(info.encryptedKeyValue).decode()}"/>'
        '</keyEncryptor></keyEncryptors></encryption>'
    )
    # Header: version major = 4, version minor = 4, flags = 0x40 (agile)
    header = pack("<HHII", 4, 4, 0x40, 0)
    return header + xml.encode("utf-8")


# OLE Compound Document Minimal Generator (CFBF)
def create_ole_package(encryption_info_bytes, encrypted_package_bytes):
    """Create an OLE compound document containing EncryptionInfo and EncryptedPackage streams."""
    SECTOR_SIZE = 512
    DIFAT_SECT = 0xFFFFFFFC
    FAT_SECT = 0xFFFFFFFD
    ENDOFCHAIN = 0xFFFFFFFE
    FREESECT = 0xFFFFFFFF

    def make_entry(name, entry_type, size, start_sect, left=0xFFFFFFFF, right=0xFFFFFFFF, child=0xFFFFFFFF):
        name_utf16 = name.encode("utf-16le")
        name_bytes = name_utf16 + b"\x00\x00"
        name_buf = name_bytes.ljust(64, b"\x00")
        name_len = len(name_bytes)
        # Entry layout: 64B name, 2B name_len, 1B type, 1B color (black=1), 4B left, 4B right, 4B child, 16B clsid, 4B flags, 16B timestamps, 4B start_sect, 8B size
        entry = (
            name_buf +
            pack("<HBBI", name_len, entry_type, 1, left) +
            pack("<II", right, child) +
            b"\x00" * 16 + # CLSID
            pack("<I", 0) + # state flags
            b"\x00" * 16 + # timestamps
            pack("<IQ", start_sect, size)
        )
        return entry

    # 1. Prepare stream buffers padded to 512 bytes
    def pad_sectors(buf):
        rem = len(buf) % SECTOR_SIZE
        return buf if rem == 0 else (buf + b"\x00" * (SECTOR_SIZE - rem))

    info_padded = pad_sectors(encryption_info_bytes)
    pkg_padded = pad_sectors(encrypted_package_bytes)

    info_sects = len(info_padded) // SECTOR_SIZE
    pkg_sects = len(pkg_padded) // SECTOR_SIZE

    # Sector mapping:
    # Sector 0 .. info_sects - 1 : EncryptionInfo stream
    # Sector info_sects .. info_sects + pkg_sects - 1 : EncryptedPackage stream
    # Sector info_sects + pkg_sects : Directory stream (1 sector = 4 entries)
    # Sector info_sects + pkg_sects + 1 : FAT sector (1 sector handles 128 sectors)

    info_start = 0
    pkg_start = info_sects
    dir_start = info_sects + pkg_sects
    fat_start = dir_start + 1

    total_data_sects = fat_start + 1

    # Directory entries (4 entries per 512B sector):
    # Entry 0: Root Entry (child = 1)
    # Entry 1: EncryptionInfo (right = 2)
    # Entry 2: EncryptedPackage
    # Entry 3: Unused
    dir_buf = (
        make_entry("Root Entry", 5, 0, 0xFFFFFFFE, child=1) +
        make_entry("EncryptionInfo", 2, len(encryption_info_bytes), info_start, right=2) +
        make_entry("EncryptedPackage", 2, len(encrypted_package_bytes), pkg_start) +
        make_entry("", 0, 0, 0xFFFFFFFE)
    )

    # Build FAT table
    fat = [FREESECT] * 128
    # Chain for EncryptionInfo
    for s in range(info_sects - 1):
        fat[info_start + s] = info_start + s + 1
    if info_sects > 0:
        fat[info_start + info_sects - 1] = ENDOFCHAIN

    # Chain for EncryptedPackage
    for s in range(pkg_sects - 1):
        fat[pkg_start + s] = pkg_start + s + 1
    if pkg_sects > 0:
        fat[pkg_start + pkg_sects - 1] = ENDOFCHAIN

    # Directory chain (1 sector)
    fat[dir_start] = ENDOFCHAIN
    # FAT sector itself
    fat[fat_start] = FAT_SECT

    fat_bytes = b"".join(pack("<I", x) for x in fat)

    # Build Header (512 bytes)
    header = bytearray(512)
    header[0:8] = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" # OLE signature
    header[24:26] = pack("<H", 0x003E) # Minor version
    header[26:28] = pack("<H", 0x0003) # Major version 3 (512B sectors)
    header[28:30] = pack("<H", 0xFFFE) # Byte order (little endian)
    header[30:32] = pack("<H", 9)      # Sector shift (2^9 = 512)
    header[32:34] = pack("<H", 6)      # Mini sector shift (2^6 = 64)
    header[44:48] = pack("<I", 1)      # Number of FAT sectors
    header[48:52] = pack("<I", dir_start) # First dir sector
    header[56:60] = pack("<I", 4096)   # Mini stream cutoff
    header[60:64] = pack("<I", 0xFFFFFFFE) # Mini FAT start
    header[64:68] = pack("<I", 0)      # Number of Mini FAT sectors
    header[68:72] = pack("<I", 0xFFFFFFFE) # DIFAT start
    header[72:76] = pack("<I", 0)      # Number of DIFAT sectors
    header[76:80] = pack("<I", fat_start)  # First FAT sector in header DIFAT array
    # Fill remaining header DIFAT entries with FREESECT
    for idx in range(1, 109):
        header[76 + idx*4:80 + idx*4] = pack("<I", FREESECT)

    return bytes(header) + info_padded + pkg_padded + dir_buf + fat_bytes


def encrypt_excel_workbook(input_xlsx_path, output_xlsx_path, password):
    """Encrypt a plain .xlsx Excel workbook with a password."""
    with open(input_xlsx_path, "rb") as f:
        plain_bytes = f.read()

    info, secret_key = generate_encryption_info(password)
    encrypted_package = encrypt_payload(plain_bytes, info.keyData, secret_key, info.keyData.saltValue)
    encryption_info = build_encryption_info_xml(info, encrypted_package, secret_key)

    ole_bytes = create_ole_package(encryption_info, encrypted_package)

    with open(output_xlsx_path, "wb") as f:
        f.write(ole_bytes)
    return output_xlsx_path


if __name__ == "__main__":
    import zipfile
    test_in = "test_plain.xlsx"
    test_out = "test_encrypted.xlsx"
    pwd = "BLE_TEST_PASSWORD"

    # Create dummy .xlsx
    with zipfile.ZipFile(test_in, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<workbook/>')

    encrypt_excel_workbook(test_in, test_out, pwd)
    print(f"Successfully created encrypted Excel file: {test_out} (size: {os.path.getsize(test_out)} bytes)")
