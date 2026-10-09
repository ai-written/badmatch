from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    # 上限与 DB 列一致（users.username 是 String(64)、email 是 String(255)）。
    # 不给上限的话超长值会在写库时抛 DataError（列宽溢出），
    # 而 DataError 不是 IntegrityError 的子类，不会被现有的 except 捕获 → 500。
    username: str = Field(min_length=1, max_length=64)
    # 上限 200 是为了挡住超长输入：bcrypt 只取前 72 字节，
    # 允许无限长既无意义又白白消耗 CPU（真正的 72 字节上限由接口层校验）
    password: str = Field(min_length=6, max_length=200)
    gender: str | None = Field(default=None, max_length=1)
    invite_code: str | None = Field(default=None, max_length=32)
    # 首个用户（成为超级管理员）所需的初始化注册码
    init_code: str | None = Field(default=None, max_length=64)
    email: str = Field(min_length=3, max_length=255)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    # 令牌由邮件链接的 URL fragment 传来，长度按 token_urlsafe(32) 的实际长度放宽
    token: str = Field(min_length=16, max_length=200)
    # 与注册/改密一致的上限；真正的 72 字节 bcrypt 限制由接口层校验
    new_password: str = Field(min_length=6, max_length=200)


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
    # 账号是否可用（False = 已禁用）。管理面板据此显示「已禁用」并提供恢复入口
    is_active: bool = True
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


class AdminSetActive(BaseModel):
    user_id: int
    is_active: bool


class UpdateProfile(BaseModel):
    # 上限必须与 RegisterRequest / DB 列宽一致：超长值写库时抛的是 DataError
    # （列宽溢出），而它不是 IntegrityError 的子类，接口的 except 抓不到 → 500
    username: str | None = Field(default=None, min_length=1, max_length=64)
    # 刻意不接受 avatar：头像只能通过 POST /auth/upload-avatar 修改。
    # 否则任意用户可把 avatar 设成「他人头像的路径」，再上传一次头像，
    # 就会经由 _remove_avatar_file 删掉对方的头像文件（对方 DB 仍指向它 → 裂图）。
    gender: str | None = Field(default=None, max_length=1)
    email: str | None = Field(default=None, max_length=255)


class SelectableUser(BaseModel):
    id: int
    username: str
    avatar: str
    gender: str | None = None
    role: str = "user"
    invited_by: int | None = None
    # 管理面板（include_disabled=true）据此显示「已禁用」并提供恢复入口；
    # 创建赛事的默认参赛人员列表默认只含可用账号
    is_active: bool = True


class UserStats(BaseModel):
    total_matches: int = 0
    total_wins: int = 0
    win_rate: float = 0.0
    tournaments_played: int = 0
