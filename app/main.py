"""
main.py

Root VPN backend. Runs as a Vercel serverless function (see vercel.json).

IMPORTANT: Vercel's serverless functions have a READ-ONLY filesystem, so
uploaded receipt images are stored directly in the database as binary
data -- never written to local disk.
"""

import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app import models, auth, devices, pricing
from app.database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Root VPN API")

# FRONTEND_ORIGIN can hold a single origin or a comma-separated list, e.g.
#   "https://root-vpn.vercel.app,https://root-vpn-admin.vercel.app"
# This lets the customer panel and the admin panel be two separate Vercel
# projects/domains while both still passing CORS.
_raw_origins = os.getenv("FRONTEND_ORIGIN", "*")
ALLOWED_ORIGINS = (
    ["*"] if _raw_origins.strip() == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas --
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=4)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OrderCreateRequest(BaseModel):
    device_keys: List[str]
    connection_type: str  # single | double | multi
    duration_months: int  # 1 | 3 | 6 | 12
    is_unlimited: bool = False
    volume_gb: Optional[int] = None


class OrderResponse(BaseModel):
    id: str
    status: str
    total_price_toman: float
    device_count: int

    class Config:
        from_attributes = True


class RejectRequest(BaseModel):
    note: Optional[str] = None


# ------------------------------------------------------------------ auth --
@app.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.username == payload.username.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="این نام کاربری قبلاً ثبت‌نام شده")

    user = models.User(
        username=payload.username.lower(),
        password_hash=auth.hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = auth.create_access_token(user.id)
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == payload.username.lower()).first()
    if not user or not auth.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="نام کاربری یا رمز عبور اشتباهه")

    token = auth.create_access_token(user.id)
    return TokenResponse(access_token=token)


@app.get("/auth/me")
def whoami(current_user: models.User = Depends(auth.get_current_user)):
    """
    Used by both panels right after login: the customer panel just needs to
    confirm the token is alive, the admin panel also checks is_admin before
    letting the user into the dashboard (in addition to the server-side
    check on every /admin/* route -- this is only for a nicer UI redirect).
    """
    return {
        "id": current_user.id,
        "username": current_user.username,
        "is_admin": current_user.is_admin,
    }


# --------------------------------------------------------------- devices --
@app.get("/devices")
def list_devices():
    return devices.DEVICE_CATALOG


# ---------------------------------------------------------------- pricing --
@app.post("/pricing/calculate")
def calculate_price(payload: OrderCreateRequest):
    try:
        result = pricing.calculate_price(
            duration_months=payload.duration_months,
            connection_type=payload.connection_type,
            device_count=len(payload.device_keys),
            is_unlimited=payload.is_unlimited,
            volume_gb=payload.volume_gb,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ----------------------------------------------------------------- orders --
@app.post("/orders", response_model=OrderResponse)
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if not payload.device_keys:
        raise HTTPException(status_code=400, detail="سبد خرید نمی‌تونه خالی باشه")

    for key in payload.device_keys:
        if key not in devices.DEVICE_CATALOG:
            raise HTTPException(status_code=400, detail=f"دستگاه ناشناخته: {key}")

    try:
        price = pricing.calculate_price(
            duration_months=payload.duration_months,
            connection_type=payload.connection_type,
            device_count=len(payload.device_keys),
            is_unlimited=payload.is_unlimited,
            volume_gb=payload.volume_gb,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    order = models.Order(
        user_id=current_user.id,
        duration_months=payload.duration_months,
        is_unlimited=payload.is_unlimited,
        volume_gb=payload.volume_gb,
        connection_type=payload.connection_type,
        total_price_toman=price["total"],
        status=models.OrderStatus.pending_payment,
    )
    db.add(order)
    db.flush()

    for key in payload.device_keys:
        db.add(models.OrderDevice(
            order_id=order.id,
            device_key=key,
            protocol=devices.resolve_protocol(key),
        ))

    db.commit()
    db.refresh(order)

    return OrderResponse(
        id=order.id,
        status=order.status.value,
        total_price_toman=order.total_price_toman,
        device_count=len(payload.device_keys),
    )


@app.post("/orders/{order_id}/receipt")
async def upload_receipt(
    order_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    order = db.query(models.Order).filter(
        models.Order.id == order_id, models.Order.user_id == current_user.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="سفارش پیدا نشد")
    if order.status != models.OrderStatus.pending_payment:
        raise HTTPException(status_code=400, detail="این سفارش قبلاً رسیدش ثبت شده")

    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:  # 5MB
        raise HTTPException(status_code=400, detail="حجم تصویر رسید نباید بیشتر از ۵ مگابایت باشه")

    receipt = models.Receipt(
        order_id=order.id,
        file_data=file_bytes,
        content_type=file.content_type or "application/octet-stream",
        filename=file.filename,
    )
    db.add(receipt)
    order.status = models.OrderStatus.pending_review
    db.commit()

    return {"detail": "رسید با موفقیت ثبت شد، منتظر تایید ادمین باش"}


@app.get("/orders/me")
def my_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    orders = db.query(models.Order).filter(models.Order.user_id == current_user.id).all()
    return [
        {
            "id": o.id,
            "status": o.status.value,
            "total_price_toman": o.total_price_toman,
            "duration_months": o.duration_months,
            "is_unlimited": o.is_unlimited,
            "volume_gb": o.volume_gb,
            "connection_type": o.connection_type.value,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "approved_at": o.approved_at.isoformat() if o.approved_at else None,
            "admin_note": o.admin_note,
            "devices": [
                {"device_key": d.device_key, "protocol": d.protocol,
                 "connection_address": d.connection_address}
                for d in o.devices
            ],
        }
        for o in orders
    ]


# ---------------------------------------------------------------- helpers --
def _serialize_order_for_admin(o: models.Order) -> dict:
    return {
        "id": o.id,
        "username": o.user.username,
        "user_id": o.user_id,
        "status": o.status.value,
        "total_price_toman": o.total_price_toman,
        "duration_months": o.duration_months,
        "is_unlimited": o.is_unlimited,
        "volume_gb": o.volume_gb,
        "connection_type": o.connection_type.value,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "admin_note": o.admin_note,
        "has_receipt": o.receipt is not None,
        "devices": [
            {"device_key": d.device_key, "protocol": d.protocol,
             "connection_address": d.connection_address}
            for d in o.devices
        ],
    }


# ------------------------------------------------------------------ admin --
# همه‌ی این‌ها پشت auth.get_current_admin هستن: توکن JWT معتبر لازمه *و*
# باید is_admin=True باشه، وگرنه 403 برمی‌گرده.

@app.get("/admin/stats")
def admin_stats(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    total_users = db.query(models.User).count()
    total_orders = db.query(models.Order).count()
    pending_review = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.pending_review
    ).count()
    approved = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.approved
    ).count()
    revenue = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.approved
    ).with_entities(models.Order.total_price_toman).all()
    total_revenue = sum(r[0] for r in revenue) if revenue else 0
    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "pending_review": pending_review,
        "approved": approved,
        "total_revenue_toman": total_revenue,
    }


@app.get("/admin/users")
def admin_list_users(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "order_count": len(u.orders),
        }
        for u in users
    ]


@app.get("/admin/orders")
def admin_list_orders(
    status: Optional[str] = Query(default=None, description="pending_payment | pending_review | approved | rejected"),
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    query = db.query(models.Order)
    if status:
        try:
            status_enum = models.OrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"وضعیت نامعتبر: {status}")
        query = query.filter(models.Order.status == status_enum)
    orders = query.order_by(models.Order.created_at.desc()).all()
    return [_serialize_order_for_admin(o) for o in orders]


# Kept for backwards-compat with anything already pointed at this URL --
# equivalent to GET /admin/orders?status=pending_review
@app.get("/admin/orders/pending")
def pending_orders(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    orders = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.pending_review
    ).order_by(models.Order.created_at.desc()).all()
    return [_serialize_order_for_admin(o) for o in orders]


@app.get("/admin/orders/{order_id}")
def admin_get_order(
    order_id: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="سفارش پیدا نشد")
    return _serialize_order_for_admin(order)


@app.get("/admin/orders/{order_id}/receipt")
def get_receipt_image(
    order_id: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    """Serves the receipt image straight from the database."""
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order or not order.receipt:
        raise HTTPException(status_code=404, detail="رسیدی پیدا نشد")
    return Response(content=order.receipt.file_data, media_type=order.receipt.content_type)


@app.post("/admin/orders/{order_id}/approve")
def approve_order(
    order_id: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="سفارش پیدا نشد")
    if order.status != models.OrderStatus.pending_review:
        raise HTTPException(status_code=400, detail="فقط سفارش‌های در انتظار بررسی رو میشه تایید کرد")

    # TODO (فاز بعدی): اینجا باید gate_mikrotik_client.py صدا زده بشه تا اکانت
    # واقعی روی روتر ساخته بشه و mikrotik_identifier / connection_address هر
    # OrderDevice پر بشه. فعلاً فقط وضعیت سفارش تغییر می‌کنه.
    order.status = models.OrderStatus.approved
    order.approved_at = datetime.utcnow()
    order.admin_note = None
    db.commit()
    db.refresh(order)
    return _serialize_order_for_admin(order)


@app.post("/admin/orders/{order_id}/reject")
def reject_order(
    order_id: str,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(auth.get_current_admin),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="سفارش پیدا نشد")
    if order.status not in (models.OrderStatus.pending_review, models.OrderStatus.pending_payment):
        raise HTTPException(status_code=400, detail="این سفارش قبلاً نهایی شده")

    order.status = models.OrderStatus.rejected
    order.admin_note = payload.note
    db.commit()
    db.refresh(order)
    return _serialize_order_for_admin(order)
