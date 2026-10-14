"""Client-side encryption and Actions-side decryption for public mail request files.

The request content uses hybrid encryption: RSA-OAEP-SHA256 wraps a random
AES/HMAC key; AES-256-CBC encrypts the JSON; HMAC-SHA256 authenticates the
wrapped key and ciphertext before decryption. OpenSSL is used without a shell.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import uuid

ALGORITHM = "RSA-OAEP-SHA256+A256CBC-PBKDF2-HS256"
VERSION = 2
MAX_REQUEST_BYTES = 128 * 1024
MAX_ENVELOPE_BYTES = 128 * 1024
MAX_KEY_BYTES = 32 * 1024
PBKDF2_ITERATIONS = "200000"
_CONTEXT = b"mail-bot-private-request-v2\0"


class RequestCryptoError(ValueError):
    """Static diagnostic only; never include plaintext or key material."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestCryptoError("请求 JSON 包含重复字段。")
        result[key] = value
    return result


def _openssl(args, *, data=b""):
    try:
        result = subprocess.run(
            ["openssl", *args], input=data, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        raise RequestCryptoError("找不到 OpenSSL；未处理请求内容。") from None
    if result.returncode:
        raise RequestCryptoError("加密或解密失败；未处理请求内容。")
    return result.stdout


def _b64decode(value, *, maximum):
    if not isinstance(value, str) or len(value) > maximum * 2:
        raise RequestCryptoError("加密请求格式无效。")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error):
        raise RequestCryptoError("加密请求格式无效。") from None
    if len(result) > maximum:
        raise RequestCryptoError("加密请求超过大小限制。")
    return result


def _rsa_options():
    return [
        "-pkeyopt", "rsa_padding_mode:oaep",
        "-pkeyopt", "rsa_oaep_md:sha256",
        "-pkeyopt", "rsa_mgf1_md:sha256",
    ]


def _mac_input(wrapped_key, ciphertext):
    return _CONTEXT + wrapped_key + ciphertext


def encrypt_envelope(public_key_path, plaintext):
    if not isinstance(plaintext, bytes) or not 0 < len(plaintext) <= MAX_REQUEST_BYTES:
        raise RequestCryptoError("请求内容为空或超过大小限制。")
    path = Path(public_key_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_KEY_BYTES:
        raise RequestCryptoError("公钥文件无效。")
    key_material = secrets.token_bytes(64)
    passphrase = key_material[:32].hex().encode("ascii") + b"\n"
    mac_key = key_material[32:]
    with tempfile.TemporaryDirectory(prefix="mail-request-encrypt-") as directory:
        password_file = Path(directory) / "password"
        password_file.write_bytes(passphrase)
        try:
            os.chmod(password_file, 0o600)
        except OSError:
            pass
        wrapped_key = _openssl([
            "pkeyutl", "-encrypt", "-pubin", "-inkey", str(path),
            *_rsa_options(),
        ], data=key_material)
        ciphertext = _openssl([
            "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter",
            PBKDF2_ITERATIONS, "-md", "sha256", "-pass",
            f"file:{password_file}",
        ], data=plaintext)
    mac = hmac.new(mac_key, _mac_input(wrapped_key, ciphertext), hashlib.sha256).digest()
    return {
        "algorithm": ALGORITHM,
        "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "mac": base64.b64encode(mac).decode("ascii"),
    }


def encrypt_request(public_key_path, plaintext):
    if not isinstance(plaintext, bytes):
        raise RequestCryptoError("请求内容格式无效。")
    try:
        data = json.loads(plaintext.decode("utf-8"), object_pairs_hook=unique_object)
        rid = data.get("request_id") if isinstance(data, dict) else None
        if (type(data.get("version")) is not int or data.get("version") != 1
                or not isinstance(rid, str) or str(uuid.UUID(rid)) != rid):
            raise ValueError
    except (UnicodeError, ValueError, TypeError, AttributeError, RequestCryptoError):
        raise RequestCryptoError("明文请求结构无效；未生成密文。") from None
    return {
        "version": VERSION,
        "request_id": rid,
        "encrypted": encrypt_envelope(public_key_path, plaintext),
    }


def decrypt_envelope(envelope, private_key_pem):
    if (not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "wrapped_key", "ciphertext", "mac"}
            or envelope.get("algorithm") != ALGORITHM):
        raise RequestCryptoError("加密请求格式无效。")
    wrapped_key = _b64decode(envelope["wrapped_key"], maximum=2048)
    ciphertext = _b64decode(envelope["ciphertext"], maximum=MAX_ENVELOPE_BYTES)
    mac = _b64decode(envelope["mac"], maximum=32)
    if not wrapped_key or len(ciphertext) < 32 or len(mac) != 32:
        raise RequestCryptoError("加密请求格式无效。")
    if not isinstance(private_key_pem, str) or not 100 <= len(private_key_pem) <= MAX_KEY_BYTES:
        raise RequestCryptoError("邮件请求私钥未配置；未发送。")
    with tempfile.TemporaryDirectory(prefix="mail-request-decrypt-") as directory:
        private_file = Path(directory) / "private.pem"
        password_file = Path(directory) / "password"
        private_file.write_text(private_key_pem, encoding="utf-8")
        try:
            os.chmod(private_file, 0o600)
        except OSError:
            pass
        key_material = _openssl([
            "pkeyutl", "-decrypt", "-inkey", str(private_file), *_rsa_options(),
        ], data=wrapped_key)
        if len(key_material) != 64:
            raise RequestCryptoError("加密请求完整性校验失败；未发送。")
        expected = hmac.new(
            key_material[32:], _mac_input(wrapped_key, ciphertext), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(mac, expected):
            raise RequestCryptoError("加密请求完整性校验失败；未发送。")
        password_file.write_bytes(key_material[:32].hex().encode("ascii") + b"\n")
        try:
            os.chmod(password_file, 0o600)
        except OSError:
            pass
        plaintext = _openssl([
            "enc", "-d", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter",
            PBKDF2_ITERATIONS, "-md", "sha256", "-pass",
            f"file:{password_file}",
        ], data=ciphertext)
    if not 0 < len(plaintext) <= MAX_REQUEST_BYTES:
        raise RequestCryptoError("解密请求为空或超过大小限制；未发送。")
    return plaintext


def generate_keypair(private_out, public_out):
    private_path = Path(private_out).expanduser()
    public_path = Path(public_out).expanduser()
    repo_root = Path(__file__).resolve().parents[1]
    private_resolved = private_path.resolve()
    try:
        private_resolved.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise RequestCryptoError("私钥必须保存在仓库目录之外。")
    if private_resolved == public_path.resolve():
        raise RequestCryptoError("公钥和私钥路径必须不同。")
    if private_path.exists() or public_path.exists():
        raise RequestCryptoError("目标文件已存在；为避免覆盖，已停止生成。")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mail-request-keygen-") as directory:
        temp_private = Path(directory) / "private.pem"
        _openssl([
            "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
            "-out", str(temp_private),
        ])
        public_pem = _openssl(["pkey", "-in", str(temp_private), "-pubout"])
        private_pem = temp_private.read_bytes()
    private_fd = None
    try:
        private_fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(private_fd, "wb") as stream:
            private_fd = None
            stream.write(private_pem)
        try:
            os.chmod(private_path, 0o600)
        except OSError:
            pass
        with public_path.open("xb") as stream:
            stream.write(public_pem)
    except Exception:
        if private_fd is not None:
            os.close(private_fd)
        try:
            private_path.unlink()
        except OSError:
            pass
        raise RequestCryptoError("无法安全写入密钥文件。") from None
    return private_path, public_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="为邮件请求生成或使用混合加密密钥。")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private-out", required=True)
    keygen.add_argument("--public-out", required=True)
    encrypt = commands.add_parser("encrypt")
    encrypt.add_argument("--public-key", required=True)
    encrypt.add_argument("--input", default="-", help="明文 JSON 文件；默认从标准输入读取")
    encrypt.add_argument("--output", default="-", help="密文 JSON 文件；默认写入标准输出")
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            private_path, public_path = generate_keypair(args.private_out, args.public_out)
            print(f"密钥已生成；私钥在仓库外：{private_path}；公钥：{public_path}")
            return 0
        source = sys.stdin.buffer if args.input == "-" else open(args.input, "rb")
        try:
            plaintext = source.read(MAX_REQUEST_BYTES + 1)
        finally:
            if source is not sys.stdin.buffer:
                source.close()
        document = encrypt_request(args.public_key, plaintext)
        output = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if args.output == "-":
            sys.stdout.buffer.write(output)
        else:
            target = Path(args.output)
            with target.open("xb") as stream:
                stream.write(output)
        return 0
    except (RequestCryptoError, OSError):
        print("加密请求操作停止；未输出明文或密钥。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
