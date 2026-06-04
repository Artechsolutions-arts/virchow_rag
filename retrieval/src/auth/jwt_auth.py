from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.config import cfg

bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: str, email: str, dept_id: str, role: str = "user", is_super_admin: bool = False) -> str:
    expire = datetime.utcnow() + timedelta(hours=cfg.jwt_expire_hours)
    payload = {"sub": user_id, "email": email, "dept_id": dept_id, "role": role, "is_super_admin": is_super_admin, "exp": expire}
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_user(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)) -> dict:
    return decode_token(credentials.credentials)


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency that requires the requesting user to have role='admin' or is_super_admin."""
    if user.get("role") != "admin" and not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
