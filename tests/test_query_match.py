# -*- coding:utf-8 -*-
"""本地求值语义验收（final-syntax.md §5）：text/owntext 层级、属性存在性 vs 空值、
WORD、大小写、正则、非 QName 属性；lxml 元素与非 lxml 元素（FakeEle）双路径。"""
import pytest

from happybrowser._locate.query import parse_loc, Q


class FakeEle:
    """非 lxml 元素：走 _EleView 的 .tag/.attrs/.text/_run_js 分支。"""

    def __init__(self, tag, attrs=None, text='', own=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.text = text
        self._own = own or []

    def _run_js(self, script):
        return self._own


@pytest.fixture(scope='module')
def els(doc):
    class E:
        ad = doc.xpath('//*[@id="ad"]')[0]
        btn = doc.xpath('//*[@data-testid="submit-btn"]')[0]
        span = doc.xpath('//span')[0]                        # <span>Cheers</span>
        upper = doc.xpath('//span[@class="Upper"]')[0]       # <SPAN CLASS="Upper">Case Test</SPAN>
        a = doc.xpath('//a')[0]
        p = doc.xpath('//p')[0]
        inp = doc.xpath('//input')[0]
        custom = doc.xpath('//custom-ele')[0]
        mix = doc.xpath('//div[contains(text(), "a")]')[0]   # <div>a<!--x-->b</div>
        li2 = doc.xpath('//li[@class="active"]')[0]
        img = doc.xpath('//img')[0]
    return E()


class TestTextLevels:
    def test_deep_text(self, els):
        assert parse_loc('text*=Cheers').match(els.btn)
        assert parse_loc('text*=提交').match(els.btn)
        assert parse_loc('owntext*=Cheers').match(els.btn) is False

    def test_own_text(self, els):
        assert parse_loc('owntext*=提交').match(els.btn)
        assert parse_loc('owntext*="!"').match(els.btn)
        assert parse_loc('owntext=提交').match(els.btn) is False   # 直属文本是 "提交 "（带空格）

    def test_comment_interleaved(self, els):
        assert parse_loc('text=ab').match(els.mix)
        assert parse_loc('owntext=ab').match(els.mix) is False
        assert parse_loc('owntext^=a').match(els.mix)
        assert parse_loc('text*=b').match(els.mix)

    def test_empty_text_eq(self, els):
        assert parse_loc('text=""').match(els.img)          # 无任何文本
        assert parse_loc('text=""').match(els.li2) is False

    def test_text_case_sensitive(self, els):
        assert parse_loc('text*=case').match(els.upper) is False
        assert parse_loc('text*=Case').match(els.upper)


class TestAttr:
    def test_missing_vs_empty(self, els):
        assert parse_loc('@alt').match(els.img)
        assert parse_loc('alt=""').match(els.img)
        assert parse_loc('@alt').match(els.p) is False
        assert parse_loc('!@alt').match(els.p)
        assert parse_loc('alt=""').match(els.p) is False

    def test_word(self, els):
        assert parse_loc('class~=promo').match(els.ad)
        assert parse_loc('class~=ban').match(els.ad) is False
        assert parse_loc('class*=banner').match(els.ad)

    def test_any_attr(self, els):
        assert parse_loc('@*').match(els.ad)
        assert parse_loc('!@*').match(els.ad) is False

    def test_non_qname_attr(self, els):
        assert parse_loc('@":class"').match(els.custom)
        assert parse_loc('@":class"=x').match(els.custom)

    def test_non_qname_attr_fake(self):
        # lxml 的 HTML 解析器会丢弃 @click 属性名，浏览器 DOM 不会——用 FakeEle 覆盖
        ele = FakeEle('button', {'@click': 'submitHandler'})
        assert parse_loc('@"@click"*=submit').match(ele)
        assert parse_loc('@"@click"').match(ele)
        assert parse_loc('@"@click"*=noop').match(ele) is False

    def test_prefix_suffix(self, els):
        assert parse_loc('href^=https://').match(els.a)
        assert parse_loc('href$=.example/x').match(els.a)
        assert parse_loc('href*=ad.example').match(els.a)


class TestTag:
    def test_case_insensitive(self, els):
        assert parse_loc('span').match(els.upper)
        assert parse_loc('SPAN').match(els.upper)
        assert parse_loc('tag*=pa').match(els.upper)   # 标签模糊匹配须用 tag 关键字（span*=pa 是属性匹配）

    def test_star(self, els):
        assert parse_loc('*').match(els.img)

    def test_regex_on_tag(self, els):
        assert parse_loc(r'tag/="^cu"').match(els.custom)


class TestRegex:
    def test_text_regex(self, els):
        assert parse_loc(r'text/="^提交"').match(els.btn)
        assert parse_loc(r'text/="\d+"').match(els.li2)

    def test_attr_regex(self, els):
        assert parse_loc(r'href/="ad\.example$"').match(els.a) is False   # 以 /x 结尾
        assert parse_loc(r'href/="ad\.example"').match(els.a)


class TestBool:
    def test_or_and_not(self, els):
        assert parse_loc('(id=ad || .btn) && !@disabled').match(els.ad)
        assert parse_loc('(id=ad || .btn) && !@disabled').match(els.btn)
        assert parse_loc('(id=ad || .btn)').match(els.p) is False


class TestFakeEle:
    def test_attr(self):
        ele = FakeEle('a', {'href': 'xyz'})
        assert Q('a', href__contains='x').match(ele)
        assert Q('a', href__startswith='y').match(ele) is False

    def test_own_texts(self):
        assert Q.owntext.contains('ok').match(FakeEle('div', own=['ok']))
        assert Q.owntext.contains('ok').match(FakeEle('div', own=['nope'])) is False

    def test_deep_text_attr(self):
        assert Q.text.eq('hello').match(FakeEle('p', text='hello'))
        assert Q.tag.eq('SPAN').match(FakeEle('SPAN'))

    def test_missing_attr_negation(self):
        assert (~Q.id('ad')).match(FakeEle('p'))
