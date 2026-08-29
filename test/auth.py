# -*- coding: utf-8 -*-
"""用户认证与数据存储：注册、登录、会话、账户信息、记住登录、自选股（SQLite）。"""

import os
import json
import hashlib
import sqlite3

from config import SCRIPT_DIR

DB_FILE = os.path.join(SCRIPT_DIR, "app.db")
REMEMBER_FILE = os.path.join(SCRIPT_DIR, "remember.json")

# 当前登录用户（会话），登录后由 login() 设置
current_user = None


def _connect():
    return sqlite3.connect(DB_FILE)


def init_db():
    """初始化数据库表（含字段迁移）。"""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            UNIQUE(username, code)
        )
    """)
    # 字段迁移（老数据库补新字段）
    for col in ("nickname TEXT", "query_count INTEGER DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE watchlist ADD COLUMN industry TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def hash_password(password, salt=None):
    """PBKDF2 加盐哈希，返回 "salt$hash"。"""
    if salt is None:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            salt.encode("utf-8"), 100000).hex()
    return f"{salt}${h}"


def verify_password(password, stored):
    """验证密码是否正确。"""
    try:
        salt, _ = stored.split("$", 1)
        return hash_password(password, salt) == stored
    except Exception:
        return False


def register(username, password, nickname=""):
    """注册新用户，返回 (是否成功, 消息)。"""
    username = (username or "").strip()
    if not username or not password:
        return False, "用户名和密码不能为空"
    if len(username) < 3:
        return False, "用户名至少 3 个字符"
    if len(password) < 4:
        return False, "密码至少 4 个字符"
    nickname = (nickname or "").strip() or username
    conn = _connect()
    try:
        conn.execute("INSERT INTO users (username, password_hash, nickname) VALUES (?, ?, ?)",
                     (username, hash_password(password), nickname))
        conn.commit()
        return True, "注册成功，请登录"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    finally:
        conn.close()


def login(username, password):
    """登录，成功返回 (True, 消息)，失败返回 (False, 消息)。"""
    username = (username or "").strip()
    conn = _connect()
    row = conn.execute("SELECT username, password_hash FROM users WHERE username = ?",
                       (username,)).fetchone()
    conn.close()
    if row is None:
        return False, "用户名不存在"
    if not verify_password(password, row[1]):
        return False, "密码错误"
    global current_user
    current_user = row[0]
    return True, "登录成功"


def logout():
    """退出登录。"""
    global current_user
    current_user = None


def change_password(username, old_password, new_password):
    """修改密码，返回 (是否成功, 消息)。"""
    conn = _connect()
    row = conn.execute("SELECT password_hash FROM users WHERE username = ?",
                       (username,)).fetchone()
    if row is None:
        conn.close()
        return False, "用户不存在"
    if not verify_password(old_password, row[0]):
        conn.close()
        return False, "原密码错误"
    if len(new_password) < 4:
        conn.close()
        return False, "新密码至少 4 个字符"
    conn.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                 (hash_password(new_password), username))
    conn.commit()
    conn.close()
    return True, "密码修改成功"


def get_user_info(username):
    """返回用户信息 dict：username, nickname, created_at, query_count, watch_count。"""
    if not username:
        return None
    conn = _connect()
    row = conn.execute(
        "SELECT username, nickname, created_at, query_count FROM users WHERE username = ?",
        (username,)).fetchone()
    watch_count = conn.execute(
        "SELECT COUNT(*) FROM watchlist WHERE username = ?", (username,)).fetchone()[0]
    conn.close()
    if row is None:
        return None
    return {
        "username": row[0],
        "nickname": row[1] or row[0],
        "created_at": row[2],
        "query_count": row[3] or 0,
        "watch_count": watch_count,
    }


def change_nickname(username, nickname):
    """修改昵称，返回 (是否成功, 消息)。"""
    nickname = (nickname or "").strip()
    if not nickname:
        return False, "昵称不能为空"
    conn = _connect()
    conn.execute("UPDATE users SET nickname = ? WHERE username = ?", (nickname, username))
    conn.commit()
    conn.close()
    return True, "昵称已更新"


def increment_query_count(username):
    """查询次数 +1。"""
    if not username:
        return
    conn = _connect()
    conn.execute("UPDATE users SET query_count = query_count + 1 WHERE username = ?",
                 (username,))
    conn.commit()
    conn.close()


# ---- 记住登录 ----

def remember_user(username):
    """记住登录用户名。"""
    try:
        with open(REMEMBER_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": username}, f)
    except Exception:
        pass


def forget_user():
    """清除记住的登录。"""
    try:
        if os.path.exists(REMEMBER_FILE):
            os.remove(REMEMBER_FILE)
    except Exception:
        pass


def load_remembered_user():
    """返回记住的用户名，没有则 None。"""
    if not os.path.exists(REMEMBER_FILE):
        return None
    try:
        with open(REMEMBER_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("username")
    except Exception:
        return None


# ---- 自选股数据库操作（按用户名隔离） ----

def db_list_watch(username):
    """返回某用户的自选股 [{"code", "name", "industry"}, ...]。"""
    if not username:
        return []
    conn = _connect()
    rows = conn.execute(
        "SELECT code, name, industry FROM watchlist WHERE username = ? ORDER BY id",
        (username,)).fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "industry": r[2] or ""} for r in rows]


def db_add_watch(username, code, name="", industry=""):
    """添加自选股，返回 (是否成功, 消息)。"""
    if not username:
        return False, "请先登录"
    conn = _connect()
    try:
        conn.execute("INSERT INTO watchlist (username, code, name, industry) VALUES (?, ?, ?, ?)",
                     (username, code, name, industry))
        conn.commit()
        return True, "已添加"
    except sqlite3.IntegrityError:
        return False, "已在自选股中"
    finally:
        conn.close()


def db_remove_watch(username, code):
    """删除自选股，返回 (是否成功, 消息)。"""
    if not username:
        return False, "请先登录"
    conn = _connect()
    cur = conn.execute("DELETE FROM watchlist WHERE username = ? AND code = ?",
                       (username, code))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if deleted:
        return True, "已删除"
    return False, "不在自选股中"
