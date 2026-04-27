"""Router: /billing — usage stats and Stripe webhook handler (F6).

Usage endpoint:
    GET /billing/usage       — current month + history for the authenticated tenant

Stripe webhook:
    POST /billing/webhook    — receives and validates Stripe events
    Handles: customer.subscription.updated, invoice.paid, invoice.payment_failed

Stripe signature validation uses raw HMAC-SHA256, not the stripe-python SDK,
to keep the dependency list minimal.  Add stripe to requirements.txt and swap
in stripe.Webhook.construct_event() for production hardening.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from api.deps import get_tenant_db
from api.models.schemas import BillingUsageResponse, UsageMonth
from api.tenant import PLAN_LIMITS

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)

_STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


# ─── Usage ────────────────────────────────────────────────────────────────────


@router.get("/usage", response_model=BillingUsageResponse)
def get_billing_usage(
    months: int = Query(default=3, ge=1, le=12),
    request: Request = None,
    conn=Depends(get_tenant_db),
) -> BillingUsageResponse:
    """Return event usage for the current billing month and recent history.

    Also returns the plan limit so the frontend can render a usage meter.
    """
    from api.tenant import fetch_current_usage, fetch_tenant_usage

    tenant = request.state.tenant
    current = fetch_current_usage(tenant.tenant_id, conn)
    history = fetch_tenant_usage(tenant.tenant_id, months, conn)
    limit   = PLAN_LIMITS[tenant.plan]["events_per_month"]

    return BillingUsageResponse(
        tenant_id=tenant.tenant_id,
        plan=tenant.plan,
        current_month_events=current,
        plan_limit=limit,
        usage_pct=round(current / limit * 100, 1) if limit else None,
        history=[UsageMonth(**r) for r in history],
    )


# ─── Stripe webhook ───────────────────────────────────────────────────────────


def _validate_stripe_signature(body: bytes, sig_header: str, secret: str) -> bool:
    """Validate Stripe-Signature header using HMAC-SHA256.

    Stripe sig format: "t=<timestamp>,v1=<signature>"
    Signed payload: "<timestamp>.<body>"
    """
    if not secret:
        return True  # Dev mode: skip validation when secret not configured

    try:
        parts = dict(s.split("=", 1) for s in sig_header.split(","))
        timestamp = parts.get("t", "")
        v1        = parts.get("v1", "")
        payload   = f"{timestamp}.{body.decode('utf-8')}"
        expected  = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, v1)
    except Exception:
        return False


@router.post("/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
) -> dict:
    """Receive and process Stripe webhook events.

    Validates the Stripe-Signature header before processing.
    Idempotent: skips events already recorded in stripe_webhook_events.
    """
    body = await request.body()

    if not _validate_stripe_signature(body, stripe_signature, _STRIPE_WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature.",
        )

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.")

    event_id   = event.get("id", "")
    event_type = event.get("type", "")

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event id.")

    # Import here to avoid circular import at module load
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "org_synapse"),
        user=os.environ.get("POSTGRES_USER", "opb"),
        password=os.environ.get("POSTGRES_PASSWORD", "changeme"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        # Idempotency check
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM public.stripe_webhook_events WHERE stripe_event_id = %s",
                (event_id,),
            )
            if cur.fetchone():
                logger.info("Stripe event %s already processed — skipping.", event_id)
                return {"status": "duplicate"}

        _handle_stripe_event(event_type, event, conn)

        # Record the event
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.stripe_webhook_events (stripe_event_id, type, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event_id, event_type, json.dumps(event)),
            )
        conn.commit()
        logger.info("Stripe event processed: %s (%s)", event_type, event_id)
    except Exception as exc:
        conn.rollback()
        logger.exception("Stripe webhook processing failed: %s", exc)
        raise HTTPException(status_code=500, detail="Webhook processing error.") from exc
    finally:
        conn.close()

    return {"status": "ok", "event_type": event_type}


def _handle_stripe_event(event_type: str, event: dict, conn) -> None:
    """Dispatch Stripe event to the appropriate handler."""
    obj = event.get("data", {}).get("object", {})

    if event_type == "customer.subscription.updated":
        _handle_subscription_updated(obj, conn)
    elif event_type == "invoice.paid":
        _handle_invoice_paid(obj, conn)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(obj, conn)
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)


def _handle_subscription_updated(sub: dict, conn) -> None:
    customer_id = sub.get("customer", "")
    new_plan    = _stripe_plan_to_internal(sub.get("items", {}).get("data", [{}])[0])

    if not customer_id or not new_plan:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.tenants
               SET plan = %s, stripe_subscription_id = %s, updated_at = NOW()
             WHERE stripe_customer_id = %s
            """,
            (new_plan, sub.get("id", ""), customer_id),
        )
    logger.info("Subscription updated: customer=%s plan=%s", customer_id, new_plan)


def _handle_invoice_paid(invoice: dict, conn) -> None:
    customer_id = invoice.get("customer", "")
    if not customer_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.tenants SET active = true, updated_at = NOW() WHERE stripe_customer_id = %s",
            (customer_id,),
        )
    logger.info("Invoice paid: customer=%s — tenant re-activated if suspended.", customer_id)


def _handle_payment_failed(invoice: dict, conn) -> None:
    # Don't immediately deactivate on first failure — Stripe retries.
    # Log only; dunning management is handled externally.
    customer_id = invoice.get("customer", "")
    amount      = invoice.get("amount_due", 0) / 100  # cents → dollars
    logger.warning(
        "Invoice payment failed: customer=%s amount=$%.2f — dunning initiated.",
        customer_id, amount,
    )


def _stripe_plan_to_internal(item: dict) -> str | None:
    """Map a Stripe subscription item to an internal plan name."""
    price_id   = (item.get("price") or {}).get("id", "")
    product_id = (item.get("price") or {}).get("product", "")

    # These mappings are set via env vars so they can change without a deploy
    plan_map = {
        os.environ.get("STRIPE_PRICE_STARTER",    ""): "starter",
        os.environ.get("STRIPE_PRICE_PRO",         ""): "pro",
        os.environ.get("STRIPE_PRICE_ENTERPRISE",  ""): "enterprise",
    }
    return plan_map.get(price_id) or plan_map.get(product_id)
