"""Readable Chinese Markdown variants for outbound channels."""
from datetime import datetime, timedelta, timezone
import os

STYLES = ('clear','brief','bilingual')
TITLES = {'forecast':('🟢','重置预告'), 'reminder':('⏰','临近公告时间'),
          'completed':('✅','作者宣布重置完成'), 'correction':('📝','时间更正'), 'cancelled':('⚪','预告取消')}


def render(message, style='clear'):
    if 'post' not in message:
        return message['text']
    icon,title=TITLES[message['kind']]
    post=message['post']
    chinese=post.get('text_zh') or '作者发布了额度重置相关消息，中文译文暂不可用，请查看原文。'
    target=message.get('target_at')
    label=message.get('time_label','预计时间')
    when='时间尚未明确'
    if target:
        local=datetime.fromisoformat(target.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
        when=f'{local:%m月%d日 %H:%M}（北京时间）'
    url=post.get('url','https://x.com/thsottiaux')
    origin=os.environ.get('PUBLIC_ORIGIN',message.get('origin','https://radar.shadowearth.cn')).rstrip('/')
    links=f'[查看原文]({url}) · [查看重置雷达]({origin}/#event/{message["event_id"]})'
    note='公开公告不保证个人额度到账，请在自己的账号核对。'
    if message['kind']=='reminder':
        note='这是公告时间前的提醒，尚不能据此认定重置已完成。'
    preview='**样式预览 · 不是新公告**\n\n' if message.get('preview') else ''
    if style=='brief':
        return preview+f'{icon} **{title}**\n\n{label}：{when}\n\n{chinese}\n\n{links}\n\n> {note}'
    result=f'### {icon} {title}\n\n**{label}**\n> {when}\n\n**Tibo 消息 · 中文译文**\n\n{chinese}\n\n'
    if style=='bilingual':
        result+='**作者原文**\n\n'+post.get('text','')+'\n\n'
    result=result+links+'\n\n---\n\n'+note
    return preview+result
