# -*- coding: utf-8 -*-
"""HELIOS — LOT WATCHDOG-SECURE-CREDENTIAL-PROVIDER-01.

Fournisseur de secrets ISOLÉ pour le watchdog, via le Gestionnaire d'informations d'identification Windows
(Credential Manager — API native `CredReadW` de `advapi32` par `ctypes` : AUCUNE dépendance externe).

Garanties :
  * noms de credentials FIXES et documentés (CRED_TARGETS) ;
  * lecture sous l'identité Windows COURANTE (celle qui exécute la tâche) ;
  * AUCUN secret codé en dur ;
  * AUCUNE valeur secrète dans les journaux, les exceptions ou les retours d'erreur ;
  * credential absent / illisible / invalide / vide -> None (FAIL-CLOSED, aucune valeur de remplacement) ;
  * `reader` INJECTABLE pour les tests (les tests unitaires n'accèdent JAMAIS au vrai Credential Manager).

Importable SANS effet de bord : tout l'accès natif (ctypes/advapi32) est chargé PARESSEUSEMENT dans
`_win_cred_read`, jamais à l'import.
"""

# Noms LOGIQUES -> cibles FIXES dans le Credential Manager (type GENERIC, portée utilisateur).
CRED_TARGETS = {
    "superadmin_pass": "HELIOS/watchdog/superadmin",
    "ssh_pass": "HELIOS/watchdog/ssh",
}


def _win_cred_read(target):
    """Lecture RÉELLE via `CredReadW` (advapi32), sous l'identité Windows courante.
    Retourne la valeur (str) ou None si absente. Ne journalise jamais la valeur ; en cas d'erreur, lève
    une exception au message GÉNÉRIQUE (sans secret)."""
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    CredReadW = advapi32.CredReadW
    CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    CredReadW.restype = wintypes.BOOL
    CredFree = advapi32.CredFree
    CredFree.argtypes = [ctypes.c_void_p]

    CRED_TYPE_GENERIC = 1
    ERROR_NOT_FOUND = 1168
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise OSError("CredReadW a echoue")        # message générique, jamais de secret
    try:
        cred = ptr.contents
        n = int(cred.CredentialBlobSize)
        if n <= 0:
            return None
        raw = ctypes.string_at(cred.CredentialBlob, n)
    finally:
        CredFree(ptr)
    # le blob est généralement en UTF-16LE (stockage cmdkey/keyring) ; repli UTF-8
    try:
        return raw.decode("utf-16-le").rstrip("\x00") or None
    except Exception:
        return raw.decode("utf-8", "replace") or None


def get_secret(logical_name, reader=None, logger=None):
    """Retourne le secret du nom LOGIQUE fixe, ou None si indisponible (FAIL-CLOSED).

    `reader(target) -> str | None` est injectable (tests) ; par défaut, lecture réelle via Credential Manager.
    Ne retourne JAMAIS de valeur de remplacement. Ne journalise JAMAIS la valeur (seulement la cible et le
    cas : nom inconnu / illisible / absent)."""
    target = CRED_TARGETS.get(logical_name)
    if not target:
        if logger is not None:
            logger.warning(f"credential: nom logique inconnu '{logical_name}'")
        return None
    rd = reader if reader is not None else _win_cred_read
    try:
        value = rd(target)
    except Exception:
        if logger is not None:
            logger.warning(f"credential '{target}' illisible")      # jamais la valeur ni le détail de l'erreur
        return None
    if not value:
        if logger is not None:
            logger.warning(f"credential '{target}' absent")          # jamais la valeur
        return None
    return value
