# -*- coding: utf-8 -*-
"""測試替身（test doubles）。

FakeFirestore：輕量 Firestore 模擬，讓需要 db 的邏輯能在無真 Firestore / 無網路下被測。
支援目前程式用到的查詢形態：collection / document / where(==, !=, array_contains) /
order_by(no-op) / limit / stream / get / set / update。

純 stdlib，可被 pytest 與 plain-assert runner 共用。
"""


class FakeDoc:
    def __init__(self, doc_id, data, parent=None):
        self.id = doc_id
        self._data = data            # None 表示文件不存在
        self._parent = parent        # 所屬 FakeCollection（供 reference.update 寫回）

    def to_dict(self):
        return dict(self._data) if self._data is not None else None

    @property
    def exists(self):
        return self._data is not None

    @property
    def reference(self):
        return FakeDocRef(self.id, self._parent)


class FakeDocRef:
    def __init__(self, doc_id, parent):
        self.id = doc_id
        self._parent = parent

    def get(self):
        data = self._parent._docs.get(self.id) if self._parent else None
        return FakeDoc(self.id, data, self._parent)

    def set(self, data, merge=False):
        if self._parent is None:
            return
        if merge and self.id in self._parent._docs:
            self._parent._docs[self.id].update(data)
        else:
            self._parent._docs[self.id] = dict(data)

    def update(self, data):
        if self._parent is None:
            return
        self._parent._docs.setdefault(self.id, {}).update(data)


def _match(value, op, target):
    if op == '==':
        return value == target
    if op == '!=':
        return value != target
    if op == 'array_contains':
        return isinstance(value, list) and target in value
    if op == 'in':
        return value in (target or [])
    raise ValueError(f'FakeFirestore 不支援的 op：{op}')


class FakeQuery:
    def __init__(self, collection, predicates=None, limit_n=None):
        self._c = collection
        self._preds = predicates or []
        self._limit = limit_n

    def where(self, field, op, value):
        return FakeQuery(self._c, self._preds + [(field, op, value)], self._limit)

    def order_by(self, *a, **k):
        return self   # 測試不關心排序；list_projects 等在 Python 端排序

    def limit(self, n):
        return FakeQuery(self._c, self._preds, n)

    def stream(self):
        out = []
        for doc_id, data in self._c._docs.items():
            if all(_match((data or {}).get(f), op, v) for f, op, v in self._preds):
                out.append(FakeDoc(doc_id, data, self._c))
        return out if self._limit is None else out[:self._limit]


class FakeCollection(FakeQuery):
    def __init__(self, docs=None):
        self._docs = dict(docs or {})
        super().__init__(self)

    def document(self, doc_id=None):
        if doc_id is None:
            doc_id = f'auto-{len(self._docs) + 1}'
        return FakeDocRef(doc_id, self)


class FakeFirestore:
    """db 取代品：db.collection('x').where(...).stream() 等。

    用法：FakeFirestore({'projects': {'p1': {...}, 'p2': {...}}})
    """
    def __init__(self, data=None):
        self._cols = {name: FakeCollection(docs) for name, docs in (data or {}).items()}

    def collection(self, name):
        return self._cols.setdefault(name, FakeCollection())
