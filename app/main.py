from datetime import datetime, date, time, timedelta, timezone
from uuid import uuid4
from pathlib import Path
import json, logging
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.database import get_db, SessionLocal
from app.redis_client import redis_client, ping_redis
from app.models import *
from app.schemas import *
from app.security import *
from app.services import make_ticket, send_email, signed_ticket, verify_ticket

logging.basicConfig(level=logging.INFO)
app=FastAPI(title="Event & Movie Ticket Booking System", version="1.0.0")
connections:dict[int,set[WebSocket]]={}
def now(): return datetime.now(timezone.utc)
def cache_delete(event_id=None):
    try:
        for key in redis_client.scan_iter("events:*"):
            redis_client.delete(key)
    except Exception:
        logging.warning("Redis unavailable. Skipping cache deletion.")
async def broadcast(show_id, payload):
    dead=[]
    for ws in list(connections.get(show_id,set())):
        try: await ws.send_json(payload)
        except Exception: dead.append(ws)
    for ws in dead: connections.get(show_id,set()).discard(ws)
def lock_key(show_id,seat_id): return f"lock:show:{show_id}:seat:{seat_id}"
def show_or_404(db, show_id):
    sh=db.get(Show,show_id)
    if not sh: raise HTTPException(404,"Show not found")
    return sh
def event_access(db,event_id,user):
    ev=db.get(Event,event_id)
    if not ev: raise HTTPException(404,"Event not found")
    if user.role=="Organizer" and ev.organizer_id!=user.id: raise HTTPException(403,"Not your event")
    return ev
@app.get("/health")
def health(db:Session=Depends(get_db)):
    return {"status":"ok","database":"connected","redis":"connected" if ping_redis() else "unavailable"}
@app.post("/auth/register",status_code=201)
def register(data:RegisterIn,db:Session=Depends(get_db)):
    if data.role not in ("Customer","Organizer"): raise HTTPException(400,"Public registration supports Customer or Organizer only")
    if db.query(User).filter_by(email=data.email).first(): raise HTTPException(409,"Email already registered")
    u=User(full_name=data.full_name,email=data.email,phone=data.phone,password_hash=hash_password(data.password),role=data.role,is_approved=(data.role=="Customer"))
    db.add(u); db.commit(); db.refresh(u)
    return {"id":u.id,"email":u.email,"role":u.role,"is_approved":u.is_approved}

@app.post("/auth/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    key = f"rate:login:{ip}"

    # Try Redis rate limiting; continue without it if Redis is unavailable.
    try:
        count = redis_client.incr(key)
        if count == 1:
            redis_client.expire(key, 60)

        if count > 5:
            raise HTTPException(
                status_code=429,
                detail=f"Too many login attempts. Retry in {redis_client.ttl(key)} seconds"
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis unavailable: skip rate limiting during local development

    u = db.query(User).filter_by(email=data.email).first()

    if not u or not verify_password(data.password, u.password_hash) or not u.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials or inactive account")

    return {
        "access_token": create_token(u),
        "token_type": "bearer",
        "expires_in": 1800
    }

@app.get("/auth/me")
def me(user:User=Depends(current_user)):
    return {"id":user.id,"full_name":user.full_name,"email":user.email,"role":user.role,"is_approved":user.is_approved}
@app.put("/admin/organizers/{user_id}/approve")
def approve_organizer(user_id:int,db:Session=Depends(get_db),admin:User=Depends(require_roles("Admin"))):
    u=db.get(User,user_id)
    if not u or u.role!="Organizer": raise HTTPException(404,"Organizer not found")
    u.is_approved=True; db.commit(); return {"message":"Organizer approved"}
@app.post("/admin/staff",status_code=201)
def create_staff(data:RegisterIn,venue_id:int,db:Session=Depends(get_db),admin:User=Depends(require_roles("Admin"))):
    if db.query(User).filter_by(email=data.email).first(): raise HTTPException(409,"Email already registered")
    if not db.get(Venue,venue_id): raise HTTPException(404,"Venue not found")
    u=User(full_name=data.full_name,email=data.email,phone=data.phone,password_hash=hash_password(data.password),role="Staff",assigned_venue_id=venue_id,is_approved=True)
    db.add(u); db.commit(); db.refresh(u); return {"id":u.id,"role":u.role,"assigned_venue_id":venue_id}
@app.post("/venues",status_code=201)
def create_venue(data:VenueIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    v=Venue(**data.model_dump()); db.add(v); db.commit(); db.refresh(v); return {"id":v.id,**data.model_dump()}
@app.get("/venues")
def list_venues(skip:int=0,limit:int=20,db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [{"id":v.id,"name":v.name,"city":v.city,"address":v.address,"is_active":v.is_active} for v in db.query(Venue).offset(skip).limit(min(limit,100)).all()]
@app.put("/venues/{venue_id}")
def update_venue(venue_id:int,data:VenueIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    v=db.get(Venue,venue_id)
    if not v: raise HTTPException(404,"Venue not found")
    for k,val in data.model_dump().items(): setattr(v,k,val)
    db.commit(); return {"message":"Venue updated"}
@app.post("/venues/{venue_id}/screens",status_code=201)
def create_screen(venue_id:int,data:ScreenIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    if not db.get(Venue,venue_id): raise HTTPException(404,"Venue not found")
    s=Screen(venue_id=venue_id,name=data.name,total_seats=0); db.add(s); db.commit(); db.refresh(s); return {"id":s.id,"venue_id":venue_id,"name":s.name}
@app.post("/screens/{screen_id}/seats/bulk",status_code=201)
def bulk_seats(screen_id:int,data:BulkSeatsIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    s=db.get(Screen,screen_id)
    if not s: raise HTTPException(404,"Screen not found")
    created=[]
    for row in data.rows:
        for num in range(1,data.seats_per_row+1):
            if db.query(Seat).filter_by(screen_id=screen_id,row_label=row,seat_number=num).first(): continue
            seat=Seat(screen_id=screen_id,row_label=row,seat_number=num,category=data.category,is_active=True); db.add(seat); created.append(seat)
    s.total_seats=db.query(Seat).filter_by(screen_id=screen_id).count()+len(created)
    db.commit(); return {"created":len(created),"total_seats":s.total_seats}
@app.get("/screens/{screen_id}/layout")
def screen_layout(screen_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not db.get(Screen,screen_id): raise HTTPException(404,"Screen not found")
    seats=db.query(Seat).filter_by(screen_id=screen_id).order_by(Seat.row_label,Seat.seat_number).all()
    rows={}
    for s in seats: rows.setdefault(s.row_label,[]).append({"id":s.id,"seat_number":s.seat_number,"category":s.category,"is_active":s.is_active})
    return {"screen_id":screen_id,"rows":rows}
@app.put("/seats/{seat_id}/status")
def seat_status(seat_id:int,is_active:bool,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    s=db.get(Seat,seat_id)
    if not s: raise HTTPException(404,"Seat not found")
    s.is_active=is_active; db.commit(); return {"id":s.id,"is_active":s.is_active}
@app.post("/events",status_code=201)
def create_event(data:EventIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Organizer"))):
    if not user.is_approved: raise HTTPException(403,"Organizer approval required")
    ev=Event(organizer_id=user.id,**data.model_dump()); db.add(ev); db.commit(); db.refresh(ev); cache_delete()
    return {"id":ev.id,"title":ev.title,"status":ev.status}
@app.get("/events")
def list_events(title:str|None=None,city:str|None=None,category:str|None=None,language:str|None=None,from_date:date|None=None,to_date:date|None=None,min_price:float|None=None,max_price:float|None=None,sort_by:str="title",order:str="asc",skip:int=0,limit:int=20,db:Session=Depends(get_db)):
    key="events:"+json.dumps({"title":title,"city":city,"category":category,"language":language,"from_date":str(from_date),"to_date":str(to_date),"min_price":min_price,"max_price":max_price,"sort_by":sort_by,"order":order,"skip":skip,"limit":limit},sort_keys=True)
    cached=redis_client.get(key)
    if cached: return json.loads(cached)
    q=db.query(Event).filter(Event.status=="Published")
    if title: q=q.filter(Event.title.ilike(f"%{title}%"))
    if category: q=q.filter(Event.category==category)
    if language: q=q.filter(Event.language==language)
    if city: q=q.join(Show,Show.event_id==Event.id).join(Screen,Screen.id==Show.screen_id).join(Venue,Venue.id==Screen.venue_id).filter(Venue.city.ilike(f"%{city}%"))
    if from_date: q=q.join(Show,Show.event_id==Event.id).filter(Show.show_date>=from_date)
    if to_date: q=q.join(Show,Show.event_id==Event.id).filter(Show.show_date<=to_date)
    q=q.distinct()
    if min_price is not None or max_price is not None:
        q=q.join(Show,Show.event_id==Event.id).join(SeatCategoryPrice,SeatCategoryPrice.show_id==Show.id)
        if min_price is not None: q=q.filter(SeatCategoryPrice.price>=min_price)
        if max_price is not None: q=q.filter(SeatCategoryPrice.price<=max_price)
        q=q.distinct()
    if sort_by=="title": q=q.order_by(Event.title.desc() if order=="desc" else Event.title.asc())
    result=[{"id":e.id,"title":e.title,"category":e.category,"language":e.language,"description":e.description,"duration_minutes":e.duration_minutes,"age_rating":e.age_rating,"status":e.status} for e in q.offset(skip).limit(min(limit,100)).all()]
    redis_client.setex(key,300,json.dumps(result)); return result
@app.get("/events/{event_id}")
def get_event(event_id:int,db:Session=Depends(get_db)):
    key=f"event:{event_id}"; cached=redis_client.get(key)
    if cached: return json.loads(cached)
    ev=db.get(Event,event_id)
    if not ev or ev.status!="Published": raise HTTPException(404,"Published event not found")
    data={"id":ev.id,"title":ev.title,"category":ev.category,"language":ev.language,"description":ev.description,"duration_minutes":ev.duration_minutes,"age_rating":ev.age_rating}
    redis_client.setex(key,300,json.dumps(data)); return data
@app.put("/events/{event_id}")
def update_event(event_id:int,data:EventIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Organizer"))):
    ev=event_access(db,event_id,user)
    for k,v in data.model_dump().items(): setattr(ev,k,v)
    db.commit(); cache_delete(event_id); return {"message":"Event updated"}
@app.post("/events/{event_id}/shows",status_code=201)
def create_show(event_id:int,data:ShowIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Organizer"))):
    ev=event_access(db,event_id,user)
    if not user.is_approved: raise HTTPException(403,"Organizer approval required")
    start=datetime.combine(data.show_date,data.start_time)
    if start<=datetime.now(): raise HTTPException(422,"Show must be in the future")
    if ev.status not in ("Draft","Published"): raise HTTPException(400,"Event cannot have shows in this state")
    screen=db.get(Screen,data.screen_id)
    if not screen: raise HTTPException(404,"Screen not found")
    end=(start+timedelta(minutes=ev.duration_minutes)).time()
    for other in db.query(Show).filter_by(screen_id=data.screen_id,show_date=data.show_date,status="Scheduled").all():
        a=datetime.combine(other.show_date,other.start_time); b=datetime.combine(other.show_date,other.end_time)
        if start < b+timedelta(minutes=15) and start+timedelta(minutes=ev.duration_minutes)+timedelta(minutes=15)>a: raise HTTPException(409,"Show overlaps another show or its cleaning gap")
    cats={s.category for s in db.query(Seat).filter_by(screen_id=screen.id,is_active=True).all()}
    if not cats or not cats.issubset(set(data.prices)): raise HTTPException(422,"Provide a price for every active seat category")
    if any(float(p)<=0 for p in data.prices.values()): raise HTTPException(422,"Prices must be greater than zero")
    sh=Show(event_id=event_id,screen_id=data.screen_id,show_date=data.show_date,start_time=data.start_time,end_time=end,booking_closes_at=start-timedelta(minutes=30),status="Scheduled")
    db.add(sh); db.flush()
    for cat,price in data.prices.items(): db.add(SeatCategoryPrice(show_id=sh.id,category=cat,price=price))
    db.commit(); db.refresh(sh); cache_delete(event_id)
    return {"id":sh.id,"event_id":event_id,"show_date":str(sh.show_date),"start_time":str(sh.start_time),"end_time":str(sh.end_time),"booking_closes_at":str(sh.booking_closes_at)}
@app.get("/events/{event_id}/shows")
def event_shows(event_id:int,skip:int=0,limit:int=20,db:Session=Depends(get_db)):
    return [{"id":s.id,"show_date":str(s.show_date),"start_time":str(s.start_time),"end_time":str(s.end_time),"screen_id":s.screen_id,"status":s.status} for s in db.query(Show).filter_by(event_id=event_id).offset(skip).limit(min(limit,100)).all()]
@app.put("/shows/{show_id}")
def update_show(show_id:int,data:ShowIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Organizer"))):
    sh=show_or_404(db,show_id); ev=event_access(db,sh.event_id,user)
    if db.query(Booking).filter_by(show_id=show_id,status="Confirmed").first(): raise HTTPException(409,"Cannot change prices after confirmed booking")
    sh.screen_id=data.screen_id; sh.show_date=data.show_date; sh.start_time=data.start_time
    st=datetime.combine(data.show_date,data.start_time); sh.end_time=(st+timedelta(minutes=ev.duration_minutes)).time(); sh.booking_closes_at=st-timedelta(minutes=30)
    for p in list(sh.prices): db.delete(p)
    for cat,price in data.prices.items(): db.add(SeatCategoryPrice(show_id=show_id,category=cat,price=price))
    db.commit(); cache_delete(sh.event_id); return {"message":"Show updated"}
@app.put("/shows/{show_id}/cancel")
async def cancel_show(show_id:int,background:BackgroundTasks,db:Session=Depends(get_db),user:User=Depends(require_roles("Organizer"))):
    sh=show_or_404(db,show_id); event_access(db,sh.event_id,user); sh.status="Cancelled"
    bookings=db.query(Booking).filter_by(show_id=show_id,status="Confirmed").all()
    for b in bookings:
        refund=Refund(booking_id=b.id,amount=b.grand_total,reason="Show Cancellation",status="Processed"); db.add(refund)
        b.status="Cancelled"
        for p in b.payments: p.status="Refunded"
        background.add_task(send_email,b.customer.email,"Show cancelled","Your show was cancelled. Full refund has been processed.")
    db.commit(); cache_delete(sh.event_id)
    return {"message":"Show cancelled","bookings_refunded":len(bookings)}
@app.get("/shows/{show_id}/seats")
def show_seats(show_id:int,db:Session=Depends(get_db)):
    sh=show_or_404(db,show_id)
    prices={p.category:p.price for p in sh.prices}
    booked={bs.seat_id for bs in db.query(BookedSeat).filter_by(show_id=show_id).all()}
    out=[]
    for seat in db.query(Seat).filter_by(screen_id=sh.screen_id).order_by(Seat.row_label,Seat.seat_number):
        owner=redis_client.get(lock_key(show_id,seat.id))
        status="Unavailable" if not seat.is_active else "Booked" if seat.id in booked else "Locked" if owner else "Available"
        out.append({"seat_id":seat.id,"row":seat.row_label,"seat_number":seat.seat_number,"category":seat.category,"price":prices.get(seat.category),"status":status})
    return out
@app.post("/shows/{show_id}/lock-seats")
async def lock_seats(show_id:int,data:LockSeatsIn,request:Request,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    key=f"rate:lock:{user.id}"; count=redis_client.incr(key)
    if count==1: redis_client.expire(key,60)
    if count>5: raise HTTPException(429,detail=f"Seat-lock rate limit reached. Retry in {redis_client.ttl(key)} seconds")
    sh=show_or_404(db,show_id)
    if sh.status!="Scheduled" or now().replace(tzinfo=None)>=sh.booking_closes_at.replace(tzinfo=None): raise HTTPException(400,"Seat locking is closed")
    if len(set(data.seat_ids))!=len(data.seat_ids): raise HTTPException(422,"Duplicate seat IDs")
    booked={x.seat_id for x in db.query(BookedSeat).filter_by(show_id=show_id).all()}
    existing=db.query(Booking).filter_by(show_id=show_id,customer_id=user.id,status="Pending Payment").all()
    existing_count=sum(b.seat_count for b in existing)
    if existing_count+len(data.seat_ids)>10: raise HTTPException(400,"Maximum 10 seats per customer per show")
    seats=db.query(Seat).filter(Seat.id.in_(data.seat_ids),Seat.screen_id==sh.screen_id).all()
    if len(seats)!=len(data.seat_ids): raise HTTPException(404,"One or more seats not found on this show screen")
    unavailable=[]; acquired=[]
    for seat in seats:
        if not seat.is_active or seat.id in booked or not redis_client.set(lock_key(show_id,seat.id),str(user.id),nx=True,ex=600):
            unavailable.append(f"{seat.row_label}{seat.seat_number}")
        else: acquired.append(seat.id)
    if unavailable:
        for sid in acquired:
            if redis_client.get(lock_key(show_id,sid))==str(user.id): redis_client.delete(lock_key(show_id,sid))
        raise HTTPException(409,detail={"message":"Some seats are unavailable; no seats were locked","seats":unavailable})
    # Basic empty-seat rule: disallow isolated single-seat gaps in the same row.
    all_seats=db.query(Seat).filter(Seat.screen_id==sh.screen_id).all()
    selected={(s.row_label,s.seat_number) for s in seats}
    taken=[]
    for s in all_seats:
        if s.id in booked or (redis_client.get(lock_key(show_id,s.id)) and s.id not in acquired): taken.append((s.row_label,s.seat_number))
    for row,num in selected:
        neighbors=sorted(n for r,n in taken if r==row)
        for n in neighbors:
            if abs(n-num)==2 and (row,num+(n-num)//2) not in selected:
                for sid in acquired: redis_client.delete(lock_key(show_id,sid))
                raise HTTPException(409,detail="Selection would leave a single empty seat")
    expires=now()+timedelta(minutes=10)
    await broadcast(show_id,{"type":"seat_lock","seat_ids":acquired,"user_id":user.id,"status":"Locked"})
    return {"show_id":show_id,"seat_ids":acquired,"expires_at":expires.isoformat(),"expires_in_seconds":600}
@app.delete("/shows/{show_id}/lock-seats")
async def release_locks(show_id:int,data:LockSeatsIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    released=[]
    for sid in data.seat_ids:
        key=lock_key(show_id,sid)
        if redis_client.get(key)==str(user.id): redis_client.delete(key); released.append(sid)
    await broadcast(show_id,{"type":"seat_release","seat_ids":released,"status":"Available"})
    return {"released":released}
@app.websocket("/ws/shows/{show_id}/seats")
async def seat_ws(ws:WebSocket,show_id:int,token:str):
    from jose import jwt
    try:
        payload=jwt.decode(token, __import__("app.config",fromlist=["settings"]).settings.JWT_SECRET_KEY, algorithms=[__import__("app.config",fromlist=["settings"]).settings.JWT_ALGORITHM])
        uid=int(payload["sub"])
    except Exception:
        await ws.close(code=1008); return
    db=SessionLocal()
    try:
        sh=db.get(Show,show_id)
        if not sh: await ws.close(code=1008); return
        await ws.accept(); connections.setdefault(show_id,set()).add(ws)
        seats=[]
        booked={x.seat_id for x in db.query(BookedSeat).filter_by(show_id=show_id).all()}
        prices={p.category:p.price for p in sh.prices}
        for s in db.query(Seat).filter_by(screen_id=sh.screen_id).order_by(Seat.row_label,Seat.seat_number):
            owner=redis_client.get(lock_key(show_id,s.id))
            state="Unavailable" if not s.is_active else "Booked" if s.id in booked else "Yours" if owner==str(uid) else "Locked" if owner else "Available"
            seats.append({"seat_id":s.id,"row":s.row_label,"seat_number":s.seat_number,"category":s.category,"price":prices.get(s.category),"status":state})
        await ws.send_json({"type":"initial_map","seats":seats})
        while True: await ws.receive_text()
    except WebSocketDisconnect: pass
    finally:
        connections.get(show_id,set()).discard(ws); db.close()
@app.post("/bookings",status_code=201)
def create_booking(show_id:int,data:BookingIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    sh=show_or_404(db,show_id)
    if sh.status!="Scheduled": raise HTTPException(400,"Show is not bookable")
    if len(data.seat_ids)>10: raise HTTPException(400,"Maximum 10 seats")
    seats=db.query(Seat).filter(Seat.id.in_(data.seat_ids),Seat.screen_id==sh.screen_id).all()
    if len(seats)!=len(data.seat_ids): raise HTTPException(404,"Seat not found")
    for seat in seats:
        if redis_client.get(lock_key(show_id,seat.id))!=str(user.id): raise HTTPException(409,f"Seat {seat.row_label}{seat.seat_number} is not locked by you")
    prices={p.category:p.price for p in sh.prices}
    ticket_total=sum(prices[s.category] for s in seats); fee=30*len(seats); gst=round((ticket_total+fee)*0.18,2); total=round(ticket_total+fee+gst,2)
    ttl=min([redis_client.ttl(lock_key(show_id,s.id)) for s in seats])
    expires=now()+timedelta(seconds=max(1,ttl))
    number=f"BKG-{now().strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"
    b=Booking(booking_number=number,customer_id=user.id,show_id=show_id,seat_count=len(seats),ticket_total=ticket_total,convenience_fee=fee,gst_amount=gst,grand_total=total,status="Pending Payment",expires_at=expires)
    db.add(b); db.commit(); db.refresh(b)
    redis_client.setex(f"booking:seats:{b.id}", max(1, ttl), json.dumps([{"seat_id":s.id,"price":prices[s.category]} for s in seats]))
    return {"id":b.id,"booking_number":number,"status":b.status,"seat_count":b.seat_count,"ticket_total":ticket_total,"convenience_fee":fee,"gst_amount":gst,"grand_total":total,"expires_at":expires.isoformat()}
@app.get("/bookings")
def list_bookings(status:str|None=None,show_id:int|None=None,from_date:date|None=None,to_date:date|None=None,skip:int=0,limit:int=20,db:Session=Depends(get_db),user:User=Depends(current_user)):
    q=db.query(Booking)
    if user.role=="Customer": q=q.filter_by(customer_id=user.id)
    elif user.role=="Organizer": q=q.join(Show).join(Event).filter(Event.organizer_id==user.id)
    elif user.role not in ("Admin",): raise HTTPException(403,"Not allowed")
    if status: q=q.filter(Booking.status==status)
    if show_id: q=q.filter(Booking.show_id==show_id)
    if from_date: q=q.join(Show).filter(Show.show_date>=from_date)
    if to_date: q=q.join(Show).filter(Show.show_date<=to_date)
    return [{"id":b.id,"booking_number":b.booking_number,"show_id":b.show_id,"seat_count":b.seat_count,"grand_total":b.grand_total,"status":b.status} for b in q.order_by(Booking.id.desc()).offset(skip).limit(min(limit,100)).all()]
@app.get("/bookings/{booking_id}")
def booking_detail(booking_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if user.role=="Customer" and b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    if user.role=="Organizer" and b.show.event.organizer_id!=user.id: raise HTTPException(403,"Not your event")
    return {"id":b.id,"booking_number":b.booking_number,"show_id":b.show_id,"status":b.status,"seat_count":b.seat_count,"ticket_total":b.ticket_total,"convenience_fee":b.convenience_fee,"gst_amount":b.gst_amount,"grand_total":b.grand_total,"expires_at":str(b.expires_at)}
@app.post("/bookings/{booking_id}/pay")
def pay(booking_id:int,data:PayIn,background:BackgroundTasks,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    if b.status!="Pending Payment": raise HTTPException(409,"Booking is not awaiting payment")
    if b.expires_at.replace(tzinfo=timezone.utc)<now(): b.status="Expired"; db.commit(); raise HTTPException(409,"Booking expired")
    if data.payment_method not in ("UPI","Card","Net Banking") or data.simulate_status not in ("Success","Failed"): raise HTTPException(422,"Invalid payment_method or simulate_status")
    payment=Payment(booking_id=b.id,transaction_id=f"TXN-{uuid4().hex[:12].upper()}",amount=b.grand_total,payment_method=data.payment_method,status=data.simulate_status,paid_at=now() if data.simulate_status=="Success" else None)
    db.add(payment)
    if data.simulate_status=="Failed":
        db.commit(); background.add_task(send_email,user.email,"Payment failed",f"Payment for {b.booking_number} failed. You may retry before lock expiry.")
        return {"payment_status":"Failed","booking_status":b.status,"transaction_id":payment.transaction_id}
    selection=json.loads(redis_client.get(f"booking:seats:{b.id}") or "[]")
    lost=[x["seat_id"] for x in selection if redis_client.get(lock_key(b.show_id,x["seat_id"]))!=str(user.id)]
    if lost:
        payment.status="Refunded"; b.status="Cancelled"; db.add(Refund(booking_id=b.id,amount=b.grand_total,reason="Late Payment",status="Processed")); db.commit()
        return {"payment_status":"Refunded","booking_status":"Cancelled","message":"Lock expired; mock refund recorded"}
    b.status="Confirmed"; b.confirmed_at=now()
    for item in selection:
        db.add(BookedSeat(booking_id=b.id,show_id=b.show_id,seat_id=item["seat_id"],price=item["price"]))
    db.commit()
    for item in selection: redis_client.delete(lock_key(b.show_id,item["seat_id"]))
    redis_client.delete(f"booking:seats:{b.id}")
    sh=b.show; venue=sh.screen.venue
    seats=db.query(BookedSeat).filter_by(booking_id=b.id).all()
    seat_names=[f"{x.seat.row_label}{x.seat.seat_number}" for x in seats]
    path=make_ticket(b,sh.event.title,venue.name,sh.screen.name,sh.show_date,sh.start_time,seat_names)
    background.add_task(send_email,user.email,"Booking confirmed",f"Booking {b.booking_number} confirmed. Seats: {', '.join(seat_names)}",path)
    return {"payment_status":"Success","booking_status":"Confirmed","transaction_id":payment.transaction_id,"ticket_url":f"/bookings/{b.id}/ticket","grand_total":b.grand_total}
@app.get("/bookings/{booking_id}/payments")
def payments(booking_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if user.role=="Customer" and b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    return [{"transaction_id":p.transaction_id,"amount":p.amount,"payment_method":p.payment_method,"status":p.status,"paid_at":str(p.paid_at)} for p in b.payments]
@app.get("/bookings/{booking_id}/ticket")
def download_ticket(booking_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer","Admin","Staff"))):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if user.role=="Customer" and b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    if b.status!="Confirmed": raise HTTPException(400,"Ticket is available only for confirmed bookings")
    path=Path("tickets")/f"{b.booking_number}.pdf"
    if not path.exists(): raise HTTPException(404,"Ticket PDF not found; ticket generation may have failed")
    return FileResponse(path,media_type="application/pdf",filename=path.name)
@app.post("/tickets/scan")
def scan_ticket(data:ScanIn,db:Session=Depends(get_db),staff:User=Depends(require_roles("Staff"))):
    number=verify_ticket(data.qr_value)
    if not number: raise HTTPException(400,"Invalid QR signature")
    b=db.query(Booking).filter_by(booking_number=number).first()
    if not b or b.status!="Confirmed": raise HTTPException(400,"Ticket booking is invalid, cancelled, or expired")
    sh=b.show
    if sh.screen.venue.id!=staff.assigned_venue_id: raise HTTPException(403,"Staff can scan only at their assigned venue")
    start=datetime.combine(sh.show_date,sh.start_time).replace(tzinfo=timezone.utc)
    end=datetime.combine(sh.show_date,sh.end_time).replace(tzinfo=timezone.utc)
    if now()<start-timedelta(hours=1) or now()>end: raise HTTPException(400,"Scanning is outside the permitted entry window")
    key=f"ticket:used:{b.id}"
    if not redis_client.set(key,now().isoformat(),nx=True,ex=86400): raise HTTPException(409, f"Ticket already used at {redis_client.get(key)}")
    seats=[f"{x.seat.row_label}{x.seat.seat_number}" for x in b.seats]
    return {"message":"Entry accepted","customer_name":b.customer.full_name,"seats":seats,"seat_count":len(seats)}
@app.get("/shows/{show_id}/entries")
def entries(show_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin","Organizer"))):
    sh=show_or_404(db,show_id)
    if user.role=="Organizer" and sh.event.organizer_id!=user.id: raise HTTPException(403,"Not your event")
    confirmed=db.query(Booking).filter_by(show_id=show_id,status="Confirmed").count()
    scanned=sum(1 for k in redis_client.scan_iter("ticket:used:*"))
    return {"show_id":show_id,"confirmed_bookings":confirmed,"scanned_ticket_keys_total":scanned}
@app.put("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    if b.status not in ("Confirmed","Pending Payment"): raise HTTPException(409,"Booking cannot be cancelled")
    sh=b.show; start=datetime.combine(sh.show_date,sh.start_time).replace(tzinfo=timezone.utc); delta=start-now()
    if b.status=="Confirmed" and delta<timedelta(hours=2): raise HTTPException(400,"Cancellation is not allowed less than 2 hours before show")
    refund_amount=0
    if b.status=="Confirmed":
        refund_amount=b.ticket_total if delta>timedelta(hours=24) else round(b.ticket_total*0.5,2)
        db.add(Refund(booking_id=b.id,amount=refund_amount,reason="Customer Cancellation",status="Processed"))
        for p in b.payments: p.status="Refunded"
    b.status="Cancelled"; db.commit()
    for x in b.seats: redis_client.delete(lock_key(b.show_id,x.seat_id))
    return {"status":"Cancelled","refund_amount":refund_amount}
@app.get("/bookings/{booking_id}/refunds")
def booking_refunds(booking_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    b=db.get(Booking,booking_id)
    if not b: raise HTTPException(404,"Booking not found")
    if user.role=="Customer" and b.customer_id!=user.id: raise HTTPException(403,"Not your booking")
    return [{"amount":r.amount,"reason":r.reason,"status":r.status,"refunded_at":str(r.refunded_at)} for r in b.refunds]
@app.post("/shows/{show_id}/waitlist",status_code=201)
def join_waitlist(show_id:int,data:WaitlistIn,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer"))):
    sh=show_or_404(db,show_id)
    available=sum(1 for s in db.query(Seat).filter_by(screen_id=sh.screen_id,is_active=True).all() if not redis_client.get(lock_key(show_id,s.id)) and not db.query(BookedSeat).filter_by(show_id=show_id,seat_id=s.id).first())
    if available: raise HTTPException(400,"Waitlist is only for sold-out shows")
    if db.query(Booking).filter_by(show_id=show_id,customer_id=user.id).filter(Booking.status.in_(["Confirmed","Pending Payment"])).first(): raise HTTPException(409,"You already have a booking for this show")
    w=Waitlist(show_id=show_id,customer_id=user.id,seat_count=data.seat_count,preferred_category=data.preferred_category); db.add(w)
    try: db.commit()
    except Exception: db.rollback(); raise HTTPException(409,"Already on this show's waitlist")
    return {"id":w.id,"position":db.query(Waitlist).filter_by(show_id=show_id).filter(Waitlist.id<=w.id).count()}
@app.get("/shows/{show_id}/waitlist")
def list_waitlist(show_id:int,skip:int=0,limit:int=20,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin","Organizer"))):
    sh=show_or_404(db,show_id)
    if user.role=="Organizer" and sh.event.organizer_id!=user.id: raise HTTPException(403,"Not your event")
    return [{"id":w.id,"customer_id":w.customer_id,"seat_count":w.seat_count,"preferred_category":w.preferred_category} for w in db.query(Waitlist).filter_by(show_id=show_id).order_by(Waitlist.id).offset(skip).limit(min(limit,100)).all()]
@app.delete("/waitlist/{waitlist_id}")
def leave_waitlist(waitlist_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("Customer","Admin"))):
    w=db.get(Waitlist,waitlist_id)
    if not w: raise HTTPException(404,"Waitlist entry not found")
    if user.role=="Customer" and w.customer_id!=user.id: raise HTTPException(403,"Not your waitlist entry")
    db.delete(w); db.commit(); return {"message":"Removed from waitlist"}
@app.get("/reports/admin-dashboard")
def admin_dashboard(db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    today=date.today()
    bookings=db.query(Booking).join(Show).filter(Show.show_date==today).all()
    return {"today_bookings":len(bookings),"revenue":round(sum(b.grand_total for b in bookings if b.status=="Confirmed"),2),"refunds":round(sum(r.amount for r in db.query(Refund).all()),2),"active_events":db.query(Event).filter_by(status="Published").count(),"tickets_scanned":sum(1 for _ in redis_client.scan_iter("ticket:used:*"))}
@app.get("/reports/event-sales")
def event_sales(from_date:date|None=None,to_date:date|None=None,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin","Organizer"))):
    q=db.query(Booking).join(Show).join(Event)
    if user.role=="Organizer": q=q.filter(Event.organizer_id==user.id)
    if from_date: q=q.filter(Show.show_date>=from_date)
    if to_date: q=q.filter(Show.show_date<=to_date)
    rows={}
    for b in q.all():
        key=f"{b.show.event.title} / show {b.show_id}"; r=rows.setdefault(key,{"tickets_sold":0,"revenue":0.0,"refunds":0.0})
        if b.status=="Confirmed": r["tickets_sold"]+=b.seat_count; r["revenue"]+=b.grand_total
        r["refunds"]+=sum(x.amount for x in b.refunds)
    return rows
@app.get("/reports/occupancy")
def occupancy(db:Session=Depends(get_db),user:User=Depends(require_roles("Admin","Organizer"))):
    out=[]
    for sh in db.query(Show).all():
        if user.role=="Organizer" and sh.event.organizer_id!=user.id: continue
        total=db.query(Seat).filter_by(screen_id=sh.screen_id,is_active=True).count()
        booked=db.query(BookedSeat).filter_by(show_id=sh.id).count()
        out.append({"show_id":sh.id,"event":sh.event.title,"booked_seats":booked,"total_seats":total,"occupancy_percent":round(booked*100/total,2) if total else 0})
    return out
@app.get("/reports/top-events")
def top_events(from_date:date|None=None,to_date:date|None=None,db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    q=db.query(Booking).join(Show).join(Event).filter(Booking.status=="Confirmed")
    if from_date:q=q.filter(Show.show_date>=from_date)
    if to_date:q=q.filter(Show.show_date<=to_date)
    result={}
    for b in q.all(): result[b.show.event.title]=result.get(b.show.event.title,0)+b.seat_count
    return sorted([{"event":k,"tickets_sold":v} for k,v in result.items()],key=lambda x:x["tickets_sold"],reverse=True)[:10]
@app.get("/reports/refunds")
def refund_report(db:Session=Depends(get_db),user:User=Depends(require_roles("Admin"))):
    result={}
    for r in db.query(Refund).all():
        x=result.setdefault(r.reason,{"count":0,"amount":0.0}); x["count"]+=1; x["amount"]+=r.amount
    return result
@app.get("/seat-map-test",response_class=HTMLResponse)
def seat_map_test():
    return FileResponse("static/seat_map_test.html")
@app.on_event("startup")
def startup_jobs():
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler=BackgroundScheduler()
    def expire_bookings():
        db=SessionLocal()
        try:
            for b in db.query(Booking).filter_by(status="Pending Payment").all():
                if b.expires_at.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc):
                    b.status="Expired"
                    for x in b.seats: redis_client.delete(lock_key(b.show_id,x.seat_id))
            db.commit()
        except Exception: db.rollback(); logging.exception("Expire job failed")
        finally: db.close()
    def complete_shows():
        db=SessionLocal()
        try:
            for s in db.query(Show).filter_by(status="Scheduled").all():
                end=datetime.combine(s.show_date,s.end_time).replace(tzinfo=timezone.utc)
                if end<datetime.now(timezone.utc): s.status="Completed"
            db.commit()
        except Exception: db.rollback(); logging.exception("Show completion job failed")
        finally: db.close()
    scheduler.add_job(expire_bookings,"interval",minutes=1,id="expire_bookings",replace_existing=True)
    scheduler.add_job(complete_shows,"interval",minutes=15,id="complete_shows",replace_existing=True)
    scheduler.start()
    app.state.scheduler=scheduler
@app.on_event("shutdown")
def shutdown_jobs():
    scheduler=getattr(app.state,"scheduler",None)
    if scheduler: scheduler.shutdown(wait=False)
