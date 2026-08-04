from pydantic import BaseModel


class RegisterRequest(BaseModel):
    username: str
    password: str
    gender: str | None = None
    invite_code: str | None = None
    email: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserProfile"


class UserProfile(BaseModel):
    id: int
    username: str
    email: str | None = None
    avatar: str
    gender: str | None = None
    role: str = "user"
    invite_code: str | None = None

    class Config:
        from_attributes = True


class AdminResetPassword(BaseModel):
    user_id: int
    new_password: str


class AdminSetRole(BaseModel):
    user_id: int
    role: str


class UpdateProfile(BaseModel):
    username: str | None = None
    avatar: str | None = None
    gender: str | None = None
    email: str | None = None


class SelectableUser(BaseModel):
    id: int
    username: str
    avatar: str
    gender: str | None = None
    role: str = "user"


class UserStats(BaseModel):
    total_matches: int = 0
    total_wins: int = 0
    win_rate: float = 0.0
    tournaments_played: int = 0
