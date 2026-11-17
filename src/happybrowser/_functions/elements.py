# -*- coding: utf-8 -*-
"""元素容器（收敛自 DrissionPage © g1879 _functions/elements.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
原 592 行的 ElementsList 过滤 DSL（filter/filter_one/vals 哨兵协议）已按设计砍除，
Python 推导式 + Q.match() 可覆盖；此处仅保留极简 list 子类作为返回类型挂载点。"""


class ElementsList(list):
    """元素列表。过滤请用推导式，如 [e for e in eles if e.attr('href')]。"""

    def __init__(self, owner=None, iterable=()):
        self.owner = owner
        super().__init__(iterable)

    def __repr__(self):
        return f'<ElementsList {list.__repr__(self)}>'

    @property
    def texts(self):
        return [e.text if hasattr(e, 'text') else e for e in self]

    @property
    def tags(self):
        return [e.tag if hasattr(e, 'tag') else None for e in self]
