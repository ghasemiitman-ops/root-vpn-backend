"""
main.py

Root VPN backend. Runs as a Vercel serverless function (see vercel.json).

IMPORTANT: Vercel's serverless functions have a READ-ONLY filesystem, so
uploaded receipt images are stored directly in the database as binary
data -- never written to local disk.
"""

import os
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app import models, auth, devices, pricing
from app.database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Root VPN API")

ALLOWED_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
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
            "devices": [
                {"device_key": d.device_key, "protocol": d.protocol,
                 "connection_address": d.connection_address}
                for d in o.devices
            ],
        }
        for o in orders
    ]


# ----------------------------------------------------------- admin (TODO) --
# نکته: این بخش هنوز محافظت (احراز هویت مخصوص ادمین) نداره — قبل از هرگونه
# دیپلوی واقعی، حتماً یه لایه‌ی جدا برای authentication ادمین اضافه کن.

@app.get("/admin/orders/pending")
def pending_orders(db: Session = Depends(get_db)):
    orders = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.pending_review
    ).all()
    return [
        {
            "id": o.id,
            "username": o.user.username,
            "total_price_toman": o.total_price_toman,
            "has_receipt": o.receipt is not None,
            "devices": [d.device_key for d in o.devices],
        }
        for o in orders
    ]


@app.get("/admin/orders/{order_id}/receipt")
def get_receipt_image(order_id: str, db: Session = Depends(get_db)):
    """Serves the receipt image straight from the database."""
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order or not order.receipt:
        raise HTTPException(status_code=404, detail="رسیدی پیدا نشد")
    return Response(content=order.receipt.file_data, media_type=order.receipt.content_type)
