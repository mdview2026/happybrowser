# -*- coding:utf-8 -*-
"""共享 fixture：lxml 解析的"脏"HTML 文档，供本地求值与下推 soundness 测试用。"""
import pytest
from lxml import etree

HTML = """<html><body>
<div id="ad" class="banner promo"><a href="https://ad.example/x">广告</a></div>
<div class="btn el-input__inner" data-testid="submit-btn">提交 <span>Cheers</span>!</div>
<p>plain text</p>
<input type="filter" placeholder="搜索 项目名称" value=""/>
<custom-ele :class="x" @click="submit">own tail</custom-ele>
<div>a<!--x-->b</div>
<ul><li>1</li><li class="active">2</li><li>3</li></ul>
<img alt=""/>
<SPAN CLASS="Upper">Case Test</SPAN>
</body></html>"""


@pytest.fixture(scope='session')
def doc():
    return etree.fromstring(HTML, etree.HTMLParser())
