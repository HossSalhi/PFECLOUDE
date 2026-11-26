# -*- coding: utf-8 -*-
"""HELIOS — LOT WATCHDOG-CREDENTIAL-PROVISIONING-01.

Outil INDÉPENDANT de provisionnement des deux secrets du watchdog dans le Gestionnaire d'informations
d'identification Windows (Credential Manager), via l'API native `CredWriteW` (`advapi32`) par `ctypes` —
**AUCUNE dépendance externe**.

Règles de sécurité (voir le rapport 02_PROVISIONING_TOOL.md) :
  * exécution INTERACTIVE sous le compte Windows attendu (`houss`) ; échec explicite sinon ;
  * saisie MASQUÉE (`getpass`) dans un terminal local ; JAMAIS de secret en argument de ligne de commande,
    en fichier source, en fichier temporaire clair, en variable shell, ni en journal ;
  * JAMAIS d'impression/journalisation/exception contenant la valeur ;
  * cibles FIXES importées de `helios_credstore.CRED_TARGETS` (source unique de vérité) ;
  * encodage UTF-16LE (cohérent avec la lecture de `helios_credstore`) + contrôle de taille Credential Manager ;
  * ANTI-ÉCRASEMENT par défaut : une cible existante n'est PAS écrasée (signalée sans lire sa valeur) ;
  * remplacement = option explicite `--replace` + confirmation distincte (non déclenchable par accident) ;
  * DOUBLE saisie concordante obligatoire avant écriture ; désaccord => abandon sans écriture ;
  * aucun défaut silencieux.

Ce module est importable SANS effet de bord (accès natif paresseux). `main()` n'est lancé que via CLI et
n'est PAS exécuté par les tests ni dans le lot de préparation.
"""
import sys

from helios_credstore import CRED_TARGETS   # noms de cibles FIXES (source unique)

EXPECTED_USER = "houss"
# CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512 octets (limite Credential Manager).
CRED_MAX_BLOB = 5 * 512


def _warn(logger, msg):
    if logger is not None:
        logger.warning(msg)


def _info(logger, msg):
    if logger is not None:
        logger.info(msg)


def provision(logical_name, get_input, writer, prober, current_user,
              expected_user=EXPECTED_USER, allow_replace=False, logger=None):
    """Cœur TESTABLE (tout injecté). Retourne un CODE de statut (jamais le secret) :
    WRONG_ACCOUNT / UNKNOWN_NAME / PROBE_ERROR / EXISTS / MISMATCH / EMPTY / TOO_LARGE / WRITE_ERROR / WRITTEN.
    `get_input(prompt)->str` est appelé DEUX fois (double saisie). `writer(target, blob)` écrit. `prober(target)
    ->bool` dit si la cible existe. La valeur n'est JAMAIS passée au logger."""
    if (current_user or "").strip().lower() != expected_user.lower():
        _warn(logger, f"compte Windows inattendu (attendu '{expected_user}') -> abandon, aucune ecriture")
        return "WRONG_ACCOUNT"
    target = CRED_TARGETS.get(logical_name)
    if not target:
        _warn(logger, f"nom logique inconnu '{logical_name}' -> abandon")
        return "UNKNOWN_NAME"
    try:
        exists = bool(prober(target))
    except Exception:
        _warn(logger, f"cible '{target}': verification d'existence impossible -> abandon, aucune ecriture")
        return "PROBE_ERROR"
    if exists and not allow_replace:
        _warn(logger, f"cible '{target}' existe deja -> NON ecrasee (remplacement explicite requis)")
        return "EXISTS"
    s1 = get_input(f"Secret pour '{target}' (saisie 1)")
    s2 = get_input(f"Secret pour '{target}' (saisie 2, confirmation)")
    if s1 != s2:
        _warn(logger, f"cible '{target}': saisies discordantes -> abandon, aucune ecriture")
        return "MISMATCH"
    if not s1:
        _warn(logger, f"cible '{target}': saisie vide -> abandon, aucune ecriture")
        return "EMPTY"
    blob = s1.encode("utf-16-le")
    if len(blob) > CRED_MAX_BLOB:
        _warn(logger, f"cible '{target}': secret trop long ({len(blob)} > {CRED_MAX_BLOB} octets) -> abandon")
        return "TOO_LARGE"
    try:
        writer(target, blob)
    except Exception:
        _warn(logger, f"cible '{target}': ecriture Credential Manager impossible -> echec")  # jamais le secret
        return "WRITE_ERROR"
    _info(logger, f"cible '{target}': credential enregistre (OK)")   # jamais la valeur
    return "WRITTEN"


def verify_target(target, prober):
    """Vérification SANS valeur : `prober(target)` -> 'PRESENT' | 'ABSENT' | 'READ_ERROR'. Pass-through
    testable ; le vrai prober ne décode ni ne renvoie jamais la valeur."""
    return prober(target)


def selected_names(argv, targets=None):
    """LOT 09.4 — noms logiques à traiter selon `argv`. `--only <nom>` restreint à UN SEUL credential
    (rotation CIBLÉE) : permet de remplacer uniquement `ssh_pass` (HELIOS/watchdog/ssh) SANS toucher
    `superadmin_pass`. Nom inconnu -> [] (refus explicite). Sans `--only` -> tous les noms."""
    targets = CRED_TARGETS if targets is None else targets
    if "--only" in argv:
        i = argv.index("--only")
        name = argv[i + 1] if i + 1 < len(argv) else ""
        return [name] if name in targets else []
    return list(targets)


# ---------------------------------------------------------------------------------------------------
# Accès natif Windows (CredWriteW / CredReadW). Chargé PARESSEUSEMENT ; NON exécuté dans ce lot/les tests.
# ---------------------------------------------------------------------------------------------------
def _ctypes_env():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    return ctypes, wintypes, advapi32, CREDENTIAL


def win_writer(target, blob):
    """Écrit (CredWriteW) un credential GENERIC persistant (LOCAL_MACHINE). `blob` = secret en UTF-16LE.
    N'imprime/journalise jamais la valeur. Lève en cas d'échec (message générique)."""
    ctypes, wintypes, advapi32, CREDENTIAL = _ctypes_env()
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    cred = CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    buf = ctypes.create_string_buffer(bytes(blob), len(blob))
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = EXPECTED_USER
    CredWriteW = advapi32.CredWriteW
    CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    CredWriteW.restype = wintypes.BOOL
    if not CredWriteW(ctypes.byref(cred), 0):
        raise OSError("CredWriteW a echoue")   # jamais de secret


def win_prober_bool(target):
    """True si la cible existe (CredReadW OK), False si absente. Ne décode pas la valeur."""
    ctypes, wintypes, advapi32, CREDENTIAL = _ctypes_env()
    CRED_TYPE_GENERIC = 1
    ERROR_NOT_FOUND = 1168
    ptr = ctypes.POINTER(CREDENTIAL)()
    CredReadW = advapi32.CredReadW
    CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    CredReadW.restype = wintypes.BOOL
    if CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        advapi32.CredFree(ptr)
        return True
    if ctypes.get_last_error() == ERROR_NOT_FOUND:
        return False
    raise OSError("CredReadW a echoue")


def win_verify_prober(target):
    """'PRESENT' (existe + blob non vide) / 'ABSENT' / 'READ_ERROR'. Ne décode ni ne renvoie la valeur."""
    ctypes, wintypes, advapi32, CREDENTIAL = _ctypes_env()
    CRED_TYPE_GENERIC = 1
    ERROR_NOT_FOUND = 1168
    ptr = ctypes.POINTER(CREDENTIAL)()
    CredReadW = advapi32.CredReadW
    CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    CredReadW.restype = wintypes.BOOL
    if CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        try:
            size = int(ptr.contents.CredentialBlobSize)
        finally:
            advapi32.CredFree(ptr)
        return "PRESENT" if size > 0 else "ABSENT"
    return "ABSENT" if ctypes.get_last_error() == ERROR_NOT_FOUND else "READ_ERROR"


def _current_user():
    import getpass
    return getpass.getuser()


def main(argv=None):
    """CLI interactive. NON exécuté par les tests ni dans le lot de préparation.
    Usage : python provision_helios_credentials.py [--verify] [--replace] [--only <nom_logique>]"""
    import getpass
    argv = list(sys.argv[1:] if argv is None else argv)
    verify = "--verify" in argv
    allow_replace = "--replace" in argv
    cur = _current_user()
    names = selected_names(argv)
    if "--only" in argv and not names:
        print("[ABANDON] --only : nom logique inconnu (attendu : " + ", ".join(CRED_TARGETS) + ").")
        return 2

    class _Log:
        def warning(self, m): print("[WARN]", m)
        def info(self, m): print("[OK]  ", m)
    log = _Log()

    if verify:
        print(f"Verification (compte '{cur}') — aucune valeur affichee :")
        for name in names:
            target = CRED_TARGETS[name]
            try:
                status = verify_target(target, win_verify_prober)
            except Exception:
                status = "READ_ERROR"
            print(f"  {target} : {status}")
        return 0

    if cur.strip().lower() != EXPECTED_USER.lower():
        print(f"[ABANDON] compte Windows '{cur}' != '{EXPECTED_USER}'. Lancez sous '{EXPECTED_USER}'.")
        return 2
    if allow_replace:
        conf = input(f"REMPLACEMENT explicite demande. Taper exactement REMPLACER pour continuer : ")
        if conf.strip() != "REMPLACER":
            print("[ABANDON] confirmation de remplacement non fournie.")
            return 2
    for name in names:
        code = provision(name, getpass.getpass, win_writer, win_prober_bool, cur,
                         allow_replace=allow_replace, logger=log)
        print(f"  {name} -> {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
