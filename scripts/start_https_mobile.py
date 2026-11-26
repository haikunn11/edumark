from __future__ import annotations

import argparse
import datetime
import ipaddress
import os
import shutil
import socket
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERTS_DIR = os.path.join(PROJECT_ROOT, "certs")
CERT_FILE = os.path.join(CERTS_DIR, "edumark.crt")
KEY_FILE = os.path.join(CERTS_DIR, "edumark.key")
SAN_CNF = os.path.join(CERTS_DIR, "san.cnf")

EXTRA_OPENSSL_PATHS = [
    r"C:\Users\lenku\AppData\Local\hermes\git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\usr\bin\openssl.exe",
]


def detect_lan_ip() -> str:
    """Phát hiện địa chỉ IPv4 mạng Wi-Fi / LAN cục bộ của máy chủ."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def find_openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    for candidate in EXTRA_OPENSSL_PATHS:
        if os.path.isfile(candidate):
            return candidate
    return None


def _generate_with_cryptography(lan_ip: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EduMark-AI-Local")])

    san_items: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        ip_obj = ipaddress.ip_address(lan_ip)
        if str(ip_obj) != "127.0.0.1":
            san_items.append(x509.IPAddress(ip_obj))
    except ValueError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_items), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(KEY_FILE, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return CERT_FILE, KEY_FILE


def ensure_san_certificate(lan_ip: str) -> tuple[str, str]:
    """Tạo chứng chỉ TLS tự ký kèm SAN (localhost, 127.0.0.1, LAN IP) để Mobile Safari/Chrome mở Camera."""
    os.makedirs(CERTS_DIR, exist_ok=True)
    try:
        return _generate_with_cryptography(lan_ip)
    except Exception:
        openssl_bin = find_openssl()
        if not openssl_bin:
            raise RuntimeError("Không thể tạo chứng chỉ HTTPS (thiếu cả cryptography lẫn openssl).")

        cnf_body = f"""[req]
default_bits = 2048
prompt = no
default_md = sha256
x509_extensions = v3_req
distinguished_name = dn

[dn]
CN = EduMark-AI-Local

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
IP.2 = {lan_ip}
"""
        with open(SAN_CNF, "w", encoding="utf-8") as f:
            f.write(cnf_body)

        subprocess.run(
            [
                openssl_bin,
                "req",
                "-x509",
                "-nodes",
                "-days",
                "365",
                "-newkey",
                "rsa:2048",
                "-keyout",
                KEY_FILE,
                "-out",
                CERT_FILE,
                "-config",
                SAN_CNF,
                "-extensions",
                "v3_req",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return CERT_FILE, KEY_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description="Start EduMark AI HTTPS Mobile Server on LAN")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert-only", action="store_true", help="Only generate TLS cert and print LAN URL")
    args = parser.parse_args()

    lan_ip = detect_lan_ip()
    cert_path, key_path = ensure_san_certificate(lan_ip)
    print(f"CERT_READY: {cert_path}")
    print(f"MOBILE_HTTPS_URL: https://{lan_ip}:{args.port}/app")
    if args.cert_only:
        return

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--app-dir",
        PROJECT_ROOT,
        "--host",
        "0.0.0.0",
        "--port",
        str(args.port),
        "--ssl-certfile",
        cert_path,
        "--ssl-keyfile",
        key_path,
    ]
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
