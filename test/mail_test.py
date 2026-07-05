import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = "smtp.cafe24.com"
SMTP_PORT = 465
SENDER    = "jsh77b@jsh77b1.cafe24.com"
PASSWORD  = os.getenv("MAIL_TEST_PASS", "")
RECEIVER  = "ack1000hu@gmail.com"

msg = MIMEText("테스트메일", "plain", "utf-8")
msg["Subject"] = "테스트메일"
msg["From"]    = SENDER
msg["To"]      = RECEIVER

import ssl
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
ctx.set_ciphers('ALL:@SECLEVEL=0')
ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT

with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
    smtp.login(SENDER, PASSWORD)
    smtp.sendmail(SENDER, RECEIVER, msg.as_string())

print("발송 완료")
