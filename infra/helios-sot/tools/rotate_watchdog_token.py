# -*- coding: utf-8 -*-
"""Rotation du jeton backend <-> watchdog. NE JAMAIS AFFICHER LE JETON (seules des empreintes sont imprimees).

But (instruction : « ne pas reutiliser aveuglement le meme secret entre Backend, Bridge et Watchdog ») : le nouveau
jeton n'existe QUE dans le fichier hote lu par le watchdog (relu a chaque requete) et dans le secret `backend-secret`
du backend. Le Bridge (qui detient l'ancien jeton dans /etc/helios/bridge.env) ne le recoit pas.

DRY-RUN PAR DEFAUT : sans --apply, le script ne lit ni n'ecrit aucun secret.

Avec --apply (session de l'administrateur) :
  1. empreintes (12 premiers hex du SHA-256) du fichier hote et du secret actuels ;
  2. sauvegarde de l'ancien fichier hote (meme dossier, suffixe .bak-<horodatage>) ;
  3. nouveau jeton (40 hex) -> fichier hote -> patch du secret `backend-secret` ;
  4. relecture et comparaison des empreintes ; au moindre ecart, l'ancien jeton est RESTAURE (fichier ET secret) ;
  5. avec --restart-backend : `kubectl rollout restart deployment/backend` + attente (les pods lisent le jeton au demarrage).

Retour arriere manuel : recopier le fichier .bak-<horodatage> sur le fichier hote, remettre l'ancienne valeur dans le
secret, redemarrer le backend.

Usage :  python infra\\helios-sot\\tools\\rotate_watchdog_token.py                      (dry-run)
         python infra\\helios-sot\\tools\\rotate_watchdog_token.py --apply --restart-backend
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys

# Memes variables d'environnement que le watchdog (HELIOS_WATCHDOG_TOKEN_FILE) ; HELIOS_KUBECTL (tableau JSON) sert aux tests.
TOKEN_FILE = os.environ.get("HELIOS_WATCHDOG_TOKEN_FILE", r"C:\Users\houss\.helios-watchdog-token")
KUBECTL = json.loads(os.environ["HELIOS_KUBECTL"]) if os.environ.get("HELIOS_KUBECTL") else ["kubectl"]
SECRET = "backend-secret"
KEY = "REMEDIATION_MASTER_WATCHDOG_TOKEN"
NAMESPACE = "pfe"


def fp(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:12]


def kubectl(*args, timeout=60):
    r = subprocess.run([*KUBECTL, "-n", NAMESPACE, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("kubectl %s : %s" % (args[0], r.stderr.strip()[:200]))
    return r.stdout


def secret_value() -> str:
    raw = kubectl("get", "secret", SECRET, "-o", "jsonpath={.data.%s}" % KEY)
    return base64.b64decode(raw).decode("utf-8")


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="applique la rotation (sinon dry-run)")
    ap.add_argument("--restart-backend", action="store_true", help="redemarre le backend apres la rotation")
    args = ap.parse_args(argv)

    if not args.apply:
        print("DRY-RUN : aucun secret lu, aucun secret ecrit.")
        print("Avec --apply : sauvegarde de %s, nouveau jeton (40 hex) dans ce fichier ET dans %s/%s (cle %s)," % (
            TOKEN_FILE, NAMESPACE, SECRET, KEY))
        print("comparaison des empreintes, restauration automatique en cas d'ecart ; --restart-backend relance le backend.")
        return 0

    with open(TOKEN_FILE, encoding="utf-8") as f:
        old_file = f.read()
    old_secret = secret_value()
    print("avant  : fichier hote fp=%s (%d car.) ; secret backend fp=%s (%d car.) ; identiques=%s" % (
        fp(old_file), len(old_file.strip()), fp(old_secret), len(old_secret.strip()),
        old_file.strip() == old_secret.strip()))

    backup = TOKEN_FILE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(TOKEN_FILE, backup)
    print("sauvegarde de l'ancien fichier hote : " + backup)

    new = secrets.token_hex(20)
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        kubectl("patch", "secret", SECRET, "--type", "merge", "-p", json.dumps({"stringData": {KEY: new}}))
        after_secret = secret_value()
        with open(TOKEN_FILE, encoding="utf-8") as f:
            after_file = f.read()
        ok = after_file.strip() == after_secret.strip() == new
        print("apres  : fichier hote fp=%s ; secret backend fp=%s ; identiques=%s ; different de l'ancien=%s" % (
            fp(after_file), fp(after_secret), ok, fp(after_file) != fp(old_file)))
        if not ok:
            raise RuntimeError("empreintes incoherentes apres rotation")
    except Exception as ex:
        with open(TOKEN_FILE, "w", encoding="utf-8", newline="") as f:
            f.write(old_file)
        try:
            kubectl("patch", "secret", SECRET, "--type", "merge", "-p", json.dumps({"stringData": {KEY: old_secret}}))
        except Exception as ex2:
            print("ATTENTION : restauration du secret impossible (%s)" % ex2)
        print("ECHEC de la rotation (%s) : ancien jeton restaure" % ex)
        return 1

    print("rotation terminee : le Bridge ne detient PAS le nouveau jeton.")
    if args.restart_backend:
        kubectl("rollout", "restart", "deployment/backend")
        print(kubectl("rollout", "status", "deployment/backend", "--timeout=240s", timeout=300).strip())
    else:
        print("A FAIRE : kubectl -n %s rollout restart deployment/backend  (les pods lisent le jeton au demarrage)" % NAMESPACE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
