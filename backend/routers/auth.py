from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from models.db import create_user, get_user_by_email
from services.auth import hash_password, verify_password, create_session
from services.rate_limiter import limiter

router = APIRouter(prefix="/api/auth")


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest):
    if "@" not in body.email:
        raise HTTPException(400, "Email không hợp lệ")
    if not (8 <= len(body.password) <= 72):
        raise HTTPException(400, "Mật khẩu phải có từ 8 đến 72 ký tự")

    existing = await get_user_by_email(body.email)
    if existing:
        raise HTTPException(409, "Email đã được đăng ký")

    await create_user(body.email, hash_password(body.password))
    return {"message": "Đăng ký thành công"}


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest):
    user = await get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Email hoặc mật khẩu không đúng")

    token = await create_session(str(user["id"]))
    return {"session_token": token}
