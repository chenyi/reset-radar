#!/usr/bin/env python3
"""Independent outbound worker. It never calls the model or the RSS source."""
import base64
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import notifications
import notification_templates
import translations
import server
import storage


def send_dingtalk(message):
    webhook = os.environ['DINGTALK_WEBHOOK']
    parsed = urllib.parse.urlsplit(webhook)
    if parsed.scheme != 'https' or parsed.hostname != 'oapi.dingtalk.com' or parsed.path != '/robot/send':
        raise ValueError('Unexpected DingTalk webhook endpoint')
    secret = os.environ.get('DINGTALK_SECRET')
    if secret:
        millis = str(int(time.time()*1000))
        signature = base64.b64encode(hmac.new(secret.encode(), (millis+'\n'+secret).encode(), hashlib.sha256).digest()).decode()
        query = urllib.parse.parse_qsl(parsed.query) + [('timestamp',millis),('sign',signature)]
        webhook = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    data = {'msgtype':'markdown','markdown':{'title':message['title'],'text':notification_templates.render(message,os.environ.get('NOTIFICATION_STYLE','clear'))},'at':{'isAtAll':False}}
    request = urllib.request.Request(webhook,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read(65536))
        if result.get('errcode') != 0:
            raise RuntimeError('DingTalk rejected the notification')
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError() from None
        # Connection loss can happen after acceptance: preserve the ambiguity.
        raise TimeoutError() from None


def main():
    db = storage.connect(server.DB_FILE)
    notifications.initialize(db)
    notifications.recover(db)
    db.close()
    while True:
        db = storage.connect(server.DB_FILE)
        try:
            snapshot = server.snapshot()
            job = notifications.claim(db,snapshot)
            if job:
                payload=json.loads(job['payload'])
                if payload.get('post',{}).get('post_id'):
                    translated=translations.translated(db)
                    payload['post']['text_zh']=translated.get(payload['post']['post_id'])
                    job['payload']=json.dumps(payload,ensure_ascii=False)
                senders = {'dingtalk':send_dingtalk}
                channel = db.execute('SELECT kind FROM notification_channels WHERE channel_id=?',(job['channel_id'],)).fetchone()
                status = notifications.deliver(db,job,senders[channel['kind']])
                print(f'Notification {job["job_id"][:12]}: {status}',flush=True)
        except Exception as exc:
            print(f'Notification worker paused: {type(exc).__name__}',flush=True)
        finally:
            db.close()
        time.sleep(15)


if __name__ == '__main__':
    main()
