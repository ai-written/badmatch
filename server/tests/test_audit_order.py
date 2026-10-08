"""审计顺序不变量：成功路径必须「先提交（有广播的还要在广播之后）再写审计」。

为什么这条不变量值得用测试守住：
- 审计用的是**独立会话并立即提交**（这样异常路径也能留痕）。因此若审计写在业务 commit
  之前，一旦业务事务回滚（约束冲突、锁超时、连接断），就会留下一条「某某操作成功了」的
  假日志 —— 事后追溯会被人当成事实。
- 反过来，register / update_profile 这类接口还**必须**先提交：audit_logs.user_id 有指向
  users 的外键，用户行还在未提交的事务里时，那条审计会因外键不满足而写入失败，又被
  audit() 静默吞掉（表现为「注册在操作日志里根本查不到」）。
- 审计**还要晚于广播**：audit() 对请求取消是向上抛的（CancelledError 不吞），写在广播
  之前的话，手机端「提交后立刻切页」会让那次广播被跳过 —— 数据已改，别人的界面停在旧状态。

唯一的例外是**失败路径**（如 login_failed）：它必须在 raise 之前写，因为请求根本走不到
任何 commit。白名单就一条，见下。

判定方式（比"同函数里存在更靠前的 commit"严）：
- 提交判定只看**直接语句**（`await db.commit()` 自成一行），允许往外层语句块找，但
  **不认"藏在更早分支里的 commit"**——那条路径未必会执行到审计。这正是旧版按行号检查的
  漏报点：删掉普通记分分支的 commit，旧检查照样全绿。
- 广播判定只看审计**所在的最内层**语句块：外层后面的广播可能落在其它分支里，静态分不清
  是否可达。
"""
import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "app" / "api"
# 允许出现在 commit 之前的审计：失败路径（请求不会走到 commit）
ALLOWED_BEFORE_COMMIT = ('action="login_failed"',)
# 扫描下限：路径/结构一变就"静默空跑"是这类静态测试最容易骗过人的地方
MIN_FILES = 5
MIN_AUDIT_CALLS = 30

_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _awaited_name(stmt: ast.stmt) -> str | None:
    """识别 `await xxx()` 这种自成一行、且被 await 的调用，返回被调名。"""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
        call = stmt.value.value
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Attribute):
                return func.attr
            if isinstance(func, ast.Name):
                return func.id
    return None


def _has_awaited(stmts, names) -> bool:
    return any(_awaited_name(s) in names for s in stmts)


def _counts_as_prior_commit(stmt: ast.stmt) -> bool:
    """这条更早的语句，能否算作"审计之前已经提交过"。

    - `await db.commit()` 自成一行：算。
    - `try:` / `async with` 块里的提交：算 —— 能走到审计就说明它已经成功执行
      （失败会 raise，根本到不了审计）。upload_avatar 就是这个形状。
    - `if` / `for` / `while` 里的提交：**不算** —— 那条分支未必会执行到审计。
      这正是旧版按行号检查的漏报点：删掉普通记分分支的 commit，旧检查照样全绿。
    """
    if _awaited_name(stmt) == "commit":
        return True
    if isinstance(stmt, (ast.Try, ast.With, ast.AsyncWith)):
        body = getattr(stmt, "body", [])
        return any(_counts_as_prior_commit(s) for s in body)
    return False


def _parents(tree: ast.AST) -> dict:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _block_chain(node: ast.AST, parents: dict) -> list[tuple[list, int]]:
    """从最内层语句块往外，返回 [(块, 该节点所在下标), ...]。"""
    chain: list[tuple[list, int]] = []
    cur = node
    while cur in parents:
        parent = parents[cur]
        moved = False
        for field in _BLOCK_FIELDS:
            block = getattr(parent, field, None)
            if isinstance(block, list) and cur in block:
                chain.append((block, block.index(cur)))
                cur = parent
                moved = True
                break
        if not moved:
            cur = parent  # 跳过非语句字段（Call 的 func/args 等）
    return chain


def _audit_stmt(node: ast.AST | None, parents: dict) -> ast.stmt | None:
    """把 audit(...) 调用节点上溯到承载它的那条语句（通常是 Expr）。"""
    cur = node
    while cur is not None:
        if isinstance(cur, ast.stmt):
            return cur
        cur = parents.get(cur)
    return None


def _iter_audits(path: Path):
    """产出 (函数名, 语句, 源码片段) —— 只认名为 audit 的直接调用。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parents(tree)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in functions:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "audit":
                stmt = _audit_stmt(node, parents)
                if stmt is not None:
                    yield tree, parents, fn, stmt, (ast.get_source_segment(source, node) or "")


def _api_files() -> list[Path]:
    return sorted(API_DIR.glob("*.py"))


def test_scanned_enough_code():
    """下限断言：API 目录结构变了、或 audit 被整段改名/搬走时，不要静默通过。"""
    files = _api_files()
    assert len(files) >= MIN_FILES, f"只扫到 {len(files)} 个 API 文件，检查 API_DIR={API_DIR}"
    total = sum(1 for path in files for _ in _iter_audits(path))
    assert total >= MIN_AUDIT_CALLS, f"只扫到 {total} 处 audit 调用，路径或写法可能已变"


def test_success_path_audits_come_after_commit():
    offenders = []
    for path in _api_files():
        for _tree, parents, fn, stmt, segment in _iter_audits(path):
            chain = _block_chain(stmt, parents)
            # 同一个块里的直接提交，或往外层块找（try/with 里的算，if/for 里的不算）
            if any(any(_counts_as_prior_commit(s) for s in block[:idx]) for block, idx in chain):
                continue
            if "login_failed" in segment:
                continue  # 失败路径：请求走不到 commit
            offenders.append(f"{path.name}:{stmt.lineno} {fn.name}()")
    assert offenders == [], "审计写在 commit 之前（业务回滚会留下假日志）：" + ", ".join(offenders)


def test_success_path_audits_come_after_broadcast():
    offenders = []
    for path in _api_files():
        for _tree, parents, fn, stmt, _segment in _iter_audits(path):
            chain = _block_chain(stmt, parents)
            if not chain:
                continue
            block, idx = chain[0]  # 只查最内层块：外层后面的广播可能在其它分支里
            if _has_awaited(block[idx + 1:], {"broadcast", "_broadcast_match"}):
                offenders.append(f"{path.name}:{stmt.lineno} {fn.name}()")
    assert offenders == [], (
        "审计后面还有广播：audit 遇到请求取消会向上抛，这次广播会被跳过：" + ", ".join(offenders)
    )


def test_session_factory_does_not_expire_on_commit():
    """守护 expire_on_commit=False。

    全仓的成功路径都是「先提交再写审计/再广播」，提交后还会读对象属性
    （_broadcast_match 读 m.score_a、_tournament_detail 读 t.*、audit() 读 user.id、
     delete_user 之后读快照）。一旦改成 True，这些读取会触发同步懒加载，
    在异步上下文里直接 MissingGreenlet —— 成片 500。
    真要用 True，必须先把所有"提交后读 ORM"改成提交前取纯数据。
    """
    from app.core.database import async_session_factory

    assert async_session_factory.kw.get("expire_on_commit") is False
