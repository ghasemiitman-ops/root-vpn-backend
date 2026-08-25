"""
models.py

Database schema for Root VPN's backend. Mirrors exactly what the
user-panel frontend already collects, so nothing needs to be re-designed:
username/password auth, a cart of devices per order, connection type,
duration+volume (or unlimited), and a payment receipt per order.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Enum, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_id():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_id)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="user")


class ConnectionType(str, enum.Enum):
    single = "single"
    double = "double"
    multi = "multi"


class OrderStatus(str, enum.Enum):
    pending_payment = "pending_payment"   # cart built, waiting for receipt
    pending_review = "pending_review"     # receipt uploaded, waiting for admin
    approved = "approved"                 # admin approved, accounts provisioned
    rejected = "rejected"


class Order(Base):
    """
    One order = one checkout = one cart. Contains N devices (OrderDevice),
    a shared duration/volume/connection-type, and one payment receipt.
    """
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    duration_months = Column(Integer, nullable=False)     # 1, 3, 6, 12
    is_unlimited = Column(Boolean, default=False)
    volume_gb = Column(Integer, nullable=True)             # null if unlimited
    connection_type = Column(Enum(ConnectionType), nullable=False)

    total_price_toman = Column(Float, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.pending_payment)

    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")
    devices = relationship("OrderDevice", back_populates="order", cascade="all, delete-orphan")
    receipt = relationship("Receipt", back_populates="order", uselist=False, cascade="all, delete-orphan")


class OrderDevice(Base):
    """One device inside an order's cart, e.g. 'iphone' or 'android_samsung'."""
    __tablename__ = "order_devices"

    id = Column(String, primary_key=True, default=gen_id)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)

    device_key = Column(String, nullable=False)   # e.g. 'android_samsung', 'iphone'
    protocol = Column(String, nullable=False)     # resolved automatically server-side

    # Filled in once an admin approves the order and the MikroTik account is created
    mikrotik_identifier = Column(String, nullable=True)   # PPP secret name or WG peer public key
    connection_address = Column(String, nullable=True)    # assigned tunnel IP

    order = relationship("Order", back_populates="devices")


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(String, primary_key=True, default=gen_id)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, unique=True)
    file_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="receipt")
