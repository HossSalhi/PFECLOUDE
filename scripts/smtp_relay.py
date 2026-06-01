#!/usr/bin/env python3
"""
HELIOS SMTP Relay — relaie les emails vers Gmail via App Password.
Lance: python scripts/smtp_relay.py
Le tunnel SSH doit être actif: ssh -R 2525:localhost:2525 kube@192.168.148.161
"""

import smtplib, ssl, socket, threading, logging
from email import message_from_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RELAY] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

GMAIL_USER = "houssamsalhi273@gmail.com"
GMAIL_PASS = "wppfmdxpyudnrsrp"
LISTEN_PORT = 2525


def relay_to_gmail(mail_from: str, rcpt_to: list, raw: bytes):
    ctx = ssl.create_default_context()
    msg = message_from_bytes(raw)
    # Gmail exige que le From soit le compte authentifié
    if "From" in msg:
        msg.replace_header("From", f"HELIOS AIOps <{GMAIL_USER}>")
    else:
        msg["From"] = f"HELIOS AIOps <{GMAIL_USER}>"

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg, from_addr=GMAIL_USER, to_addrs=rcpt_to)
    log.info(f"Email envoyé → {rcpt_to}")


def handle(conn: socket.socket):
    mail_from, rcpt_to, body_lines, in_data = "", [], [], False
    try:
        conn.sendall(b"220 HELIOS SMTP Relay\r\n")
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                cmd = line.decode("utf-8", errors="replace")
                if in_data:
                    if cmd == ".":
                        in_data = False
                        raw = "\r\n".join(body_lines).encode()
                        try:
                            relay_to_gmail(mail_from, rcpt_to, raw)
                            conn.sendall(b"250 Message relaye vers Gmail\r\n")
                        except Exception as e:
                            log.error(f"Erreur relay: {e}")
                            conn.sendall(f"550 Echec relay: {e}\r\n".encode())
                        mail_from, rcpt_to, body_lines = "", [], []
                    else:
                        body_lines.append(cmd)
                elif cmd.upper().startswith(("EHLO", "HELO")):
                    conn.sendall(b"250-HELIOS\r\n250 OK\r\n")
                elif cmd.upper().startswith("MAIL FROM"):
                    mail_from = cmd.split(":", 1)[1].strip().strip("<>") if ":" in cmd else ""
                    conn.sendall(b"250 OK\r\n")
                elif cmd.upper().startswith("RCPT TO"):
                    rcpt_to.append(cmd.split(":", 1)[1].strip().strip("<>") if ":" in cmd else "")
                    conn.sendall(b"250 OK\r\n")
                elif cmd.upper() == "DATA":
                    in_data = True
                    conn.sendall(b"354 Envoyez le message, terminez par <CRLF>.<CRLF>\r\n")
                elif cmd.upper() == "QUIT":
                    conn.sendall(b"221 Au revoir\r\n")
                    return
                elif cmd.upper() == "RSET":
                    mail_from, rcpt_to, body_lines, in_data = "", [], [], False
                    conn.sendall(b"250 OK\r\n")
                else:
                    conn.sendall(b"250 OK\r\n")
    except Exception as e:
        log.warning(f"Connexion fermée: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    srv.listen(10)
    log.info(f"Relay actif sur 0.0.0.0:{LISTEN_PORT} (accessible depuis les VMs via 192.168.148.1:{LISTEN_PORT})")
    log.info(f"Gmail: {GMAIL_USER}")
    while True:
        conn, addr = srv.accept()
        log.info(f"Connexion de {addr}")
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
