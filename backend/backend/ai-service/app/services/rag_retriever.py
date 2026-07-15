"""
HELIOS RAG — récupération sémantique sur le catalogue des 120 incidents.

Rôle : ANCRER le LLM de décision (helios-120-3b) dans le vrai savoir HELIOS.
À chaque incident, on retrouve les k cartes catalogue les plus proches
sémantiquement (type + catégorie + résolution PRO + action câblée) et on les
injecte dans le prompt du LLM → moins d'hallucination, décisions fidèles au
catalogue, surtout pour les ~70 types non auto-exécutables (à expliquer).

Design volontairement léger (contrainte .161 : CPU, ~6 Go, air-gap) :
  - base de connaissance = les 120 cartes du catalogue (aucun fichier externe) ;
  - embeddings = all-MiniLM-L6-v2 réutilisé via LogEmbeddingEncoder
    (repli haché si sentence-transformers indisponible) ;
  - index construit UNE fois au démarrage (120 vecteurs 384-d = négligeable) ;
  - similarité cosinus (les vecteurs sont déjà normalisés).

Aucune dépendance nouvelle, aucun serveur ChromaDB.
"""
from __future__ import annotations

import logging
import re

import numpy as np

from app.features.log_embeddings import LogEmbeddingEncoder
from app.models.incident_catalog import INCIDENT_CATALOG, INCIDENT_ACTIONS

logger = logging.getLogger(__name__)

# Mots trop génériques dans les noms de type : présents partout, peu discriminants.
_STOP = {"pod", "node", "high", "low", "failure", "error", "down", "lost",
         "slow", "and", "the", "for"}
# Poids du signal lexical dans le score hybride (le cosinus est ~0.1-0.4).
_LEX_WEIGHT = 0.6
# Poids du signal de re-ranking (recouvrement avec le TEXTE COMPLET de la carte).
_RERANK_WEIGHT = 0.3


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower())


class RagRetriever:
    """Index sémantique des 120 cartes catalogue + récupération top-k."""

    def __init__(self, encoder: LogEmbeddingEncoder | None = None) -> None:
        # On réutilise l'encodeur MiniLM déjà présent dans le service.
        self._encoder = encoder or LogEmbeddingEncoder()
        self._types: list[str] = []
        self._cards: list[dict] = []          # {type, category, resolution, action, auto, text}
        self._lex: list[list[str]] = []       # mots-clés discriminants par carte
        self._index: dict[str, int] = {}      # type -> position (lookup exact)
        self._matrix: np.ndarray | None = None  # (N, dim) normalisé
        self._built = False

    # ── construction de l'index ───────────────────────────────────────────────
    @staticmethod
    def _card_text(itype: str, entry: dict, action: str) -> str:
        """Texte embeddé = tout ce qui aide à retrouver la bonne carte."""
        cat = entry.get("category", "")
        res = entry.get("resolution", "")
        auto = "auto-remédiable" if entry.get("auto") else "escalade opérateur"
        # On étale le TYPE en mots pour rapprocher la sémantique des symptômes.
        spelled = itype.replace("_", " ").lower()
        return f"{itype} ({spelled}) [{cat}] — {res} Action: {action}. ({auto})"

    def build(self) -> "RagRetriever":
        """Construit l'index à partir du catalogue (idempotent)."""
        if self._built:
            return self
        types, cards, texts, lex = [], [], [], []
        for itype, entry in INCIDENT_CATALOG.items():
            action = INCIDENT_ACTIONS.get(itype, "")
            text = self._card_text(itype, entry, action)
            types.append(itype)
            cards.append({
                "type": itype,
                "category": entry.get("category", ""),
                "resolution": entry.get("resolution", ""),
                "action": action,
                "auto": bool(entry.get("auto", False)),
                "text": text,
            })
            texts.append(text)
            # mots-clés discriminants du nom de type (pour le signal lexical)
            toks = [w for w in itype.lower().split("_") if len(w) >= 3 and w not in _STOP]
            lex.append(toks)
        self._lex = lex
        self._index = {t: i for i, t in enumerate(types)}

        # encode() fait une moyenne sur la liste ; on encode carte par carte pour
        # avoir un vecteur par carte (normalisé par l'encodeur).
        vecs = [self._encoder.encode([t]) for t in texts]
        self._matrix = np.vstack(vecs).astype(np.float32)
        self._types = types
        self._cards = cards
        self._built = True
        backend = "MiniLM" if self._encoder.loaded else "hashed-fallback"
        logger.info("RAG index construit : %d cartes, dim=%d, encodeur=%s",
                    len(types), self._matrix.shape[1], backend)
        return self

    @property
    def ready(self) -> bool:
        return self._built and self._matrix is not None

    # ── récupération ──────────────────────────────────────────────────────────
    def card_for(self, itype: str) -> dict | None:
        """Carte catalogue exacte pour un type (lookup direct, jamais faux)."""
        if not self.ready:
            self.build()
        i = self._index.get(str(itype or "").strip().upper())
        return dict(self._cards[i]) if i is not None else None

    def _lex_score(self, qnorm: str, toks: list[str]) -> float:
        """Fraction des mots-clés du type présents (en sous-chaîne) dans la requête.
        Robuste aux tokens collés du terrain K8s (OOMKilled ⊃ oom+killed)."""
        if not toks:
            return 0.0
        hit = sum(1 for w in toks if w in qnorm)
        return hit / len(toks)

    def retrieve(self, query: str, k: int = 3, rerank: bool = False) -> list[dict]:
        """Retourne les k cartes les plus proches (score HYBRIDE = cosinus + lexical).

        rerank=True : RE-CLASSEMENT en 2 temps (rappel puis précision). On part d'un POOL
        élargi trié par le score hybride, puis on re-note finement chaque candidat par le
        recouvrement des mots de la requête avec le TEXTE COMPLET de la carte (résolution +
        catégorie) — un signal que le lexical (qui ne voit que le nom du type) rate. Cela
        remonte la carte dont la RÉSOLUTION colle aux symptômes, pas juste le nom."""
        if not self.ready:
            self.build()
        if not query or self._matrix is None:
            return []
        q = self._encoder.encode([query]).astype(np.float32)
        qn = float(np.linalg.norm(q))
        if qn > 0:
            q = q / qn
        cos = self._matrix @ q  # cosinus (matrice déjà normalisée)
        qnorm = _norm(query)
        lex = np.array([self._lex_score(qnorm, t) for t in self._lex], dtype=np.float32)
        scores = cos + _LEX_WEIGHT * lex  # hybride
        k = max(1, min(k, len(self._cards)))

        if rerank and len(self._cards) > k:
            pool_n = min(len(self._cards), max(3 * k, k + 6))
            pool = np.argsort(-scores)[:pool_n]
            qtoks = {t for t in qnorm.split() if len(t) >= 3}
            scored = []
            for i in pool:
                i = int(i)
                ctoks = {t for t in _norm(self._cards[i]["text"]).split() if len(t) >= 3}
                ov = (len(qtoks & ctoks) / len(qtoks)) if qtoks else 0.0
                scored.append((i, float(scores[i]) + _RERANK_WEIGHT * ov, ov))
            scored.sort(key=lambda z: -z[1])
            out = []
            for i, s, ov in scored[:k]:
                card = dict(self._cards[i])
                card["score"] = round(s, 4)
                card["cos"] = round(float(cos[i]), 4)
                card["lex"] = round(float(lex[i]), 4)
                card["rerank"] = round(ov, 4)
                out.append(card)
            return out

        top = np.argsort(-scores)[:k]
        out = []
        for i in top:
            i = int(i)
            card = dict(self._cards[i])
            card["score"] = round(float(scores[i]), 4)
            card["cos"] = round(float(cos[i]), 4)
            card["lex"] = round(float(lex[i]), 4)
            out.append(card)
        return out

    def context_block(self, query: str, k: int = 3) -> str:
        """Bloc texte prêt à injecter dans le prompt système du LLM."""
        cards = self.retrieve(query, k=k)
        if not cards:
            return ""
        lines = ["HELIOS knowledge base (most similar known incidents):"]
        for c in cards:
            mode = "AUTO" if c["auto"] else "ESCALATE"
            lines.append(
                f"- {c['type']} [{c['category']}] -> action={c['action']} "
                f"({mode}); fix: {c['resolution']}"
            )
        return "\n".join(lines)
