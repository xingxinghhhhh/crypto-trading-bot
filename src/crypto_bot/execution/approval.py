from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from crypto_bot.errors import SafetyError
from crypto_bot.execution.models import OrderIntent

_APPROVAL_SECRET = secrets.token_bytes(32)
_CONSUMED_NONCES: set[str] = set()
_CONSUMED_LOCK = threading.Lock()


@dataclass(frozen=True)
class RiskApproval:
    order_id: str
    order_digest: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    signature: str


def _issue_risk_approval(order: OrderIntent, *, ttl_seconds: float = 30.0) -> RiskApproval:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    order_digest = _order_digest(order)
    nonce = str(uuid4())
    signature = _signature(order.id, order_digest, nonce, issued_at, expires_at)
    return RiskApproval(
        order_id=order.id,
        order_digest=order_digest,
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        signature=signature,
    )


def consume_risk_approval(
    order: OrderIntent,
    approval: RiskApproval | None,
    *,
    now: datetime | None = None,
) -> None:
    if approval is None:
        raise SafetyError("paper orders require a RiskApproval")

    current_time = now or datetime.now(timezone.utc)
    if approval.issued_at.tzinfo is None or approval.expires_at.tzinfo is None:
        raise SafetyError("risk approval timestamps must be timezone-aware")
    if current_time < approval.issued_at or current_time > approval.expires_at:
        raise SafetyError("risk approval is expired or not yet valid")
    if approval.order_id != order.id or approval.order_digest != _order_digest(order):
        raise SafetyError("risk approval does not match order")

    expected = _signature(
        approval.order_id,
        approval.order_digest,
        approval.nonce,
        approval.issued_at,
        approval.expires_at,
    )
    if not hmac.compare_digest(approval.signature, expected):
        raise SafetyError("risk approval signature is invalid")

    with _CONSUMED_LOCK:
        if approval.nonce in _CONSUMED_NONCES:
            raise SafetyError("risk approval has already been consumed")
        _CONSUMED_NONCES.add(approval.nonce)


def _order_digest(order: OrderIntent) -> str:
    payload = {
        "id": order.id,
        "signal_id": order.signal_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": format(order.quantity, ".17g"),
        "reference_price": None if order.reference_price is None else format(order.reference_price, ".17g"),
        "reason": order.reason,
        "created_at": order.created_at.isoformat(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _signature(
    order_id: str,
    order_digest: str,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    payload = "|".join(
        [
            order_id,
            order_digest,
            nonce,
            issued_at.isoformat(),
            expires_at.isoformat(),
        ]
    ).encode("utf-8")
    return hmac.new(_APPROVAL_SECRET, payload, hashlib.sha256).hexdigest()
