"""
Shared SQLite database module for Reddit data.

Replaces the JSON-based UFL_master.json with a SQLite database using FTS5
for full-text search. Used by both tools/reddit_search.py and all scrapers.
"""

import sqlite3
import json
import os
import threading

DB_PATH = os.path.join(
    os.path.dirname(__file__), "reddit_scrapes", "master", "UFL_reddit.db"
)

_local = threading.local()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    title         TEXT NOT NULL DEFAULT '',
    selftext      TEXT NOT NULL DEFAULT '',
    author        TEXT,
    created_utc   REAL,
    score         INTEGER DEFAULT 0,
    num_comments  INTEGER DEFAULT 0,
    flair         TEXT DEFAULT '',
    permalink     TEXT DEFAULT '',
    url           TEXT DEFAULT '',
    raw           TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_flair ON posts(flair);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);

CREATE TABLE IF NOT EXISTS comments (
    id                TEXT PRIMARY KEY,
    post_id           TEXT NOT NULL,
    parent_comment_id TEXT,
    author            TEXT,
    body              TEXT NOT NULL DEFAULT '',
    score             INTEGER DEFAULT 0,
    created_utc       REAL,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);

-- FTS5 virtual tables (content-sync to avoid storing text twice)

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    title,
    selftext,
    content='posts',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(
    body,
    content='comments',
    content_rowid='rowid'
);

-- Triggers to keep FTS indexes in sync

CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, title, selftext)
    VALUES (new.rowid, new.title, new.selftext);
END;

CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, selftext)
    VALUES ('delete', old.rowid, old.title, old.selftext);
END;

CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, selftext)
    VALUES ('delete', old.rowid, old.title, old.selftext);
    INSERT INTO posts_fts(rowid, title, selftext)
    VALUES (new.rowid, new.title, new.selftext);
END;

CREATE TRIGGER IF NOT EXISTS comments_ai AFTER INSERT ON comments BEGIN
    INSERT INTO comments_fts(rowid, body)
    VALUES (new.rowid, new.body);
END;

CREATE TRIGGER IF NOT EXISTS comments_ad AFTER DELETE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, body)
    VALUES ('delete', old.rowid, old.body);
END;

CREATE TRIGGER IF NOT EXISTS comments_au AFTER UPDATE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, body)
    VALUES ('delete', old.rowid, old.body);
    INSERT INTO comments_fts(rowid, body)
    VALUES (new.rowid, new.body);
END;
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Return a thread-local SQLite connection, created on first call per thread."""
    path = db_path or DB_PATH
    key = f"conn_{path}"
    if not hasattr(_local, key) or getattr(_local, key) is None:
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        setattr(_local, key, conn)
    return getattr(_local, key)


def init_db(db_path: str | None = None):
    """Create tables, indexes, FTS5 virtual tables, and triggers if needed."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def get_meta(key: str, db_path: str | None = None) -> str | None:
    conn = get_connection(db_path)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str, db_path: str | None = None):
    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def get_post_count(db_path: str | None = None) -> int:
    conn = get_connection(db_path)
    return conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


def get_all_post_ids(db_path: str | None = None) -> set[str]:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT id FROM posts").fetchall()
    return {row["id"] for row in rows}


def post_exists(post_id: str, db_path: str | None = None) -> bool:
    conn = get_connection(db_path)
    row = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row is not None


def _flatten_comments(comments, post_id, parent_id=None):
    """Recursively flatten nested comment dicts into a flat list of tuples."""
    rows = []
    if not comments:
        return rows
    for c in comments:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not cid:
            continue
        rows.append((
            cid,
            post_id,
            parent_id,
            c.get("author"),
            c.get("body") or "",
            c.get("score") or 0,
            c.get("created_utc"),
        ))
        # Recurse into replies
        for reply in c.get("replies") or []:
            if isinstance(reply, dict):
                rows.extend(_flatten_comments([reply], post_id, parent_id=cid))
    return rows


def upsert_post(post: dict, db_path: str | None = None):
    """Insert or replace a single post and its comments."""
    conn = get_connection(db_path)
    raw = post.get("raw")
    raw_json = json.dumps(raw, ensure_ascii=False) if raw else None

    conn.execute(
        """INSERT OR REPLACE INTO posts
           (id, name, title, selftext, author, created_utc, score,
            num_comments, flair, permalink, url, raw)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post["id"],
            post.get("name"),
            post.get("title") or "",
            post.get("selftext") or "",
            post.get("author"),
            post.get("created_utc"),
            post.get("score") or 0,
            post.get("num_comments") or 0,
            post.get("flair") or "",
            post.get("permalink") or "",
            post.get("url") or "",
            raw_json,
        ),
    )

    # Replace comments: delete old, insert new
    comments = post.get("comments")
    if comments is not None:
        conn.execute("DELETE FROM comments WHERE post_id = ?", (post["id"],))
        comment_rows = _flatten_comments(comments, post["id"])
        if comment_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO comments
                   (id, post_id, parent_comment_id, author, body, score, created_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                comment_rows,
            )

    conn.commit()


def upsert_posts_batch(posts: list[dict], db_path: str | None = None):
    """Batch upsert posts within a single transaction."""
    conn = get_connection(db_path)
    try:
        for post in posts:
            raw = post.get("raw")
            raw_json = json.dumps(raw, ensure_ascii=False) if raw else None

            conn.execute(
                """INSERT OR REPLACE INTO posts
                   (id, name, title, selftext, author, created_utc, score,
                    num_comments, flair, permalink, url, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    post["id"],
                    post.get("name"),
                    post.get("title") or "",
                    post.get("selftext") or "",
                    post.get("author"),
                    post.get("created_utc"),
                    post.get("score") or 0,
                    post.get("num_comments") or 0,
                    post.get("flair") or "",
                    post.get("permalink") or "",
                    post.get("url") or "",
                    raw_json,
                ),
            )

            comments = post.get("comments")
            if comments is not None:
                conn.execute("DELETE FROM comments WHERE post_id = ?", (post["id"],))
                comment_rows = _flatten_comments(comments, post["id"])
                if comment_rows:
                    conn.executemany(
                        """INSERT OR REPLACE INTO comments
                           (id, post_id, parent_comment_id, author, body, score, created_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        comment_rows,
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def upsert_comment(comment: dict, post_id: str, parent_comment_id: str | None = None,
                    db_path: str | None = None):
    """Insert or replace a single comment."""
    conn = get_connection(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO comments
           (id, post_id, parent_comment_id, author, body, score, created_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            comment.get("id"),
            post_id,
            parent_comment_id,
            comment.get("author"),
            comment.get("body") or "",
            comment.get("score") or 0,
            comment.get("created_utc"),
        ),
    )
    conn.commit()


def delete_posts(post_ids: list[str], db_path: str | None = None):
    """Delete posts by ID. Comments are cascade-deleted via FK."""
    if not post_ids:
        return
    conn = get_connection(db_path)
    # Batch in chunks to avoid SQLite variable limit
    chunk_size = 500
    for i in range(0, len(post_ids), chunk_size):
        chunk = post_ids[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM posts WHERE id IN ({placeholders})", chunk)
    conn.commit()


def get_post_with_comments(post_id: str, db_path: str | None = None) -> dict | None:
    """Fetch a single post with its comments as a dict."""
    conn = get_connection(db_path)
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        return None
    result = dict(post)
    comments = conn.execute(
        "SELECT * FROM comments WHERE post_id = ? ORDER BY score DESC",
        (post_id,),
    ).fetchall()
    result["comments"] = [dict(c) for c in comments]
    return result


def export_to_json(output_path: str, db_path: str | None = None):
    """Export the entire database to a JSON file matching the original format."""
    conn = get_connection(db_path)
    posts_rows = conn.execute("SELECT * FROM posts").fetchall()

    posts = {}
    for row in posts_rows:
        post = dict(row)
        pid = post["id"]
        # Restore raw from JSON string
        if post.get("raw"):
            post["raw"] = json.loads(post["raw"])
        else:
            post.pop("raw", None)

        comments = conn.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY created_utc",
            (pid,),
        ).fetchall()
        post["comments"] = [
            {
                "id": c["id"],
                "author": c["author"],
                "body": c["body"],
                "score": c["score"],
                "created_utc": c["created_utc"],
                "replies": [],
            }
            for c in comments
            if c["parent_comment_id"] is None
        ]
        # Nest replies under their parents
        reply_map = {}
        for c in comments:
            if c["parent_comment_id"] is not None:
                reply_map.setdefault(c["parent_comment_id"], []).append({
                    "id": c["id"],
                    "author": c["author"],
                    "body": c["body"],
                    "score": c["score"],
                    "created_utc": c["created_utc"],
                    "replies": [],
                })
        for comment in post["comments"]:
            comment["replies"] = reply_map.get(comment["id"], [])

        posts[pid] = post

    # Meta
    meta = {}
    for row in conn.execute("SELECT key, value FROM meta").fetchall():
        meta[row["key"]] = row["value"]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"posts": posts, "meta": meta}, f, indent=2, ensure_ascii=False)
