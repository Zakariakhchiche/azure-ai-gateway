"""API de la passerelle : un point d'entrée unique devant plusieurs LLM.

Lancer : uvicorn gateway.app:app --reload
Sans variables Azure, la passerelle tourne en mode « dry-run » : elle renvoie
la décision de routage sans appeler de modèle (utile pour les tests et les démos).
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI
from pydantic import BaseModel, Field

from gateway.router import Router

log = logging.getLogger("ai-gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Azure AI Gateway", version="0.1.0")
router = Router()


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    min_quality: int = Field(1, ge=1, le=3, description="1 = basique, 3 = raisonnement avancé")
    sensitivity: str | None = Field(None, description="Forcer un niveau au lieu de la détection")
    max_tokens: int = Field(512, ge=1, le=4096)


class ChatResponse(BaseModel):
    model: str
    sensitivity: str
    reasons: list[str]
    estimated_cost_usd: float
    latency_ms: int
    answer: str | None


def call_model(deployment: str, provider: str, prompt: str, max_tokens: int) -> str | None:
    """Appelle Azure OpenAI si configuré, sinon renvoie None (dry-run)."""
    if provider != "azure-openai" or not os.getenv("AZURE_OPENAI_ENDPOINT"):
        return None
    from openai import AzureOpenAI  # import tardif : dépendance optionnelle

    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )
    resp = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "models": [m.name for m in router.models]}


@app.post("/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    start = time.perf_counter()
    decision = router.route(req.prompt, req.min_quality, req.sensitivity)
    answer = call_model(decision.model.deployment, decision.model.provider, req.prompt, req.max_tokens)
    latency = int((time.perf_counter() - start) * 1000)
    tokens_in = max(1, len(req.prompt) // 4)  # estimation grossière, à remplacer par tiktoken
    cost = decision.estimated_cost(tokens_in, req.max_tokens)
    # Journal d'observabilité : jamais le prompt en clair, seulement des métadonnées.
    log.info("route model=%s sensitivity=%s latency_ms=%d cost_usd=%.6f",
             decision.model.name, decision.sensitivity, latency, cost)
    return ChatResponse(
        model=decision.model.name,
        sensitivity=decision.sensitivity,
        reasons=list(decision.reasons),
        estimated_cost_usd=round(cost, 6),
        latency_ms=latency,
        answer=answer,
    )
