"""
HELIOS — Lanceur du test de production E2E, EXÉCUTÉ DANS LE CLUSTER.

Ce lanceur ne fait qu'ORCHESTRER : il copie prod_test_in_cluster.sh sur le MASTER
(192.168.148.153) et l'exécute LÀ-BAS. Le vrai incident est déclenché par kubectl
DANS la VM master, jamais depuis Windows (règle HELIOS respectée).

    python scripts/prod_test_in_cluster.py            # déclenche + observe la remédiation
    python scripts/prod_test_in_cluster.py --restore  # restaure podinfo (teardown)
"""
from __future__ import annotations
import os, sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import paramiko

MASTER = "192.168.148.153"
USER = "kube"
KEY = "C:/Users/houss/.ssh/id_ed25519"
LOCAL_SH = Path(__file__).resolve().parent / "prod_test_in_cluster.sh"
REMOTE_SH = "/home/kube/prod_test_in_cluster.sh"


def main():
    restore = "--restore" in sys.argv[1:]
    if not LOCAL_SH.exists():
        sys.exit(f"introuvable : {LOCAL_SH}")

    key = paramiko.Ed25519Key.from_private_key_file(KEY)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connexion {USER}@{MASTER} (master) ...")
    c.connect(MASTER, username=USER, pkey=key, timeout=15,
              allow_agent=False, look_for_keys=False)
    print("  Connecté au master.\n")

    # 1) copier le script de test sur le master (normalise les fins de ligne en LF)
    sftp = c.open_sftp()
    data = LOCAL_SH.read_text(encoding="utf-8").replace("\r\n", "\n")
    with sftp.open(REMOTE_SH, "w") as f:
        f.write(data)
    sftp.chmod(REMOTE_SH, 0o755)
    sftp.close()
    print(f"  Script copié -> {MASTER}:{REMOTE_SH}\n")

    # 2) l'exécuter SUR le master (l'incident est déclenché dans le cluster), sortie en direct
    arg = "--restore" if restore else ""
    chan = c.get_transport().open_session()
    chan.get_pty()
    chan.exec_command(f"bash {REMOTE_SH} {arg}")
    while True:
        if chan.recv_ready():
            sys.stdout.write(chan.recv(4096).decode(errors="replace"))
            sys.stdout.flush()
        if chan.exit_status_ready() and not chan.recv_ready():
            break
    code = chan.recv_exit_status()
    # vider le reste
    while chan.recv_ready():
        sys.stdout.write(chan.recv(4096).decode(errors="replace"))
    c.close()
    print(f"\n[exit={code}] test terminé sur le master.")
    sys.exit(code)


if __name__ == "__main__":
    main()
