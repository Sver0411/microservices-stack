"""
智慧图书馆 · 知识服务平台 — 后端 API
FastAPI + SQLAlchemy + SQLite + JWT + bcrypt
"""
import os, re, time, json, logging
from datetime import datetime, timedelta, date
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, Text, DateTime, Date, ForeignKey, event
from sqlalchemy.orm import sessionmaker, Session, declarative_base, relationship
from passlib.context import CryptContext
import jwt as pyjwt

# ═══════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
DB_PATH = os.environ.get("DB_PATH", "/data/library.db")
BCRYPT_ROUNDS = 12
RATE_LIMIT_PER_MINUTE = 10
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=BCRYPT_ROUNDS)
security = HTTPBearer(auto_error=False)

# ═══════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ═══════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════
class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class Category(Base):
    __tablename__ = "categories"
    id = Column(String, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    parent = Column(String, default="")
    desc = Column(Text, default="")

class Book(Base):
    __tablename__ = "books"
    id = Column(String, primary_key=True)
    isbn = Column(String, default="")
    title = Column(String, nullable=False)
    author = Column(String, default="")
    publisher = Column(String, default="")
    category = Column(String, default="")
    pub_date = Column(String, default="")
    price = Column(Float, default=0)
    total = Column(Integer, default=1)
    available = Column(Integer, default=1)
    borrowed = Column(Integer, default=0)
    shelf = Column(String, default="")
    status = Column(String, default="可借")
    desc = Column(Text, default="")
    cover = Column(String, default="")

class Reader(Base):
    __tablename__ = "readers"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    email = Column(String, default="")
    dept = Column(String, default="")
    reg_date = Column(String, default="")
    borrowed = Column(Integer, default=0)
    max_borrow = Column(Integer, default=5)
    status = Column(String, default="正常")

class Borrow(Base):
    __tablename__ = "borrows"
    id = Column(String, primary_key=True)
    reader_id = Column(String, ForeignKey("readers.id"), nullable=False)
    book_id = Column(String, ForeignKey("books.id"), nullable=False)
    borrow_date = Column(String, nullable=False)
    due_date = Column(String, nullable=False)
    return_date = Column(String, default="")
    status = Column(String, default="借出中")
    renew_count = Column(Integer, default=0)
    return_book_status = Column(String, default="正常")

class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(String, primary_key=True)
    reader_id = Column(String, ForeignKey("readers.id"), nullable=False)
    book_id = Column(String, ForeignKey("books.id"), nullable=False)
    res_date = Column(String, nullable=False)
    valid_until = Column(String, nullable=False)
    status = Column(String, default="待处理")
    queue_pos = Column(Integer, default=1)

class Fine(Base):
    __tablename__ = "fines"
    id = Column(String, primary_key=True)
    borrow_id = Column(String, default="")
    reader_id = Column(String, default="")
    book_id = Column(String, default="")
    overdue_days = Column(Integer, default=0)
    amount = Column(Float, default=0)
    status = Column(String, default="未缴纳")

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, default="reader")
    status = Column(String, default="启用")
    reader_binding = Column(String, default="")

class Announcement(Base):
    __tablename__ = "announcements"
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    content = Column(Text, default="")
    date = Column(String, default="")
    status = Column(String, default="已发布")
    top = Column(Boolean, default=False)

class Log(Base):
    __tablename__ = "logs"
    id = Column(String, primary_key=True)
    time = Column(String, default="")
    operator = Column(String, default="")
    action = Column(String, default="")
    target = Column(String, default="")
    detail = Column(Text, default="")

class InventoryLog(Base):
    __tablename__ = "inventory_logs"
    id = Column(String, primary_key=True)
    book_id = Column(String, default="")
    type = Column(String, default="入库")
    qty = Column(Integer, default=1)
    date = Column(String, default="")
    operator = Column(String, default="")
    note = Column(Text, default="")

class IdCounter(Base):
    __tablename__ = "id_counters"
    key = Column(String, primary_key=True)
    value = Column(Integer, default=1)

# ═══════════════════════════════════════════
# LIFECYCLE — Create tables + seed data
# ═══════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).first():
            _seed(db)
    finally:
        db.close()
    yield

app = FastAPI(title="智慧图书馆 API", version="1.0.0", lifespan=lifespan)

# ═══════════════════════════════════════════
# CORS
# ═══════════════════════════════════════════
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════
rate_store: dict = {}
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/auth/login"):
        client_ip = request.client.host
        now = time.time()
        window = now - 60
        rate_store.setdefault(client_ip, []).append(now)
        rate_store[client_ip] = [t for t in rate_store[client_ip] if t > window]
        if len(rate_store[client_ip]) > RATE_LIMIT_PER_MINUTE:
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    response = await call_next(request)
    return response

# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + expires_delta, "iat": datetime.utcnow()})
    return pyjwt.encode(to_encode, SECRET_KEY, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return pyjwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token 无效")

def gen_id(db: Session, key: str, prefix: str) -> str:
    counter = db.query(IdCounter).filter(IdCounter.key == key).with_for_update().first()
    if not counter:
        counter = IdCounter(key=key, value=1)
        db.add(counter)
        db.flush()
    n = counter.value
    counter.value = n + 1
    db.commit()
    return f"{prefix}{n:03d}"

def today_str() -> str:
    return date.today().isoformat()

def add_days_str(ds: str, days: int) -> str:
    d = datetime.strptime(ds, "%Y-%m-%d") + timedelta(days=days)
    return d.strftime("%Y-%m-%d")

def days_between(d1: str, d2: str) -> int:
    return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def html_escape(text: str) -> str:
    if not text:
        return text
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")

def add_log(db: Session, operator: str, action: str, target: str, detail: str):
    log = Log(
        id=gen_id(db, "log", "L"),
        time=now_str(),
        operator=operator,
        action=action,
        target=target,
        detail=detail
    )
    db.add(log)
    db.commit()

# ═══════════════════════════════════════════
# PERMISSION SYSTEM
# ═══════════════════════════════════════════
ADMIN_PERMS = {"all"}
LIBRARIAN_PERMS = {"books", "categories", "borrow", "return", "renew", "reserve", "fines", "inventory", "announcements", "logs", "dashboard"}
READER_PERMS = {"reader_dashboard", "reader_books", "reader_borrow", "reader_myborrows", "reader_return", "reader_renew", "reader_reserve", "reader_fines", "reader_profile", "reader_announcements"}

def get_required_perms(route_name: str) -> set:
    """Map route operations to permission sets."""
    # Admin-only routes
    admin_routes = {"users", "roles", "system"}
    librarian_routes = {"books", "categories", "borrows", "reservations", "fines", "inventory", "announcements", "logs", "settings"}
    reader_routes = {"reader", "reader_dashboard"}

    for prefix in admin_routes:
        if route_name.startswith(prefix):
            return ADMIN_PERMS
    for prefix in librarian_routes:
        if route_name.startswith(prefix):
            return LIBRARIAN_PERMS
    return READER_PERMS

def require_role(roles: List[str]):
    """Dependency: check if current user has required role."""
    def checker(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        db: Session = Depends(get_db)
    ) -> User:
        if not credentials:
            raise HTTPException(status_code=401, detail="未提供认证凭据")
        payload = decode_token(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token 无效")
        user = db.query(User).filter(User.id == user_id).first()
        if not user or user.status != "启用":
            raise HTTPException(status_code=401, detail="用户不存在或已禁用")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="无权限访问")
        return user
    return checker

def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        user = db.query(User).filter(User.id == payload.get("sub")).first()
        if user and user.status == "启用":
            return user
    except:
        pass
    return None

# ═══════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=4, max_length=20)
    password: str = Field(..., min_length=4, max_length=100)
    name: str = Field(..., min_length=1, max_length=50)
    phone: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=100)
    dept: str = Field(default="", max_length=100)

    @validator("username")
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_]{4,20}$', v):
            raise ValueError("账号需4-20位字母、数字或下划线")
        return v

    @validator("email")
    def validate_email(cls, v):
        if v and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', v):
            raise ValueError("邮箱格式不正确")
        return v

class RefreshRequest(BaseModel):
    refresh_token: str

class BookCreate(BaseModel):
    isbn: str = Field(default="")
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(default="")
    publisher: str = Field(default="")
    category: str = Field(default="")
    pub_date: str = Field(default="")
    price: float = Field(default=0)
    total: int = Field(default=1, ge=1)
    shelf: str = Field(default="")
    desc: str = Field(default="")

class BookUpdate(BaseModel):
    isbn: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    category: Optional[str] = None
    pub_date: Optional[str] = None
    price: Optional[float] = None
    total: Optional[int] = None
    shelf: Optional[str] = None
    status: Optional[str] = None
    desc: Optional[str] = None

class ReaderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    phone: str = Field(default="")
    email: str = Field(default="")
    dept: str = Field(default="")
    max_borrow: int = Field(default=5, ge=1)

class ReaderUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    dept: Optional[str] = None
    max_borrow: Optional[int] = None
    status: Optional[str] = None

class BorrowCreate(BaseModel):
    reader_id: str
    book_id: str

class BorrowReturn(BaseModel):
    return_book_status: str = Field(default="正常")

class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    role: str = "librarian"
    reader_binding: str = ""

class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    password: Optional[str] = None
    reader_binding: Optional[str] = None

class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(default="")
    top: bool = False

class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    parent: str = Field(default="")
    desc: str = Field(default="")

class ReservationCreate(BaseModel):
    book_id: str

class SettingUpdate(BaseModel):
    value: str

# ═══════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════
@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if user.status == "禁用":
        raise HTTPException(status_code=403, detail="该账号已被禁用")

    access_token = create_token(
        {"sub": user.id, "role": user.role, "type": "access"},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = create_token(
        {"sub": user.id, "role": user.role, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    add_log(db, user.name, "登录", "系统", f"{user.name}({user.role})登录成功")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "role": user.role,
            "status": user.status,
            "reader_binding": user.reader_binding,
        }
    }

@app.post("/api/auth/refresh")
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Token 类型错误")
    except HTTPException:
        raise
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user or user.status != "启用":
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    access_token = create_token(
        {"sub": user.id, "role": user.role, "type": "access"},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token_new = create_token(
        {"sub": user.id, "role": user.role, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_new,
        "token_type": "bearer",
    }

@app.post("/api/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="该账号已被注册")

    uid = gen_id(db, "user", "U")
    rid = gen_id(db, "reader", "R")
    reg_date = today_str()

    default_max = int(_get_setting(db, "maxBorrowCount", "5"))

    user = User(
        id=uid,
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        role="reader",
        status="启用",
        reader_binding=rid
    )
    reader = Reader(
        id=rid,
        name=body.name,
        phone=body.phone,
        email=body.email,
        dept=body.dept,
        reg_date=reg_date,
        borrowed=0,
        max_borrow=default_max,
        status="正常"
    )
    db.add(user)
    db.add(reader)
    db.commit()

    access_token = create_token(
        {"sub": uid, "role": "reader", "type": "access"},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token_str = create_token(
        {"sub": uid, "role": "reader", "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    add_log(db, body.name, "注册", "系统", f"{body.name}通过注册成为读者")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user": {
            "id": uid, "username": body.username, "name": body.name,
            "role": "reader", "status": "启用", "reader_binding": rid
        }
    }

@app.get("/api/auth/me")
def get_me(user: User = Depends(require_role(["admin", "librarian", "reader"]))):
    return {
        "id": user.id, "username": user.username, "name": user.name,
        "role": user.role, "status": user.status, "reader_binding": user.reader_binding
    }

# ═══════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════
def _get_setting(db: Session, key: str, default: str = "") -> str:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else default

def _set_setting(db: Session, key: str, value: str):
    s = db.query(Setting).filter(Setting.key == key).first()
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()

@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(Setting).all()
    result = {"defaultBorrowDays": 30, "maxBorrowCount": 5, "maxRenewCount": 2,
              "renewDays": 15, "reserveValidDays": 3, "overdueFinePerDay": 0.5,
              "damageRatio": 0.5, "lossRatio": 2, "systemName": "智慧图书馆·知识服务平台"}
    for s in settings:
        val = s.value
        if val.replace('.','',1).replace('-','',1).isdigit():
            val = float(val) if '.' in val else int(val)
        result[s.key] = val
    return result

@app.put("/api/settings/{key}")
def update_setting(key: str, body: SettingUpdate, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    _set_setting(db, key, body.value)
    add_log(db, user.name, "设置", "系统设置", f"修改 {key} 为 {body.value}")
    return {"ok": True}

# ═══════════════════════════════════════════
# BOOKS
# ═══════════════════════════════════════════
@app.get("/api/books")
def list_books(
    search: str = Query(default=""),
    category: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian", "reader"]))
):
    q = db.query(Book)
    if search:
        q = q.filter(Book.title.contains(search) | Book.author.contains(search) | Book.isbn.contains(search))
    if category:
        q = q.filter(Book.category == category)
    if status:
        q = q.filter(Book.status == status)
    total = q.count()
    books = q.offset((page-1)*page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_book_dict(b) for b in books]}

@app.get("/api/books/{book_id}")
def get_book(book_id: str, db: Session = Depends(get_db)):
    b = db.query(Book).filter(Book.id == book_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="图书不存在")
    return _book_dict(b)

def _book_dict(b: Book) -> dict:
    return {c.name: getattr(b, c.name) for c in Book.__table__.columns}

@app.post("/api/books")
def create_book(body: BookCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    bid = gen_id(db, "book", "B")
    book = Book(id=bid, **{k: v for k, v in body.dict().items() if k != 'pub_date'},
                pub_date=body.pub_date, available=body.total, borrowed=0, status="可借")
    db.add(book)
    ilog = InventoryLog(id=gen_id(db, "inventoryLog", "IL"), book_id=bid,
                        type="入库", qty=body.total, date=today_str(),
                        operator=user.name, note="新书入库")
    db.add(ilog)
    db.commit()
    add_log(db, user.name, "新增图书", bid, f"新增《{html_escape(body.title)}》")
    return _book_dict(book)

@app.put("/api/books/{book_id}")
def update_book(book_id: str, body: BookUpdate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    b = db.query(Book).filter(Book.id == book_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="图书不存在")
    update_data = {k: v for k, v in body.dict(exclude_none=True).items()}
    if 'total' in update_data:
        diff = update_data['total'] - b.total
        b.available = max(0, b.available + diff)
        ilog = InventoryLog(id=gen_id(db, "inventoryLog", "IL"), book_id=book_id,
                            type="入库" if diff > 0 else "出库", qty=abs(diff),
                            date=today_str(), operator=user.name,
                            note=f"库存变更 {diff:+d}")
        db.add(ilog)
    for k, v in update_data.items():
        if k == 'pub_date':
            setattr(b, 'pub_date', v)
        elif hasattr(b, k):
            setattr(b, k, v)
    db.commit()
    add_log(db, user.name, "编辑图书", book_id, f"更新《{html_escape(b.title)}》")
    return _book_dict(b)

@app.delete("/api/books/{book_id}")
def delete_book(book_id: str, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    b = db.query(Book).filter(Book.id == book_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="图书不存在")
    active = db.query(Borrow).filter(Borrow.book_id == book_id, Borrow.status == "借出中").count()
    if active > 0:
        raise HTTPException(status_code=400, detail="还有借出未归还，无法删除")
    title = b.title
    db.delete(b)
    db.commit()
    add_log(db, user.name, "删除图书", book_id, f"删除《{html_escape(title)}》")
    return {"ok": True}

# ═══════════════════════════════════════════
# CATEGORIES
# ═══════════════════════════════════════════
@app.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    cats = db.query(Category).all()
    result = []
    for c in cats:
        d = {col.name: getattr(c, col.name) for col in Category.__table__.columns}
        d["bookCount"] = db.query(Book).filter(Book.category == c.name).count()
        result.append(d)
    return result

@app.post("/api/categories")
def create_category(body: CategoryCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    if db.query(Category).filter(Category.name == body.name).first():
        raise HTTPException(status_code=409, detail="分类名称已存在")
    cat = Category(id=gen_id(db, "category", "C"), **body.dict())
    db.add(cat)
    db.commit()
    add_log(db, user.name, "新增分类", cat.id, f"新增分类 {body.name}")
    return {"id": cat.id, "name": cat.name, "parent": cat.parent, "desc": cat.desc, "bookCount": 0}

@app.put("/api/categories/{cat_id}")
def update_category(cat_id: str, body: CategoryCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    old_name = cat.name
    for k, v in body.dict().items():
        setattr(cat, k, v)
    if old_name != body.name:
        db.query(Book).filter(Book.category == old_name).update({Book.category: body.name})
    db.commit()
    return {"ok": True}

@app.delete("/api/categories/{cat_id}")
def delete_category(cat_id: str, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    db.delete(cat)
    db.commit()
    return {"ok": True}

# ═══════════════════════════════════════════
# READERS
# ═══════════════════════════════════════════
@app.get("/api/readers")
def list_readers(
    search: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(Reader)
    if search:
        q = q.filter(Reader.name.contains(search) | Reader.dept.contains(search) | Reader.phone.contains(search))
    if status:
        q = q.filter(Reader.status == status)
    total = q.count()
    readers = q.offset((page-1)*page_size).limit(page_size).all()
    return {"total": total, "items": [_reader_dict(r, db) for r in readers]}

def _reader_dict(r: Reader, db: Session) -> dict:
    d = {c.name: getattr(r, c.name) for c in Reader.__table__.columns}
    d["borrowed"] = db.query(Borrow).filter(Borrow.reader_id == r.id, Borrow.status == "借出中").count()
    return d

@app.get("/api/readers/{reader_id}")
def get_reader(reader_id: str, db: Session = Depends(get_db)):
    r = db.query(Reader).filter(Reader.id == reader_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="读者不存在")
    return _reader_dict(r, db)

@app.post("/api/readers")
def create_reader(body: ReaderCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    rid = gen_id(db, "reader", "R")
    reader = Reader(id=rid, **body.dict(), reg_date=today_str(), borrowed=0, status="正常")
    db.add(reader)
    db.commit()
    add_log(db, user.name, "新增读者", rid, f"新增读者 {body.name}")
    return _reader_dict(reader, db)

@app.put("/api/readers/{reader_id}")
def update_reader(reader_id: str, body: ReaderUpdate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    r = db.query(Reader).filter(Reader.id == reader_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="读者不存在")
    for k, v in body.dict(exclude_none=True).items():
        setattr(r, k, v)
    if body.status:
        linked_user = db.query(User).filter(User.reader_binding == reader_id).first()
        if linked_user:
            linked_user.status = body.status if body.status == "正常" else "禁用"
    db.commit()
    add_log(db, user.name, "编辑读者", reader_id, f"更新读者 {r.name}")
    return _reader_dict(r, db)

@app.delete("/api/readers/{reader_id}")
def delete_reader(reader_id: str, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    r = db.query(Reader).filter(Reader.id == reader_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="读者不存在")
    active = db.query(Borrow).filter(Borrow.reader_id == reader_id, Borrow.status == "借出中").count()
    if active > 0:
        raise HTTPException(status_code=400, detail="还有借出未归还，无法删除")
    db.delete(r)
    db.commit()
    return {"ok": True}

# ═══════════════════════════════════════════
# BORROWS
# ═══════════════════════════════════════════
@app.get("/api/borrows")
def list_borrows(
    status: str = Query(default=""),
    reader_id: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(Borrow)
    if status:
        q = q.filter(Borrow.status == status)
    if reader_id:
        q = q.filter(Borrow.reader_id == reader_id)
    total = q.count()
    borrows = q.order_by(Borrow.id.desc()).offset((page-1)*page_size).limit(page_size).all()
    items = [_borrow_dict(b, db) for b in borrows]
    return {"total": total, "items": items}

def _borrow_dict(b: Borrow, db: Session) -> dict:
    d = {c.name: getattr(b, c.name) for c in Borrow.__table__.columns}
    book = db.query(Book).filter(Book.id == b.book_id).first()
    reader = db.query(Reader).filter(Reader.id == b.reader_id).first()
    d["book_title"] = book.title if book else ""
    d["reader_name"] = reader.name if reader else ""
    return d

@app.post("/api/borrows")
def create_borrow(body: BorrowCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    reader = db.query(Reader).filter(Reader.id == body.reader_id).first()
    book = db.query(Book).filter(Book.id == body.book_id).first()
    if not reader:
        raise HTTPException(status_code=404, detail="读者不存在")
    if not book:
        raise HTTPException(status_code=404, detail="图书不存在")
    if reader.status != "正常":
        raise HTTPException(status_code=400, detail="该读者状态异常，无法借阅")
    if book.available <= 0:
        raise HTTPException(status_code=400, detail="该图书已全部借出")
    active_count = db.query(Borrow).filter(Borrow.reader_id == body.reader_id, Borrow.status == "借出中").count()
    if active_count >= reader.max_borrow:
        raise HTTPException(status_code=400, detail=f"已达到最大借阅数 ({reader.max_borrow})")

    default_days = int(_get_setting(db, "defaultBorrowDays", "30"))
    now = today_str()
    due = add_days_str(now, default_days)

    bid = gen_id(db, "borrow", "BR")
    borrow = Borrow(id=bid, reader_id=body.reader_id, book_id=body.book_id,
                    borrow_date=now, due_date=due, status="借出中", renew_count=0)
    book.borrowed += 1
    book.available = book.total - book.borrowed
    reader.borrowed = active_count + 1
    db.add(borrow)
    db.commit()
    add_log(db, user.name, "借阅", bid, f"{reader.name}借阅《{book.title}》")
    return _borrow_dict(borrow, db)

@app.put("/api/borrows/{borrow_id}/return")
def return_borrow(borrow_id: str, body: BorrowReturn = BorrowReturn(),
                  user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    borrow = db.query(Borrow).filter(Borrow.id == borrow_id).first()
    if not borrow or borrow.status != "借出中":
        raise HTTPException(status_code=404, detail="借阅记录不存在或已归还")
    borrow.status = "已归还"
    borrow.return_date = today_str()
    borrow.return_book_status = body.return_book_status
    book = db.query(Book).filter(Book.id == borrow.book_id).first()
    if book:
        book.borrowed = max(0, book.borrowed - 1)
        book.available = book.total - book.borrowed
    reader = db.query(Reader).filter(Reader.id == borrow.reader_id).first()
    if reader:
        reader.borrowed = max(0, reader.borrowed - 1)
    db.commit()
    add_log(db, user.name, "归还", borrow_id, f"{reader.name if reader else ''}归还《{book.title if book else ''}》")
    return {"ok": True}

@app.put("/api/borrows/{borrow_id}/renew")
def renew_borrow(borrow_id: str, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    borrow = db.query(Borrow).filter(Borrow.id == borrow_id).first()
    if not borrow or borrow.status != "借出中":
        raise HTTPException(status_code=404, detail="借阅记录不存在或已归还")
    max_renew = int(_get_setting(db, "maxRenewCount", "2"))
    if borrow.renew_count >= max_renew:
        raise HTTPException(status_code=400, detail=f"已达到最大续借次数 ({max_renew})")
    renew_days = int(_get_setting(db, "renewDays", "15"))
    borrow.renew_count += 1
    borrow.due_date = add_days_str(borrow.due_date, renew_days)
    db.commit()
    add_log(db, user.name, "续借", borrow_id, f"续借成功，新到期日 {borrow.due_date}")
    return {"ok": True, "due_date": borrow.due_date, "renew_count": borrow.renew_count}

# Reader self-service borrow endpoints
@app.get("/api/reader/my-borrows")
def reader_my_borrows(
    user: User = Depends(require_role(["reader"])),
    db: Session = Depends(get_db)
):
    if not user.reader_binding:
        return []
    borrows = db.query(Borrow).filter(Borrow.reader_id == user.reader_binding).order_by(Borrow.id.desc()).all()
    return [_borrow_dict(b, db) for b in borrows]

# ═══════════════════════════════════════════
# RESERVATIONS
# ═══════════════════════════════════════════
@app.get("/api/reservations")
def list_reservations(
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(Reservation)
    if status:
        q = q.filter(Reservation.status == status)
    reservations = q.order_by(Reservation.id.desc()).all()
    items = []
    for r in reservations:
        d = {c.name: getattr(r, c.name) for c in Reservation.__table__.columns}
        book = db.query(Book).filter(Book.id == r.book_id).first()
        reader = db.query(Reader).filter(Reader.id == r.reader_id).first()
        d["book_title"] = book.title if book else ""
        d["reader_name"] = reader.name if reader else ""
        items.append(d)
    return items

@app.post("/api/reservations")
def create_reservation(body: ReservationCreate, user: User = Depends(require_role(["admin", "librarian", "reader"])), db: Session = Depends(get_db)):
    if user.role == "reader":
        reader_id = user.reader_binding
    else:
        reader_id = body.reader_id if hasattr(body, 'reader_id') else user.reader_binding
    if not reader_id:
        raise HTTPException(status_code=400, detail="无法确定读者身份")

    book = db.query(Book).filter(Book.id == body.book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="图书不存在")
    if book.available > 0:
        raise HTTPException(status_code=400, detail="该书还有可借库存，可直接借阅无需预约")

    valid_days = int(_get_setting(db, "reserveValidDays", "3"))
    now = today_str()
    existing = db.query(Reservation).filter(
        Reservation.book_id == body.book_id, Reservation.reader_id == reader_id,
        Reservation.status.in_(["待处理", "已通知"])
    ).count()
    if existing > 0:
        raise HTTPException(status_code=400, detail="您已预约该书，请勿重复预约")

    queue_pos = db.query(Reservation).filter(
        Reservation.book_id == body.book_id, Reservation.status.in_(["待处理", "已通知"])
    ).count() + 1

    rid = gen_id(db, "reserve", "RS")
    reservation = Reservation(id=rid, reader_id=reader_id, book_id=body.book_id,
                              res_date=now, valid_until=add_days_str(now, valid_days),
                              status="待处理", queue_pos=queue_pos)
    db.add(reservation)
    db.commit()
    add_log(db, user.name, "预约", rid, f"预约《{book.title}》")
    return {"ok": True, "id": rid}

@app.put("/api/reservations/{res_id}/status")
def update_reservation_status(res_id: str, status: str = Query(...),
                               user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    r = db.query(Reservation).filter(Reservation.id == res_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="预约不存在")
    r.status = status
    db.commit()
    return {"ok": True}

@app.get("/api/reader/my-reservations")
def reader_my_reservations(user: User = Depends(require_role(["reader"])), db: Session = Depends(get_db)):
    if not user.reader_binding:
        return []
    reservations = db.query(Reservation).filter(Reservation.reader_id == user.reader_binding).order_by(Reservation.id.desc()).all()
    items = []
    for r in reservations:
        d = {c.name: getattr(r, c.name) for c in Reservation.__table__.columns}
        book = db.query(Book).filter(Book.id == r.book_id).first()
        d["book_title"] = book.title if book else ""
        items.append(d)
    return items

# ═══════════════════════════════════════════
# FINES
# ═══════════════════════════════════════════
@app.get("/api/fines")
def list_fines(
    status: str = Query(default=""),
    reader_id: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(Fine)
    if status:
        q = q.filter(Fine.status == status)
    if reader_id:
        q = q.filter(Fine.reader_id == reader_id)
    fines = q.order_by(Fine.id.desc()).all()
    items = []
    for f in fines:
        d = {c.name: getattr(f, c.name) for c in Fine.__table__.columns}
        book = db.query(Book).filter(Book.id == f.book_id).first()
        reader = db.query(Reader).filter(Reader.id == f.reader_id).first()
        d["book_title"] = book.title if book else ""
        d["reader_name"] = reader.name if reader else ""
        items.append(d)
    return items

@app.put("/api/fines/{fine_id}/status")
def update_fine_status(fine_id: str, status: str = Query(...),
                        user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    f = db.query(Fine).filter(Fine.id == fine_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="罚款记录不存在")
    f.status = status
    db.commit()
    return {"ok": True}

@app.get("/api/reader/my-fines")
def reader_my_fines(user: User = Depends(require_role(["reader"])), db: Session = Depends(get_db)):
    if not user.reader_binding:
        return []
    fines = db.query(Fine).filter(Fine.reader_id == user.reader_binding).order_by(Fine.id.desc()).all()
    items = []
    for f in fines:
        d = {c.name: getattr(f, c.name) for c in Fine.__table__.columns}
        book = db.query(Book).filter(Book.id == f.book_id).first()
        d["book_title"] = book.title if book else ""
        items.append(d)
    return items

# ═══════════════════════════════════════════
# OVERDUE CHECK (called periodically)
# ═══════════════════════════════════════════
@app.post("/api/system/check-overdue")
def check_overdue(user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    now = today_str()
    fine_per_day = float(_get_setting(db, "overdueFinePerDay", "0.5"))
    borrows = db.query(Borrow).filter(Borrow.status == "借出中").all()
    new_fines = 0
    for b in borrows:
        od = days_between(b.due_date, now)
        if od > 0:
            exists = db.query(Fine).filter(Fine.borrow_id == b.id, Fine.status == "未缴纳").first()
            if not exists:
                amount = round(od * fine_per_day, 2)
                if amount > 0:
                    fine = Fine(id=gen_id(db, "fine", "F"), borrow_id=b.id,
                                reader_id=b.reader_id, book_id=b.book_id,
                                overdue_days=od, amount=amount, status="未缴纳")
                    db.add(fine)
                    new_fines += 1
    # Expire stale reservations
    valid_days = int(_get_setting(db, "reserveValidDays", "3"))
    reservations = db.query(Reservation).filter(Reservation.status.in_(["待处理", "已通知"])).all()
    for r in reservations:
        if days_between(r.valid_until, now) > 0:
            r.status = "已过期"
    db.commit()
    return {"new_fines": new_fines, "message": f"检查完成，生成 {new_fines} 条新罚款"}

# ═══════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════
@app.get("/api/users")
def list_users(user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [{ "id": u.id, "username": u.username, "name": u.name,
              "role": u.role, "status": u.status, "reader_binding": u.reader_binding } for u in users]

@app.post("/api/users")
def create_user(body: UserCreate, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    uid = gen_id(db, "user", "U")
    new_user = User(id=uid, username=body.username, password_hash=hash_password(body.password),
                    name=body.name, role=body.role, status="启用", reader_binding=body.reader_binding)
    db.add(new_user)
    db.commit()
    add_log(db, user.name, "用户管理", uid, f"创建用户 {body.username}({body.role})")
    return {"id": uid, "username": body.username, "name": body.name, "role": body.role, "status": "启用"}

@app.put("/api/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, current_user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    for k, v in body.dict(exclude_none=True).items():
        if k == "password":
            setattr(u, "password_hash", hash_password(v))
        elif hasattr(u, k):
            setattr(u, k, v)
    db.commit()
    add_log(db, current_user.name, "用户管理", user_id, f"更新用户 {u.username}")
    return {"ok": True}

# ═══════════════════════════════════════════
# ANNOUNCEMENTS
# ═══════════════════════════════════════════
@app.get("/api/announcements")
def list_announcements(db: Session = Depends(get_db)):
    return [{"id": a.id, "title": a.title, "content": a.content, "date": a.date,
             "status": a.status, "top": a.top} for a in db.query(Announcement).order_by(Announcement.top.desc(), Announcement.id.desc()).all()]

@app.post("/api/announcements")
def create_announcement(body: AnnouncementCreate, user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    aid = gen_id(db, "announcement", "A")
    ann = Announcement(id=aid, title=html_escape(body.title), content=html_escape(body.content),
                       date=today_str(), status="已发布", top=body.top)
    db.add(ann)
    db.commit()
    add_log(db, user.name, "公告", aid, f"发布公告 {body.title}")
    return {"id": aid, "title": body.title, "status": "已发布"}

@app.put("/api/announcements/{ann_id}/status")
def update_announcement_status(ann_id: str, status: str = Query(...),
                                user: User = Depends(require_role(["admin", "librarian"])), db: Session = Depends(get_db)):
    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    ann.status = status
    db.commit()
    return {"ok": True}

# ═══════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════
@app.get("/api/logs")
def list_logs(
    action: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(Log)
    if action:
        q = q.filter(Log.action == action)
    total = q.count()
    logs = q.order_by(Log.id.desc()).offset((page-1)*page_size).limit(page_size).all()
    items = [{c.name: getattr(l, c.name) for c in Log.__table__.columns} for l in logs]
    return {"total": total, "items": items}

# ═══════════════════════════════════════════
# INVENTORY LOGS
# ═══════════════════════════════════════════
@app.get("/api/inventory-logs")
def list_inventory_logs(
    book_id: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(["admin", "librarian"]))
):
    q = db.query(InventoryLog)
    if book_id:
        q = q.filter(InventoryLog.book_id == book_id)
    logs = q.order_by(InventoryLog.id.desc()).all()
    items = []
    for l in logs:
        d = {c.name: getattr(l, c.name) for c in InventoryLog.__table__.columns}
        book = db.query(Book).filter(Book.id == l.book_id).first()
        d["book_title"] = book.title if book else ""
        items.append(d)
    return items

# ═══════════════════════════════════════════
# DASHBOARD STATS
# ═══════════════════════════════════════════
@app.get("/api/dashboard")
def get_dashboard(db: Session = Depends(get_db),
                   user: User = Depends(require_role(["admin", "librarian"]))):
    total_books = db.query(Book).count()
    total_readers = db.query(Reader).count()
    active_borrows = db.query(Borrow).filter(Borrow.status == "借出中").count()
    overdue_borrows = db.query(Borrow).filter(Borrow.status == "借出中", Borrow.due_date < today_str()).count()
    pending_fines = db.query(Fine).filter(Fine.status == "未缴纳").count()
    pending_reservations = db.query(Reservation).filter(Reservation.status.in_(["待处理", "已通知"])).count()
    return {
        "total_books": total_books,
        "total_readers": total_readers,
        "active_borrows": active_borrows,
        "overdue_borrows": overdue_borrows,
        "pending_fines": pending_fines,
        "pending_reservations": pending_reservations,
    }

@app.get("/api/reader/dashboard")
def get_reader_dashboard(user: User = Depends(require_role(["reader"])), db: Session = Depends(get_db)):
    if not user.reader_binding:
        return {"my_borrows": 0, "my_reservations": 0, "my_fines": 0}
    my_borrows = db.query(Borrow).filter(Borrow.reader_id == user.reader_binding, Borrow.status == "借出中").count()
    my_overdue = db.query(Borrow).filter(Borrow.reader_id == user.reader_binding, Borrow.status == "借出中", Borrow.due_date < today_str()).count()
    my_reservations = db.query(Reservation).filter(Reservation.reader_id == user.reader_binding, Reservation.status.in_(["待处理", "已通知"])).count()
    my_fines = db.query(Fine).filter(Fine.reader_id == user.reader_binding, Fine.status == "未缴纳").count()
    return {
        "my_borrows": my_borrows,
        "my_overdue": my_overdue,
        "my_reservations": my_reservations,
        "my_fines": my_fines,
    }

# ═══════════════════════════════════════════
# READER PROFILE
# ═══════════════════════════════════════════
@app.get("/api/reader/profile")
def get_reader_profile(user: User = Depends(require_role(["reader"])), db: Session = Depends(get_db)):
    if not user.reader_binding:
        raise HTTPException(status_code=404, detail="未绑定读者信息")
    reader = db.query(Reader).filter(Reader.id == user.reader_binding).first()
    if not reader:
        raise HTTPException(status_code=404, detail="读者信息不存在")
    return _reader_dict(reader, db)

# ═══════════════════════════════════════════
# SEED DATA
# ═══════════════════════════════════════════
def _seed(db: Session):
    """Initialize database with demo data."""
    settings = {
        "defaultBorrowDays": "30", "maxBorrowCount": "5", "maxRenewCount": "2",
        "renewDays": "15", "reserveValidDays": "3", "overdueFinePerDay": "0.5",
        "damageRatio": "0.5", "lossRatio": "2", "systemName": "智慧图书馆·知识服务平台"
    }
    for k, v in settings.items():
        db.add(Setting(key=k, value=v))

    users = [
        User(id="U001", username="admin", password_hash=hash_password("123456"), name="系统管理员", role="admin", status="启用", reader_binding=""),
        User(id="U002", username="librarian", password_hash=hash_password("123456"), name="张馆员", role="librarian", status="启用", reader_binding=""),
        User(id="U003", username="reader", password_hash=hash_password("123456"), name="张三", role="reader", status="启用", reader_binding="R001"),
    ]
    for u in users: db.add(u)

    categories = [
        Category(id="C01", name="计算机科学", desc="计算机科学基础理论"),
        Category(id="C02", name="软件工程", desc="软件工程方法与工具"),
        Category(id="C03", name="编程语言", desc="各类编程语言学习"),
        Category(id="C04", name="数据库", desc="数据库理论与应用"),
        Category(id="C05", name="计算机网络", desc="网络技术与协议"),
        Category(id="C06", name="操作系统", desc="操作系统原理与实践"),
        Category(id="C07", name="前端开发", desc="前端框架与技术"),
        Category(id="C08", name="人工智能", desc="AI机器学习深度学习"),
        Category(id="C09", name="软件架构", desc="系统架构与设计"),
        Category(id="C10", name="分布式系统", desc="分布式计算与系统"),
    ]
    for c in categories: db.add(c)

    books = [
        Book(id="B001", isbn="978-7-111-70791-2", title="深入理解计算机系统", author="Randal E. Bryant", publisher="机械工业出版社", category="计算机科学", pub_date="2016-11-01", price=139, total=5, available=3, borrowed=2, shelf="A-01-03", status="可借", desc="从程序员视角全面剖析计算机系统的经典教材。"),
        Book(id="B002", isbn="978-7-115-49060-1", title="算法导论", author="Thomas H. Cormen", publisher="人民邮电出版社", category="计算机科学", pub_date="2013-01-01", price=128, total=4, available=2, borrowed=2, shelf="A-01-04", status="可借", desc="算法领域的标准参考书。"),
        Book(id="B003", isbn="978-7-302-52086-3", title="数据结构与算法分析", author="Mark Allen Weiss", publisher="清华大学出版社", category="计算机科学", pub_date="2019-03-01", price=89, total=6, available=6, borrowed=0, shelf="A-01-05", status="可借", desc="数据结构与算法的经典教材。"),
        Book(id="B004", isbn="978-7-111-39282-8", title="设计模式", author="Erich Gamma", publisher="机械工业出版社", category="软件工程", pub_date="2007-01-01", price=69, total=3, available=3, borrowed=0, shelf="A-02-01", status="可借", desc="GoF设计模式经典著作。"),
        Book(id="B005", isbn="978-7-115-46444-2", title="Python编程：从入门到实践", author="Eric Matthes", publisher="人民邮电出版社", category="编程语言", pub_date="2020-10-01", price=89, total=8, available=5, borrowed=3, shelf="B-01-01", status="可借", desc="Python入门最佳实践。"),
        Book(id="B006", isbn="978-7-111-59513-0", title="Java核心技术", author="Cay S. Horstmann", publisher="机械工业出版社", category="编程语言", pub_date="2019-12-01", price=149, total=4, available=4, borrowed=0, shelf="B-01-02", status="可借", desc="Java开发的权威指南。"),
        Book(id="B007", isbn="978-7-115-50662-8", title="JavaScript高级程序设计", author="Matt Frisbie", publisher="人民邮电出版社", category="编程语言", pub_date="2020-04-01", price=129, total=5, available=3, borrowed=2, shelf="B-01-03", status="可借", desc="前端开发必读经典。"),
        Book(id="B008", isbn="978-7-111-65673-9", title="数据库系统概念", author="Abraham Silberschatz", publisher="机械工业出版社", category="数据库", pub_date="2021-01-01", price=139, total=3, available=1, borrowed=2, shelf="A-03-01", status="可借", desc="数据库领域的圣经级教材。"),
        Book(id="B009", isbn="978-7-302-57786-3", title="计算机网络：自顶向下方法", author="James F. Kurose", publisher="清华大学出版社", category="计算机网络", pub_date="2021-08-01", price=109, total=4, available=2, borrowed=2, shelf="A-03-02", status="可借", desc="网络课程经典教材。"),
        Book(id="B010", isbn="978-7-111-55478-2", title="操作系统概念", author="Abraham Silberschatz", publisher="机械工业出版社", category="操作系统", pub_date="2018-07-01", price=99, total=3, available=3, borrowed=0, shelf="A-03-03", status="可借", desc="操作系统入门经典。"),
        Book(id="B011", isbn="978-7-115-52943-0", title="React设计原理", author="卡颂", publisher="人民邮电出版社", category="前端开发", pub_date="2023-03-01", price=79, total=5, available=5, borrowed=0, shelf="B-02-01", status="可借", desc="深入浅出React实现原理。"),
        Book(id="B012", isbn="978-7-111-71999-1", title="机器学习", author="周志华", publisher="机械工业出版社", category="人工智能", pub_date="2016-01-01", price=88, total=6, available=4, borrowed=2, shelf="C-01-01", status="可借", desc="西瓜书，机器学习领域必读中文著作。"),
        Book(id="B013", isbn="978-7-115-53673-9", title="深度学习", author="Ian Goodfellow", publisher="人民邮电出版社", category="人工智能", pub_date="2017-08-01", price=168, total=3, available=2, borrowed=1, shelf="C-01-02", status="可借", desc="深度学习领域的奠基之作。"),
        Book(id="B014", isbn="978-7-111-62257-3", title="人月神话", author="Frederick P. Brooks", publisher="机械工业出版社", category="软件工程", pub_date="2015-03-01", price=69, total=4, available=3, borrowed=1, shelf="A-02-02", status="可借", desc="软件工程管理经典。"),
        Book(id="B015", isbn="978-7-115-48336-0", title="代码整洁之道", author="Robert C. Martin", publisher="人民邮电出版社", category="软件工程", pub_date="2020-06-01", price=79, total=5, available=4, borrowed=1, shelf="A-02-03", status="可借", desc="写出整洁代码的实践指南。"),
        Book(id="B016", isbn="978-7-302-56912-6", title="编译原理", author="Alfred V. Aho", publisher="清华大学出版社", category="计算机科学", pub_date="2020-08-01", price=119, total=3, available=2, borrowed=1, shelf="A-01-06", status="可借", desc="龙书，编译原理权威教材。"),
        Book(id="B017", isbn="978-7-111-67334-5", title="Go语言程序设计", author="Alan Donovan", publisher="机械工业出版社", category="编程语言", pub_date="2021-05-01", price=99, total=4, available=3, borrowed=1, shelf="B-01-04", status="可借", desc="Go语言圣经中文版。"),
        Book(id="B018", isbn="978-7-115-54442-3", title="微服务架构设计模式", author="Chris Richardson", publisher="人民邮电出版社", category="软件架构", pub_date="2021-01-01", price=119, total=3, available=3, borrowed=0, shelf="A-04-01", status="可借", desc="微服务架构实战指南。"),
        Book(id="B019", isbn="978-7-111-69444-8", title="分布式系统：概念与设计", author="George Coulouris", publisher="机械工业出版社", category="分布式系统", pub_date="2022-03-01", price=139, total=2, available=1, borrowed=1, shelf="A-04-02", status="可借", desc="分布式系统经典教材。"),
        Book(id="B020", isbn="978-7-115-58923-0", title="Linux命令行与Shell脚本编程", author="Richard Blum", publisher="人民邮电出版社", category="操作系统", pub_date="2022-09-01", price=109, total=5, available=5, borrowed=0, shelf="A-03-04", status="可借", desc="Shell脚本编程大全。"),
        Book(id="B021", isbn="978-7-111-72073-8", title="C++ Primer", author="Stanley B. Lippman", publisher="机械工业出版社", category="编程语言", pub_date="2023-02-01", price=139, total=3, available=3, borrowed=0, shelf="B-01-05", status="可借", desc="C++经典入门教材。"),
        Book(id="B022", isbn="978-7-302-60163-5", title="人工智能：一种现代方法", author="Stuart Russell", publisher="清华大学出版社", category="人工智能", pub_date="2023-05-01", price=198, total=2, available=1, borrowed=1, shelf="C-01-03", status="可借", desc="AI经典教材第四版。"),
    ]
    for b in books: db.add(b)

    readers = [
        Reader(id="R001", name="张三", phone="13800001001", email="zhangsan@example.com", dept="计算机科学2022级", reg_date="2023-09-01", borrowed=2, max_borrow=5, status="正常"),
        Reader(id="R002", name="李四", phone="13800001002", email="lisi@example.com", dept="软件工程2022级", reg_date="2023-09-02", borrowed=1, max_borrow=5, status="正常"),
        Reader(id="R003", name="王五", phone="13800001003", email="wangwu@example.com", dept="人工智能2021级", reg_date="2022-09-01", borrowed=3, max_borrow=5, status="正常"),
        Reader(id="R004", name="赵六", phone="13800001004", email="zhaoliu@example.com", dept="计算机科学2021级", reg_date="2022-09-03", borrowed=0, max_borrow=5, status="禁用"),
        Reader(id="R005", name="孙七", phone="13800001005", email="sunqi@example.com", dept="电子信息2020级", reg_date="2021-09-01", borrowed=0, max_borrow=3, status="黑名单"),
        Reader(id="R006", name="周八", phone="13800001006", email="zhouba@example.com", dept="网络工程2023级", reg_date="2024-09-01", borrowed=1, max_borrow=5, status="正常"),
        Reader(id="R007", name="吴九", phone="13800001007", email="wujiu@example.com", dept="数据科学2022级", reg_date="2023-09-05", borrowed=2, max_borrow=5, status="正常"),
        Reader(id="R008", name="郑十", phone="13800001008", email="zhengshi@example.com", dept="软件工程2023级", reg_date="2024-09-02", borrowed=0, max_borrow=5, status="正常"),
        Reader(id="R009", name="刘老师", phone="13800001009", email="liulaoshi@example.com", dept="计算机学院", reg_date="2020-03-01", borrowed=4, max_borrow=10, status="正常"),
        Reader(id="R010", name="陈教授", phone="13800001010", email="chenjs@example.com", dept="信息学院", reg_date="2019-03-01", borrowed=1, max_borrow=10, status="正常"),
    ]
    for r in readers: db.add(r)

    now = date.today()
    def d(offset): return (now + timedelta(days=offset)).isoformat()

    borrows = [
        Borrow(id="BR001", reader_id="R001", book_id="B001", borrow_date=d(-10), due_date=d(20), status="借出中", renew_count=0),
        Borrow(id="BR002", reader_id="R001", book_id="B005", borrow_date=d(-5), due_date=d(25), status="借出中", renew_count=0),
        Borrow(id="BR003", reader_id="R002", book_id="B007", borrow_date=d(-8), due_date=d(22), status="借出中", renew_count=0),
        Borrow(id="BR004", reader_id="R003", book_id="B002", borrow_date=d(-15), due_date=d(15), status="借出中", renew_count=1),
        Borrow(id="BR005", reader_id="R003", book_id="B008", borrow_date=d(-20), due_date=d(10), status="借出中", renew_count=0),
        Borrow(id="BR006", reader_id="R003", book_id="B012", borrow_date=d(-12), due_date=d(18), status="借出中", renew_count=0),
        Borrow(id="BR007", reader_id="R006", book_id="B009", borrow_date=d(-3), due_date=d(27), status="借出中", renew_count=0),
        Borrow(id="BR008", reader_id="R007", book_id="B013", borrow_date=d(-25), due_date=d(5), status="借出中", renew_count=0),
        Borrow(id="BR009", reader_id="R007", book_id="B015", borrow_date=d(-7), due_date=d(23), status="借出中", renew_count=0),
        Borrow(id="BR010", reader_id="R009", book_id="B002", borrow_date=d(-30), due_date=d(0), status="借出中", renew_count=1),
        Borrow(id="BR011", reader_id="R009", book_id="B005", borrow_date=d(-18), due_date=d(12), status="借出中", renew_count=0),
        Borrow(id="BR012", reader_id="R009", book_id="B012", borrow_date=d(-40), due_date=d(-10), return_date=d(-8), status="已归还", renew_count=0, return_book_status="正常"),
        Borrow(id="BR013", reader_id="R009", book_id="B016", borrow_date=d(-6), due_date=d(24), status="借出中", renew_count=0),
        Borrow(id="BR014", reader_id="R010", book_id="B017", borrow_date=d(-4), due_date=d(26), status="借出中", renew_count=0),
        Borrow(id="BR015", reader_id="R003", book_id="B014", borrow_date=d(-35), due_date=d(-5), return_date=d(-2), status="已归还", renew_count=0, return_book_status="正常"),
        Borrow(id="BR016", reader_id="R007", book_id="B022", borrow_date=d(-9), due_date=d(21), status="借出中", renew_count=0),
        Borrow(id="BR017", reader_id="R003", book_id="B019", borrow_date=d(-2), due_date=d(28), status="借出中", renew_count=0),
    ]
    for b in borrows: db.add(b)

    reservations = [
        Reservation(id="RS001", reader_id="R008", book_id="B008", res_date=d(-2), valid_until=d(1), status="待处理", queue_pos=1),
        Reservation(id="RS002", reader_id="R006", book_id="B001", res_date=d(-1), valid_until=d(2), status="待处理", queue_pos=1),
        Reservation(id="RS003", reader_id="R004", book_id="B022", res_date=d(-5), valid_until=d(-2), status="已过期", queue_pos=1),
        Reservation(id="RS004", reader_id="R002", book_id="B013", res_date=d(-3), valid_until=d(0), status="已通知", queue_pos=1),
        Reservation(id="RS005", reader_id="R008", book_id="B019", res_date=d(-1), valid_until=d(2), status="待处理", queue_pos=2),
    ]
    for r in reservations: db.add(r)

    fines = [
        Fine(id="F001", borrow_id="BR008", reader_id="R007", book_id="B013", overdue_days=20, amount=10, status="未缴纳"),
        Fine(id="F002", borrow_id="BR010", reader_id="R009", book_id="B002", overdue_days=0, amount=0, status="未缴纳"),
        Fine(id="F003", borrow_id="BR012", reader_id="R009", book_id="B012", overdue_days=2, amount=1, status="已缴纳"),
        Fine(id="F004", borrow_id="BR005", reader_id="R003", book_id="B008", overdue_days=14, amount=7, status="未缴纳"),
        Fine(id="F005", borrow_id="BR015", reader_id="R003", book_id="B014", overdue_days=3, amount=1.5, status="已减免"),
    ]
    for f in fines: db.add(f)

    announcements = [
        Announcement(id="A001", title="图书馆2024年暑假开放通知", content="暑假期间图书馆开放时间调整为周一至周五 8:00-17:00，周末闭馆。", date="2024-06-20", status="已发布", top=True),
        Announcement(id="A002", title="新到图书通知", content="本月新增计算机、人工智能类图书200余册，欢迎借阅。", date="2024-05-15", status="已发布", top=False),
        Announcement(id="A003", title="系统维护通知", content="本周六晚22:00-24:00进行系统升级维护。", date="2024-04-10", status="已下线", top=False),
    ]
    for a in announcements: db.add(a)

    logs_data = [
        Log(id="L001", time="2024-06-24 09:15:30", operator="admin", action="登录", target="系统", detail="管理员登录系统"),
        Log(id="L002", time="2024-06-24 09:20:12", operator="admin", action="借阅", target="BR001", detail="张三借阅《深入理解计算机系统》"),
        Log(id="L003", time="2024-06-24 10:05:45", operator="librarian", action="归还", target="BR012", detail="刘老师归还《机器学习》"),
        Log(id="L004", time="2024-06-24 11:30:00", operator="admin", action="新增图书", target="B022", detail="新增《人工智能：一种现代方法》"),
        Log(id="L005", time="2024-06-23 14:20:30", operator="admin", action="用户管理", target="U003", detail="创建读者账号 reader"),
        Log(id="L006", time="2024-06-23 15:00:00", operator="librarian", action="续借", target="BR004", detail="王五续借《算法导论》"),
        Log(id="L007", time="2024-06-23 16:30:00", operator="admin", action="罚款", target="F004", detail="生成逾期罚款记录"),
        Log(id="L008", time="2024-06-22 09:00:00", operator="admin", action="公告", target="A001", detail="发布暑假开放通知"),
        Log(id="L009", time="2024-06-22 10:00:00", operator="librarian", action="预约", target="RS001", detail="周八预约《数据库系统概念》"),
        Log(id="L010", time="2024-06-21 08:30:00", operator="admin", action="设置", target="系统设置", detail="修改默认借阅天数为30天"),
    ]
    for l in logs_data: db.add(l)

    inventory_logs = [
        InventoryLog(id="IL001", book_id="B001", type="入库", qty=5, date="2024-01-15", operator="admin", note="新书入库"),
        InventoryLog(id="IL002", book_id="B022", type="入库", qty=2, date="2024-05-20", operator="admin", note="新书入库"),
    ]
    for il in inventory_logs: db.add(il)

    id_counters = [
        IdCounter(key="book", value=23), IdCounter(key="reader", value=11),
        IdCounter(key="borrow", value=18), IdCounter(key="reserve", value=6),
        IdCounter(key="fine", value=6), IdCounter(key="user", value=4),
        IdCounter(key="announcement", value=4), IdCounter(key="log", value=11),
        IdCounter(key="inventoryLog", value=3), IdCounter(key="category", value=11),
    ]
    for ic in id_counters: db.add(ic)

    db.commit()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
