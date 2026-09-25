from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr
from models.db import create_user, get_user_by_email
from services.auth import (
    hash_password, verify_password, create_session, end_login,
    is_login_locked, record_login_failure, clear_login_failures,
    DUMMY_PASSWORD_HASH
)
from services.rate_limiter import limiter

router = APIRouter(prefix="/api/auth")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest):
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
    if await is_login_locked(body.email):
        raise HTTPException(429, "Sai mật khẩu quá nhiều lần, vui lòng thử lại sau ít phút")

    user = await get_user_by_email(body.email)
    # Always run the bcrypt comparison, even for an email that doesn't exist,
    # against a fixed dummy hash -- otherwise a nonexistent-account login
    # returns near-instantly while a wrong-password one pays the bcrypt cost,
    # and that timing gap lets an attacker enumerate registered emails.
    password_ok = verify_password(body.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)

    if not user or not password_ok:
        await record_login_failure(body.email)
        raise HTTPException(401, "Email hoặc mật khẩu không đúng")

    await clear_login_failures(body.email)
    token = await create_session(str(user["id"]))
    return {"session_token": token}


@router.post("/logout")
async def logout(request: Request):
    tokens = []
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        tokens.append(auth_header.removeprefix("Bearer ").strip())
    # the post-MFA JWT, when the client has one, so it is revoked along with the session
    access_token = request.headers.get("X-Access-Token", "").strip()
    if access_token:
        tokens.append(access_token)
    await end_login(tokens)
    return {"message": "Đã đăng xuất"}
