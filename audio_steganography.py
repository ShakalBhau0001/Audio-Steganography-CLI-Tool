import os
import sys
import wave
import struct
import secrets
import base64
import argparse
from array import array
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet

#  Key derivation


def derive_fernet_key_from_password(
    password: str, salt: bytes, iterations: int = 390000
) -> bytes:
    password_bytes = password.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive(password_bytes)
    return base64.urlsafe_b64encode(key)


#  Bit utilities


def bytes_to_bits(data: bytes):
    for b in data:
        for i in range(7, -1, -1):
            yield (b >> i) & 1


def bits_to_bytes(bits):
    out = bytearray()
    bit_iter = iter(bits)
    while True:
        byte = 0
        try:
            for _ in range(8):
                bit = next(bit_iter)
                byte = (byte << 1) | bit
        except StopIteration:
            break
        out.append(byte)
    return bytes(out)


#  Payload formatting

MAGIC = b"AUDS"


def make_payload(encrypted: bytes, salt: bytes) -> bytes:
    return MAGIC + salt + struct.pack(">I", len(encrypted)) + encrypted


def parse_payload(raw: bytes):
    if raw[:4] != MAGIC:
        raise ValueError("MAGIC header missing")
    salt = raw[4:20]
    length = struct.unpack(">I", raw[20:24])[0]
    return salt, raw[24 : 24 + length]


#  WAV steganography


def embed_payload(wav_in, payload, wav_out):
    with wave.open(wav_in, "rb") as wf:
        params = wf.getparams()
        frames = wf.readframes(wf.getnframes())
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM WAV supported")

    samples = array("h")
    samples.frombytes(frames)

    if sys.byteorder == "big":
        samples.byteswap()

    if len(payload) * 8 > len(samples):
        raise ValueError("Payload too large for this WAV")

    bit_iter = bytes_to_bits(payload)
    for i in range(len(samples)):
        try:
            samples[i] = (samples[i] & ~1) | next(bit_iter)
        except StopIteration:
            break

    out_frames = samples.tobytes()
    if sys.byteorder == "big":
        samples.byteswap()
        out_frames = samples.tobytes()

    with wave.open(wav_out, "wb") as out:
        out.setparams(params)
        out.writeframes(out_frames)


def extract_payload(wav_in, size):
    with wave.open(wav_in, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sampwidth = wf.getsampwidth()

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM WAV supported")

    samples = array("h")
    samples.frombytes(frames)

    if sys.byteorder == "big":
        samples.byteswap()

    bits = [(samples[i] & 1) for i in range(size * 8)]
    return bits_to_bytes(bits)


def encrypt_and_embed(wav_in, wav_out, message, password):
    salt = secrets.token_bytes(16)
    key = derive_fernet_key_from_password(password, salt)
    encrypted = Fernet(key).encrypt(message)
    payload = make_payload(encrypted, salt)
    embed_payload(wav_in, payload, wav_out)


def extract_and_decrypt(wav_in, password):
    header = extract_payload(wav_in, 24)
    salt, _ = parse_payload(header)
    enc_len = struct.unpack(">I", header[20:24])[0]
    full = extract_payload(wav_in, 24 + enc_len)
    salt, encrypted = parse_payload(full)
    key = derive_fernet_key_from_password(password, salt)
    return Fernet(key).decrypt(encrypted)


#  CLI


def main():
    parser = argparse.ArgumentParser(description="Audio Steganography CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    enc = sub.add_parser("encrypt")
    enc.add_argument("--in-wav", required=True)
    enc.add_argument("--out-wav", required=True)
    enc.add_argument("--password", required=True)
    enc.add_argument("--message")
    enc.add_argument("--message-file")

    dec = sub.add_parser("decrypt")
    dec.add_argument("--in-wav", required=True)
    dec.add_argument("--password", required=True)

    args = parser.parse_args()

    if args.cmd == "encrypt":
        if not args.message and not args.message_file:
            parser.error("Provide --message or --message-file")

        if args.message_file:
            with open(args.message_file, "r", encoding="utf-8") as f:
                msg = f.read()
        else:
            msg = args.message

        encrypt_and_embed(
            args.in_wav,
            args.out_wav,
            msg.encode("utf-8"),
            args.password,
        )
        print("[+] Message encrypted & embedded successfully")

    else:
        data = extract_and_decrypt(args.in_wav, args.password)
        try:
            print(data.decode("utf-8"))
        except UnicodeDecodeError:
            print(data)


if __name__ == "__main__":
    main()
  
