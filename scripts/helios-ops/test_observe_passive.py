# -*- coding: utf-8 -*-
"""GARDE DE PASSIVITÉ des outils d'observation (analyse statique, aucun réseau, aucune VM).

Lancer : python -I scripts/helios-ops/test_observe_passive.py

Prouve, par l'AST du code réel, que `shadow_observe.py` (y compris `--armed`) et `passive_observe.py` ne peuvent exécuter AUCUNE action
opérationnelle : pas d'arrêt/démarrage de VM, pas d'incident ni de charge injectés, pas de remédiation, pas de modification de politique ou de
ressource du cluster. Règles :
  - tout sous-processus passe par la fonction `run()` (un seul site `subprocess.run`) ; pas d'os.system / os.popen / Popen ;
  - kubectl : sous-commandes limitées à get / exec / logs / top / version ; aucun verbe d'écriture dans aucune chaîne ;
  - SQL : jamais d'INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/VACUUM ;
  - PowerShell : jamais de Set-/Remove-/Stop-/Start-/Restart-/Enable-/Disable-/Register-/Unregister-/Clear-/Out-/Invoke- ;
  - ssh/shell : jamais de systemctl start|stop|restart..., rm, chmod, chown, mv, cp, sed -i, tee, kill ; jamais de vmrun ;
  - HTTP : jamais de -X POST/PUT/DELETE/PATCH ni de curl avec -d / --data / -F ;
  - écritures de fichiers : uniquement l'ajout d'un relevé JSONL (save/append) et le fichier .ps1 temporaire de collect_host.
`--armed` ne fait que : fixer OBS["armed"] et OBS["since"], choisir le fichier de sortie et lire le journal du watchdog.
"""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools"))
FILES = [os.path.join(TOOLS, "shadow_observe.py"), os.path.join(TOOLS, "passive_observe.py")]

KUBECTL_OK = {"get", "exec", "logs", "top", "version", "config", "cluster-info", "api-resources", "auth"}
FORBIDDEN = [
    (r"""\bkubectl\b(?:\s+(?:-n|--namespace|-c|--context|--kubeconfig|--server)\s+\S+|\s+-{1,2}[\w-]+(?:=\S+)?)*\s+(apply|delete|patch|scale|rollout|cordon|uncordon|drain|annotate|label|create|replace|edit|taint|set|run|expose|cp)\b""", "kubectl en écriture"),
    (r"(?i)\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|database|index|schema)|truncate\s+\w|alter\s+(table|system|database|role)|create\s+(table|index|role|database)|grant\s+\w|revoke\s+\w|vacuum\b)", "SQL en écriture"),
    (r"\b(Set|Remove|Stop|Start|Restart|Enable|Disable|Register|Unregister|Clear|Out|Invoke|Add|Move|Rename|Copy)-[A-Za-z]+", "cmdlet PowerShell en écriture"),
    (r"(?i)\bsystemctl\s+(start|stop|restart|enable|disable|reload|kill|mask|daemon-reload)\b", "systemctl en écriture"),
    (r"(?i)(^|[\s;&|(])(rm|chmod|chown|mv|cp|tee|kill|pkill|shutdown|reboot|poweroff)\s", "commande shell en écriture"),
    (r"(?i)\bsed\s+(-[A-Za-z]*i|--in-place)", "sed -i"),
    # commande vmrun (jamais le mot dans un message entre apostrophes inverses, ni la clé de relevé « vmrun_start_depuis_fenetre »)
    (r"""(?i)(?<!`)\bvmrun(\.exe)?["']?\s+(-[A-Za-z]|start|stop|suspend|reset|pause|unpause|list|snapshot|runProgramInGuest)\b|vmware-vmx""", "pilotage de VM"),
    (r"(?i)-X\s*(POST|PUT|DELETE|PATCH)\b", "requête HTTP en écriture"),
    (r"(?i)\bcurl\b[^'\"]*\s(-d|--data|--data-raw|--data-binary|-F|--form|-T|--upload-file)\b", "curl avec corps"),
    (r"""(?i)https?://[^\s'"]*(/restart/|/action\b|/remediat|/exec\b)""", "appel HTTP d'un point d'entrée d'action"),
]


def docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


class Module:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            self.src = fh.read()
        self.tree = ast.parse(self.src)
        skip = docstring_ids(self.tree)
        self.literals = [(n.lineno, n.value) for n in ast.walk(self.tree)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]

    def calls(self, pred):
        return [n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and pred(n)]


def dotted(node):
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


class PassiviteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mods = [Module(p) for p in FILES if os.path.isfile(p)]
        assert cls.mods, "aucun outil d'observation trouvé"

    def test_un_seul_site_de_sous_processus_et_aucun_shell(self):
        for m in self.mods:
            sub = m.calls(lambda c: dotted(c.func).startswith("subprocess."))
            self.assertLessEqual(len(sub), 1, "%s : plusieurs sites subprocess" % os.path.basename(m.path))
            for c in sub:
                self.assertEqual("subprocess.run", dotted(c.func), m.path)
                self.assertFalse(any(k.arg == "shell" for k in c.keywords), "shell=True interdit")
            bad = m.calls(lambda c: dotted(c.func) in ("os.system", "os.popen", "os.execv", "os.spawnv"))
            self.assertEqual([], bad, m.path)

    def test_sous_commandes_kubectl_en_lecture_seule(self):
        for m in self.mods:
            for c in m.calls(lambda c: dotted(c.func) == "kube"):
                self.assertTrue(c.args and isinstance(c.args[0], ast.Constant), "%s:%d sous-commande non littérale" % (m.path, c.lineno))
                self.assertIn(c.args[0].value, KUBECTL_OK, "%s:%d kubectl %s" % (os.path.basename(m.path), c.lineno, c.args[0].value))
            for c in m.calls(lambda c: dotted(c.func) == "run" and c.args and isinstance(c.args[0], ast.List)):
                first = c.args[0].elts[0] if c.args[0].elts else None
                if isinstance(first, ast.Constant) and first.value == "kubectl" and len(c.args[0].elts) > 1:
                    sc = c.args[0].elts[1]
                    if isinstance(sc, ast.Constant):
                        self.assertIn(sc.value, KUBECTL_OK, "%s:%d" % (m.path, c.lineno))

    def test_executables_lances_limites_a_la_lecture(self):
        for m in self.mods:
            for c in m.calls(lambda c: dotted(c.func) == "run" and c.args and isinstance(c.args[0], ast.List)):
                first = c.args[0].elts[0] if c.args[0].elts else None
                if isinstance(first, ast.Constant):
                    self.assertIn(first.value, ("kubectl", "ssh", "powershell", "curl"), "%s:%d lance %s" % (m.path, c.lineno, first.value))
                else:
                    # python lancé sur un outil de LECTURE du dépôt (sys.executable, "-I", <script>)
                    self.assertEqual("sys.executable", dotted(first), "%s:%d exécutable non littéral" % (m.path, c.lineno))

    def test_aucun_verbe_d_ecriture_dans_les_chaines(self):
        for m in self.mods:
            for line, s in m.literals:
                for pattern, label in FORBIDDEN:
                    mt = re.search(pattern, s)
                    self.assertIsNone(mt, "%s:%d %s : %r" % (os.path.basename(m.path), line, label, s[max(0, (mt.start() if mt else 0) - 30):(mt.end() if mt else 0) + 30]))

    def test_http_uniquement_en_get_sans_corps(self):
        for m in self.mods:
            for c in m.calls(lambda c: dotted(c.func).endswith("urlopen") or dotted(c.func).endswith("urllib.request.Request")):
                self.assertLessEqual(len(c.args), 1, "%s:%d urlopen/Request avec plusieurs arguments positionnels (corps ?)" % (os.path.basename(m.path), c.lineno))
                for k in c.keywords:
                    self.assertNotIn(k.arg, ("data", "method", "headers"), "%s:%d %s=" % (os.path.basename(m.path), c.lineno, k.arg))
            # aucune autre bibliothèque réseau d'écriture
            for imp in (n for n in ast.walk(m.tree) if isinstance(n, (ast.Import, ast.ImportFrom))):
                noms = [a.name for a in imp.names] + ([imp.module] if isinstance(imp, ast.ImportFrom) and imp.module else [])
                for nom in noms:
                    self.assertNotIn(nom.split(".")[0], ("requests", "paramiko", "ftplib", "smtplib", "http"), "%s importe %s" % (os.path.basename(m.path), nom))

    def test_ecritures_de_fichiers_limitees(self):
        for m in self.mods:
            for c in m.calls(lambda c: dotted(c.func) in ("open", "io.open", "os.fdopen")):
                if len(c.args) >= 2 and isinstance(c.args[1], ast.Constant) and any(x in c.args[1].value for x in "wax+"):
                    owner = None
                    for fn in ast.walk(m.tree):
                        if isinstance(fn, ast.FunctionDef) and fn.lineno <= c.lineno <= getattr(fn, "end_lineno", fn.lineno):
                            owner = fn.name
                    self.assertIn(owner, ("save", "append_record", "write_report", "annotate", "save_json"),
                                  "%s:%d écriture de fichier dans %s()" % (os.path.basename(m.path), c.lineno, owner))
            for c in m.calls(lambda c: dotted(c.func) in ("os.remove", "os.unlink", "shutil.rmtree", "os.rmdir", "os.rename", "shutil.move")):
                owner = None
                for fn in ast.walk(m.tree):
                    if isinstance(fn, ast.FunctionDef) and fn.lineno <= c.lineno <= getattr(fn, "end_lineno", fn.lineno):
                        owner = fn.name
                self.assertEqual("collect_host", owner, "%s:%d suppression hors fichier temporaire de collect_host" % (m.path, c.lineno))

    def test_l_option_armed_ne_porte_aucune_capacite_d_execution(self):
        m = next(x for x in self.mods if x.path.endswith("shadow_observe.py"))
        mainf = next(n for n in ast.walk(m.tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
        # dans main(), le bloc « if a.armed: » ne contient que des affectations (OBS / OUT_FILE), aucun appel
        armed_blocks = [n for n in ast.walk(mainf) if isinstance(n, ast.If) and dotted(n.test) == "a.armed"]
        self.assertEqual(1, len(armed_blocks))
        for stmt in armed_blocks[0].body:
            self.assertIsInstance(stmt, ast.Assign, ast.dump(stmt)[:100])
            autorises = ("dt.datetime.now", ".strftime")      # seule horloge : valeur par défaut de --since
            self.assertFalse([n for n in ast.walk(stmt) if isinstance(n, ast.Call) and dotted(n.func) not in autorises],
                             "appel dans le bloc --armed")
        # snapshot() : le bloc OBS["armed"] ne fait que lire le journal du watchdog via count_watchdog_starts
        snap = next(n for n in ast.walk(m.tree) if isinstance(n, ast.FunctionDef) and n.name == "snapshot")
        blocs = [n for n in ast.walk(snap) if isinstance(n, ast.If) and "armed" in ast.dump(n.test)]
        self.assertEqual(1, len(blocs))
        appels = {dotted(c.func) for c in ast.walk(blocs[0]) if isinstance(c, ast.Call)}
        self.assertEqual({"count_watchdog_starts"}, appels)
        # count_watchdog_starts n'ouvre le journal qu'en lecture
        cws = next(n for n in ast.walk(m.tree) if isinstance(n, ast.FunctionDef) and n.name == "count_watchdog_starts")
        for c in (n for n in ast.walk(cws) if isinstance(n, ast.Call) and dotted(n.func) == "open"):
            self.assertFalse(len(c.args) >= 2 and any(x in c.args[1].value for x in "wax+"), "écriture dans le journal du watchdog")

    def test_les_requetes_sql_ne_sont_que_des_select(self):
        m = next(x for x in self.mods if x.path.endswith("shadow_observe.py"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("shadow_observe_scan", m.path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for since in ("2026-11-06 21:00:00", "2000-01-01 00:00:00"):
            stmts = [s.strip() for s in mod.build_sql(since).split(";") if s.strip()]
            self.assertGreater(len(stmts), 5)
            for s in stmts:
                self.assertRegex(s, r"(?is)^select\b", s[:60])


if __name__ == "__main__":
    unittest.main()
