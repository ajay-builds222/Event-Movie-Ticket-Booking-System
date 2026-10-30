from datetime import date, time
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
class RegisterIn(BaseModel):
    full_name:str=Field(min_length=2,max_length=120)
    email:EmailStr
    phone:str=Field(pattern=r"^\d{10}$")
    password:str=Field(min_length=8)
    role:str="Customer"
class LoginIn(BaseModel):
    email:EmailStr
    password:str
class VenueIn(BaseModel):
    name:str
    city:str
    address:str
    is_active:bool=True
class ScreenIn(BaseModel):
    name:str
class BulkSeatsIn(BaseModel):
    rows:List[str]
    seats_per_row:int=Field(ge=1,le=50)
    category:str
class EventIn(BaseModel):
    title:str
    category:str
    language:str
    description:str=""
    duration_minutes:int=Field(ge=30,le=300)
    age_rating:str="U"
    status:str="Draft"
class ShowIn(BaseModel):
    screen_id:int
    show_date:date
    start_time:time
    prices:dict[str,float]
class LockSeatsIn(BaseModel):
    seat_ids:List[int]=Field(min_length=1,max_length=10)
class BookingIn(BaseModel):
    seat_ids:List[int]=Field(min_length=1,max_length=10)
class PayIn(BaseModel):
    payment_method:str
    simulate_status:str
class ScanIn(BaseModel):
    qr_value:str
class WaitlistIn(BaseModel):
    seat_count:int=Field(ge=1,le=10)
    preferred_category:str
