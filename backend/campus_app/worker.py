"""Periodic reminders, no-show release, application timeout, and SLA escalation."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select

from campus_app.clock import now_local
from campus_app.config import (
    CHECKIN_LATE_MINUTES,
    CHECKIN_REMINDER_MINUTES,
    EMAIL_MAX_ATTEMPTS,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SSL,
    SMTP_STARTTLS,
    SMTP_USERNAME,
)
from campus_app.db import SessionLocal, begin_sqlite_write
from campus_app.domain import expire_application, expire_no_show, notify, write_audit
from campus_app.models import ClassroomApplication, Notification, Reservation, User, WorkOrder

logger = logging.getLogger("campus.worker")


def process_due_events(now: datetime | None = None) -> dict[str, int]:
    """Process due jobs once; database status and notification keys make retries safe."""
    current = now or now_local()
    result = {
        "reminders": 0,
        "no_shows": 0,
        "expired_applications": 0,
        "escalations": 0,
        "emails_sent": 0,
        "emails_failed": 0,
    }
    with SessionLocal() as db:
        begin_sqlite_write(db)
        reservations = db.scalars(
            select(Reservation)
            .where(
                Reservation.status == "confirmed",
                Reservation.start_at <= current + timedelta(minutes=15),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for row in reservations:
            if current >= row.start_at - timedelta(minutes=15) and current < row.start_at:
                before_key = f"reservation:{row.id}:reminder-before"
                added = db.scalar(
                    select(Notification.id).where(Notification.unique_key == before_key)
                )
                if not added:
                    notify(
                        db,
                        row.user_id,
                        "reservation",
                        "座位预约即将开始",
                        f"座位 {row.seat_id} 预约将于 {row.start_at:%H:%M} 开始，请准备签到。",
                        row.id,
                        before_key,
                    )
                    result["reminders"] += 1
            if current >= row.start_at + timedelta(
                minutes=CHECKIN_REMINDER_MINUTES
            ) and current < row.start_at + timedelta(minutes=CHECKIN_LATE_MINUTES):
                late_key = f"reservation:{row.id}:reminder-late"
                if (
                    db.scalar(select(Notification.id).where(Notification.unique_key == late_key))
                    is None
                ):
                    notify(
                        db,
                        row.user_id,
                        "reservation",
                        "请尽快签到",
                        f"座位 {row.seat_id} 已开始预约，请在 {(row.start_at + timedelta(minutes=CHECKIN_LATE_MINUTES)):%H:%M} 前签到，到期释放。",
                        row.id,
                        late_key,
                    )
                    result["reminders"] += 1
            if expire_no_show(db, row, current):
                result["no_shows"] += 1

        expired = db.scalars(
            select(ClassroomApplication)
            .where(
                ClassroomApplication.status == "pending",
                ClassroomApplication.timeout_at <= current,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for application in expired:
            if expire_application(db, application, current):
                result["expired_applications"] += 1

        overdue = db.scalars(
            select(WorkOrder).where(
                WorkOrder.status.in_(("submitted", "assigned", "in_repair")),
                WorkOrder.due_at <= current,
                WorkOrder.escalation_sent.is_(False),
            )
        ).all()
        admins = db.scalars(select(User).where(User.role == "admin", User.active.is_(True))).all()
        for work_order in overdue:
            work_order.escalation_sent = True
            for admin in admins:
                notify(
                    db,
                    admin.id,
                    "escalation",
                    "物业工单超时升级",
                    f"工单 {work_order.id} 已超过处理时限，请跟进。",
                    work_order.id,
                    f"work-order:{work_order.id}:escalation:{admin.id}",
                )
            write_audit(db, None, "work_order.escalated", "work_order", work_order.id)
            result["escalations"] += 1

        db.commit()
    email_counts = deliver_pending_emails(current)
    result.update(email_counts)
    return result


def send_notification_email(notification: Notification, recipient: str) -> None:
    """Send one plain-text notification through the configured SMTP relay."""
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message["Subject"] = notification.title
    message.set_content(notification.message)
    tls_context = ssl.create_default_context()
    if SMTP_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=tls_context, timeout=15) as client:
            if SMTP_USERNAME:
                client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as client:
        if SMTP_STARTTLS:
            client.starttls(context=tls_context)
        if SMTP_USERNAME:
            client.login(SMTP_USERNAME, SMTP_PASSWORD)
        client.send_message(message)


def deliver_pending_emails(now: datetime | None = None) -> dict[str, int]:
    """Deliver queued notices with bounded retries while keeping in-app notices available."""
    counts = {"emails_sent": 0, "emails_failed": 0}
    if not SMTP_HOST or not SMTP_FROM:
        return counts
    current = now or now_local()
    with SessionLocal() as db:
        pending = db.scalars(
            select(Notification)
            .join(User)
            .where(
                Notification.email_sent_at.is_(None),
                Notification.email_queued_at.is_not(None),
                Notification.email_attempts < EMAIL_MAX_ATTEMPTS,
                User.email.is_not(None),
            )
            .order_by(Notification.created_at, Notification.id)
            .limit(100)
        ).all()
        for notification in pending:
            recipient = notification.user.email
            if not recipient:
                continue
            notification.email_attempts += 1
            notification.email_last_attempt_at = current
            try:
                send_notification_email(notification, recipient)
            except Exception as error:  # SMTP errors should not stop booking reminders.
                notification.email_last_error = f"{type(error).__name__}: 邮件发送失败"[:500]
                counts["emails_failed"] += 1
                logger.warning(
                    "notification email delivery failed for notification_id=%s attempt=%s",
                    notification.id,
                    notification.email_attempts,
                )
            else:
                notification.email_sent_at = current
                notification.email_last_error = ""
                counts["emails_sent"] += 1
            db.commit()
    return counts


async def run_worker(poll_seconds: int = 30) -> None:
    """Run the scheduled business jobs until the process is stopped."""
    while True:
        try:
            counts = process_due_events()
            if any(counts.values()):
                logger.info("processed due work: %s", counts)
        except Exception:
            logger.exception("scheduled work failed")
        await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
