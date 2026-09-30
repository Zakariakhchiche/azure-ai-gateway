import pytest
from fastapi.testclient import TestClient

from gateway.app import app
from gateway.router import Router

router = Router()
client = TestClient(app)


@pytest.mark.parametrize("text,level", [
    ("Résume ce texte sur l'histoire du rail.", "public"),
    ("Prépare le budget du projet client.", "internal"),
    ("Écris à jean.dupont@exemple.fr au sujet du contrat.", "confidential"),
    ("Mon IBAN est FR76 3000 6000 0112 3456 7890 189", "restricted"),
    ("api_key= sk-123", "restricted"),
])
def test_classify(text, level):
    assert router.classify(text) == level


def test_public_simple_goes_to_cheapest_cloud_model():
    d = router.route("Donne-moi trois idées de titres.", min_quality=2)
    assert d.model.name == "gpt-4o-mini"


def test_complex_reasoning_goes_to_strong_model():
    d = router.route("Analyse ce problème d'optimisation.", min_quality=3)
    assert d.model.name == "gpt-4o"


def test_restricted_data_never_leaves_the_network():
    d = router.route("Vérifie l'IBAN FR76 3000 6000 0112 3456 7890 189", min_quality=3)
    assert d.model.provider == "local"
    assert d.sensitivity == "restricted"


def test_api_dry_run():
    r = client.post("/v1/chat", json={"prompt": "Bonjour", "min_quality": 1})
    body = r.json()
    assert r.status_code == 200
    assert body["answer"] is None          # pas d'appel réel sans configuration Azure
    assert body["model"] == "mistral-small-onprem"  # gratuit et suffisant
