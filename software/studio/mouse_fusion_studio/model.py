"""World geometry (X right, Y up), independent image pose and transaction history."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math
import uuid
from .triggers import Trigger, find_conflicts
from mouse_fusion.run_policy import DEFAULT_POLICY,normalize_policy


def number(value, name, low=-1_000_000, high=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} 必须是 {low} 到 {high} 的有限数值')
    return value


@dataclass
class Point:
    x: float
    y: float
    duration_ms: int = 300
    curve: str = 'smooth'
    easing: str = 'linear'
    handle_in: tuple | None = None
    handle_out: tuple | None = None

    def validate(self):
        number(self.x, 'X'); number(self.y, 'Y')
        number(self.duration_ms, '持续时间', 1, 60000)
        if type(self.duration_ms) is not int:
            raise ValueError('持续时间必须为整数毫秒')
        if self.curve not in ('linear', 'bezier', 'smooth') or self.easing not in ('linear', 'ease_in', 'ease_out', 'ease_in_out'):
            raise ValueError('未知曲线或缓动类型')
        for handle in (self.handle_in, self.handle_out):
            if handle is not None:
                if not isinstance(handle, (list, tuple)) or len(handle) != 2:
                    raise ValueError('贝塞尔手柄必须为世界坐标 X/Y')
                number(handle[0], '手柄X'); number(handle[1], '手柄Y')


@dataclass
class ReferenceImage:
    name: str = ''
    data: str = ''  # embedded original PNG/JPEG bytes in base64
    width: int = 0
    height: int = 0
    x: float = 0
    y: float = 0
    scale: float = 1
    rotation: float = 0  # counter-clockwise degrees in the world
    opacity: float = .55
    visible: bool = True
    locked: bool = False
    reference_x: float = 0  # local image pixels, origin top-left, Y down
    reference_y: float = 0

    def validate(self):
        number(self.x, '底图X'); number(self.y, '底图Y')
        number(self.scale, '底图缩放', .001, 100)
        number(self.rotation, '底图旋转', -36000, 36000)
        number(self.opacity, '透明度', 0, 1)
        number(self.width, '图片宽', 0, 8192); number(self.height, '图片高', 0, 8192)
        number(self.reference_x, '参考像素X', 0, self.width)
        number(self.reference_y, '参考像素Y', 0, self.height)
        if type(self.visible) is not bool or type(self.locked) is not bool:
            raise ValueError('底图显示和锁定状态须为布尔值')

    def pixel_to_world(self, px, py):
        angle = math.radians(self.rotation)
        c, s = math.cos(angle), math.sin(angle)
        return self.x + self.scale * (px*c + py*s), self.y + self.scale * (px*s - py*c)

    def world_to_pixel(self, x, y):
        angle = math.radians(self.rotation)
        c, s = math.cos(angle), math.sin(angle)
        x, y = (x-self.x)/self.scale, (y-self.y)/self.scale
        return c*x + s*y, s*x - c*y

    def contains(self, x, y):
        px, py = self.world_to_pixel(x, y)
        return self.visible and 0 <= px <= self.width and 0 <= py <= self.height


@dataclass
class Mode:
    name: str = '新模式'
    points: list = field(default_factory=list)
    image: ReferenceImage | None = None
    perturbation: dict = field(default_factory=lambda: dict(amplitude=0, frequency_hz=1, seed=0))
    mode_id: str = field(default_factory=lambda:uuid.uuid4().hex)
    triggers: list = field(default_factory=list)
    run_policy: dict = field(default_factory=lambda:dict(DEFAULT_POLICY))

    def validate(self):
        normalize_policy(self.run_policy)
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError('模式名称须为 1..100 字符')
        if not isinstance(self.mode_id,str) or not self.mode_id or len(self.mode_id)>64: raise ValueError('模式 ID 无效')
        if len(self.triggers)>32: raise ValueError('每个模式最多 32 个触发器')
        for binding in self.triggers: binding.validate()
        if len(self.points) > 2048:
            raise ValueError('每条轨迹最多 2048 个点')
        for point in self.points: point.validate()
        if sum(p.duration_ms for p in self.points[:-1]) > 300000:
            raise ValueError('轨迹总时长最多 300 秒')
        if self.image: self.image.validate()
        if set(self.perturbation) != {'amplitude', 'frequency_hz', 'seed'}:
            from mouse_fusion.perturbation import normalize_perturbation
            normalize_perturbation(self.perturbation)
            return
        number(self.perturbation['amplitude'], '扰动幅度', 0, 1000)
        number(self.perturbation['frequency_hz'], '扰动频率', .01, 10)
        if type(self.perturbation['seed']) is not int:
            raise ValueError('扰动种子须为整数')
        number(self.perturbation['seed'], '扰动种子', 0, 65535)


@dataclass
class View:
    center_x: float = 0
    center_y: float = 0
    zoom: float = 1  # screen pixels / world unit; independent of image.scale

    def world_to_screen(self, x, y, width, height):
        return width/2 + (x-self.center_x)*self.zoom, height/2 - (y-self.center_y)*self.zoom

    def screen_to_world(self, x, y, width, height):
        return self.center_x + (x-width/2)/self.zoom, self.center_y - (y-height/2)/self.zoom

    def zoom_at(self, factor, x, y, width, height):
        wx, wy = self.screen_to_world(x, y, width, height)
        self.zoom = min(30, max(.03, self.zoom*factor))
        self.center_x = wx - (x-width/2)/self.zoom
        self.center_y = wy + (y-height/2)/self.zoom

    def pan(self, dx, dy):
        self.center_x -= dx/self.zoom
        self.center_y += dy/self.zoom


@dataclass
class Project:
    modes: list = field(default_factory=lambda: [Mode('轨迹 01', [Point(0, 0), Point(60, 45), Point(150, 0)])])
    active_mode: int = 0
    view: View = field(default_factory=View)
    editor_state: dict = field(default_factory=dict)
    bypass: dict = field(default_factory=lambda:dict(button=None,behavior='hold'))

    def validate(self):
        from mouse_fusion.bypass import normalize_config
        self.bypass=normalize_config(self.bypass)
        if not 1 <= len(self.modes) <= 32: raise ValueError('工程须包含 1..32 个模式')
        if type(self.active_mode) is not int or not 0 <= self.active_mode < len(self.modes):
            raise ValueError('当前模式索引无效')
        for mode in self.modes: mode.validate()
        ids={mode.mode_id for mode in self.modes}
        if len(ids)!=len(self.modes): raise ValueError('模式 ID 重复')
        bindings=[b for mode in self.modes for b in mode.triggers]
        if len(bindings)>128 or len({b.trigger_id for b in bindings})!=len(bindings): raise ValueError('触发器过多或 ID 重复')
        for binding in bindings:
            if binding.action in ('switch_mode','start_trajectory','stop_trajectory','toggle_mode','start_mode_trajectory') and binding.target_mode not in ids:
                raise ValueError('触发器目标模式不存在；请先调整相关绑定')
        errors=[c for c in find_conflicts(bindings,bypass=self.bypass) if c.severity=='error']
        if errors: raise ValueError(errors[0].message)
        number(self.view.zoom, '画布缩放', .03, 30)
        number(self.view.center_x, '视图X'); number(self.view.center_y, '视图Y')
        if not isinstance(self.editor_state, dict): raise ValueError('无效编辑器状态')
        state=self.editor_state
        if 'edit_mode' in state and state['edit_mode'] not in ('轨迹编辑','图片编辑'): raise ValueError('无效编辑模式')
        if 'display' in state and (not isinstance(state['display'],dict) or any(type(v) is not bool for v in state['display'].values())):
            raise ValueError('显示设置须为布尔值映射')
        if 'selection' in state and (not isinstance(state['selection'],list) or any(type(i) is not int or not 0<=i<2048 for i in state['selection'])):
            raise ValueError('选区索引无效')
        if 'grid_spacing' in state: number(state['grid_spacing'],'网格间距',.01,100000)
        if 'playhead_ms' in state: number(state['playhead_ms'],'播放位置',0,300000)
        for name in ('uniform_scale','keyboard_enabled'):
            if name in state and type(state[name]) is not bool: raise ValueError(f'{name} 须为布尔值')
        if 'transform' in state and (not isinstance(state['transform'],dict) or any(not isinstance(v,str) for v in state['transform'].values())):
            raise ValueError('变换面板状态无效')

    def to_dict(self):
        schema=3 if any(t.action=='start_mode_trajectory' or any(k.startswith('wheel_') for k in t.buttons) for m in self.modes for t in m.triggers) else 2 if any(t.action in ('toggle_mode','start_current_trajectory') for m in self.modes for t in m.triggers) else 1
        return dict(format='mouse-fusion-studio', version=1, trigger_schema_version=schema, run_policy_schema_version=1, bypass_schema_version=1, **asdict(self))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get('format') != 'mouse-fusion-studio' or data.get('version') != 1:
            raise ValueError('不支持的工程格式/版本')
        if data.get('trigger_schema_version',1) not in (1,2,3): raise ValueError('不支持的触发配置版本')
        if data.get('run_policy_schema_version',1)!=1: raise ValueError('不支持的运行规则版本')
        if data.get('bypass_schema_version',1)!=1: raise ValueError('不支持的纯物理模式配置版本')
        try:
            modes = []
            for index,item in enumerate(data['modes']):
                points = [Point(**p) for p in item.get('points', [])]
                for p in points:
                    if p.handle_in is not None: p.handle_in = tuple(p.handle_in)
                    if p.handle_out is not None: p.handle_out = tuple(p.handle_out)
                modes.append(Mode(item['name'], points,
                                  ReferenceImage(**item['image']) if item.get('image') else None,
                                  item.get('perturbation', dict(amplitude=0, frequency_hz=1, seed=0)),
                                  item.get('mode_id',f'legacy-mode-{index+1}'),[Trigger(**b) for b in item.get('triggers',[])],
                                  normalize_policy(item.get('run_policy',{}))))
            result = cls(modes, data.get('active_mode', 0), View(**data.get('view', {})), data.get('editor_state', {}),data.get('bypass',dict(button=None,behavior='hold')))
            result.validate()
            return result
        except (TypeError, KeyError, AttributeError) as error:
            raise ValueError('工程数据结构无效') from error


class Editor:
    """A whole drag is a single history transaction. Image bytes are immutable."""
    def __init__(self, project=None):
        self.project = project or Project()
        self.selection = set()
        self._undo, self._redo, self._pending = [], [], None

    @property
    def mode(self): return self.project.modes[self.project.active_mode]
    @property
    def can_undo(self): return bool(self._undo)
    @property
    def can_redo(self): return bool(self._redo)

    def _snapshot(self): return deepcopy(self.project), self.selection.copy()
    def _restore(self, snapshot): self.project, self.selection = deepcopy(snapshot)

    def begin(self):
        if self._pending is None: self._pending = self._snapshot()

    def finish(self):
        if self._pending is not None:
            before, self._pending = self._pending, None
            try: self.project.validate()
            except ValueError:
                self._restore(before)
                raise
            if before[0] != self.project:
                self._undo.append(before)
                self._undo = self._undo[-100:]
                self._redo.clear()

    def cancel(self):
        if self._pending is not None:
            self._restore(self._pending)
            self._pending = None

    def apply(self, change):
        self.begin()
        try:
            change()
            self.finish()
        except Exception:
            self.cancel()
            raise

    def undo(self):
        self.cancel()
        if self._undo:
            self._redo.append(self._snapshot()); self._restore(self._undo.pop())

    def redo(self):
        self.cancel()
        if self._redo:
            self._undo.append(self._snapshot()); self._restore(self._redo.pop())

    def select_box(self, bounds, additive=False):
        x0, y0, x1, y1 = bounds
        found = {i for i, p in enumerate(self.mode.points) if min(x0,x1) <= p.x <= max(x0,x1) and min(y0,y1) <= p.y <= max(y0,y1)}
        self.selection = self.selection | found if additive else found

    def _transform(self, sx, sy, dx, dy, pivot):
        for value in (sx, sy, dx, dy, *pivot): number(value, '变换参数')
        def mapped(pos): return pivot[0]+(pos[0]-pivot[0])*sx+dx, pivot[1]+(pos[1]-pivot[1])*sy+dy
        changes = []
        for i in sorted(self.selection):
            p = deepcopy(self.mode.points[i]); p.x, p.y = mapped((p.x, p.y))
            if p.handle_in is not None: p.handle_in = mapped(p.handle_in)
            if p.handle_out is not None: p.handle_out = mapped(p.handle_out)
            p.validate(); changes.append((i, p))
        for i, p in changes: self.mode.points[i] = p

    def selection_center(self):
        points = [self.mode.points[i] for i in self.selection]
        if not points: raise ValueError('请先选择控制点')
        return (min(p.x for p in points)+max(p.x for p in points))/2, (min(p.y for p in points)+max(p.y for p in points))/2

    def transform_selection(self, *, sx=1, sy=1, dx=0, dy=0, pivot=None):
        if not self.selection: raise ValueError('请先选择控制点')
        pivot = self.selection_center() if pivot is None else pivot
        self.apply(lambda: self._transform(sx, sy, dx, dy, pivot))

    def translate_live(self, dx, dy): self._transform(1, 1, dx, dy, (0, 0))

    def edit_point(self, index, *, x=None, y=None, order=None, **params):
        def change():
            p = self.mode.points[index]
            nx, ny = p.x if x is None else x, p.y if y is None else y
            dx, dy = nx-p.x, ny-p.y
            p.x, p.y = nx, ny
            for field_name in ('handle_in', 'handle_out'):
                handle = getattr(p, field_name)
                if handle is not None: setattr(p, field_name, (handle[0]+dx, handle[1]+dy))
            for key, value in params.items():
                if key not in ('duration_ms', 'curve', 'easing', 'handle_in', 'handle_out'): raise ValueError('未知点参数')
                setattr(p, key, value)
            p.validate()
            target = index if order is None else order-1
            if type(target) is not int or not 0 <= target < len(self.mode.points): raise ValueError('执行顺序超出范围')
            self.mode.points.insert(target, self.mode.points.pop(index))
            self.selection = {target}
        self.apply(change)

    def add_point(self, x, y):
        def change():
            self.mode.points.append(Point(x,y)); self.selection = {len(self.mode.points)-1}
        self.apply(change)

    def insert_after(self, index):
        def change():
            p = self.mode.points[index]
            next_p = self.mode.points[index+1] if index+1 < len(self.mode.points) else Point(p.x+30,p.y)
            self.mode.points.insert(index+1, Point((p.x+next_p.x)/2,(p.y+next_p.y)/2, p.duration_ms, p.curve, p.easing))
            self.selection = {index+1}
        self.apply(change)

    def duplicate_selected(self):
        def change():
            new, selection = [], set()
            for i,p in enumerate(self.mode.points):
                new.append(p)
                if i in self.selection:
                    new.append(deepcopy(p)); selection.add(len(new)-1)
            self.mode.points, self.selection = new, selection
        self.apply(change)

    def delete_selected(self):
        def change():
            self.mode.points = [p for i,p in enumerate(self.mode.points) if i not in self.selection]
            self.selection.clear()
        self.apply(change)

    def set_image_pose(self, **params):
        if not self.mode.image: raise ValueError('请先导入参考图片')
        def change():
            for key, value in params.items():
                if key not in ('x','y','scale','rotation','opacity','visible','locked','reference_x','reference_y'): raise ValueError('未知底图参数')
                setattr(self.mode.image, key, value)
        self.apply(change)

    def move_image_live(self, dx, dy):
        image = self.mode.image
        if not image or image.locked: return False
        number(image.x+dx,'底图X'); number(image.y+dy,'底图Y')
        image.x += dx; image.y += dy
        return True

    def align_image(self, px=0, py=0):
        if not self.mode.image: raise ValueError('请先导入参考图片')
        image = self.mode.image
        wx, wy = image.pixel_to_world(px,py)
        self.set_image_pose(x=image.x-wx, y=image.y-wy, reference_x=px, reference_y=py)

    def merge_project(self, imported):
        imported.validate()
        def change():
            modes=deepcopy(imported.modes)
            mapping={m.mode_id:uuid.uuid4().hex for m in modes}
            active=[b for m in self.project.modes for b in m.triggers]
            for mode in modes:
                mode.mode_id=mapping[mode.mode_id]
                for binding in mode.triggers:
                    binding.trigger_id=uuid.uuid4().hex
                    if binding.target_mode is not None: binding.target_mode=mapping[binding.target_mode]
                    if any(c.severity=='error' for c in find_conflicts(active+[binding])): binding.enabled=False
                    active.append(binding)
            self.project.modes.extend(modes)
        self.apply(change)
