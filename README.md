# Event & Movie Ticket Booking System (FastAPI)

This repository is a practical backend starter for the assignment: JWT/RBAC, MySQL + SQLAlchemy, Alembic migrations, Redis seat locks/cache/rate limits, shows, bookings, mock payments, signed QR/PDF tickets, scanning, waitlists, WebSockets, and reports.

> **Important scope note:** this is an educational MVP, not a production ticketing/payment system. See “Known gaps” before submission. Never use real payment credentials or real customer data.

## Requirements
- Python 3.9+ (Python 3.11/3.12 recommended)
- MySQL 8+
- Redis 6+
- Git

## Setup (Windows PowerShell)
```powershell
cd C:\Python\Event_Movie_Ticket_Booking_System
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```
Edit `.env`: set the MySQL password, long random JWT and ticket HMAC secrets, and optional SMTP app password. Create the database in MySQL:
```sql
CREATE DATABASE ticket_booking_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
Start Redis (for example, run Redis with Docker: `docker run --name ticket-redis -p 6379:6379 -d redis:7-alpine`).

## Migrations (do not use create_all)
```powershell
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```
After changing models, generate another revision and run `alembic upgrade head`. Do not use SQLAlchemy `create_all()`.

## Run
```powershell
uvicorn app.main:app --reload
```
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health
- Seat-map test page: http://127.0.0.1:8000/seat-map-test

## First admin user
Public registration deliberately refuses Admin and Staff roles. Create the first Admin securely using a one-time local script/SQL operation; easiest for development is this Python command after `.env` and database migrations:
```powershell
python -c "from app.database import SessionLocal; from app.models import User; from app.security import hash_password; d=SessionLocal(); u=User(full_name='Local Admin',email='admin@example.com',phone='9999999999',password_hash=hash_password('ChangeMe123!'),role='Admin',is_approved=True); d.add(u); d.commit(); d.close(); print('Admin created')"
```
Run it only once; use a unique email/password and change the example credentials immediately. Organizer/customer accounts are created through `/auth/register`. Approve organizers with the Admin token via `PUT /admin/organizers/{user_id}/approve`. Admin creates staff through `POST /admin/staff?venue_id=1`.

## Suggested Swagger smoke-test sequence
1. Register Customer and Organizer using `/auth/register`.
2. Create the Admin with the local command above; log in at `/auth/login`.
3. As Admin, create a venue, screen, and seats (e.g. rows A-E, 12 seats/row; category Silver).
4. Approve the Organizer.
5. As Organizer, create a Published event and a future show. Set prices for every active category present on the screen.
6. As Customer, call `GET /shows/{show_id}/seats`, then `POST /shows/{show_id}/lock-seats` with seat IDs.
7. Create the booking using `POST /bookings?show_id={show_id}`, then pay with `POST /bookings/{booking_id}/pay` using `simulate_status` `Failed` and retry with `Success` while locks remain valid.
8. Download the PDF from `/bookings/{booking_id}/ticket`. The QR payload is `booking_number.HMAC_SHA256_signature`.
9. Admin creates Staff assigned to the venue. For scanning, submit the signed QR value to `/tickets/scan`. Scanning only works within the one-hour-before to show-end window.
10. Test repeated scan (409), cancellation/refund, waitlist, and reports.

## WebSocket live seat map
Browser console:
```javascript
const ws = new WebSocket("ws://localhost:8000/ws/shows/1/seats?token=PASTE_ACCESS_TOKEN");
ws.onmessage = e => console.log("Update:", e.data);
```
Or open `/seat-map-test` in two browser tabs, paste the same show ID and each user's access token, connect, then lock/release seats from Swagger. WebSockets use `?token=` and send the full map on connect.

## SMTP
For Gmail, enable 2-Step Verification and create an App Password. Put the SMTP host, port 587, username, app password and sender in `.env`. If SMTP is blank, email is skipped and logged; booking actions still work. Never commit `.env`.

## Test credentials
No shared default credentials are shipped. Create the initial Admin with the one-time command above, then register test Organizer and Customer accounts and create a Staff user via the Admin endpoint. Use disposable local credentials; do not put real passwords in README or screenshots.

## Key rules implemented
- 30-minute JWTs and role-based API dependencies.
- Public registration only for Customer/Organizer; unique email and 10-digit phone validation.
- MySQL ORM models with timestamp fields and seat uniqueness constraints.
- Redis `SET NX EX` seat locks (10 minutes), lock rate limiting and login rate limiting.
- Show conflict check with 15-minute cleaning gap, seat category prices, event cache (5 minutes).
- Booking bill: ticket total + ₹30 per seat + 18% GST on ticket total plus fee.
- Mock success/failure payments, signed QR, PDF ticket, venue-restricted staff scanning, one-time scan key.
- Customer cancellation refund policy and full show-cancellation refunds.
- APScheduler expires pending bookings every minute and completes shows every 15 minutes.
- WebSocket initial map and broadcast events for lock/release.
- Reports and pagination on core list routes.

## Known gaps / required hardening before claiming full assignment completion
This MVP is a foundation and should be extended/tested before final submission:
- The empty-seat rule is basic and should be tested for all edge cases; lock-expiry notifications are not broadcast by a dedicated Redis expiry worker.
- Payment confirmation uses a database transaction but external ticket file creation/email cannot be atomic with MySQL; add retry/outbox handling for production.
- Show cancellation currently emails customers but should broadcast show cancellation to every connected WebSocket and send all notifications reliably.
- Waitlist notification-on-release, the 30-minute reminder job, daily event completion job, robust occupancy-by-category and exact per-show entry counts need further implementation.
- `GET /shows/{show_id}/entries` currently reports total scanned keys rather than only that show's scans; store show ID in scan records for accurate counts.
- Add automated pytest cases for seat locking, empty-seat rule, payment outcomes and cancellation policy; add concurrency tests and transactional row locking.
- Cache invalidation and event search sort options should be expanded for date/price sorting; MySQL join/filter combinations need integration tests.
- For production, use Redis Lua scripts for all-or-nothing multi-seat locks, distributed rate limiting, background worker/outbox, timezone-aware show datetimes, idempotent payment handling and robust auth on WebSockets.
- Razorpay, coupons, dynamic pricing, best-seat suggestions, Excel export, Docker Compose are bonus items and are not included.

## Submission checklist
- [ ] Push code, migrations and README; never commit `.env` or generated `tickets/`.
- [ ] Generate and commit Alembic migration scripts.
- [ ] Capture Swagger screenshots for auth, venues/layout, event/show, lock, booking, failed/success payment, cancellation and reports.
- [ ] Capture successful and repeated scan responses.
- [ ] Capture PDF ticket with QR code.
- [ ] Record two tabs showing live seat-map updates.
