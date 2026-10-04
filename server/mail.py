"""메일 발송 (비밀번호 찾기). SMTP_HOST가 없으면 보내지 않고 서버 로그에 남긴다 (개발·테스트용)"""
import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("mail")


def enabled():
    return bool(settings.smtp_host)


def send(to, subject, body):
    if not enabled():
        log.warning("SMTP 미설정: 메일을 보내지 않고 기록만 합니다. 받는 사람=%s 제목=%s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError):
        log.exception("메일 발송 실패: 받는 사람=%s", to)
