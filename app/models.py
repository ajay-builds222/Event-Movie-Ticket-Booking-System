from sqlalchemy import (Column, Integer, String, Boolean, DateTime, Date, Time, ForeignKey, Float, Text, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class User(TimestampMixin, Base):
    __tablename__="users"
    id=Column(Integer, primary_key=True)
    full_name=Column(String(120), nullable=False)
    email=Column(String(255), unique=True, nullable=False, index=True)
    phone=Column(String(10), nullable=False)
    password_hash=Column(String(255), nullable=False)
    role=Column(String(20), nullable=False, default="Customer")
    is_active=Column(Boolean, default=True, nullable=False)
    is_approved=Column(Boolean, default=False, nullable=False)
    assigned_venue_id=Column(Integer, ForeignKey("venues.id"), nullable=True)

class Venue(TimestampMixin, Base):
    __tablename__="venues"
    id=Column(Integer, primary_key=True)
    name=Column(String(150), nullable=False)
    city=Column(String(100), nullable=False, index=True)
    address=Column(String(255), nullable=False)
    is_active=Column(Boolean, default=True, nullable=False)
    screens=relationship("Screen", back_populates="venue", cascade="all, delete-orphan")

class Screen(TimestampMixin, Base):
    __tablename__="screens"
    id=Column(Integer, primary_key=True)
    venue_id=Column(Integer, ForeignKey("venues.id"), nullable=False)
    name=Column(String(100), nullable=False)
    total_seats=Column(Integer, default=0, nullable=False)
    venue=relationship("Venue", back_populates="screens")
    seats=relationship("Seat", back_populates="screen", cascade="all, delete-orphan")

class Seat(TimestampMixin, Base):
    __tablename__="seats"
    __table_args__=(UniqueConstraint("screen_id","row_label","seat_number", name="uq_screen_row_seat"),)
    id=Column(Integer, primary_key=True)
    screen_id=Column(Integer, ForeignKey("screens.id"), nullable=False)
    row_label=Column(String(5), nullable=False)
    seat_number=Column(Integer, nullable=False)
    category=Column(String(20), nullable=False)
    is_active=Column(Boolean, default=True, nullable=False)
    screen=relationship("Screen", back_populates="seats")

class Event(TimestampMixin, Base):
    __tablename__="events"
    id=Column(Integer, primary_key=True)
    organizer_id=Column(Integer, ForeignKey("users.id"), nullable=False)
    title=Column(String(200), nullable=False, index=True)
    category=Column(String(30), nullable=False)
    language=Column(String(50), nullable=False)
    description=Column(Text, default="")
    duration_minutes=Column(Integer, nullable=False)
    age_rating=Column(String(10), default="U")
    status=Column(String(20), default="Draft", nullable=False)
    organizer=relationship("User")

class Show(TimestampMixin, Base):
    __tablename__="shows"
    id=Column(Integer, primary_key=True)
    event_id=Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    screen_id=Column(Integer, ForeignKey("screens.id"), nullable=False)
    show_date=Column(Date, nullable=False)
    start_time=Column(Time, nullable=False)
    end_time=Column(Time, nullable=False)
    booking_closes_at=Column(DateTime(timezone=True), nullable=False)
    status=Column(String(20), default="Scheduled", nullable=False)
    event=relationship("Event")
    screen=relationship("Screen")
    prices=relationship("SeatCategoryPrice", cascade="all, delete-orphan")

class SeatCategoryPrice(TimestampMixin, Base):
    __tablename__="seat_category_prices"
    __table_args__=(UniqueConstraint("show_id","category",name="uq_show_category"),)
    id=Column(Integer, primary_key=True)
    show_id=Column(Integer, ForeignKey("shows.id"), nullable=False)
    category=Column(String(20), nullable=False)
    price=Column(Float, nullable=False)

class Booking(TimestampMixin, Base):
    __tablename__="bookings"
    id=Column(Integer, primary_key=True)
    booking_number=Column(String(40), unique=True, nullable=False, index=True)
    customer_id=Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    show_id=Column(Integer, ForeignKey("shows.id"), nullable=False, index=True)
    seat_count=Column(Integer, nullable=False)
    ticket_total=Column(Float, nullable=False)
    convenience_fee=Column(Float, nullable=False)
    gst_amount=Column(Float, nullable=False)
    grand_total=Column(Float, nullable=False)
    status=Column(String(30), default="Pending Payment", nullable=False)
    expires_at=Column(DateTime(timezone=True), nullable=False)
    confirmed_at=Column(DateTime(timezone=True), nullable=True)
    reminder_sent=Column(Boolean, default=False, nullable=False)
    customer=relationship("User")
    show=relationship("Show")
    seats=relationship("BookedSeat", cascade="all, delete-orphan")
    payments=relationship("Payment", cascade="all, delete-orphan")
    refunds=relationship("Refund", cascade="all, delete-orphan")

class BookedSeat(TimestampMixin, Base):
    __tablename__="booked_seats"
    __table_args__=(UniqueConstraint("show_id","seat_id",name="uq_show_booked_seat"),)
    id=Column(Integer, primary_key=True)
    booking_id=Column(Integer, ForeignKey("bookings.id"), nullable=False)
    show_id=Column(Integer, ForeignKey("shows.id"), nullable=False)
    seat_id=Column(Integer, ForeignKey("seats.id"), nullable=False)
    price=Column(Float, nullable=False)
    seat=relationship("Seat")

class Payment(TimestampMixin, Base):
    __tablename__="payments"
    id=Column(Integer, primary_key=True)
    booking_id=Column(Integer, ForeignKey("bookings.id"), nullable=False)
    transaction_id=Column(String(60), unique=True, nullable=False)
    amount=Column(Float, nullable=False)
    payment_method=Column(String(30), nullable=False)
    status=Column(String(20), nullable=False)
    paid_at=Column(DateTime(timezone=True), nullable=True)

class Refund(TimestampMixin, Base):
    __tablename__="refunds"
    id=Column(Integer, primary_key=True)
    booking_id=Column(Integer, ForeignKey("bookings.id"), nullable=False)
    amount=Column(Float, nullable=False)
    reason=Column(String(40), nullable=False)
    status=Column(String(20), default="Processed", nullable=False)
    refunded_at=Column(DateTime(timezone=True), server_default=func.now())

class Waitlist(TimestampMixin, Base):
    __tablename__="waitlist"
    __table_args__=(UniqueConstraint("show_id","customer_id",name="uq_waitlist_customer_show"),)
    id=Column(Integer, primary_key=True)
    show_id=Column(Integer, ForeignKey("shows.id"), nullable=False)
    customer_id=Column(Integer, ForeignKey("users.id"), nullable=False)
    seat_count=Column(Integer, nullable=False)
    preferred_category=Column(String(20), nullable=False)
