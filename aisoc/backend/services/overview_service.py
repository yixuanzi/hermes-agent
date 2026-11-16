"""Overview service adapters."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta
import re
from typing import Any
import time

from hermes_state import SessionDB
from hermes_cli.config import load_config
from hermes_cli.profiles import get_active_profile_name

from aisoc.backend.services import cron_service, memory_service


_STATUS_RECENT_WINDOW_SECONDS = 6 * 3600
_ACTIVE_SESSION_WINDOW_SECONDS = 24 * 3600
_DEFAULT_MEMORY_LIMIT = 2200
_DEFAULT_USER_LIMIT = 1375
_KEYWORD_WINDOW_SECONDS = 7 * 86400
_EVENT_SUMMARY_MAX_CHARS = 180
_SECURITY_SESSION_WINDOW_SECONDS = 3 * 86400

_SECURITY_JOB_TYPES: dict[str, dict[str, str]] = {
    "f721eacc24df": {"type": "vuln_tracking", "label": "漏洞追踪", "icon": "shield"},
    "172f0e3f08af": {"type": "attack_sim", "label": "AD域攻击模拟", "icon": "sword"},
    "ddb63af96737": {"type": "attack_sim", "label": "终端攻击模拟", "icon": "terminal"},
    "beffcc9eee81": {"type": "email_security", "label": "邮件安全", "icon": "mail"},
    "e5eccc44d2fa": {"type": "daily_report", "label": "安全日报", "icon": "report"},
    "614ad4c64bdb": {"type": "vuln_assessment", "label": "漏洞研判", "icon": "search"},
}
_DEFAULT_SECURITY_TYPE = {"type": "investigate", "label": "安全事件", "icon": "investigate"}
_SESSION_INVESTIGATION_TYPE = {"type": "session_investigation", "label": "会话调查", "icon": "investigate"}

# Heuristic keyword set for flagging a non-cron session as a security
# investigation.  Curated + easily tunable; bare "安全" is intentionally
# excluded to avoid matching ordinary dev/chat sessions.
_SECURITY_KEYWORDS: list[str] = [
    # english (matched as lowercased substrings)
    "cve", "vulnerab", "exploit", "malware", "phishing", "ransomware", "attack",
    "threat", "incident", "breach", "compromise", "backdoor", "webshell", "ioc", "0day", "apt",
    # chinese
    "漏洞", "攻击", "钓鱼", "恶意", "应急", "溯源", "入侵", "威胁", "勒索",
    "后门", "提权", "横向移动", "失陷", "研判", "安全事件",
]

_EN_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "have",
    "will",
    "your",
    "about",
    "into",
    "user",
    "session",
    "message",
    "messages",
    "content",
    "response",
    "prompt",
    "tool",
    "result",
    "output",
    "input",
    "error",
    "status",
    "task",
    "cron",
    "today",
    "daily",
}

_ZH_STOP_WORDS = {
    "这个",
    "那个",
    "一些",
    "可以",
    "需要",
    "使用",
    "进行",
    "问题",
    "功能",
    "系统",
    "内容",
    "消息",
    "会话",
    "今天",
    "安全",
}


def _today_start_ts(now_ts: float) -> float:
    now_dt = datetime.fromtimestamp(now_ts)
    return now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _query_one(db: SessionDB, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    with db._lock:
        row = db._conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def _query_all(db: SessionDB, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with db._lock:
        rows = db._conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _read_memory_len(reader) -> int:
    try:
        payload = reader()
    except Exception:
        return 0
    return len((payload or {}).get("content") or "")


def _memory_breakdown() -> dict[str, int]:
    """Per-store character usage + limits for soul and user-preferences memory."""
    soul_used = _read_memory_len(memory_service.read_soul)
    user_used = _read_memory_len(memory_service.read_user_preferences)

    soul_limit = _DEFAULT_MEMORY_LIMIT
    user_limit = _DEFAULT_USER_LIMIT
    try:
        config = load_config()
        mem_cfg = (config or {}).get("memory") or {}
        soul_limit = int(mem_cfg.get("memory_char_limit", _DEFAULT_MEMORY_LIMIT))
        user_limit = int(mem_cfg.get("user_char_limit", _DEFAULT_USER_LIMIT))
    except Exception:
        pass
    return {
        "soul_used": soul_used,
        "soul_limit": soul_limit,
        "user_used": user_used,
        "user_limit": user_limit,
    }


def _memory_percent(used: int, limit: int) -> float:
    return round(used / limit * 100, 1) if limit > 0 else 0.0


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cron_session_pattern(job_id: str) -> str:
    return f"cron_{_escape_like(job_id)}%"


def _format_schedule(schedule: Any) -> str:
    if isinstance(schedule, dict):
        display = schedule.get("display")
        expr = schedule.get("expr")
        return str(display or expr or "unknown")
    return str(schedule or "unknown")


def _extract_cron_job_id(session_id: str) -> str:
    if not session_id.startswith("cron_"):
        return ""
    suffix = session_id[5:]
    return (suffix.split("_", 1)[0] or suffix[:12]).strip()


def _event_type_from_session_id(session_id: str) -> dict[str, str]:
    job_id = _extract_cron_job_id(session_id)
    if not job_id:
        return _DEFAULT_SECURITY_TYPE
    return _SECURITY_JOB_TYPES.get(job_id, _DEFAULT_SECURITY_TYPE)


def _extract_entities(text: str) -> list[str]:
    if not text:
        return []
    entities: list[str] = []
    seen: set[str] = set()

    def _add(values: list[str]) -> None:
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            entities.append(value)
            if len(entities) >= 6:
                return

    _add(re.findall(r"CVE-\d{4}-\d{4,}", text))
    _add(
        [
            ip
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
            if not ip.startswith("127.") and not ip.startswith("0.")
        ]
    )
    _add(re.findall(r"\b[a-z0-9][a-z0-9.-]+\.[a-z]{2,}\b", text.lower()))
    return entities[:6]


def _detect_event_status(end_reason: str, text: str) -> str:
    blob = f"{end_reason} {text}".lower()
    has_fail = any(k in blob for k in ("fail", "error", "timeout", "超时", "失败"))
    has_ok = any(k in blob for k in ("done", "success", "completed", "完成", "成功"))
    if has_fail and has_ok:
        return "partial"
    if has_fail:
        return "failed"
    return "completed"


def _detect_risk_level(text: str) -> str:
    blob = text.lower()
    score_match = re.search(r"(\d{1,3})\s*/\s*100", blob)
    if score_match:
        score = int(score_match.group(1))
        if score >= 80:
            return "Critical"
        if score >= 60:
            return "High"
        if score >= 40:
            return "Medium"
        if score >= 20:
            return "Low"
        return "Info"
    if any(k in blob for k in ("critical", "严重", "紧急")):
        return "Critical"
    if any(k in blob for k in ("high", "高危")):
        return "High"
    if any(k in blob for k in ("medium", "中危")):
        return "Medium"
    if any(k in blob for k in ("low", "低危")):
        return "Low"
    return "Info"


def _build_event_summary(title: str, text: str) -> str:
    title = (title or "").strip()
    if title:
        return title[:_EVENT_SUMMARY_MAX_CHARS]

    snippet = re.sub(r"\s+", " ", (text or "")).strip()
    if snippet:
        return snippet[:_EVENT_SUMMARY_MAX_CHARS]
    return "Security event captured."


def _normalize_keyword_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"^\[IMPORTANT:.*?\]\s*", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"^---\s*\n?name:\s*\S+\s*", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _period_start(period: str, now_ts: float) -> float:
    if period == "today":
        return _today_start_ts(now_ts)
    if period == "7d":
        return now_ts - 7 * 86400
    if period == "30d":
        return now_ts - 30 * 86400
    raise ValueError("period must be today, 7d, or 30d")


def get_cronjobs() -> list[dict[str, Any]]:
    try:
        jobs = cron_service.list_jobs(profile="all")
    except Exception:
        return []
    db = SessionDB()
    try:
        result: list[dict[str, Any]] = []
        for job in jobs:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            like_pattern = _cron_session_pattern(job_id)
            last_run = _query_one(
                db,
                """
                SELECT id, started_at, ended_at,
                       COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS total_tokens,
                       end_reason
                FROM sessions
                WHERE id LIKE ? ESCAPE '\\'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (like_pattern,),
            )
            run_count_row = _query_one(
                db,
                "SELECT COUNT(*) AS count FROM sessions WHERE id LIKE ? ESCAPE '\\'",
                (like_pattern,),
            )
            result.append(
                {
                    "id": job_id,
                    "name": str(job.get("name") or "unnamed"),
                    "enabled": bool(job.get("enabled", True)),
                    "schedule": _format_schedule(job.get("schedule")),
                    "last_run": (
                        {
                            "session_id": str(last_run.get("id") or ""),
                            "started_at": last_run.get("started_at"),
                            "ended_at": last_run.get("ended_at"),
                            "tokens": int(last_run.get("total_tokens") or 0),
                            "status": str(last_run.get("end_reason") or "completed"),
                        }
                        if last_run
                        else None
                    ),
                    "run_count": int(run_count_row.get("count") or 0),
                }
            )
        return result
    finally:
        db.close()


def get_status() -> dict[str, Any]:
    now_ts = time.time()
    db = SessionDB()
    try:
        summary = _query_one(
            db,
            """
            SELECT
              COUNT(*) AS total_sessions,
              COALESCE(MAX(COALESCE(
                (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id),
                s.started_at
              )), 0) AS last_activity,
              COALESCE(MIN(s.started_at), 0) AS first_seen
            FROM sessions s
            """,
        )
        latest = _query_one(
            db,
            """
            SELECT s.model, s.billing_provider
            FROM sessions s
            ORDER BY COALESCE(
                (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id),
                s.started_at
            ) DESC, s.started_at DESC
            LIMIT 1
            """,
        )
    finally:
        db.close()

    total_sessions = int(summary.get("total_sessions") or 0)
    last_activity = float(summary.get("last_activity") or 0)
    first_seen = float(summary.get("first_seen") or 0)
    if total_sessions == 0:
        uptime_seconds = 0
    else:
        uptime_seconds = int(max(now_ts - first_seen, 0))

    status = "ONLINE" if last_activity and (now_ts - last_activity) < _STATUS_RECENT_WINDOW_SECONDS else "IDLE"
    try:
        profile = get_active_profile_name()
    except Exception:
        profile = "default"

    return {
        "status": status,
        "model": str(latest.get("model") or ""),
        "provider": str(latest.get("billing_provider") or ""),
        "profile": profile,
        "uptime_seconds": uptime_seconds,
        "last_activity": last_activity,
    }


def get_stats() -> dict[str, Any]:
    now_ts = time.time()
    today_start = _today_start_ts(now_ts)
    day_ago = now_ts - _ACTIVE_SESSION_WINDOW_SECONDS

    db = SessionDB()
    try:
        # Intentionally bucket today's counters by sessions.started_at to mirror
        # aisoc-dashboard reference behavior.
        summary = _query_one(
            db,
            """
            SELECT
              COUNT(*) AS total_sessions,
              SUM(CASE WHEN s.started_at > ? AND s.ended_at IS NULL THEN 1 ELSE 0 END) AS active_sessions,
              SUM(CASE WHEN s.started_at >= ? THEN COALESCE(s.input_tokens, 0) ELSE 0 END) AS today_input_tokens,
              SUM(CASE WHEN s.started_at >= ? THEN COALESCE(s.output_tokens, 0) ELSE 0 END) AS today_output_tokens,
              SUM(CASE WHEN s.started_at >= ? THEN COALESCE(s.actual_cost_usd, s.estimated_cost_usd, 0) ELSE 0 END) AS today_cost_usd
            FROM sessions s
            """,
            (day_ago, today_start, today_start, today_start),
        )
        source_rows = _query_all(
            db,
            """
            SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS count
            FROM sessions
            GROUP BY COALESCE(source, 'unknown')
            ORDER BY source ASC
            """,
        )
    finally:
        db.close()

    jobs: list[dict[str, Any]]
    try:
        jobs = cron_service.list_jobs(profile="all")
    except Exception:
        jobs = []
    cron_total = len(jobs)
    cron_enabled = sum(1 for job in jobs if job.get("enabled", True))

    mem = _memory_breakdown()
    soul_used, soul_limit = mem["soul_used"], mem["soul_limit"]
    user_used, user_limit = mem["user_used"], mem["user_limit"]
    memory_used_chars = soul_used + user_used
    memory_total_chars = max(soul_limit + user_limit, 0)
    memory_percent = _memory_percent(memory_used_chars, memory_total_chars)
    soul_percent = _memory_percent(soul_used, soul_limit)
    user_percent = _memory_percent(user_used, user_limit)

    today_input_tokens = int(summary.get("today_input_tokens") or 0)
    today_output_tokens = int(summary.get("today_output_tokens") or 0)
    today_cost_usd = round(float(summary.get("today_cost_usd") or 0.0), 4)
    source_distribution = {str(row.get("source") or "unknown"): int(row.get("count") or 0) for row in source_rows}

    return {
        "total_sessions": int(summary.get("total_sessions") or 0),
        "active_sessions": int(summary.get("active_sessions") or 0),
        "today_tokens": today_input_tokens + today_output_tokens,
        "today_input_tokens": today_input_tokens,
        "today_output_tokens": today_output_tokens,
        "today_cost_usd": today_cost_usd,
        "cron_jobs_total": cron_total,
        "cron_jobs_enabled": cron_enabled,
        "memory_used_chars": memory_used_chars,
        "memory_total_chars": memory_total_chars,
        "memory_percent": memory_percent,
        "memory_soul_chars": soul_used,
        "memory_soul_limit": soul_limit,
        "memory_soul_percent": soul_percent,
        "memory_user_chars": user_used,
        "memory_user_limit": user_limit,
        "memory_user_percent": user_percent,
        "source_distribution": source_distribution,
    }


def get_token_trend(days: int) -> list[dict[str, Any]]:
    now_ts = time.time()
    today = datetime.fromtimestamp(now_ts).date()
    start_day = today - timedelta(days=days - 1)
    start_ts = datetime.combine(start_day, dt_time.min).timestamp()
    end_ts = datetime.combine(today + timedelta(days=1), dt_time.min).timestamp()

    db = SessionDB()
    try:
        # Intentionally aggregate trend by sessions.started_at day to mirror
        # aisoc-dashboard reference behavior.
        trend_rows = _query_all(
            db,
            """
            SELECT
              date(s.started_at, 'unixepoch', 'localtime') AS day,
              SUM(COALESCE(s.input_tokens, 0)) AS input_tokens,
              SUM(COALESCE(s.output_tokens, 0)) AS output_tokens,
              COUNT(*) AS sessions
            FROM sessions s
            WHERE s.started_at >= ? AND s.started_at < ?
            GROUP BY day
            """,
            (start_ts, end_ts),
        )
    finally:
        db.close()

    day_map = {str(row.get("day") or ""): row for row in trend_rows}
    results: list[dict[str, Any]] = []
    for i in range(days):
        current_day: date = start_day + timedelta(days=i)
        day_key = current_day.isoformat()
        row = day_map.get(day_key, {})
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        sessions = int(row.get("sessions") or 0)
        results.append(
            {
                "date": current_day.strftime("%m-%d"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "sessions": sessions,
            }
        )
    return results


def get_model_usage(period: str) -> dict[str, Any]:
    now_ts = time.time()
    start_ts = _period_start(period, now_ts)

    db = SessionDB()
    try:
        rows = _query_all(
            db,
            """
            SELECT
              COALESCE(model, 'unknown') AS model,
              COUNT(*) AS sessions,
              COALESCE(SUM(input_tokens), 0) AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
              COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
              COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
              COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) AS cost_usd
            FROM sessions
            WHERE started_at >= ?
            GROUP BY COALESCE(model, 'unknown')
            ORDER BY (COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)) DESC
            """,
            (start_ts,),
        )
    finally:
        db.close()

    models: list[dict[str, Any]] = []
    grand_total_tokens = 0
    grand_cost = 0.0
    for row in rows:
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        total_tokens = input_tokens + output_tokens
        cost_usd = float(row.get("cost_usd") or 0.0)
        grand_total_tokens += total_tokens
        grand_cost += cost_usd
        models.append(
            {
                "model": str(row.get("model") or "unknown"),
                "sessions": int(row.get("sessions") or 0),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(row.get("cache_write_tokens") or 0),
                "reasoning_tokens": int(row.get("reasoning_tokens") or 0),
                "cost_usd": round(cost_usd, 4),
            }
        )

    for model in models:
        model["percent_of_total"] = (
            round(model["total_tokens"] / grand_total_tokens * 100, 1) if grand_total_tokens > 0 else 0.0
        )

    return {
        "period": period,
        "total_tokens": grand_total_tokens,
        "total_cost_usd": round(grand_cost, 4),
        "models": models,
    }


def _session_row_to_event(db: SessionDB, row: dict[str, Any], meta: dict[str, str]) -> dict[str, Any] | None:
    session_id = str(row.get("id") or "")
    if not session_id:
        return None
    assistant = _query_one(
        db,
        """
        SELECT content FROM messages
        WHERE session_id = ? AND role = 'assistant'
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (session_id,),
    )
    final_text = str(assistant.get("content") or "")
    started_at = row.get("started_at")
    ended_at = row.get("ended_at")
    duration = None
    if started_at is not None and ended_at is not None:
        try:
            duration = max(int(float(ended_at) - float(started_at)), 0)
        except Exception:
            duration = None
    risk_level = _detect_risk_level(final_text)
    status = _detect_event_status(str(row.get("end_reason") or ""), final_text)
    verdict = ""
    if status == "failed":
        verdict = "BLOCK"
    elif risk_level in {"Critical", "High"}:
        verdict = "REVIEW"
    return {
        "session_id": session_id,
        "type": meta["type"],
        "type_label": meta["label"],
        "icon": meta["icon"],
        "time": started_at,
        "duration": duration,
        "tokens": int(row.get("total_tokens") or 0),
        "status": status,
        "risk_level": risk_level,
        "summary": _build_event_summary(str(row.get("title") or ""), final_text),
        "entities": _extract_entities(final_text),
        "verdict": verdict,
    }


def _recent_security_session_rows(db: SessionDB, since: float, limit: int) -> list[dict[str, Any]]:
    """Recent non-cron sessions whose title or any message hits a security keyword."""
    like_patterns = [f"%{_escape_like(keyword.lower())}%" for keyword in _SECURITY_KEYWORDS]
    title_ors = " OR ".join(["LOWER(COALESCE(s.title, '')) LIKE ? ESCAPE '\\'"] * len(like_patterns))
    msg_ors = " OR ".join(["LOWER(COALESCE(m.content, '')) LIKE ? ESCAPE '\\'"] * len(like_patterns))
    sql = f"""
        SELECT s.id AS id, s.title AS title, s.started_at AS started_at, s.ended_at AS ended_at,
               s.end_reason AS end_reason,
               COALESCE(s.input_tokens, 0) + COALESCE(s.output_tokens, 0) AS total_tokens
        FROM sessions s
        WHERE s.started_at > ?
          AND COALESCE(s.source, '') != 'cron'
          AND (
            ({title_ors})
            OR EXISTS (
              SELECT 1 FROM messages m
              WHERE m.session_id = s.id AND ({msg_ors})
            )
          )
        ORDER BY s.started_at DESC
        LIMIT ?
    """
    params = [since, *like_patterns, *like_patterns, limit]
    return _query_all(db, sql, tuple(params))


def list_security_events(limit: int) -> list[dict[str, Any]]:
    safe_limit = min(max(int(limit or 0), 1), 50)
    db = SessionDB()
    try:
        cron_rows = _query_all(
            db,
            """
            SELECT id, title, started_at, ended_at, end_reason,
                   COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS total_tokens
            FROM sessions
            WHERE source = 'cron'
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        session_rows = _recent_security_session_rows(
            db, time.time() - _SECURITY_SESSION_WINDOW_SECONDS, safe_limit
        )

        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row, meta in (
            *((row, _event_type_from_session_id(str(row.get("id") or ""))) for row in cron_rows),
            *((row, _SESSION_INVESTIGATION_TYPE) for row in session_rows),
        ):
            session_id = str(row.get("id") or "")
            if not session_id or session_id in seen:
                continue
            event = _session_row_to_event(db, row, meta)
            if event is None:
                continue
            seen.add(session_id)
            events.append(event)

        events.sort(key=lambda event: (event.get("time") is not None, event.get("time") or 0), reverse=True)
        return events[:safe_limit]
    finally:
        db.close()


def list_keywords() -> list[dict[str, Any]]:
    since = time.time() - _KEYWORD_WINDOW_SECONDS
    db = SessionDB()
    try:
        rows = _query_all(
            db,
            """
            SELECT s.id, s.title,
                   (
                     SELECT substr(m.content, 1, 240)
                     FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.timestamp
                     LIMIT 1
                   ) AS first_msg
            FROM sessions s
            WHERE s.started_at > ? AND COALESCE(s.message_count, 0) >= 1
            ORDER BY s.started_at DESC
            LIMIT 120
            """,
            (since,),
        )
    finally:
        db.close()

    text_pool: list[str] = []
    for row in rows:
        title = _normalize_keyword_text(str(row.get("title") or ""))
        if title and "### Task:" not in title and "Suggest 3-5" not in title:
            text_pool.append(title)
        first_msg = _normalize_keyword_text(str(row.get("first_msg") or ""))
        if first_msg:
            text_pool.append(first_msg)

    corpus = " ".join(text_pool)
    if not corpus:
        return []

    # Only surface security-relevant keywords: count occurrences of the curated
    # security vocabulary (_SECURITY_KEYWORDS) in the recent-session corpus.
    lowered = corpus.lower()
    keywords: list[dict[str, Any]] = []
    for term in _SECURITY_KEYWORDS:
        term_lower = term.lower()
        if re.search(r"[\u4e00-\u9fff]", term):
            count = lowered.count(term_lower)
            lang = "zh"
        else:
            # Word-initial match so short stems (e.g. "apt") don't match inside
            # unrelated words like "laptop"; the trailing run catches inflections
            # ("attack" -> "attacks", "vulnerab" -> "vulnerability").
            count = len(re.findall(r"(?<![a-z0-9])" + re.escape(term_lower) + r"[a-z0-9]*", lowered))
            lang = "en"
        if count <= 0:
            continue
        keywords.append({"word": term, "count": int(count), "lang": lang})

    keywords.sort(key=lambda item: (-int(item["count"]), str(item["word"])))
    return keywords[:20]


def list_keyword_sessions(keyword: str) -> list[dict[str, Any]]:
    needle = (keyword or "").strip()
    if not needle:
        return []
    since = time.time() - _KEYWORD_WINDOW_SECONDS
    like_pattern = f"%{_escape_like(needle)}%"

    db = SessionDB()
    try:
        rows = _query_all(
            db,
            """
            SELECT
              s.id AS session_id,
              COALESCE(
                NULLIF(s.title, ''),
                (
                  SELECT substr(m.content, 1, 120)
                  FROM messages m
                  WHERE m.session_id = s.id AND m.role = 'user'
                  ORDER BY m.timestamp
                  LIMIT 1
                ),
                s.id
              ) AS title,
              COALESCE(s.source, 'unknown') AS source,
              s.started_at AS started_at,
              COALESCE(s.message_count, 0) AS messages,
              COALESCE(s.input_tokens, 0) + COALESCE(s.output_tokens, 0) AS tokens
            FROM sessions s
            WHERE s.started_at > ?
              AND (
                LOWER(COALESCE(s.title, '')) LIKE LOWER(?) ESCAPE '\\'
                OR EXISTS (
                  SELECT 1
                  FROM messages m
                  WHERE m.session_id = s.id
                    AND m.role = 'user'
                    AND LOWER(COALESCE(m.content, '')) LIKE LOWER(?) ESCAPE '\\'
                )
              )
            ORDER BY s.started_at DESC
            LIMIT 10
            """,
            (since, like_pattern, like_pattern),
        )
    finally:
        db.close()

    return [
        {
            "session_id": str(row.get("session_id") or ""),
            "title": str(row.get("title") or ""),
            "source": str(row.get("source") or "unknown"),
            "started_at": row.get("started_at"),
            "messages": int(row.get("messages") or 0),
            "tokens": int(row.get("tokens") or 0),
        }
        for row in rows
    ]


def get_cron_token_distribution(period: str) -> dict[str, Any]:
    now_ts = time.time()
    since = _period_start(period, now_ts)
    jobs: list[dict[str, Any]]
    try:
        jobs = cron_service.list_jobs(profile="all")
    except Exception:
        jobs = []
    job_name_candidates = [
        (str(job.get("id") or ""), str(job.get("name") or job.get("id") or ""))
        for job in jobs
        if str(job.get("id") or "")
    ]

    db = SessionDB()
    try:
        cron_rows = _query_all(
            db,
            """
            SELECT
              CASE
                WHEN instr(substr(id, 6), '_') > 0
                THEN substr(substr(id, 6), 1, instr(substr(id, 6), '_') - 1)
                ELSE substr(id, 6, 12)
              END AS job_id,
              COUNT(*) AS runs,
              COALESCE(SUM(input_tokens), 0) AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) AS io_tokens,
              COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
              COALESCE(SUM(cache_write_tokens), 0) AS cache_write
            FROM sessions
            WHERE source = 'cron'
              AND id LIKE 'cron_%'
              AND started_at >= ?
            GROUP BY job_id
            ORDER BY io_tokens DESC
            """,
            (since,),
        )
        non_cron = _query_one(
            db,
            """
            SELECT COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) AS io_tokens
            FROM sessions
            WHERE (source != 'cron' OR source IS NULL)
              AND started_at >= ?
            """,
            (since,),
        )
    finally:
        db.close()

    non_cron_tokens = int(non_cron.get("io_tokens") or 0)
    total_cron_tokens = sum(int(row.get("io_tokens") or 0) for row in cron_rows)
    grand_total = total_cron_tokens + non_cron_tokens

    def _resolve_job_name(job_id: str) -> str:
        for full_id, name in job_name_candidates:
            if full_id == job_id or full_id.startswith(job_id):
                return name or job_id
        return job_id

    dist_jobs: list[dict[str, Any]] = []
    for row in cron_rows:
        io_tokens = int(row.get("io_tokens") or 0)
        dist_jobs.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "name": _resolve_job_name(str(row.get("job_id") or "")),
                "runs": int(row.get("runs") or 0),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "io_tokens": io_tokens,
                "cache_read": int(row.get("cache_read") or 0),
                "cache_write": int(row.get("cache_write") or 0),
                "percent_of_cron": round(io_tokens / total_cron_tokens * 100, 1) if total_cron_tokens > 0 else 0,
                "percent_of_total": round(io_tokens / grand_total * 100, 1) if grand_total > 0 else 0,
            }
        )

    return {
        "period": period,
        "total_cron_tokens": total_cron_tokens,
        "non_cron_tokens": non_cron_tokens,
        "grand_total": grand_total,
        "cron_percent": round(total_cron_tokens / grand_total * 100, 1) if grand_total > 0 else 0,
        "jobs": dist_jobs,
    }
