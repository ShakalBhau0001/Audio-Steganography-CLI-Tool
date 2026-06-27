import os
import sys
import wave
import struct
import secrets
import base64
import warnings
from array import array
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet, InvalidToken
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich.rule import Rule
from rich import box
from rich.align import Align
from rich.padding import Padding

warnings.filterwarnings("ignore", category=DeprecationWarning)

console = Console()

# Key Derivation


def derive_fernet_key(password: str, salt: bytes, iterations: int = 390000) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


# Bit Utilities


def bytes_to_bits(data: bytes):
    for byte in data:
        for i in range(7, -1, -1):
            yield (byte >> i) & 1


def bits_to_bytes(bits):
    out = bytearray()
    bit_iter = iter(bits)
    while True:
        byte = 0
        try:
            for _ in range(8):
                byte = (byte << 1) | next(bit_iter)
        except StopIteration:
            break
        out.append(byte)
    return bytes(out)


# Payload Format

MAGIC = b"AUDS"


def make_payload(encrypted: bytes, salt: bytes) -> bytes:
    return MAGIC + salt + struct.pack(">I", len(encrypted)) + encrypted


def parse_payload(raw: bytes):
    if len(raw) < 24:
        raise ValueError("Incomplete payload header")

    if raw[:4] != MAGIC:
        raise ValueError("MAGIC header missing — no valid steg payload found")

    salt = raw[4:20]
    length = struct.unpack(">I", raw[20:24])[0]
    expected_size = 24 + length
    if len(raw) < expected_size:
        raise ValueError("Payload appears to be truncated or corrupted")

    return salt, raw[24:expected_size]


# WAV Steganography


def embed_payload(wav_in: str, payload: bytes, wav_out: str):
    with wave.open(wav_in, "rb") as wf:
        params = wf.getparams()
        frames = wf.readframes(wf.getnframes())
        sampwidth = wf.getsampwidth()

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM WAV files are supported")

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()

    if len(payload) * 8 > len(samples):
        raise ValueError(
            f"Payload too large: needs {len(payload) * 8} bits, "
            f"WAV has {len(samples)} samples"
        )

    bit_iter = bytes_to_bits(payload)
    finished = False
    for i in range(len(samples)):
        if finished:
            break
        try:
            samples[i] = (samples[i] & ~1) | next(bit_iter)
        except StopIteration:
            finished = True

    if sys.byteorder == "big":
        samples.byteswap()

    with wave.open(wav_out, "wb") as out:
        out.setparams(params)
        out.writeframes(samples.tobytes())


def extract_payload(wav_in: str, size: int) -> bytes:
    with wave.open(wav_in, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sampwidth = wf.getsampwidth()

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM WAV files are supported")

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()

    required_bits = size * 8
    if required_bits > len(samples):
        raise ValueError("Not enough audio samples to extract payload")

    bits = [(samples[i] & 1) for i in range(required_bits)]
    return bits_to_bytes(bits)


def print_banner():
    console.clear()
    banner = Text()
    banner.append("  ░█████╗ ██╗   ██╗██████╗ ███████╗\n", style="bold cyan")
    banner.append("  ██╔══██╗██║   ██║██╔══██╗██╔════╝\n", style="bold cyan")
    banner.append("  ███████║██║   ██║██║  ██║███████╗\n", style="bold blue")
    banner.append("  ██╔══██║██║   ██║██║  ██║╚════██║\n", style="bold blue")
    banner.append("  ██║  ██║╚██████╔╝██████╔╝███████║\n", style="bold magenta")
    banner.append("  ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝\n", style="bold magenta")
    banner.append(
        "\n  Audio Steganography  •  LSB + Fernet/PBKDF2  •  WAV\n",
        style="dim white",
    )
    console.print(Panel(Align.center(banner), border_style="cyan", box=box.DOUBLE_EDGE))


def divider(title: str = ""):
    console.print(Rule(title, style="dim cyan"))


def success(msg: str):
    console.print(f"\n  [bold green]✔[/bold green]  {msg}\n")


def error(msg: str):
    console.print(f"\n  [bold red]✘[/bold red]  {msg}\n")


def info(msg: str):
    console.print(f"  [bold yellow]ℹ[/bold yellow]  {msg}")


def prompt_path(label: str, must_exist: bool = False) -> str:
    while True:
        path = Prompt.ask(f"  [cyan]{label}[/cyan]").strip()
        if must_exist and not os.path.exists(path):
            error(f"File not found: [bold]{path}[/bold]")
        else:
            return path


def prompt_password(label: str = "Password") -> str:
    return Prompt.ask(f"  [cyan]{label}[/cyan]", password=True)


def run_encrypt():
    console.print()
    divider("🔒  ENCRYPT & EMBED INTO WAV")
    console.print()
    in_wav = prompt_path("Cover WAV file path (input)", must_exist=True)
    with wave.open(in_wav, "rb") as wf:
        n_samples = wf.getnframes() * wf.getnchannels()
        sampwidth = wf.getsampwidth()

    if sampwidth != 2:
        error("Only 16-bit PCM WAV files are supported.")
        return

    capacity_bytes = n_samples // 8
    info(f"WAV capacity : [bold]{capacity_bytes:,}[/bold] bytes")
    out_wav = prompt_path("Output stego-WAV path (e.g. secret.wav)")
    console.print()
    console.print("  [cyan]Message source[/cyan]")
    choice = Prompt.ask(
        "  [1] Type message   [2] Load from file\n  Choice",
        choices=["1", "2"],
        default="1",
    )

    if choice == "1":
        message_bytes = Prompt.ask(
            "\n  [cyan]Enter secret message[/cyan]"
        ).encode("utf-8")
    else:
        mfile = prompt_path("Message file path", must_exist=True)
        with open(mfile, "r", encoding="utf-8") as f:
            message_bytes = f.read().encode("utf-8")
        info(f"Loaded [bold]{len(message_bytes)}[/bold] bytes from [bold]{mfile}[/bold]")

    password = prompt_password("Encryption password")
    console.print()
    with console.status(
        "[bold cyan]Encrypting & embedding into WAV…[/bold cyan]", spinner="dots"
    ):
        salt = secrets.token_bytes(16)
        key = derive_fernet_key(password, salt)
        encrypted = Fernet(key).encrypt(message_bytes)
        payload = make_payload(encrypted, salt)
        embed_payload(in_wav, payload, out_wav)

    success(f"Message embedded → [bold white]{out_wav}[/bold white]")
    info(f"Payload size  : [bold]{len(payload)}[/bold] bytes")
    info(f"Cover WAV     : [bold]{in_wav}[/bold]")
    info(f"Stego WAV     : [bold]{out_wav}[/bold]")
    console.print()


def run_decrypt():
    console.print()
    divider("🔓  EXTRACT & DECRYPT FROM WAV")
    console.print()
    in_wav = prompt_path("Stego-WAV file path (input)", must_exist=True)
    password = prompt_password("Decryption password")
    out_file = Prompt.ask(
        "\n  [cyan]Save to file? (Leave blank to print to terminal)[/cyan]",
        default="",
    ).strip()

    console.print()
    with console.status(
        "[bold cyan]Extracting & decrypting…[/bold cyan]", spinner="dots"
    ):
        header = extract_payload(in_wav, 24)
        if header[:4] != MAGIC:
            error("No valid steg payload found in this WAV file.")
            return

        salt = header[4:20]
        enc_len = struct.unpack(">I", header[20:24])[0]
        full = extract_payload(in_wav, 24 + enc_len)
        try:
            salt2, encrypted = parse_payload(full)
        except ValueError as e:
            error(str(e))
            return

        if salt != salt2:
            error("Payload corruption detected — aborting.")
            return

        key = derive_fernet_key(password, salt2)
        try:
            decrypted = Fernet(key).decrypt(encrypted)
        except InvalidToken:
            error("Incorrect password or corrupted WAV file.")
            return

    if out_file:
        with open(out_file, "wb") as f:
            f.write(decrypted)
        success(f"Decrypted content saved → [bold white]{out_file}[/bold white]")
    else:
        console.print()
        divider("Decrypted Message")
        try:
            console.print(
                Padding(decrypted.decode("utf-8"), (1, 4)),
                style="bold white",
            )
        except UnicodeDecodeError:
            console.print(
                Padding(repr(decrypted), (1, 4)),
                style="bold white",
            )
        divider()
        console.print()


def show_about():
    console.print()
    table = Table(
        box=box.SIMPLE_HEAVY, border_style="cyan", show_header=False, padding=(0, 2)
    )
    table.add_column(style="bold cyan", width=22)
    table.add_column(style="white")
    table.add_row("Encryption",    "Fernet (AES-128-CBC + HMAC-SHA256)")
    table.add_row("KDF",           "PBKDF2-HMAC-SHA256 (390,000 iterations)")
    table.add_row("Salt",          "16 bytes — random per operation")
    table.add_row("Steganography", "LSB (Least Significant Bit) — audio samples")
    table.add_row("Audio format",  "16-bit PCM WAV only")
    table.add_row("Payload magic", "AUDS header for integrity check")
    table.add_row("Sample depth",  "1 bit per sample (inaudible change)")
    console.print(
        Panel(
            table,
            title="[bold cyan]ℹ  About AUDS[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print()


MENU_ITEMS = [
    ("1", "🔒  Encrypt & embed message into WAV"),
    ("2", "🔓  Extract & decrypt message from WAV"),
    ("3", "ℹ   About / Algorithm info"),
    ("0", "🚪  Exit"),
]


def draw_menu():
    table = Table(
        box=box.ROUNDED, border_style="cyan", show_header=False, padding=(0, 3)
    )
    table.add_column("key", style="bold magenta", width=4)
    table.add_column("action", style="white")

    for key, label in MENU_ITEMS:
        table.add_row(f"[{key}]", label)

    console.print(Align.center(table))


def main():
    while True:
        print_banner()
        draw_menu()
        console.print()
        choice = Prompt.ask(
            "  [bold cyan]Select option[/bold cyan]",
            choices=["0", "1", "2", "3"],
            show_choices=False,
        )
        if choice == "1":
            try:
                run_encrypt()
            except ValueError as e:
                error(str(e))
            except Exception as e:
                error(f"Unexpected error: {e}")
        elif choice == "2":
            try:
                run_decrypt()
            except ValueError as e:
                error(str(e))
            except Exception as e:
                error(f"Unexpected error: {e}")
        elif choice == "3":
            show_about()
        elif choice == "0":
            console.print()
            console.print(
                Panel(
                    Align.center(
                        Text(
                            "See You Soon! Stay hidden, stay secure. 🎵🕵️",
                            style="bold cyan",
                        )
                    ),
                    border_style="magenta",
                    box=box.DOUBLE_EDGE,
                )
            )
            console.print()
            sys.exit(0)

        if choice != "0":
            Prompt.ask("  [dim]Press Enter to return to menu…[/dim]", default="")


if __name__ == "__main__":
    main()
