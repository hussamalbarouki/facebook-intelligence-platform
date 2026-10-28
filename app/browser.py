"""Optional browser layer; does not alter or patch Facebook CLI.
It only reads information rendered to an authenticated, authorized browser session.
No CAPTCHA bypass, invisible GraphQL endpoints, or account rotation to evade restrictions.
"""
import asyncio, io, json, re, time
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from .core import validate_ref

class BrowserManager:
    def __init__(self):
        self.playwright=None; self.browser=None; self.sessions={}; self.lock=asyncio.Lock()

    async def init(self):
        if not self.playwright:
            self.playwright=await async_playwright().start()
            self.browser=await self.playwright.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])

    async def open(self,account_id,storage_state=None):
        async with self.lock:
            await self.init()
            if account_id in self.sessions:
                return self.sessions[account_id]['page']
            ctx=await self.browser.new_context(viewport={'width':1100,'height':780}, locale='en-US',storage_state=storage_state)
            page=await ctx.new_page()
            self.sessions[account_id]={'context':ctx,'page':page,'created':time.time()}
            await page.goto('https://www.facebook.com/',wait_until='domcontentloaded',timeout=35000)
            return page

    async def screenshot(self,account_id):
        obj=self.sessions.get(account_id)
        if not obj:raise ValueError('Browser session not started')
        return await obj['page'].screenshot(type='jpeg',quality=65)

    async def action(self,account_id,kind,**kwargs):
        obj=self.sessions.get(account_id)
        if not obj:raise ValueError('Browser session not started')
        page=obj['page']
        if kind=='click':
            x,y=float(kwargs['x']),float(kwargs['y'])
            if not (0<=x<=1100 and 0<=y<=780):raise ValueError('Out of bounds')
            await page.mouse.click(x,y)
        elif kind=='type':
            value=kwargs.get('value','')
            if len(value)>1024:raise ValueError('Text too long')
            await page.keyboard.insert_text(value)
        elif kind=='key':
            if kwargs['value'] not in ('Enter','Tab','Backspace','Escape','ArrowDown','ArrowUp'):raise ValueError('Unsupported key')
            await page.keyboard.press(kwargs['value'])
        elif kind=='scroll':
            await page.mouse.wheel(0,min(max(int(kwargs.get('delta',600)),-1000),1000))
        else:raise ValueError('Unsupported interaction')
        await page.wait_for_timeout(350)

    async def snapshot(self,account_id):
        obj=self.sessions.get(account_id)
        if not obj:raise ValueError('Browser session not started')
        state=await obj['context'].storage_state()
        cookies=state.get('cookies',[])
        def take(name):
            for c in cookies:
                if c.get('name')==name and 'facebook.com' in c.get('domain',''):
                    return c.get('value')
        return {'storage_state':state,'c_user':take('c_user'),'xs':take('xs')}

    async def close(self,account_id):
        obj=self.sessions.pop(account_id,None)
        if obj:await obj['context'].close()

    async def shutdown(self):
        for key in list(self.sessions):await self.close(key)
        if self.browser:await self.browser.close()
        if self.playwright:await self.playwright.stop()
        self.playwright=None;self.browser=None

    async def collect_reactors(self,ref,storage_state,limit=500):
        """Best effort list from browser-visible reaction panel. Never claim exhaustive results."""
        validate_ref(ref)
        if not ref.startswith('https://') and not ref.startswith('http://'):
            raise ValueError('Reaction identities require a full Facebook post URL')
        await self.init()
        ctx=await self.browser.new_context(storage_state=storage_state,locale='en-US',viewport={'width':1200,'height':850})
        page=await ctx.new_page()
        names=[];seen=set(); notes=[]
        try:
            await page.goto(ref,wait_until='domcontentloaded',timeout=45000)
            await page.wait_for_timeout(2000)
            if any(k in page.url.lower() for k in ('/login','checkpoint')):
                return {'status':'unavailable','complete':False,'people':[],'count':0,'notes':['Facebook requested login or verification']}
            # Click a visible reactions count/summary. Facebook's markup changes often.
            candidates=[
                page.locator('[aria-label*="reaction" i]').first,
                page.locator('[aria-label*="Like" i]').first,
                page.get_by_text(re.compile(r'All reactions|See who reacted',re.I)).first,
            ]
            opened=False
            for el in candidates:
                try:
                    if await el.count() and await el.is_visible(timeout=1200):
                        await el.click(timeout=3000); opened=True;break
                except Exception:continue
            if not opened:
                return {'status':'unavailable','complete':False,'people':[],'count':0,'notes':['No visible reaction list was available to this account']}
            await page.wait_for_timeout(900)
            for _ in range(min(60,max(1,(limit+19)//20))):
                # Count only visible profile anchors in the currently opened dialog.
                profiles=await page.locator('[role="dialog"] a[href], [role="dialog"] [role="link"]').evaluate_all('''nodes => nodes.map(n=>({name:(n.innerText||n.getAttribute('aria-label')||'').trim(),url:n.href||n.getAttribute('href')||''})).filter(x=>x.name && x.url)''')
                count_before=len(names)
                for p in profiles:
                    url=p.get('url','');name=p.get('name','').split('\n')[0][:160]
                    if not url.startswith('http'):continue
                    parsed=urlparse(url)
                    if parsed.hostname not in ('www.facebook.com','facebook.com','m.facebook.com'):continue
                    if any(s in parsed.path for s in ('/posts/','/reactions/','/photo','/watch')):continue
                    key=url.split('?')[0]
                    if key in seen or not name:continue
                    seen.add(key);names.append({'name':name,'profile_url':key})
                    if len(names)>=limit:break
                if len(names)>=limit: notes.append('Operator limit reached');break
                # Scroll the element with the largest scrollable area inside the dialog.
                await page.evaluate('''() => { const d=document.querySelector('[role=dialog]'); if(!d) return; const es=[d,...d.querySelectorAll('*')]; const e=es.sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight))[0]; if(e) e.scrollBy(0,700); }''')
                await page.wait_for_timeout(500)
                if len(names)==count_before:break
            return {'status':'partial' if names else 'unavailable','complete':False,'people':names,'count':len(names), 'notes':notes or ['Facebook controls visibility of reaction identities; completeness cannot be verified']}
        finally:
            await ctx.close()

browser=BrowserManager()