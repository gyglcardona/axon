"""
Pruebas de src/siigo_client.py: crear_purchase (POST /v1/purchases real) y
su reintento de autocorrección por 'invalid_total_payments' -- confirmado
como necesario en producción por el aplicativo anterior del usuario
(C:\\Users\\User\\Desktop\\Automatizar\\core\\enviar_siigo_individual.py).
Nunca toca la red real: se reemplaza siigo_client._post_purchases.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import siigo_client  # noqa: E402


def _cuerpo_error_total(total_calculado):
    return {"errors": [{"code": "invalid_total_payments",
                         "message": f"Invalid total payments. The total purchase calculated is '{total_calculado}'"}]}


def test_crear_purchase_exitoso(monkeypatch):
    monkeypatch.setattr(
        siigo_client, "_post_purchases",
        lambda token, partner_id, payload: (201, {"id": "abc-123"}, '{"id":"abc-123"}'),
    )
    resultado = siigo_client.crear_purchase("TOKEN", "Axon", {"payments": [{"value": 100}]})
    assert resultado == {"id": "abc-123"}


def test_crear_purchase_error_sin_reintento_posible(monkeypatch):
    monkeypatch.setattr(
        siigo_client, "_post_purchases",
        lambda token, partner_id, payload: (400, {"errors": [{"message": "Algo distinto"}]}, "otro error"),
    )
    with pytest.raises(siigo_client.SiigoError, match="HTTP 400"):
        siigo_client.crear_purchase("TOKEN", "Axon", {"payments": [{"value": 100}]})


def test_reintenta_y_corrige_por_invalid_total_payments_diferencia_chica(monkeypatch):
    llamadas = []

    def _post_falso(token, partner_id, payload):
        llamadas.append(payload["payments"][0]["value"])
        if len(llamadas) == 1:
            cuerpo = _cuerpo_error_total("103.00")
            return 400, cuerpo, "invalid_total_payments: " + str(cuerpo)
        return 201, {"id": "ok-tras-reintento"}, '{"id":"ok-tras-reintento"}'

    monkeypatch.setattr(siigo_client, "_post_purchases", _post_falso)

    resultado = siigo_client.crear_purchase("TOKEN", "Axon", {"payments": [{"value": 100.0}]})

    assert resultado == {"id": "ok-tras-reintento"}
    assert llamadas == [100.0, 103.0]  # el segundo intento ya va con el valor que dijo Siigo


def test_no_reintenta_si_la_diferencia_es_grande(monkeypatch):
    llamadas = []

    def _post_falso(token, partner_id, payload):
        llamadas.append(payload["payments"][0]["value"])
        cuerpo = _cuerpo_error_total("500.00")
        return 400, cuerpo, "invalid_total_payments: " + str(cuerpo)

    monkeypatch.setattr(siigo_client, "_post_purchases", _post_falso)

    with pytest.raises(siigo_client.SiigoError):
        siigo_client.crear_purchase("TOKEN", "Axon", {"payments": [{"value": 100.0}]})

    assert len(llamadas) == 1  # no reintentó -- la diferencia (400) es demasiado grande


def test_extraer_total_esperado_de_un_mensaje_real():
    cuerpo = _cuerpo_error_total("119000.00")
    assert siigo_client._total_esperado_por_siigo(cuerpo) == 119000.00


def test_extraer_total_esperado_devuelve_none_si_no_aplica():
    assert siigo_client._total_esperado_por_siigo({"errors": [{"message": "otra cosa"}]}) is None
    assert siigo_client._total_esperado_por_siigo(None) is None
