"""标准库内存桩：仅实现本项目风险/交付服务实际用到的 SQLAlchemy 子集。

不修改 backend 源码；通过 sys.modules 注入后导入真实 src.* 模块运行。
"""
import sys
import types


class Criterion:
    def __init__(self, model, name, op, value):
        self.model, self.name, self.op, self.value = model, name, op, value

    def matches(self, obj):
        actual = getattr(obj, self.name, None)
        if self.op == '==':
            return actual == self.value
        if self.op == '!=':
            return actual != self.value
        if self.op == 'in':
            return actual in self.value
        raise ValueError(self.op)


class ColumnInfo:
    """数据描述符：类访问返回列对象（供构造条件），实例访问返回真实值。"""
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.model = None
        self.primary_key = kwargs.get('primary_key', False)
        self.nullable = kwargs.get('nullable', True)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__.get(self.name)

    def __set__(self, instance, value):
        instance.__dict__[self.name] = value

    def __eq__(self, other):
        return Criterion(self.model, self.name, '==', other)

    def __ne__(self, other):
        return Criterion(self.model, self.name, '!=', other)

    def in_(self, values):
        return Criterion(self.model, self.name, 'in', list(values))

    def __hash__(self):
        return id(self)


class _Select:
    def __init__(self, targets):
        self.targets = targets
        self.criteria = []

    def where(self, *cs):
        self.criteria.extend(cs)
        return self

    def with_for_update(self):
        return self

    def execution_options(self, **kw):
        return self

    def order_by(self, *a):
        return self

    def select_from(self, model):
        self.targets = [model]
        return self


def select(*targets):
    return _Select(list(targets))


# 轻量类型桩，构造参数全部忽略
def _ignored(*a, **k):
    return None


class _Type:
    def __init__(self, *a, **k):
        pass


String = Integer = Float = JSON = ForeignKey = Numeric = _Type


class CheckConstraint:
    def __init__(self, *a, **k):
        self.args = a


class UniqueConstraint:
    def __init__(self, *a, **k):
        self.cols = a


class Index:
    def __init__(self, name, *cols, **k):
        self.name = name
        self.cols = cols
        self.kwargs = k


class _Mapped:
    def __class_getitem__(cls, item):
        return cls

    def __getitem__(self, item):
        return self


Mapped = _Mapped()


def mapped_column(*args, **kwargs):
    name = kwargs.get('__name__')
    col = ColumnInfo(name, *args, **kwargs)
    if args and isinstance(args[0], str):
        pass
    # 主键/可空等参数仅记录，不影响内存行为
    col.primary_key = kwargs.get('primary_key', False)
    col.nullable = kwargs.get('nullable', True)
    return col


class _Table:
    def __init__(self, name, columns):
        self.name = name
        self.columns = columns


class _Registry:
    pass


class _MetaData:
    def __init__(self):
        self.tables = {}

    def create_all(self, engine):
        return None


class DeclarativeBase:
    metadata = _MetaData()

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _build_model(cls):
    cols = []
    for name, val in list(vars(cls).items()):
        if isinstance(val, ColumnInfo):
            val.name = name
            val.model = cls
            cols.append(val)
    cls.__table__ = _Table(cls.__tablename__, cols)
    cls._columns = cols
    return cls


# 元类在类创建后绑定列信息
class _Meta(type):
    def __new__(mcs, name, bases, ns):
        cls = super().__new__(mcs, name, bases, ns)
        if '__tablename__' in ns:
            _build_model(cls)
        return cls


DeclarativeBase = _Meta('DeclarativeBase', (), {
    '__init__': DeclarativeBase.__init__,
    'metadata': _MetaData(),
})


class Session:
    """所有实例共享同一内存库，便于模拟两个会话并发看到已提交数据。"""
    _store = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _bucket(self, model):
        return Session._store.setdefault(model, {})

    def get(self, model, pk):
        return self._bucket(model).get(pk)

    def add(self, obj):
        model = type(obj)
        bucket = self._bucket(model)
        pk_col = next((c for c in model._columns if c.primary_key), None)
        pk = getattr(obj, pk_col.name, None)
        if pk is None:
            pk = max(bucket.keys(), default=0) + 1
            setattr(obj, pk_col.name, pk)
        bucket[pk] = obj

    def flush(self):
        return None

    def commit(self):
        return None

    def _eval(self, q):
        model = None
        for t in q.targets:
            if isinstance(t, ColumnInfo):
                model = t.model
            elif isinstance(t, type):
                model = t
        rows = list(self._bucket(model).values())
        out = []
        for row in rows:
            if all(c.matches(row) for c in q.criteria):
                out.append(row)
        return out, q.targets

    def scalars(self, q):
        rows, _ = self._eval(q)
        return list(rows)

    def scalar(self, q):
        rows, targets = self._eval(q)
        if not rows:
            # 聚合 count 在空表时返回 0；普通主键查询返回 None
            if targets and isinstance(targets[0], tuple) and targets[0][0] == 'count':
                return 0
            return None
        if isinstance(targets[0], tuple) and targets[0][0] == 'count':
            return len(rows)
        if isinstance(targets[0], ColumnInfo):
            return getattr(rows[0], targets[0].name)
        return rows[0]

    def query(self, model):
        return _Query(self, model)


class _Query:
    def __init__(self, s, model):
        self.s, self.model = s, model
        self.criteria = []

    def filter_by(self, **kw):
        for k, v in kw.items():
            col = next(c for c in self.model._columns if c.name == k)
            self.criteria.append(Criterion(self.model, k, '==', v))
        return self

    def with_for_update(self):
        return self

    def one_or_none(self):
        rows = [o for o in self.s._bucket(self.model).values()
                if all(c.matches(o) for c in self.criteria)]
        return rows[0] if rows else None

    def one(self):
        return self.one_or_none()


def create_engine(url, **kw):
    return types.SimpleNamespace(url=url)


class _Func:
    def count(self, *a):
        return ('count', a)


func = _Func()


def sessionmaker(engine=None, **kw):
    return Session


def install(backend_dir):
    """构造并注入 sqlalchemy / fastapi 桩，返回待 import 的路径设置。"""
    sa = types.ModuleType('sqlalchemy')
    sa.select = select
    sa.String = String
    sa.Integer = Integer
    sa.Float = Float
    sa.JSON = JSON
    sa.ForeignKey = ForeignKey
    sa.Numeric = Numeric
    sa.CheckConstraint = CheckConstraint
    sa.UniqueConstraint = UniqueConstraint
    sa.Index = Index
    sa.create_engine = create_engine
    sa.func = func
    sa.sessionmaker = sessionmaker
    orm = types.ModuleType('sqlalchemy.orm')
    orm.Mapped = Mapped
    orm.mapped_column = mapped_column
    orm.sessionmaker = sessionmaker
    orm.DeclarativeBase = DeclarativeBase
    sa.orm = orm
    exc = types.ModuleType('sqlalchemy.exc')

    class IntegrityError(Exception):
        pass

    exc.IntegrityError = IntegrityError
    sys.modules['sqlalchemy'] = sa
    sys.modules['sqlalchemy.orm'] = orm
    sys.modules['sqlalchemy.exc'] = exc

    fastapi = types.ModuleType('fastapi')

    class HTTPException(Exception):
        def __init__(self, status_code=400, detail=''):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    fastapi.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), patch=lambda *a, **k: (lambda f: f))
    fastapi.Depends = lambda *a, **k: None
    sys.modules['fastapi'] = fastapi

    # PyJWT 桩：seed 仅用 hash_password（hashlib），token 不会在测试中调用
    jwt_stub = types.ModuleType('jwt')
    jwt_stub.encode = lambda payload, secret, algorithm=None: 'stub-token'
    jwt_stub.decode = lambda token, secret, algorithms=None: {}
    sys.modules['jwt'] = jwt_stub

    # fastapi.security 桩
    security = types.ModuleType('fastapi.security')
    security.HTTPBearer = lambda *a, **k: None
    sys.modules['fastapi.security'] = security

    sys.path.insert(0, str(backend_dir))
    return sa, fastapi
