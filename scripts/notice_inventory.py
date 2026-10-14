"""Persistent no-repeat notice inventory; no mail is sent by this module."""
import base64
import json
import random
from .github_api import GitHubAPIError
from .state_store import STATE_BRANCH, StateBranch
from . import send_mail

PATH='.mail-bot/notice-inventory.json'
REFILL_SIZE=10
LOW_WATERMARK=5
EXTRA_LINES=(
    "信纸刚刚清点完毕，新故事还在路上，先收下一个微笑。",
    "你来得刚刚好，只是今天的信箱暂时没有新任务。",
    "小澪把重复的信折成纸飞机，又悄悄放回抽屉。",
    "新信尚未抵达，但窗外的星光替我向你问好。",
    "邮差今天不用跑第二趟，换小澪送你一声加油。",
    "信箱的小灯还亮着，下一封惊喜正在慢慢酝酿。",
    "今天没有新信，也请记得认真吃饭和休息。",
    "龙角轻轻碰到信箱盖：咦，今天又来巡视啦？",
    "你听见了吗？那是信纸翻页的声音，不是重复发送。",
    "这一封只是小纸条：愿你今天遇到一点点好事。",
    "没有新的任务编号，却有新的晚风吹过窗台。",
    "小澪在信箱旁画了颗星星，留给下次来看信的你。",
    "原来的心意已经送出，这次换一句轻轻的问候。",
    "小澪认真检查完毕：信件没有走丢，只是暂时没有新篇章。",
    "你又按了一次呀？那我就再说一次：欢迎回来。",
    "小小邮局今日安静营业，暂无新的正文等待出发。",
    "信封排好了队，但今天没有需要再次出发的旧信。",
    "这张提示纸条是新写的，上一封正文可没有偷偷复制。",
    "把今天的忙碌暂时放一放，给自己一个短短的休息。",
    "紫色眼睛眨了眨：检测完毕，信箱一切安好。",
    "今天的风有点轻，适合等待下一封全新的故事。",
    "小澪把邮戳收起来啦，旧信不会再被盖第二次。",
    "本次没有新增正文任务，送达的是一条独立提示。",
    "空信箱不代表空白的一天，愿你今天也有小惊喜。",
    "信纸还没写满，下一次见面再讲新的故事吧。",
    "巡检结束，信箱整整齐齐，重复正文已被挡在门外。",
    "小澪正慢慢整理故事本，下一页还没有翻到哦。",
    "又来看信啦？我在这里留下一句温柔的你好。",
    "这一次邮差只带来通知，没有带来第二份相同的正文。",
    "今天暂时没有新任务，但小澪的小纸条已经准备好了。",
    "旧信已经寄出，新的一页会在合适的时候打开。",
    "这张便签写着：保持好奇，下一封信会更有趣。",
    "小澪把尾巴绕成问号：今天想看看哪一页故事呢？",
    "信箱里的回声说：没有新任务，一切正常。",
    "这是一封小小的状态报告，也是一句悄悄的问候。",
    "没有待发正文，先给你留下一片想象中的云。",
    "邮局窗台上的花开了，信箱今天仍然安静。",
    "别担心，信件记录都还在，新的故事暂未排队。",
    "按键声被小澪听见啦，今天送你一张温柔便签。",
    "下一封信还在构思，这一封只负责告诉你平安。",
    "夜色降临时，信箱里仍然没有重复的脚步声。",
    "今天的任务完成啦，剩下的时间留给好心情。",
    "如果期待有声音，大概就是你轻轻点下发送按钮。",
    "小澪把一颗星星画在账本边：没有新任务。",
    "这一次只有简短报告：已检查，未重复发送正文。",
    "故事还没有续篇，先把一句祝福放进信封。",
    "欢迎来到小澪邮局，今天的库存是温柔的问候。",
    "纸张沙沙作响，小澪确认没有新的正文要寄出。",
    "旧信不会重播，但今天可以收到一份新的小纸条。",
    "邮差挥挥手：没有新正文，祝你今晚睡个好觉。",
    "下一份惊喜还在路上，这一次就先轻轻说声你好。",
    "小澪把日历翻了一页，信箱暂时没有新增任务。",
    "小小的提醒：你已经收到原信，这里没有第二份。",
    "信箱门打开又关上，带走的是一声俏皮的问候。",
    "星光落在信封上，今天没有新的正文等待寄出。",
    "小澪在桌边伸了个懒腰，确认本轮没有新任务。",
    "这次的消息很简单：旧信已处理，愿你一切顺利。",
    "新故事还在长大，暂时先用一句晚安陪伴你。",
    "你的小小邮局运行正常，重复邮件没有偷偷溜出去。",
    "小澪正在为下一封新信留出空白，今天先到这里。",
)

def _fresh():
    return {'version':1,'available':list(range(len(send_mail.NOTICE_LINES))),
            'used':[],'extras_added':0,'last':None,'refills':0}

def _validate(data):
    if not isinstance(data,dict) or data.get('version')!=1:raise ValueError('invalid inventory')
    n=len(send_mail.NOTICE_LINES)+len(EXTRA_LINES)
    for key in ('available','used'):
        ids=data.get(key)
        if not isinstance(ids,list) or any(type(i)!=int or i<0 or i>=n for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('invalid ids')
    if set(data['available'])&set(data['used']):raise ValueError('overlapping ids')
    k=data.get('extras_added')
    if type(k)!=int or not 0<=k<=len(EXTRA_LINES):raise ValueError('invalid extras')
    if type(data.get('refills'))!=int or data['refills']<0:raise ValueError('invalid refills')
    last=data.get('last')
    if last is not None and (type(last)!=int or not 0<=last<n):raise ValueError('invalid last')
    if any(i>=len(send_mail.NOTICE_LINES)+k for i in data['available']+data['used']):
        raise ValueError('unreleased extra id')

def reserve(api):
    """CAS-reserve before SMTP: failure may consume a line, never duplicates a line."""
    StateBranch(api).ensure_branch()
    endpoint=f'/repos/{api.repo}/contents/{PATH}'
    for attempt in range(6):
        try:
            item=api.request('GET',endpoint+f'?ref={STATE_BRANCH}')
            state=json.loads(base64.b64decode(item['content']))
            sha=item['sha']
            _validate(state)
        except GitHubAPIError as exc:
            if exc.status!=404:raise
            state=_fresh();sha=None
        if len(state['available'])<LOW_WATERMARK:
            start=state['extras_added']
            end=min(start+REFILL_SIZE,len(EXTRA_LINES))
            state['available'].extend(range(len(send_mail.NOTICE_LINES)+start,
                                            len(send_mail.NOTICE_LINES)+end))
            state['extras_added']=end
            if end>start:state['refills']+=1
        if not state['available']:
            # Finite offline reserve exhausted: reshuffle used lines, avoiding immediate repeat.
            state['available']=state['used']
            state['used']=[]
            if not state['available']:raise ValueError('notice inventory empty')
        choices=[i for i in state['available'] if i!=state['last']]
        chosen=random.SystemRandom().choice(choices or state['available'])
        state['available'].remove(chosen)
        state['used'].append(chosen)
        state['last']=chosen
        _validate(state)
        payload={'branch':STATE_BRANCH,'message':'Reserve next notice line',
                 'content':base64.b64encode((json.dumps(state,ensure_ascii=False)+'\n').encode()).decode()}
        if sha:payload['sha']=sha
        try:
            ack=api.request('PUT',endpoint,payload)
            if not ack.get('content',{}).get('sha'):raise ValueError('inventory write unconfirmed')
            return (send_mail.NOTICE_LINES+EXTRA_LINES)[chosen]
        except GitHubAPIError as exc:
            if exc.status!=409 or attempt==5:raise
    raise ValueError('notice reservation conflict')
