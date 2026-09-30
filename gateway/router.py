"""Politique de routage multi-LLM.

Pour chaque requête, la passerelle :
1. détecte la sensibilité des données (PII, secrets, mots-clés métier) ;
2. écarte les modèles non autorisés pour ce niveau de sensibilité ;
3. écarte les modèles trop faibles pour la complexité demandée ;
4. choisit le moins cher, puis le plus rapide, parmi les candidats restants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"

# Motifs de détection : volontairement simples et lisibles, à enrichir
# (ou à remplacer par Azure AI Language PII) en production.
PATTERNS = {
    "restricted": [
        re.compile(r"\bFR\d{2}(?:\s?\d{4}){5}\s?\d{3}\b"),          # IBAN FR
        re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),  # n° sécu
        re.compile(r"(?i)\b(api[_-]?key|secret|password|mot de passe)\b\s*[:=]"),
    ],
    "confidential": [
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                    # e-mail
        re.compile(r"(?:\+33|0)[1-9](?:[\s.-]?\d{2}){4}"),         # téléphone FR
        re.compile(r"(?i)\b(salaire|contrat|confidentiel|rh)\b"),
    ],
    "internal": [
        re.compile(r"(?i)\b(interne|projet|client|budget)\b"),
    ],
}


@dataclass(frozen=True)
class Model:
    name: str
    deployment: str
    provider: str
    cost_in: float
    cost_out: float
    p50_latency_ms: int
    quality: int
    max_sensitivity: str


@dataclass(frozen=True)
class Decision:
    model: Model
    sensitivity: str
    reasons: tuple[str, ...]

    def estimated_cost(self, tokens_in: int, tokens_out: int) -> float:
        m = self.model
        return (tokens_in * m.cost_in + tokens_out * m.cost_out) / 1_000_000


class Router:
    def __init__(self, config_path: Path = CONFIG_PATH):
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.levels: list[str] = cfg["sensitivity_levels"]
        self.models = [Model(**m) for m in cfg["models"]]

    def rank(self, level: str) -> int:
        return self.levels.index(level)

    def classify(self, text: str) -> str:
        """Renvoie le niveau de sensibilité le plus élevé détecté."""
        for level in ("restricted", "confidential", "internal"):
            if any(p.search(text) for p in PATTERNS[level]):
                return level
        return "public"

    def route(self, prompt: str, min_quality: int = 1, sensitivity: str | None = None) -> Decision:
        level = sensitivity or self.classify(prompt)
        reasons = [f"sensibilité détectée : {level}"]

        allowed = [m for m in self.models if self.rank(m.max_sensitivity) >= self.rank(level)]
        if not allowed:
            raise ValueError(f"Aucun modèle autorisé pour le niveau « {level} »")

        capable = [m for m in allowed if m.quality >= min_quality]
        if not capable:
            # Mieux vaut un modèle moins puissant qu'une fuite de données.
            capable = allowed
            reasons.append(f"aucun modèle de qualité ≥ {min_quality} autorisé : repli sur le meilleur disponible")
            capable = [max(capable, key=lambda m: m.quality)]

        best = min(capable, key=lambda m: (m.cost_in + m.cost_out, m.p50_latency_ms))
        reasons.append(f"{len(capable)} candidat(s), choix du moins cher puis du plus rapide")
        return Decision(model=best, sensitivity=level, reasons=tuple(reasons))
