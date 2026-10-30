import hashlib, hmac, io, logging, smtplib
from email.message import EmailMessage
from pathlib import Path
import qrcode
from reportlab.pdfgen import canvas
from app.config import settings
log=logging.getLogger(__name__)
def signed_ticket(booking_number):
    sig=hmac.new(settings.TICKET_HMAC_SECRET.encode(),booking_number.encode(),hashlib.sha256).hexdigest()
    return f"{booking_number}.{sig}"
def verify_ticket(value):
    try:
        number,sig=value.rsplit(".",1)
        expected=hmac.new(settings.TICKET_HMAC_SECRET.encode(),number.encode(),hashlib.sha256).hexdigest()
        return number if hmac.compare_digest(sig,expected) else None
    except Exception: return None
def make_ticket(booking, event_title, venue_name, screen_name, show_date, start_time, seats):
    Path("tickets").mkdir(exist_ok=True)
    path=Path("tickets")/f"{booking.booking_number}.pdf"
    qr=qrcode.make(signed_ticket(booking.booking_number))
    qrpath=Path("tickets")/f"{booking.booking_number}.png"; qr.save(qrpath)
    c=canvas.Canvas(str(path), pagesize=(420,600))
    c.setFont("Helvetica-Bold",18); c.drawString(35,555,"Event & Movie Ticket")
    c.setFont("Helvetica",11)
    lines=[f"Event: {event_title}",f"Venue: {venue_name}",f"Screen: {screen_name}",f"Show: {show_date} {start_time}",f"Booking: {booking.booking_number}",f"Seats: {', '.join(seats)}",f"Amount paid: Rs. {booking.grand_total:.2f}"]
    y=515
    for line in lines: c.drawString(35,y,line); y-=25
    c.drawImage(str(qrpath),130,60,width=150,height=150)
    c.save(); qrpath.unlink(missing_ok=True)
    return str(path)
def send_email(to, subject, body, attachment=None):
    if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
        log.info("SMTP not configured; skipped email to %s: %s",to,subject); return
    try:
        msg=EmailMessage(); msg["Subject"]=subject; msg["From"]=settings.SMTP_FROM or settings.SMTP_USERNAME; msg["To"]=to; msg.set_content(body)
        if attachment:
            with open(attachment,"rb") as f: data=f.read()
            msg.add_attachment(data,maintype="application",subtype="pdf",filename=Path(attachment).name)
        with smtplib.SMTP(settings.SMTP_HOST,settings.SMTP_PORT,timeout=10) as server:
            server.starttls(); server.login(settings.SMTP_USERNAME,settings.SMTP_PASSWORD); server.send_message(msg)
    except Exception: log.exception("Email failed; main action is unaffected")
