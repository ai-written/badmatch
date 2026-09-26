from pydantic import BaseModel


class RegisterRequest(BaseModel):
    username: str
    password: str
    gender: str | None = None
    invite_code: str | None = None
    # 首个用户（成为超级管理员）所需的初始化注册码
    init_code: str | None = None
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
    invited_by: int | None = None
    invited_by_username: str | None = None

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
    # 刻意不接受 avatar：头像只能通过 POST /auth/upload-avatar 修改。
    # 否则任意用户可把 avatar 设成「他人头像的路径」，再上传一次头像，
    # 就会经由 _remove_avatar_file 删掉对方的头像文件（对方 DB 仍指向它 → 裂图）。
    gender: str | None = None
    email: str | None = None


class SelectableUser(BaseModel):
    id: int
    username: str
    avatar: str
    gender: str | None = None
    role: str = "user"
    invited_by: int | None = None


class UserStats(BaseModel):
    total_matches: int = 0
    total_wins: int = 0
    win_rate: float = 0.0
    tournaments_played: int = 0
